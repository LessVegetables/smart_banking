"""Mock source service (T06): world payloads, latency, failures, scenarios, HTTP."""
import asyncio
import json
import time
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api import contracts as c
from apps.api.contracts import SourceId
from apps.sources import (DEFAULT_BEHAVIOUR, INTERNAL, SOURCES, MockSourceService, Scenario, Settings, SourceBehaviour,
                          SourceNotFound, SourceTimeout, SourceUnavailable, load_world, service_from_env)
from apps.sources.router import make_router
from apps.world import generate_world
from apps.world.__main__ import write_world

AS_OF = date(2026, 10, 1)
INSTANT = Settings(latency_scale=0, hang_seconds=0)
PAYLOADS = {
    SourceId.ACCOUNTS: c.AccountsPayload, SourceId.CLIENT_CARD: c.ClientCardPayload, SourceId.EGRUL: c.EgrulPayload,
    SourceId.FNS: c.FnsPayload, SourceId.BKI: c.BkiPayload, SourceId.FSSP: c.FsspPayload,
    SourceId.ARBITRATION: c.ArbitrationPayload, SourceId.AML: c.AmlPayload,
}


@pytest.fixture(scope='module')
def world():
    return generate_world(seed=5, n=60, as_of=AS_OF)


def run(coro):
    return asyncio.run(coro)


def _outcomes(service, source, inn, calls):
    async def go():
        found = []
        for _ in range(calls):
            try:
                await service.fetch(source, inn)
                found.append('ok')
            except SourceUnavailable:
                found.append('error')
            except SourceTimeout:
                found.append('timeout')
        return found
    return run(go())


def test_service_plays_every_source_but_statement_upload():
    assert set(SOURCES) == set(SourceId) - {SourceId.STATEMENT_UPLOAD} == set(PAYLOADS) == set(DEFAULT_BEHAVIOUR)
    assert INTERNAL == {SourceId.ACCOUNTS, SourceId.CLIENT_CARD}


def test_every_source_answers_from_the_world(world):
    service = MockSourceService(world, INSTANT)
    for company in world.companies:
        for source in SOURCES:
            payload = run(service.fetch(source, company.inn))
            assert isinstance(payload, PAYLOADS[source])
            assert payload is getattr(company, source.value)
            # It fits the contract envelope the adapters (T07) will build.
            c.SOURCE_RESPONSE.validate_python({
                'source': source.value, 'inn': company.inn, 'status': 'ok', 'requested_at': '2026-10-24T10:15:00+03:00',
                'received_at': '2026-10-24T10:15:01+03:00', 'latency_ms': 1000, 'attempts': 1, 'error': None,
                'payload': payload.model_dump(mode='json')})
    assert run(service.fetch('egrul', world.companies[0].inn)).inn == world.companies[0].inn


def test_unknown_inn_and_source(world):
    service = MockSourceService(world, INSTANT)
    with pytest.raises(SourceNotFound) as e:
        run(service.fetch(SourceId.EGRUL, '7700000000'))
    assert e.value.source == SourceId.EGRUL and e.value.inn == '7700000000'
    with pytest.raises(ValueError):
        run(service.fetch(SourceId.STATEMENT_UPLOAD, world.companies[0].inn))
    with pytest.raises(ValueError):
        run(service.fetch('nalog', world.companies[0].inn))


def test_latency_follows_the_source_and_the_scale(world):
    behaviour = {s: SourceBehaviour(median_ms=40, sigma=0) for s in SOURCES}
    behaviour[SourceId.BKI] = SourceBehaviour(median_ms=200, sigma=0)
    service = MockSourceService(world, Settings(behaviour=behaviour))
    inn = world.companies[0].inn

    def timed(source, svc=service):
        start = time.perf_counter()
        run(svc.fetch(source, inn))
        return time.perf_counter() - start

    assert 0.035 < timed(SourceId.FSSP) < 0.15
    assert 0.19 < timed(SourceId.BKI) < 0.4
    assert timed(SourceId.BKI, MockSourceService(world, Settings(latency_scale=0.1, behaviour=behaviour))) < 0.1


def test_sources_are_called_in_parallel(world):
    behaviour = {s: SourceBehaviour(median_ms=150, sigma=0) for s in SOURCES}
    service = MockSourceService(world, Settings(behaviour=behaviour))
    inn = world.companies[0].inn

    async def everything():
        return await asyncio.gather(*(service.fetch(s, inn) for s in SOURCES))

    start = time.perf_counter()
    assert len(run(everything())) == len(SOURCES)
    assert time.perf_counter() - start < 0.5  # not 8 × 150 ms


def test_no_random_failures_by_default(world):
    service = MockSourceService(world, Settings(latency_scale=0, hang_seconds=0,
                                                behaviour={s: SourceBehaviour(1, error_rate=0.5, timeout_rate=0.5)
                                                           for s in SOURCES}))
    assert _outcomes(service, SourceId.FSSP, world.companies[0].inn, 30) == ['ok'] * 30


def test_random_failures_follow_the_rates(world):
    behaviour = {s: SourceBehaviour(1, error_rate=0.2, timeout_rate=0.1) for s in SOURCES}
    service = MockSourceService(world, Settings(latency_scale=0, hang_seconds=0, failures=True, behaviour=behaviour))
    found = [o for x in world.companies for o in _outcomes(service, SourceId.FNS, x.inn, 10)]
    assert 0.15 < found.count('error') / len(found) < 0.25
    assert 0.06 < found.count('timeout') / len(found) < 0.14
    # Internal sources are never unavailable (SPEC §5).
    for source in INTERNAL:
        assert set(_outcomes(service, source, world.companies[0].inn, 50)) == {'ok'}


def test_failures_are_reproducible_and_reset(world):
    settings = Settings(latency_scale=0, hang_seconds=0, failures=True,
                        behaviour={s: SourceBehaviour(1, error_rate=0.3, timeout_rate=0.2) for s in SOURCES})
    inn = world.companies[3].inn
    first = MockSourceService(world, settings)
    second = MockSourceService(world, settings)
    a = _outcomes(first, SourceId.BKI, inn, 20)
    assert a == _outcomes(second, SourceId.BKI, inn, 20)
    assert len(set(a)) > 1  # retries draw again, so a failed call can succeed next time
    first.reset()
    assert _outcomes(first, SourceId.BKI, inn, 20) == a


def test_timeout_hangs_longer_than_the_adapter_waits(world):
    inn = world.companies[0].inn
    service = MockSourceService(world, Settings(latency_scale=0, hang_seconds=5,
                                                scenarios={inn: {SourceId.FSSP: Scenario('timeout')}}))
    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        run(asyncio.wait_for(service.fetch(SourceId.FSSP, inn), timeout=0.05))
    assert time.perf_counter() - start < 1
    quick = MockSourceService(world, Settings(latency_scale=0, hang_seconds=0.01,
                                              scenarios={inn: {SourceId.FSSP: Scenario('timeout')}}))
    with pytest.raises(SourceTimeout):
        run(quick.fetch(SourceId.FSSP, inn))


def test_scenarios_force_and_recover(world):
    a, b = world.companies[0].inn, world.companies[1].inn
    scenarios = {a: {SourceId.FSSP: Scenario('timeout'), SourceId.EGRUL: Scenario('error', times=2)},
                 b: {SourceId.BKI: Scenario('ok')}}
    behaviour = {s: SourceBehaviour(1, error_rate=1.0) for s in SOURCES}
    service = MockSourceService(world, Settings(latency_scale=0, hang_seconds=0, failures=True,
                                                behaviour=behaviour, scenarios=scenarios))
    assert _outcomes(service, SourceId.FSSP, a, 4) == ['timeout'] * 4
    assert _outcomes(service, SourceId.EGRUL, a, 4) == ['error', 'error', 'error', 'error']  # then random: rate 1
    assert _outcomes(service, SourceId.BKI, b, 4) == ['ok'] * 4
    calm = MockSourceService(world, Settings(latency_scale=0, hang_seconds=0, scenarios=scenarios))
    assert _outcomes(calm, SourceId.EGRUL, a, 4) == ['error', 'error', 'ok', 'ok']
    assert _outcomes(calm, SourceId.FNS, a, 2) == ['ok', 'ok']


def test_scenario_parsing():
    assert Scenario.parse('timeout') == Scenario('timeout')
    assert Scenario.parse(' error:1 ') == Scenario('error', 1)
    for bad in ('slow', 'error:', 'timeout:x', ''):
        with pytest.raises(ValueError):
            Scenario.parse(bad)


def test_settings_from_env():
    assert Settings.from_env({}) == Settings()
    settings = Settings.from_env({
        'MOCK_SOURCES_LATENCY_SCALE': '0.5', 'MOCK_SOURCES_FAILURES': 'on', 'MOCK_SOURCES_HANG_SECONDS': '12',
        'MOCK_SOURCES_SCENARIOS': json.dumps({'5406000001': {'fssp': 'timeout', 'egrul': 'error:1'}}),
    })
    assert settings.latency_scale == 0.5 and settings.failures and settings.hang_seconds == 12
    assert settings.scenarios == {'5406000001': {SourceId.FSSP: Scenario('timeout'),
                                                 SourceId.EGRUL: Scenario('error', 1)}}
    assert not Settings.from_env({'MOCK_SOURCES_FAILURES': 'off'}).failures
    with pytest.raises(ValueError):
        Settings.from_env({'MOCK_SOURCES_FAILURES': 'sometimes'})
    with pytest.raises(ValueError):
        Settings.from_env({'MOCK_SOURCES_SCENARIOS': json.dumps({'5406000001': {'nalog': 'error'}})})


def test_load_world_reads_current_files_and_regenerates_stale_ones(tmp_path):
    world = generate_world(seed=9, n=20, as_of=AS_OF)
    write_world(world, tmp_path)
    assert load_world({'WORLD_DIR': str(tmp_path)}) == world
    meta = json.loads((tmp_path / 'meta.json').read_text(encoding='utf-8'))
    meta['generator_version'] = 'world-t05-1'
    (tmp_path / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')
    env = {'WORLD_DIR': str(tmp_path), 'WORLD_SEED': '9', 'WORLD_N': '20', 'WORLD_AS_OF': '2026-10-01'}
    assert load_world(env) == world
    service = service_from_env({**env, 'MOCK_SOURCES_LATENCY_SCALE': '0'})
    assert service.settings.latency_scale == 0
    assert service.company(world.companies[0].inn) == world.companies[0]


def test_http_router(world):
    inn = world.companies[0].inn
    service = MockSourceService(world, Settings(latency_scale=0, hang_seconds=0, scenarios={
        inn: {SourceId.FSSP: Scenario('timeout'), SourceId.AML: Scenario('error')}}))
    app = FastAPI()
    app.include_router(make_router(lambda: service))
    client = TestClient(app)
    response = client.get(f'/mock-sources/egrul/{inn}')
    assert response.status_code == 200
    assert c.EgrulPayload.model_validate(response.json()) == world.companies[0].egrul
    assert client.get(f'/mock-sources/fssp/{inn}').status_code == 504
    assert client.get(f'/mock-sources/aml/{inn}').status_code == 503
    assert client.get('/mock-sources/egrul/7700000000').status_code == 404
    assert client.get(f'/mock-sources/statement_upload/{inn}').status_code == 404
    assert client.get(f'/mock-sources/nalog/{inn}').status_code == 422
