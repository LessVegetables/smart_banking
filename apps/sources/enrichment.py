"""Enrichment orchestrator (SPEC §5, §6 stage 3, §11; T07).

`enrich(request, client)` asks every source about the applicant in parallel and wraps
each call in the contract envelope `SourceResponse`:

- each attempt waits at most `timeout_seconds` (10 s, SPEC §6); a timeout or an error
  is retried up to `attempts` in all (3), with a growing pause between attempts; an
  unknown INN is not retried, asking again won't find it;
- latency_ms is the whole time spent on the source, all attempts included;
- the credit bureau is asked only with the client's consent (218-ФЗ), and only the
  reports of the subjects who gave it are kept.

Then the failure policy of SPEC §5 sorts the failed sources:

- required (ЕГРЮЛ, БКИ, 115-ФЗ and the internal ones): no decision without them, so the
  application goes to the underwriter with the reason "source unavailable" (SPEC §11);
- optional (ФНС, ФССП, арбитраж): the conveyor goes on, the stop factors and flags
  (T14) add a flag and the score penalty.

`on_event` reports every attempt as it happens, for the live per-source status (T27).
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from apps.api.contracts import (SOURCE_RESPONSE, ApplicationRequest, BkiPayload, Fact, LegalForm, SourceCallStatus,
                                SourceId, SourceResponse)

from .client import SourceClient
from .facts import build_facts
from .service import SOURCES, SourceNotFound, SourceTimeout, SourceUnavailable

REQUIRED = frozenset({SourceId.ACCOUNTS, SourceId.CLIENT_CARD, SourceId.EGRUL, SourceId.BKI, SourceId.AML})
OPTIONAL = frozenset({SourceId.FNS, SourceId.FSSP, SourceId.ARBITRATION})
MSK = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class RetryPolicy:
    timeout_seconds: float = 10.0  # per attempt (SPEC §6)
    attempts: int = 3  # in all, the first one included
    backoff_seconds: float = 0.5  # pause before attempt k is backoff × (k - 1)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RetryPolicy:
        """SOURCE_TIMEOUT_SECONDS, SOURCE_ATTEMPTS, SOURCE_RETRY_BACKOFF_SECONDS; empty keeps the default."""
        env = os.environ if env is None else env
        policy = cls(
            timeout_seconds=float(env.get('SOURCE_TIMEOUT_SECONDS') or cls.timeout_seconds),
            attempts=int(env.get('SOURCE_ATTEMPTS') or cls.attempts),
            backoff_seconds=float(env.get('SOURCE_RETRY_BACKOFF_SECONDS') or cls.backoff_seconds),
        )
        if policy.timeout_seconds <= 0 or policy.attempts < 1 or policy.backoff_seconds < 0:
            raise ValueError('source timeout must be positive, attempts at least 1, backoff not negative')
        return policy


EventStatus = Literal['started', 'ok', 'timeout', 'error', 'not_found', 'no_consent']


@dataclass(frozen=True)
class SourceEvent:
    """One step of one source: an attempt started, or ended. `final` marks the source's last event."""
    source: SourceId
    attempt: int
    status: EventStatus
    final: bool
    error: str | None = None


EventHandler = Callable[[SourceEvent], None | Awaitable[None]]


class Enrichment(BaseModel):
    """What enrichment found. Built from contract models; stored and read by the workflow (T13, T19)."""
    model_config = ConfigDict(extra='forbid')

    application_id: str
    inn: str
    responses: list[SourceResponse]  # one per source, in SOURCES order
    facts: list[Fact]
    unavailable_required: list[SourceId]  # → underwriter, "source unavailable" (SPEC §11)
    degraded: list[SourceId]  # optional sources that failed → flag and penalty (T14)
    company_not_found: bool  # ЕГРЮЛ / ЕГРИП has no such INN

    def response(self, source: SourceId | str):
        return next(r for r in self.responses if r.source == SourceId(source))

    @property
    def complete(self) -> bool:
        """Every required source answered: the conveyor can go on to the stop factors and scoring."""
        return not self.unavailable_required and not self.company_not_found


async def enrich(request: ApplicationRequest, client: SourceClient, *, policy: RetryPolicy | None = None,
                 on_event: EventHandler | None = None, now: Callable[[], datetime] | None = None) -> Enrichment:
    policy = policy or RetryPolicy()
    now = now or (lambda: datetime.now(MSK))
    not_found: set[SourceId] = set()
    responses = await asyncio.gather(*(_call(source, request, client, policy, on_event, now, not_found)
                                       for source in SOURCES))
    failed = {r.source for r in responses if r.status != SourceCallStatus.OK}
    return Enrichment(
        application_id=request.application_id, inn=request.inn, responses=list(responses),
        facts=build_facts(request, responses),
        unavailable_required=[s for s in SOURCES if s in failed and s in REQUIRED],
        degraded=[s for s in SOURCES if s in failed and s in OPTIONAL],
        company_not_found=SourceId.EGRUL in not_found,
    )


async def _call(source: SourceId, request: ApplicationRequest, client: SourceClient, policy: RetryPolicy,
                on_event: EventHandler | None, now: Callable[[], datetime], not_found: set[SourceId]) -> SourceResponse:
    requested_at = now()
    started = time.monotonic()

    async def emit(attempt: int, status: EventStatus, final: bool = False, error: str | None = None) -> None:
        if on_event:
            result = on_event(SourceEvent(source, attempt, status, final, error))
            if inspect.isawaitable(result):
                await result

    def envelope(status: SourceCallStatus, attempts: int, payload=None, error: str | None = None) -> SourceResponse:
        latency_ms = round((time.monotonic() - started) * 1000)
        received_at = requested_at + timedelta(milliseconds=latency_ms) if payload is not None else None
        return SOURCE_RESPONSE.validate_python({
            'source': source, 'inn': request.inn, 'status': status, 'requested_at': requested_at,
            'received_at': received_at, 'latency_ms': latency_ms, 'attempts': attempts, 'error': error,
            'payload': payload})

    if source == SourceId.BKI and (missing := _missing_consents(request)):
        error = f'Нет согласия на запрос кредитной истории (218-ФЗ): {missing}'
        await emit(1, 'no_consent', final=True, error=error)
        return envelope(SourceCallStatus.ERROR, 1, error=error)

    status, detail = SourceCallStatus.ERROR, ''
    for attempt in range(1, policy.attempts + 1):
        if attempt > 1:
            await asyncio.sleep(policy.backoff_seconds * (attempt - 1))
        await emit(attempt, 'started')
        try:
            payload = await asyncio.wait_for(client.fetch(source, request.inn), policy.timeout_seconds)
        except (TimeoutError, SourceTimeout):
            status, detail = SourceCallStatus.TIMEOUT, ''
        except SourceNotFound:
            not_found.add(source)
            error = f'Компания с ИНН {request.inn} не найдена'
            await emit(attempt, 'not_found', final=True, error=error)
            return envelope(SourceCallStatus.ERROR, attempt, error=error)
        except SourceUnavailable as e:
            status, detail = SourceCallStatus.ERROR, str(e)
        except Exception as e:  # a broken integration is an unavailable source, not a crashed conveyor
            status, detail = SourceCallStatus.ERROR, f'{e.__class__.__name__}: {e}'
        else:
            if source == SourceId.BKI:
                payload, error = _consented_reports(payload, request)
                if error:
                    await emit(attempt, 'no_consent', final=True, error=error)
                    return envelope(SourceCallStatus.ERROR, attempt, error=error)
            await emit(attempt, 'ok', final=True)
            return envelope(SourceCallStatus.OK, attempt, payload=payload)
        last = attempt == policy.attempts
        error = _failure_text(status, detail, policy, attempt)
        await emit(attempt, status.value, final=last, error=error)
    return envelope(status, policy.attempts, error=error)


def _failure_text(status: SourceCallStatus, detail: str, policy: RetryPolicy, attempts: int) -> str:
    tries = f'{attempts} {_plural(attempts, "попытка", "попытки", "попыток")}'
    if status == SourceCallStatus.TIMEOUT:
        return f'Источник не ответил за {policy.timeout_seconds:g} с, {tries}'
    return f'Источник вернул ошибку ({detail}), {tries}' if detail else f'Источник вернул ошибку, {tries}'


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


# --- Consents (218-ФЗ) ------------------------------------------------------------------

_SUBJECT_TEXT = {'company': 'компания', 'owner': 'собственник'}


def _needed_subjects(request: ApplicationRequest) -> tuple[str, ...]:
    """ООО: the company and its main owner. ИП: the entrepreneur, who is the business (SPEC §3)."""
    return ('owner',) if request.legal_form == LegalForm.IP else ('company', 'owner')


def _consents(request: ApplicationRequest) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {'company': set(), 'owner': set()}
    for consent in request.consents:
        if consent.kind == 'bki_company':
            found['company'].add(consent.subject_inn)
        elif consent.kind == 'bki_owner':
            found['owner'].add(consent.subject_inn)
    return found


def _missing_consents(request: ApplicationRequest) -> str:
    """Subjects without any consent, checked before asking. The company's must name its own INN;
    whose the owner's is, only the bureau's answer shows (`_consented_reports`)."""
    consents = _consents(request)
    given = {'company': request.inn in consents['company'], 'owner': bool(consents['owner'])}
    return ', '.join(_SUBJECT_TEXT[s] for s in _needed_subjects(request) if not given[s])


def _consented_reports(payload: BkiPayload, request: ApplicationRequest) -> tuple[BkiPayload, str | None]:
    """Only the reports of subjects who consented; an error when a needed subject's report is not covered."""
    consents = _consents(request)
    reports = [r for r in payload.reports if r.subject_inn in consents[r.subject]]
    covered = {r.subject for r in reports}
    missing = [s for s in _needed_subjects(request) if s not in covered and any(r.subject == s for r in payload.reports)]
    if missing:
        names = ', '.join(_SUBJECT_TEXT[s] for s in missing)
        return payload, f'Согласие на запрос кредитной истории (218-ФЗ) дано не тем лицом: {names}'
    return BkiPayload(reports=reports), None
