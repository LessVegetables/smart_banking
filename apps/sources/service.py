"""Mock source service (SPEC §5, T06): answers every source from the synthetic world.

MOCK: there are no calls to ЕГРЮЛ, ФНС, БКИ, ФССП, арбитраж, Федресурс or 115-ФЗ
systems. `MockSourceService.fetch(source, inn)` returns the source's payload from the
world after a simulated latency, or fails the way a real integration fails:

- `SourceUnavailable`: the source answered with an error;
- a timeout: the source hangs for `hang_seconds`, longer than any adapter timeout
  (SPEC §6: 10 s per source), then raises `SourceTimeout`;
- `SourceNotFound`: no company with this INN in the world.

The SourceResponse envelope (status, attempts, latency) is the adapter's job (T07);
this service only plays the source.

Failures come from forced scenarios per INN (always on) and, when `failures` is on,
from random draws at each source's error and timeout rates. Every draw is seeded by
(world seed, INN, source, call number), so the same sequence of calls fails the same
way on every run. Random failures are off by default, so that the same application
always gets the same decision.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import threading
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from apps.api.contracts import SourceId
from apps.world import Company, World, generate_world
from apps.world.__main__ import DEFAULT_OUT, read_world
from apps.world.generator import DEFAULT_AS_OF, DEFAULT_N, DEFAULT_SEED, GENERATOR_VERSION

# The sources this service plays; statement uploads (T23) come from the client.
SOURCES = tuple(s for s in SourceId if s != SourceId.STATEMENT_UPLOAD)
INTERNAL = frozenset({SourceId.ACCOUNTS, SourceId.CLIENT_CARD})  # never unavailable (SPEC §5)


class SourceError(Exception):
    def __init__(self, source: SourceId, inn: str, message: str):
        super().__init__(message)
        self.source, self.inn = source, inn


class SourceNotFound(SourceError):
    pass


class SourceUnavailable(SourceError):
    pass


class SourceTimeout(SourceError):
    pass


@dataclass(frozen=True)
class SourceBehaviour:
    """Latency is lognormal around `median_ms`; the rates apply when random failures are on."""
    median_ms: int
    sigma: float = 0.35
    error_rate: float = 0.0
    timeout_rate: float = 0.0


# MOCK: latencies are in the range of the T00 fixtures, failure rates are a guess.
DEFAULT_BEHAVIOUR: dict[SourceId, SourceBehaviour] = {
    SourceId.ACCOUNTS: SourceBehaviour(140),
    SourceId.CLIENT_CARD: SourceBehaviour(60),
    SourceId.EGRUL: SourceBehaviour(800, error_rate=0.02, timeout_rate=0.01),
    SourceId.FNS: SourceBehaviour(1800, error_rate=0.03, timeout_rate=0.02),
    SourceId.BKI: SourceBehaviour(2400, error_rate=0.02, timeout_rate=0.02),
    SourceId.FSSP: SourceBehaviour(1100, error_rate=0.05, timeout_rate=0.05),
    SourceId.ARBITRATION: SourceBehaviour(1300, error_rate=0.03, timeout_rate=0.03),
    SourceId.AML: SourceBehaviour(400, error_rate=0.01, timeout_rate=0.01),
}
HANG_SECONDS = 15.0  # longer than the 10 s adapter timeout of SPEC §6

_SCENARIO = re.compile(r'^(ok|error|timeout)(?::(\d+))?$')


@dataclass(frozen=True)
class Scenario:
    """A forced outcome for one INN and source: `error` or `timeout` on every call, or on
    the first `times` calls only (then the source recovers, which exercises retries);
    `ok` never fails, even with random failures on."""
    outcome: str
    times: int | None = None

    @classmethod
    def parse(cls, text: str) -> Scenario:
        """'timeout', 'error', 'ok', or 'error:1' for the first call only."""
        match = _SCENARIO.match(text.strip())
        if not match:
            raise ValueError(f'unknown scenario {text!r}: use ok, error, timeout, error:N or timeout:N')
        return cls(match[1], int(match[2]) if match[2] else None)


@dataclass(frozen=True)
class Settings:
    latency_scale: float = 1.0  # 0 answers at once (tests), 1 is the realistic latency
    failures: bool = False  # random failures at each source's rates
    hang_seconds: float = HANG_SECONDS
    behaviour: dict[SourceId, SourceBehaviour] = field(default_factory=lambda: dict(DEFAULT_BEHAVIOUR))
    scenarios: dict[str, dict[SourceId, Scenario]] = field(default_factory=dict)  # by INN

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        """MOCK_SOURCES_LATENCY_SCALE, MOCK_SOURCES_FAILURES (on/off), MOCK_SOURCES_HANG_SECONDS
        and MOCK_SOURCES_SCENARIOS, a JSON object {"<inn>": {"<source>": "<scenario>"}}."""
        env = os.environ if env is None else env
        settings = cls()
        if value := env.get('MOCK_SOURCES_LATENCY_SCALE'):
            settings = replace(settings, latency_scale=float(value))
        if value := env.get('MOCK_SOURCES_FAILURES'):
            if value.lower() not in ('on', 'off', 'true', 'false', '1', '0'):
                raise ValueError('MOCK_SOURCES_FAILURES must be on or off')
            settings = replace(settings, failures=value.lower() in ('on', 'true', '1'))
        if value := env.get('MOCK_SOURCES_HANG_SECONDS'):
            settings = replace(settings, hang_seconds=float(value))
        if value := env.get('MOCK_SOURCES_SCENARIOS'):
            settings = replace(settings, scenarios=parse_scenarios(json.loads(value)))
        return settings


def parse_scenarios(raw: dict[str, dict[str, str]]) -> dict[str, dict[SourceId, Scenario]]:
    return {inn: {SourceId(source): Scenario.parse(text) for source, text in by_source.items()}
            for inn, by_source in raw.items()}


def payload(company: Company, source: SourceId) -> BaseModel:
    """What the source knows about the company: the world's copy of its answer."""
    return getattr(company, source.value)


class MockSourceService:
    def __init__(self, world: World, settings: Settings | None = None):
        self.overrides = None  # Optional shared configuration provider, installed by the live API.
        self.world = world
        self.settings = settings or Settings()
        self._by_inn = {company.inn: company for company in world.companies}
        self._calls: Counter[tuple[str, SourceId]] = Counter()
        self._lock = threading.Lock()

    def company(self, inn: str) -> Company | None:
        return self._by_inn.get(inn)

    def reset(self) -> None:
        """Forget the call counts, so the next calls draw the same failures again."""
        with self._lock:
            self._calls.clear()

    async def fetch(self, source: SourceId | str, inn: str) -> BaseModel:
        """The source's payload for this INN, after the simulated latency, or a SourceError."""
        source = SourceId(source)
        if source not in SOURCES:
            raise ValueError(f'{source} is not a source this service plays')
        with self._lock:
            self._calls[inn, source] += 1
            call = self._calls[inn, source]
        rng = random.Random(f'{self.world.meta.seed}:{inn}:{source.value}:{call}')
        override = (self.overrides() if self.overrides else {}).get(source.value, {})
        behaviour = self.settings.behaviour[source]
        outcome = self._outcome(rng, source, inn, call, behaviour)

        forced = override.get('outcome', 'default')
        if forced != 'default':
            outcome = forced

        if outcome == 'timeout':
            await asyncio.sleep(self.settings.hang_seconds)
            raise SourceTimeout(source, inn, f'{source.value}: no answer in {self.settings.hang_seconds:g} s')
        latency_ms = behaviour.median_ms * math.exp(rng.gauss(0, behaviour.sigma))
        delay = override.get('latency_ms')
        await asyncio.sleep((delay if delay is not None else latency_ms * self.settings.latency_scale) / 1000)
        if outcome == 'error':
            raise SourceUnavailable(source, inn, f'{source.value}: the source is unavailable')
        company = self._by_inn.get(inn)
        if company is None:
            raise SourceNotFound(source, inn, f'{source.value}: no company with INN {inn}')
        return payload(company, source)

    def _outcome(self, rng: random.Random, source: SourceId, inn: str, call: int, behaviour: SourceBehaviour) -> str:
        r = rng.random()  # drawn on every call, so scenarios and failures never move the latencies
        scenario = self.settings.scenarios.get(inn, {}).get(source)
        if scenario and (scenario.times is None or call <= scenario.times):
            return scenario.outcome
        if not self.settings.failures or source in INTERNAL:
            return 'ok'
        if r < behaviour.timeout_rate:
            return 'timeout'
        if r < behaviour.timeout_rate + behaviour.error_rate:
            return 'error'
        return 'ok'


def load_world(env: dict[str, str] | None = None) -> World:
    """The world in WORLD_DIR (default apps/world/out) if it was written by this generator
    version, otherwise a fresh one from WORLD_SEED, WORLD_N and WORLD_AS_OF."""
    env = os.environ if env is None else env
    out = Path(env.get('WORLD_DIR') or DEFAULT_OUT)
    meta_path = out / 'meta.json'
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        if meta.get('generator_version') == GENERATOR_VERSION:
            return read_world(out)
    seed = int(env.get('WORLD_SEED') or DEFAULT_SEED)
    n = int(env.get('WORLD_N') or DEFAULT_N)
    as_of = date.fromisoformat(env['WORLD_AS_OF']) if env.get('WORLD_AS_OF') else DEFAULT_AS_OF
    return generate_world(seed, n, as_of)


def service_from_env(env: dict[str, str] | None = None) -> MockSourceService:
    return MockSourceService(load_world(env), Settings.from_env(env))
