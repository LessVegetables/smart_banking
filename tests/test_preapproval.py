"""Overdraft pre-approval batch (T22): FULL tier only, clean auto route, stored per client, served to the banner."""
import asyncio
import os
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api import preapproval as pa
from apps.api.contracts import ApplicationStatus as S, DataQualityTier, LegalForm, ProductId, Route, SourceId
from apps.api.credit_policy import default_policy
from apps.api.db import PreApprovalRow, Session
from apps.api.features import data_quality_from_responses
from apps.sources import Scenario, enrich
from apps.sources.enrichment import MSK
from test_enrichment import NOW, request_for
from test_workflow import decision_for, pipeline, world  # noqa: F401 (fixture)

UNKNOWN_INN = '7700000000'


def run(inn, p):
    return asyncio.run(pa.preapprove_client(inn, p))


@pytest.fixture(scope='module')
def results(world):
    p = pipeline(world)
    return {r.inn: r for r in asyncio.run(pa._preapprove_all([c.inn for c in world.companies], p, 20))}


def by_code(results, code):
    return [r for r in results.values() if r.reasons[0].code == code]


def test_every_outcome_is_reachable(results):
    codes = {r.reasons[0].code for r in results.values()}
    assert {'preapproval_data_partial', 'preapproval_data_thin', 'preapproval_flag', 'preapproval_stop_factor',
            'preapproval_below_product_minimum', 'preapproval_not_auto'} <= codes
    assert any(r.approved for r in results.values())


def test_only_full_clean_auto_clients_are_preapproved(world, results):
    overdraft = default_policy().products[ProductId.OVERDRAFT]
    for r in results.values():
        assert len(r.reasons) == 1
        if not r.approved:
            assert r.amount is None and r.rate is None and r.valid_until is None and r.client_view() is None
            continue
        assert r.data_quality_tier == DataQualityTier.FULL
        assert overdraft.min_amount <= r.amount <= overdraft.max_amount
        assert r.amount % default_policy().limit.amount_step == 0
        assert r.reasons[0].code == f'preapproval_limit_{r.binding}'
        assert r.valid_until == NOW.date() + timedelta(days=pa.VALID_DAYS)
        assert r.as_of == pipeline(world).as_of and r.model_version
        view = r.client_view()
        assert view['amount'] == r.amount and view['product'] == 'overdraft' and view['term_months'] == 12
        assert view['client_text'].startswith('Вам доступно до ')
    for r in by_code(results, 'preapproval_data_partial') + by_code(results, 'preapproval_data_thin'):
        assert r.data_quality_tier != DataQualityTier.FULL and r.grade is None


def test_no_declared_revenue_without_fns_revenue_means_thin(world, results):
    """The batch has no application: data quality never falls back to a declared revenue (T09)."""
    for company in world.companies:
        request = pa.standing_request(company.inn, company.egrul, NOW)
        assert request.declared_annual_revenue is None
    thin = by_code(results, 'preapproval_data_thin')
    assert thin and all(not r.approved for r in thin)


def test_standing_consent_covers_the_company_and_its_main_owner(world):
    ooo = next(c for c in world.companies if c.legal_form == LegalForm.OOO)
    ip = next(c for c in world.companies if c.legal_form == LegalForm.IP)
    owner = max(ooo.egrul.founders, key=lambda f: f.share).inn
    assert {(x.kind, x.subject_inn) for x in pa.standing_request(ooo.inn, ooo.egrul, NOW).consents} == {
        ('bki_company', ooo.inn), ('bki_owner', owner)}
    assert [(x.kind, x.subject_inn) for x in pa.standing_request(ip.inn, ip.egrul, NOW).consents] == [
        ('bki_owner', ip.inn)]
    request = pa.standing_request(ip.inn, ip.egrul, NOW)
    assert request.preferred_product == ProductId.OVERDRAFT and request.term_months == 12


def test_the_bureau_is_queried_with_the_standing_consent(world, results):
    """Pre-approved clients were scored on a bureau report: the consent got the БКИ past enrichment."""
    assert all(r.grade is not None for r in results.values() if r.approved)


def test_preapproved_client_applying_for_the_overdraft_gets_it(world, results):
    """The pre-approval is the engine's own answer: an application on the same data gets the same amount."""
    by_inn = {c.inn: c for c in world.companies}
    for r in [r for r in results.values() if r.approved]:
        request = request_for(by_inn[r.inn], purpose='cash_gap', preferred_product='overdraft', amount=r.amount,
                              term_months=12, declared_annual_revenue=None)
        d = decision_for(world, request)
        assert d.status == S.OFFERED and d.route == Route.AUTO, (r.inn, d.route_reasons)
        offer = d.offers[0]
        assert offer.product == ProductId.OVERDRAFT and offer.amount == r.amount and offer.rate == r.rate


def test_unavailable_required_source_or_unknown_company(world, results):
    approved = next(r for r in results.values() if r.approved)
    p = pipeline(world, scenarios={approved.inn: {SourceId.BKI: Scenario('error')}})
    r = run(approved.inn, p)
    assert not r.approved and r.reasons[0].code == 'preapproval_source_unavailable'
    p = pipeline(world, scenarios={approved.inn: {SourceId.EGRUL: Scenario('error')}})
    assert run(approved.inn, p).reasons[0].code == 'preapproval_source_unavailable'
    assert run(UNKNOWN_INN, pipeline(world)).reasons[0].code == 'preapproval_company_not_found'


def test_rate_above_cap_is_not_preapproved(world, results, monkeypatch):
    approved = next(r for r in results.values() if r.approved)
    quote = pa.quote
    monkeypatch.setattr(pa, 'quote', lambda *a, **k: replace(quote(*a, **k), within_cap=False))
    r = run(approved.inn, pipeline(world))
    assert not r.approved and r.reasons[0].code == 'preapproval_rate_above_cap'


def test_preapprove_is_pure(world):
    company = world.companies[0]
    p = pipeline(world)
    assert run(company.inn, p) == run(company.inn, p)


def test_batch_stores_the_latest_result_and_expires_it(world, results):
    p = pipeline(world)
    approved = next(r for r in results.values() if r.approved)
    declined = next(r for r in results.values() if not r.approved)
    batch = pa.run_batch(p, inns=[approved.inn, declined.inn])
    assert (batch.clients, batch.approved) == (2, 1)
    assert pa.latest(approved.inn, on=NOW.date()) == approved
    assert pa.latest(declined.inn, on=NOW.date()) == declined
    assert pa.latest(UNKNOWN_INN) is None
    expired = pa.latest(approved.inn, on=approved.valid_until + timedelta(days=1))
    assert not expired.approved and expired.reasons[0].code == 'preapproval_expired' and expired.client_view() is None

    pa.run_batch(p, inns=[approved.inn])  # the next night replaces the row, not adds one
    with Session() as s:
        assert s.query(PreApprovalRow).filter_by(inn=approved.inn).count() == 1
    assert pa.last_run() == NOW
    assert not pa.due(NOW + timedelta(hours=23)) and pa.due(NOW + timedelta(hours=24))


def test_latency_is_off_for_the_batch(world):
    p = pipeline(world)
    assert pa.batch_pipeline(p).client.settings.latency_scale == 0
    assert pa.batch_pipeline(p).client.world is world


def test_api(world, results, monkeypatch):
    from apps.api.main import app
    inn = next(r.inn for r in results.values() if r.approved)
    pa.run_batch(replace(pipeline(world), clock=lambda: datetime.now(MSK)), inns=[inn])  # valid as of today
    approved = pa.latest(inn)
    assert approved.approved
    monkeypatch.setattr(pa, 'start_batch', lambda pipeline=None: True)
    with TestClient(app) as customer:
        assert customer.get('/api/v2/preapproval', params={'inn': approved.inn}).status_code == 401
        customer.post('/api/auth/demo', json={'name': 'Клиент'})
        r = customer.get('/api/v2/preapproval', params={'inn': approved.inn})
        assert r.status_code == 200 and r.json() == approved.client_view()
        assert customer.get('/api/v2/preapproval', params={'inn': UNKNOWN_INN}).json() is None
        assert customer.get('/api/v2/preapproval', params={'inn': 'abc'}).status_code == 422
        assert customer.post('/api/v2/staff/preapproval/run').status_code == 403

        staff = TestClient(app)
        assert staff.post('/api/auth/staff', json={'password': os.environ['STAFF_PASSWORD'],
                                                    'position': 'underwriter'}).status_code == 200
        full = staff.get('/api/v2/preapproval', params={'inn': approved.inn}).json()
        assert full['inn'] == approved.inn and full['grade'] == approved.grade.value
        r = staff.post('/api/v2/staff/preapproval/run')
        assert r.status_code == 202 and r.json()['started'] is True


def test_data_quality_without_application_matches_the_batch(world, results):
    """The tier the batch stores is `data_quality` without a declared revenue, as T09 described."""
    company = next(c for c in world.companies if results[c.inn].data_quality_tier is not None)
    p = pipeline(world)
    request = pa.standing_request(company.inn, company.egrul, NOW)
    e = asyncio.run(enrich(request, p.client, policy=p.retry, now=lambda: NOW))
    assert data_quality_from_responses(e.responses).tier == results[company.inn].data_quality_tier
