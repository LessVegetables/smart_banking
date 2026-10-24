"""Source adapters and the enrichment orchestrator (T07)."""
import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from apps.api import contracts as c
from apps.api.contracts import SourceCallStatus, SourceId, TrustLevel
from apps.sources import (OPTIONAL, REQUIRED, SOURCES, HttpSourceClient, MockSourceService, RetryPolicy, Scenario,
                          Settings, SourceBehaviour, SourceUnavailable, build_facts, enrich)
from apps.sources.router import make_router
from apps.world import generate_world

AS_OF = date(2026, 10, 1)
FIXTURES = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 10, 24, 10, 15, tzinfo=timezone(timedelta(hours=3)))
FAST = RetryPolicy(timeout_seconds=0.2, attempts=3, backoff_seconds=0)
INSTANT = Settings(latency_scale=0, hang_seconds=5)  # a hang outlasts any FAST timeout


@pytest.fixture(scope='module')
def world():
    return generate_world(seed=5, n=80, as_of=AS_OF)


def _first(world, legal_form):
    return next(x for x in world.companies if x.legal_form == legal_form and x.archetype == 'regular')


def request_for(company, consents=None, **changes):
    """An application with every consent a company needs, as the client interface (T25) collects them."""
    given = '2026-10-24T10:14:30+03:00'
    if consents is None:
        if company.legal_form == c.LegalForm.OOO:
            consents = [('bki_company', company.inn), ('bki_owner', company.egrul.founders[0].inn),
                        ('personal_data', company.egrul.founders[0].inn)]
        else:
            consents = [('bki_owner', company.inn), ('personal_data', company.inn)]
    data = dict(application_id='app-t07', inn=company.inn, legal_form=company.legal_form.value, purpose='inventory',
                amount=1_000_000, term_months=12, needed_by='month', preferred_product=None,
                declared_annual_revenue=company.declared_annual_revenue, equipment=None,
                consents=[{'kind': k, 'subject_inn': inn, 'given_at': given} for k, inn in consents],
                submitted_at=given)
    return c.ApplicationRequest.model_validate({**data, **changes})


def run_enrich(request, client, **kwargs):
    kwargs.setdefault('policy', FAST)
    kwargs.setdefault('now', lambda: NOW)
    return asyncio.run(enrich(request, client, **kwargs))


def _facts(result):
    return {f.name: f for f in result.facts}


def _with(world, scenarios, **settings):
    return MockSourceService(world, Settings(latency_scale=0, hang_seconds=5, scenarios=scenarios, **settings))


# --- Happy path ------------------------------------------------------------------------------

@pytest.mark.parametrize('legal_form', [c.LegalForm.OOO, c.LegalForm.IP])
def test_every_source_answers(world, legal_form):
    company = _first(world, legal_form)
    result = run_enrich(request_for(company), MockSourceService(world, INSTANT))
    assert [r.source for r in result.responses] == list(SOURCES)
    for r in result.responses:
        assert r.status == SourceCallStatus.OK and r.attempts == 1 and r.error is None and r.inn == company.inn
        assert r.requested_at == NOW and r.received_at == NOW + timedelta(milliseconds=r.latency_ms)
        assert c.SOURCE_RESPONSE.validate_python(r.model_dump(mode='json')) == r
    assert result.complete and not result.unavailable_required and not result.degraded and not result.company_not_found
    assert result.response('egrul').payload == company.egrul
    assert result.response(SourceId.BKI).payload == company.bki  # every report consented
    # The result survives a round trip through JSON, for storage (T13).
    assert type(result).model_validate_json(result.model_dump_json()) == result


def test_sources_are_called_in_parallel(world):
    behaviour = {s: SourceBehaviour(median_ms=150, sigma=0) for s in SOURCES}
    service = MockSourceService(world, Settings(behaviour=behaviour))
    start = time.perf_counter()
    result = run_enrich(request_for(_first(world, c.LegalForm.OOO)), service, policy=RetryPolicy())
    assert time.perf_counter() - start < 0.6  # not 8 × 150 ms
    assert all(140 <= r.latency_ms < 600 for r in result.responses)


# --- Timeouts, retries and the failure policy -------------------------------------------------

def test_a_failed_attempt_is_retried(world):
    company = _first(world, c.LegalForm.OOO)
    service = _with(world, {company.inn: {SourceId.EGRUL: Scenario('error', 1), SourceId.FNS: Scenario('timeout', 2)}})
    result = run_enrich(request_for(company), service)
    egrul, fns = result.response('egrul'), result.response('fns')
    assert (egrul.status, egrul.attempts) == (SourceCallStatus.OK, 2)
    assert (fns.status, fns.attempts) == (SourceCallStatus.OK, 3)
    assert fns.latency_ms >= 2 * FAST.timeout_seconds * 1000  # two attempts waited out the timeout
    assert result.complete


def test_a_source_that_never_answers_times_out(world):
    """The shape of tests/fixtures/sources/fssp_timeout.json."""
    company = _first(world, c.LegalForm.OOO)
    service = _with(world, {company.inn: {SourceId.FSSP: Scenario('timeout')}})
    result = run_enrich(request_for(company), service)
    fssp = result.response('fssp')
    assert fssp.status == SourceCallStatus.TIMEOUT and fssp.attempts == 3 and fssp.payload is None
    assert fssp.received_at is None and fssp.latency_ms >= 600
    assert fssp.error == 'Источник не ответил за 0.2 с, 3 попытки'
    assert result.complete and result.degraded == [SourceId.FSSP]


def test_timeout_text_matches_the_fixture(world):
    company = _first(world, c.LegalForm.OOO)
    # The source gives up after 10 ms here, so the 10 s policy shows in the text without the wait.
    service = MockSourceService(world, Settings(latency_scale=0, hang_seconds=0.01,
                                                scenarios={company.inn: {SourceId.FSSP: Scenario('timeout')}}))
    result = run_enrich(request_for(company), service, policy=RetryPolicy(timeout_seconds=10, backoff_seconds=0))
    fixture = json.loads((FIXTURES / 'sources' / 'fssp_timeout.json').read_text(encoding='utf-8'))
    fssp = result.response('fssp')
    assert (fssp.status.value, fssp.attempts, fssp.error) == (fixture['status'], fixture['attempts'], fixture['error'])


def test_failure_policy(world):
    company = _first(world, c.LegalForm.OOO)
    down = {s: Scenario('error') for s in SOURCES}
    result = run_enrich(request_for(company), _with(world, {company.inn: down}))
    assert set(result.unavailable_required) == REQUIRED and set(result.degraded) == OPTIONAL
    assert [s for s in SOURCES if s in result.unavailable_required] == result.unavailable_required
    assert not result.complete and not result.company_not_found
    for r in result.responses:
        assert r.status == SourceCallStatus.ERROR and r.attempts == 3
        assert r.error.startswith('Источник вернул ошибку (') and r.error.endswith('3 попытки')

    optional_down = {s: Scenario('timeout') for s in OPTIONAL}
    result = run_enrich(request_for(company), _with(world, {company.inn: optional_down}))
    assert result.complete and result.unavailable_required == [] and set(result.degraded) == OPTIONAL


def test_attempts_and_backoff_follow_the_policy(world):
    company = _first(world, c.LegalForm.OOO)
    service = _with(world, {company.inn: {SourceId.AML: Scenario('error')}})
    start = time.perf_counter()
    result = run_enrich(request_for(company), service, policy=RetryPolicy(timeout_seconds=1, attempts=4,
                                                                         backoff_seconds=0.05))
    aml = result.response('aml')
    assert aml.attempts == 4 and aml.error.endswith('4 попытки')
    assert time.perf_counter() - start >= 0.05 * (1 + 2 + 3)
    one = run_enrich(request_for(company), _with(world, {company.inn: {SourceId.AML: Scenario('error')}}),
                     policy=RetryPolicy(attempts=1))
    assert one.response('aml').error.endswith('1 попытка')


def test_an_unknown_inn_is_not_retried(world):
    company = _first(world, c.LegalForm.OOO)
    request = request_for(company, inn='7700000000', consents=[('bki_company', '7700000000'),
                                                               ('bki_owner', '770000000000')])
    result = run_enrich(request, MockSourceService(world, INSTANT))
    assert result.company_not_found and not result.complete
    for r in result.responses:
        assert r.status == SourceCallStatus.ERROR and r.attempts == 1
        assert r.error == 'Компания с ИНН 7700000000 не найдена'
    assert result.facts and all(f.trust_level == TrustLevel.DECLARED for f in result.facts)


def test_a_broken_client_is_an_unavailable_source(world):
    class Broken:
        async def fetch(self, source, inn):
            raise RuntimeError('connection reset')

    result = run_enrich(request_for(_first(world, c.LegalForm.IP)), Broken())
    assert all(r.status == SourceCallStatus.ERROR for r in result.responses)
    assert result.response('egrul').error == 'Источник вернул ошибку (RuntimeError: connection reset), 3 попытки'


def test_retry_policy_from_env():
    assert RetryPolicy.from_env({}) == RetryPolicy()
    assert RetryPolicy.from_env({'SOURCE_TIMEOUT_SECONDS': '', 'SOURCE_ATTEMPTS': ''}) == RetryPolicy()
    policy = RetryPolicy.from_env({'SOURCE_TIMEOUT_SECONDS': '2.5', 'SOURCE_ATTEMPTS': '2',
                                   'SOURCE_RETRY_BACKOFF_SECONDS': '0'})
    assert policy == RetryPolicy(timeout_seconds=2.5, attempts=2, backoff_seconds=0)
    for bad in ({'SOURCE_ATTEMPTS': '0'}, {'SOURCE_TIMEOUT_SECONDS': '0'}, {'SOURCE_RETRY_BACKOFF_SECONDS': '-1'}):
        with pytest.raises(ValueError):
            RetryPolicy.from_env(bad)


# --- Consents (218-ФЗ) -------------------------------------------------------------------------

def test_no_bureau_consent_no_bureau_call(world):
    company = _first(world, c.LegalForm.OOO)

    class Counting(MockSourceService):
        calls = []

        async def fetch(self, source, inn):
            self.calls.append(SourceId(source))
            return await super().fetch(source, inn)

    service = Counting(world, INSTANT)
    owner = company.egrul.founders[0].inn
    result = run_enrich(request_for(company, consents=[('bki_owner', owner)]), service)
    bki = result.response('bki')
    assert SourceId.BKI not in service.calls
    assert bki.status == SourceCallStatus.ERROR and bki.error == 'Нет согласия на запрос кредитной истории (218-ФЗ): компания'
    assert SourceId.BKI in result.unavailable_required

    ip = _first(world, c.LegalForm.IP)
    result = run_enrich(request_for(ip, consents=[('personal_data', ip.inn)]), service)
    assert result.response('bki').error.endswith(': собственник')


def test_bureau_keeps_only_consented_reports(world):
    company = _first(world, c.LegalForm.OOO)
    wrong_owner = '540699999999'
    result = run_enrich(request_for(company, consents=[('bki_company', company.inn), ('bki_owner', wrong_owner)]),
                        MockSourceService(world, INSTANT))
    bki = result.response('bki')
    assert bki.status == SourceCallStatus.ERROR
    assert bki.error == 'Согласие на запрос кредитной истории (218-ФЗ) дано не тем лицом: собственник'
    # A company consent for another INN does not count.
    result = run_enrich(request_for(company, consents=[('bki_company', '7700000000'),
                                                       ('bki_owner', company.egrul.founders[0].inn)]),
                        MockSourceService(world, INSTANT))
    assert result.response('bki').error.endswith(': компания')


# --- Events for the live status (T27) -----------------------------------------------------------

def test_events_report_every_attempt(world):
    company = _first(world, c.LegalForm.OOO)
    service = _with(world, {company.inn: {SourceId.EGRUL: Scenario('error', 1), SourceId.FSSP: Scenario('timeout')}})
    events = []

    async def collect(event):  # async handlers are awaited, plain ones called
        events.append(event)

    run_enrich(request_for(company), service, on_event=collect)
    by_source = {s: [(e.attempt, e.status, e.final) for e in events if e.source == s] for s in SOURCES}
    assert by_source[SourceId.ACCOUNTS] == [(1, 'started', False), (1, 'ok', True)]
    assert by_source[SourceId.EGRUL] == [(1, 'started', False), (1, 'error', False), (2, 'started', False),
                                        (2, 'ok', True)]
    assert by_source[SourceId.FSSP][-1] == (3, 'timeout', True)
    assert all(sum(e.final for e in events if e.source == s) == 1 for s in SOURCES)
    plain = []
    run_enrich(request_for(company), MockSourceService(world, INSTANT), on_event=plain.append)
    assert len(plain) == 2 * len(SOURCES)


# --- Facts ---------------------------------------------------------------------------------------

def test_facts_from_the_world(world):
    company = _first(world, c.LegalForm.OOO)
    result = run_enrich(request_for(company), MockSourceService(world, INSTANT))
    facts = _facts(result)
    assert len(facts) == len(result.facts), 'one fact per name and source'
    assert facts['legal_form'].declared_value == facts['legal_form'].verified_value == 'ooo'
    revenue = facts['annual_revenue']
    assert revenue.declared_value == company.declared_annual_revenue
    assert revenue.verified_value == (company.fns.financials.revenue if company.fns.financials else None)
    assert facts['okved_main'].verified_value == company.egrul.okved_main
    assert facts['operating_inflows_12m'].verified_value == sum(m.inflows.customers for m in company.accounts.months[-12:])
    assert facts['operating_inflows_12m'].trust_level == TrustLevel.BANK
    assert facts['tax_debt'].trust_level == TrustLevel.REGISTRY and facts['tax_debt'].source == SourceId.FNS
    assert facts['fssp_debt_remaining'].verified_value == sum(p.remaining for p in company.fssp.proceedings)
    assert facts['zsk_zone'].verified_value == company.aml.zsk_zone
    assert facts['loan_purpose'].trust_level == TrustLevel.DECLARED and facts['loan_purpose'].verified_value is None
    for fact in result.facts:
        if fact.source:
            assert fact.fetched_at == result.response(fact.source).received_at


def test_facts_match_the_t00_example():
    """The same rules give the verified values of tests/fixtures/facts.json from the source examples."""
    load = lambda name: json.loads((FIXTURES / name).read_text(encoding='utf-8'))
    request = c.ApplicationRequest.model_validate(load('application_request.json'))
    names = ('egrul', 'fns', 'accounts', 'bki', 'client_card', 'fssp', 'arbitration', 'aml')
    responses = [c.SOURCE_RESPONSE.validate_python(load(f'sources/{n}.json')) for n in names]
    built = {f.name: f for f in build_facts(request, responses)}
    for expected in map(c.Fact.model_validate, load('facts.json')):
        if expected.name == 'employees':  # the example declares employees; the application has no such field
            assert built['employees'].verified_value == expected.verified_value
            continue
        got = built[expected.name]
        assert (got.declared_value, got.verified_value, got.source, got.trust_level, got.fetched_at) == \
            (expected.declared_value, expected.verified_value, expected.source, expected.trust_level,
             expected.fetched_at), expected.name


def test_failed_sources_leave_declared_facts_only(world):
    company = _first(world, c.LegalForm.OOO)
    service = _with(world, {company.inn: {SourceId.FNS: Scenario('error'), SourceId.EGRUL: Scenario('error')}})
    facts = _facts(run_enrich(request_for(company), service))
    for name in ('annual_revenue', 'legal_form'):
        assert facts[name].verified_value is None and facts[name].trust_level == TrustLevel.DECLARED
        assert facts[name].source is None and facts[name].declared_value is not None
    assert 'tax_debt' not in facts and 'okved_main' not in facts
    assert 'operating_inflows_12m' in facts


def test_ip_facts(world):
    ip = _first(world, c.LegalForm.IP)
    facts = _facts(run_enrich(request_for(ip), MockSourceService(world, INSTANT)))
    assert 'founder_changed_6m' not in facts
    income = ip.fns.tax_return.income if ip.fns.tax_return else None
    assert facts['annual_revenue'].verified_value == income
    open_loans = [loan for loan in ip.bki.reports[0].loans if loan.closed_at is None]
    assert facts['monthly_debt_payments'].verified_value == sum(x.monthly_payment for x in open_loans)


def test_director_change_window():
    from apps.sources.facts import _changed_recently, months_between
    registered = date(2020, 1, 15)
    today = date(2026, 10, 24)
    assert months_between(date(2023, 10, 12), today) == 36
    assert months_between(date(2023, 10, 25), today) == 35
    assert _changed_recently(date(2026, 5, 1), registered, today)
    assert not _changed_recently(date(2026, 4, 24), registered, today)  # exactly 6 months ago
    assert not _changed_recently(registered, registered, today)  # the founding director
    assert not _changed_recently(None, registered, today)


# --- HTTP ------------------------------------------------------------------------------------------

def test_http_client_against_the_router(world):
    company = _first(world, c.LegalForm.OOO)
    service = MockSourceService(world, Settings(latency_scale=0, hang_seconds=0.01, scenarios={
        company.inn: {SourceId.FSSP: Scenario('timeout'), SourceId.AML: Scenario('error', 1)}}))
    app = FastAPI()
    app.include_router(make_router(lambda: service))
    client = HttpSourceClient('http://mock', transport=httpx.ASGITransport(app=app))
    result = run_enrich(request_for(company), client)
    assert result.response('egrul').payload == company.egrul
    assert result.response('aml').attempts == 2 and result.response('aml').status == SourceCallStatus.OK
    assert result.response('fssp').status == SourceCallStatus.TIMEOUT
    unknown = run_enrich(request_for(company, inn='7700000000', consents=[('bki_company', '7700000000'),
                                                                          ('bki_owner', '770000000000')]), client)
    assert unknown.company_not_found


def test_http_client_turns_transport_errors_into_unavailable():
    def refuse(request):
        raise httpx.ConnectError('refused', request=request)

    client = HttpSourceClient('http://mock', transport=httpx.MockTransport(refuse))
    with pytest.raises(SourceUnavailable):
        asyncio.run(client.fetch(SourceId.EGRUL, '5406000001'))
    broken = HttpSourceClient('http://mock', transport=httpx.MockTransport(lambda r: httpx.Response(500, text='oops')))
    with pytest.raises(SourceUnavailable, match='oops'):
        asyncio.run(broken.fetch(SourceId.EGRUL, '5406000001'))
