"""Statements from other banks (T23): parsing, merging, checks, and the needs_data → enriching re-run."""
import asyncio
import json
from dataclasses import dataclass, replace

import pytest
from fastapi.testclient import TestClient

from apps.api import contracts as c
from apps.api import statements as st, workflow
from apps.api.contracts import ApplicationStatus as S, DataQualityTier as T, Route, SourceId, TrustLevel
from apps.api.db import LoanApplication, Session, User
from apps.api.fact_store import load_facts
from apps.api.features import SourcePayloads, fns_revenue
from apps.api.limits import cash_flow
from apps.api.routing import route_application
from apps.api.staff_actions import Action, Position, Staff, review
from apps.sources import Enrichment, enrich
from test_enrichment import FAST, FIXTURES
from test_workflow import (AS_OF, NOW, decision_for, drain, load, new_application, pipeline, request_for,
                           world)  # noqa: F401 (world is a fixture)


def month_name(index: int) -> str:
    return f'{index // 12}-{index % 12 + 1:02d}'


def turnover(month: str, customers: int) -> c.MonthlyTurnover:
    return c.MonthlyTurnover(
        month=month, average_balance=customers // 4,
        inflows=c.Inflows(customers=customers, own_transfers=0, loans_received=0, founder_contributions=0, other=0),
        outflows=c.Outflows(suppliers=customers * 7 // 10, payroll=0, taxes=0, rent=0, loan_payments=0,
                            cash_withdrawals=0, own_transfers=0, other=0))


def statement(inn: str, monthly: int, *, months: int = 12, end: int | None = None, bank: str = 'Банк «Пример»') -> dict:
    """What a client uploads: `months` of statement ending `end` (the last full month before AS_OF by default)."""
    end = st.last_full_month(AS_OF) if end is None else end
    return {'bank_name': bank, 'inn': inn,
            'months': [turnover(month_name(i), monthly).model_dump(mode='json') for i in range(end - months + 1, end + 1)]}


def file(data: dict, name: str = 'statement.json') -> tuple[str, bytes]:
    return name, json.dumps(data, ensure_ascii=False).encode()


@dataclass(frozen=True)
class Case:
    company: object
    request: c.ApplicationRequest
    decision: c.Decision
    enrichment: Enrichment

    @property
    def payloads(self) -> SourcePayloads:
        return SourcePayloads.from_responses(self.enrichment.responses)

    @property
    def revenue(self) -> int:
        """The revenue coverage's denominator: ФНС, else declared."""
        if self.decision.data_quality.revenue_basis == 'fns':
            p = self.payloads
            return fns_revenue(p.fns, p.egrul, p.accounts).annualized
        return self.request.declared_annual_revenue

    def uploaded(self, data: dict) -> st.UploadedStatement:
        return st.parse_statement('s.json', file(data)[1], self.request, NOW)

    def statement(self, share_of_revenue: float, **kwargs) -> dict:
        """A 12-month statement whose customer inflows are this share of the revenue."""
        return statement(self.request.inn, int(self.revenue * share_of_revenue / 12), **kwargs)


@pytest.fixture(scope='module')
def cases(world):
    """The first THIN application the engine asks a statement for, and one in review whose account shows only
    part of the revenue (main bank elsewhere)."""
    found = {}
    for company in world.companies:
        request = request_for(company, amount=300_000)
        d = decision_for(world, request)
        dq = d.data_quality
        if d.status == S.NEEDS_DATA:
            key = 'thin'
        elif d.status == S.IN_REVIEW and dq and dq.revenue_coverage.tier == T.PARTIAL and dq.history_months.tier == T.FULL:
            key = 'partial'
        else:
            continue
        if key not in found:
            e = asyncio.run(enrich(request, pipeline(world).client, policy=FAST, now=lambda: NOW))
            found[key] = Case(company, request, d, e)
        if len(found) == 2:
            return found
    raise AssertionError(f'world has no case for {set(("thin", "partial")) - set(found)}')


def decide(world, case: Case, statements):
    dq, _ = workflow.data_quality_for(case.request, case.enrichment, statements, AS_OF)
    return workflow.decide(case.request, case.enrichment, dq, as_of=AS_OF, decided_at=NOW,
                           scorer=pipeline(world).scorer, statements=statements)


# --- Parsing -----------------------------------------------------------------------------

def test_parse_refuses_what_it_cannot_read(cases):
    case = cases['thin']
    request, good = case.request, statement(case.request.inn, 100_000)
    u = case.uploaded(good)
    assert u.statement.bank_name == 'Банк «Пример»' and len(u.statement.months) == 12 and len(u.sha256) == 64
    for content, text in ((b'', 'пустой'), (b'%PDF-1.7', 'распознать'), (b'{"bank_name": "x"}', 'распознать'),
                          (b'x' * (st.MAX_FILE_BYTES + 1), 'больше'),
                          (file({**good, 'inn': '7700000000'})[1], 'другой ИНН')):
        with pytest.raises(st.StatementError, match=text):
            st.parse_statement('s.json', content, request, NOW)
    with pytest.raises(st.StatementError, match='уже загружен'):
        st.parse_statements([file(good)], request, NOW, already=[u])
    with pytest.raises(st.StatementError, match='не больше'):
        st.parse_statements([file(statement(request.inn, 1_000 + i)) for i in range(st.MAX_FILES + 1)], request, NOW)
    with pytest.raises(st.StatementError, match='хотя бы одну'):
        st.parse_statements([], request, NOW)


# --- Merge, checks, facts --------------------------------------------------------------------

def test_merge_sums_months_and_keeps_our_counterparties(cases):
    case = cases['thin']
    ours = case.payloads
    u = case.uploaded(statement(case.request.inn, 100_000, months=20))
    merged = st.with_statements(ours, [u]).accounts
    assert st.with_statements(ours, []) is ours
    assert merged.counterparties_12m == ours.accounts.counterparties_12m
    assert len(merged.months) == len({m.month for m in ours.accounts.months} | {m.month for m in u.statement.months})
    by_month = {m.month: m for m in ours.accounts.months}
    for m in merged.months:
        own = by_month[m.month].inflows.customers if m.month in by_month else 0
        assert m.inflows.customers == own + 100_000
    assert merged.account_opened_at <= ours.accounts.account_opened_at
    long = case.uploaded(statement(case.request.inn, 1_000, months=24, end=st.last_full_month(AS_OF) - 6))
    assert len(st.with_statements(ours, [long]).accounts.months) == 24  # AccountsPayload keeps 24 months


def test_without_our_account_the_statement_alone_is_the_account(cases):
    case = cases['thin']
    u = case.uploaded(statement(case.request.inn, 50_000))
    merged = st.with_statements(replace(case.payloads, accounts=None), [u]).accounts
    assert merged.months == u.statement.months and merged.counterparties_12m.customers_count == 0


def test_stale_and_future_statements_are_left_out_with_a_flag(cases):
    case = cases['thin']
    last = st.last_full_month(AS_OF)
    fresh = case.uploaded(statement(case.request.inn, 10_000, end=last - st.STALE_MONTHS))
    stale = case.uploaded(statement(case.request.inn, 10_000, end=last - st.STALE_MONTHS - 1, bank='Старый'))
    future = case.uploaded(statement(case.request.inn, 10_000, end=last + 1, bank='Будущий'))
    check = st.check_statements([fresh, stale, future], case.payloads, AS_OF)
    assert check.used == (fresh,)
    assert [f.code for f in check.flags] == ['statement_period', 'statement_period']


def test_inflows_far_above_fns_revenue_are_a_flag(cases):
    case = cases['partial']
    assert case.decision.data_quality.revenue_basis == 'fns'
    ok = case.uploaded(case.statement(0.25))
    assert st.check_statements([ok], case.payloads, AS_OF).flags == ()
    big = case.uploaded(case.statement(2))
    check = st.check_statements([big], case.payloads, AS_OF)
    assert check.used == (big,) and [f.code for f in check.flags] == ['statement_inflows_above_fns']


def test_statement_facts_are_client_documents(cases):
    case = cases['thin']
    facts = {f.name: f for f in st.statement_facts([case.uploaded(statement(case.request.inn, 10_000, months=14))])}
    assert facts['operating_inflows_12m'].verified_value == 120_000 and facts['statement_months'].verified_value == 14
    assert all(f.source == SourceId.STATEMENT_UPLOAD and f.trust_level == TrustLevel.CLIENT_DOCUMENT
               for f in facts.values())
    assert st.statement_facts([]) == []


def test_a_statement_sends_the_application_to_the_underwriter():
    full = c.DataQuality.model_validate_json((FIXTURES / 'data_quality_full.json').read_text(encoding='utf-8'))
    assert route_application(300_000, grade=c.Grade.A, data_quality=full).route == Route.AUTO
    routing = route_application(300_000, grade=c.Grade.A, data_quality=full, statements=True)
    assert routing.route == Route.UNDERWRITER and [r.code for r in routing.reasons] == ['route_statement_upload']


# --- The decision ------------------------------------------------------------------------

def test_thin_asks_for_a_statement_then_counts_it(world, cases):
    case = cases['thin']
    d = case.decision
    assert d.requested_documents == [st.REQUESTED_STATEMENT] and d.score and not d.offers
    assert d.matches_request is None and 'route_data_thin' in {r.code for r in d.route_reasons}

    skipped = decide(world, case, [])
    assert skipped.status == S.IN_REVIEW and 'route_data_thin' in {r.code for r in skipped.route_reasons}

    u = case.uploaded(case.statement(0.5))
    dq, used = workflow.data_quality_for(case.request, case.enrichment, [u], AS_OF)
    assert used == (u,) and dq.tier.rank < T.THIN.rank
    assert dq.revenue_coverage.value > d.data_quality.revenue_coverage.value
    after = decide(world, case, [u])
    assert after.status == S.IN_REVIEW and 'route_statement_upload' in {r.code for r in after.route_reasons}
    assert after.score == d.score  # the card reads our own account only
    assert after == decide(world, case, [u])


def test_a_statement_raises_a_partial_cash_flow_limit(cases):
    case = cases['partial']
    u = case.uploaded(case.statement(0.33))
    dq, used = workflow.data_quality_for(case.request, case.enrichment, [u], AS_OF)
    assert dq.revenue_coverage.value > case.decision.data_quality.revenue_coverage.value
    ours = cash_flow(case.payloads, case.decision.data_quality)
    merged = cash_flow(st.with_statements(case.payloads, used), dq)
    assert merged.average_monthly_inflows > ours.average_monthly_inflows
    assert merged.cfads is not None and (ours.cfads is None or merged.cfads > ours.cfads)


# --- The run -----------------------------------------------------------------------------

def _decided(world, company):
    application_id, user_id = new_application(company, amount=300_000)
    drain(pipeline(world))
    return application_id, user_id


def test_upload_resumes_the_run_on_the_stored_sources(world, cases):
    case = cases['thin']
    application_id, user_id = _decided(world, case.company)
    app, history = load(application_id)
    assert app.status == S.NEEDS_DATA and app.decision['requested_documents'] == [st.REQUESTED_STATEMENT]
    assert app.statements is None
    sources_before = sum(e.kind == 'source' for e in history)

    with Session.begin() as s:
        a = s.get(LoanApplication, application_id)
        with pytest.raises(st.StatementError):
            workflow.upload_statements(s, a, [('bad.json', b'{}')], actor=f'client:{user_id}')
        assert a.status == S.NEEDS_DATA
        workflow.upload_statements(s, a, [file(case.statement(0.5))], actor=f'client:{user_id}', at=NOW)
    app, history = load(application_id)
    assert app.status == S.ENRICHING and app.lease_until is None and len(app.statements) == 1
    assert history[-1].from_status == S.NEEDS_DATA and 'Банк «Пример» (12 мес.)' in history[-1].message
    assert workflow.claim_next() is None  # not a new submission

    drain(pipeline(world))
    app, history = load(application_id)
    assert app.status == S.IN_REVIEW
    assert sum(e.kind == 'source' for e in history) == sources_before  # no second credit-bureau inquiry
    run = next(e for e in reversed(history) if e.kind == 'action' and 'Данные из источников' in e.message)
    assert run.data['statements_used'] == 1 and run.data['sources_reused'] and run.data['own_account_tier'] == T.THIN
    assert c.DataQuality.model_validate(app.data_quality).tier != T.THIN
    assert 'route_statement_upload' in {r['code'] for r in app.decision['route_reasons']}
    with Session() as s:
        facts = load_facts(s, application_id)
    assert {f.name for f in facts if f.source == SourceId.STATEMENT_UPLOAD} == {'operating_inflows_12m',
                                                                                'statement_months'}
    with Session.begin() as s, pytest.raises(workflow.IllegalTransition):
        workflow.upload_statements(s, s.get(LoanApplication, application_id), [file(case.statement(0.1))],
                                   actor=f'client:{user_id}')


def test_skip_goes_to_the_underwriter_without_asking_again(world, cases):
    application_id, user_id = _decided(world, cases['thin'].company)
    with Session.begin() as s:
        workflow.skip_statements(s, s.get(LoanApplication, application_id), actor=f'client:{user_id}')
    drain(pipeline(world))
    app, _ = load(application_id)
    assert app.status == S.IN_REVIEW and app.statements == []
    assert 'route_data_thin' in {r['code'] for r in app.decision['route_reasons']}


def test_staff_data_request_is_answered_by_an_upload(world, cases):
    case = cases['partial']
    application_id, user_id = _decided(world, case.company)
    with Session.begin() as s:
        a = s.get(LoanApplication, application_id)
        assert a.status == S.IN_REVIEW
        review(s, a, Staff(user_id='u1', position=Position.UNDERWRITER), Action.REQUEST_DATA, 'statement_needed',
               'Нужна выписка из основного банка', documents=['Выписка из другого банка'])
        assert a.status == S.NEEDS_DATA
        workflow.upload_statements(s, a, [file(case.statement(0.33))], actor=f'client:{user_id}', at=NOW)
    drain(pipeline(world))
    app, _ = load(application_id)
    dq = c.DataQuality.model_validate(app.data_quality)
    assert app.status == S.IN_REVIEW and dq.revenue_coverage.value > case.decision.data_quality.revenue_coverage.value
    assert 'route_statement_upload' in {r['code'] for r in app.decision['route_reasons']}


# --- API ---------------------------------------------------------------------------------

def test_statement_api(world, cases):
    from apps.api.main import app as api
    case = cases['thin']
    with TestClient(api) as client:
        assert client.post('/api/auth/demo', json={'name': 'Клиент'}).status_code == 200
        user_id=client.get('/api/auth/me').json()['id']
        with Session.begin() as s:
            s.get(User,user_id).identity='telegram:statement-api-test'
        body = {'idempotency_key': 'statement-api-1', 'request': {
            k: v for k, v in case.request.model_dump(mode='json').items() if k not in ('application_id', 'submitted_at')}}
        created = client.post('/api/v2/applications', json=body).json()
        drain(pipeline(world))
        view = client.get(f'/api/v2/applications/{created["id"]}').json()
        assert view['status'] == 'needs_data' and view['decision']['requested_documents'] == [st.REQUESTED_STATEMENT]
        assert view['statements'] == []

        url = f'/api/v2/applications/{created["id"]}/statements'
        pdf = ('files', ('x.pdf', b'%PDF-1.7', 'application/pdf'))
        r = client.post(url, data={'revision': view['revision']}, files=[pdf])
        assert r.status_code == 422 and 'распознать' in r.json()['detail']
        good = ('files', (*file(case.statement(0.5)), 'application/json'))
        assert client.post(url, data={'revision': view['revision'] + 5}, files=[good]).status_code == 409
        r = client.post(url, data={'revision': view['revision']}, files=[good])
        assert r.status_code == 200, r.text
        view = r.json()
        assert view['status'] == 'enriching' and view['statements'][0]['bank_name'] == 'Банк «Пример»'
        assert client.post(f'{url}/skip', json={'revision': view['revision']}).status_code == 409
        drain(pipeline(world))  # leave nothing claimable behind for other tests
        assert client.get(f'/api/v2/applications/{created["id"]}').json()['status'] == 'in_review'
