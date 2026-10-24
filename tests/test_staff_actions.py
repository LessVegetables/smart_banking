"""Staff actions API (T20): review with a reason code, re-routing by authority, committee with two signatures."""
import functools
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from apps.api import db, staff_actions as sa, workflow
from apps.api.contracts import ApplicationStatus as S, Decision, Route, SourceId
from apps.api.db import LoanApplication, Session
from apps.api.routing import RoutingPolicy, route_application
from apps.api.staff_actions import Action, OfferChange, Position, Staff, StaffActionError
from apps.sources import Scenario
from test_enrichment import NOW
from test_workflow import drain, first, load, new_application, outcomes, pipeline, world  # noqa: F401 (fixtures)

AT = NOW + timedelta(days=2)  # staff act two days after the engine's run
os.environ.setdefault('STAFF_PASSWORD', 'test-staff-password-only')
COMMENT = 'Проверено по выписке и документам клиента'


def staff_user(position=Position.UNDERWRITER) -> Staff:
    with Session.begin() as s:
        user = db.User(name='Сотрудник', role='staff', staff_position=position.value)
        s.add(user)
        s.flush()
        return sa.staff_of(user)


def decided(world, company, amount, scenarios=None) -> str:
    application_id, _ = new_application(company, amount=amount)
    drain(pipeline(world, scenarios))
    return application_id


@pytest.fixture
def review_case(world, outcomes):
    """An application the engine sent to the underwriter with offers."""
    company, request, d = first(outcomes, lambda d: d.status == S.IN_REVIEW and d.offers and d.route == Route.UNDERWRITER)
    application_id = decided(world, company, request.amount)
    assert load(application_id)[0].status == S.IN_REVIEW
    return application_id, d


def act(application_id, staff, action, reason_code, comment=COMMENT, **kwargs):
    with Session.begin() as s:
        return sa.review(s, s.get(LoanApplication, application_id), staff, action, reason_code, comment, at=AT,
                         **kwargs)


def sign(application_id, staff, **kwargs):
    with Session.begin() as s:
        return sa.sign(s, s.get(LoanApplication, application_id), staff, comment=COMMENT, at=AT, **kwargs)


def decision_of(application_id) -> Decision:
    return Decision.model_validate(load(application_id)[0].decision)


@pytest.fixture
def committee(monkeypatch):
    """Every amount needs the committee, so a small-world application can reach it."""
    monkeypatch.setattr(sa, 'route_application',
                        functools.partial(route_application, policy=RoutingPolicy(auto_max_full=0, underwriter_max=0)))


# --- One signature ---------------------------------------------------------------------------

def test_approve_keeps_the_offers_and_redates_them(review_case):
    application_id, d = review_case
    underwriter = staff_user()
    assert act(application_id, underwriter, Action.APPROVE, 'flag_explained') == 'applied'
    app, events = load(application_id)
    out = decision_of(application_id)
    assert app.status == S.OFFERED and [(o.kind, o.amount) for o in out.offers] == [(o.kind, o.amount) for o in d.offers]
    assert all(o.valid_until == AT.date() + timedelta(days=10) for o in out.offers)
    assert app.offer_valid_until == (AT.date() + timedelta(days=10)).isoformat()
    assert out.route == Route.UNDERWRITER and out.reasons[-1].code == 'staff_flag_explained'
    assert COMMENT in out.reasons[-1].staff_text and 'flag' not in out.reasons[-1].client_text
    review = next(e for e in events if e.kind == 'review')
    assert review.actor == underwriter.actor and review.data['override'] is False
    assert review.data['reason_code'] == 'flag_explained' and review.data['comment'] == COMMENT
    assert [e.to_status for e in events if e.kind == 'status'][-1] == 'offered'
    assert events[-1].actor == underwriter.actor


def test_approving_some_offers_is_an_override(review_case):
    application_id, d = review_case
    last = decision_of(application_id).offers[-1].offer_id
    act(application_id, staff_user(), Action.APPROVE, 'confirmed_by_review', offer_ids=[last])
    assert [o.offer_id for o in decision_of(application_id).offers] == [last]
    review = next(e for e in load(application_id)[1] if e.kind == 'review')
    assert review.data['override'] is (len(d.offers) > 1)


def test_change_reprices_within_the_limit(review_case):
    application_id, d = review_case
    base = d.offers[0]
    amount = base.amount - 10_000  # one amount step down
    act(application_id, staff_user(), Action.CHANGE, 'risk_not_in_model',
        changes=[OfferChange(amount=amount, term_months=base.term_months, product=base.product)])
    [offer] = decision_of(application_id).offers
    assert offer.amount == amount and offer.offer_id.endswith('-staff-1') and offer.amount <= offer.limit.limit
    assert offer.price.rate == offer.rate and offer.valid_until == AT.date() + timedelta(days=10)
    assert load(application_id)[0].status == S.OFFERED


def test_change_over_the_limit_is_refused(review_case):
    application_id, d = review_case
    base = d.offers[0]
    with pytest.raises(StaffActionError, match='выше лимита'):
        act(application_id, staff_user(), Action.CHANGE, 'client_request',
            changes=[OfferChange(amount=base.limit.limit * 3 + 1_000_000, term_months=base.term_months,
                                 product=base.product)])
    assert load(application_id)[0].status == S.IN_REVIEW


def test_reason_code_and_comment_are_required(review_case):
    application_id, _ = review_case
    with pytest.raises(StaffActionError, match='код причины'):
        act(application_id, staff_user(), Action.APPROVE, 'fraud_suspected')  # a decline code
    with pytest.raises(StaffActionError, match='Комментарий'):
        act(application_id, staff_user(), Action.APPROVE, 'flag_explained', comment='ок')
    with pytest.raises(StaffActionError):
        act(application_id, staff_user(), Action.REQUEST_DATA, 'statement_needed', documents=[' '])


def test_decline_hides_the_reason_from_the_client(review_case):
    application_id, _ = review_case
    act(application_id, staff_user(), Action.DECLINE, 'aml_risk')
    app, events = load(application_id)
    out = decision_of(application_id)
    assert app.status == S.DECLINED and not out.offers and out.reapply_after == AT.date() + timedelta(days=90)
    assert next(e for e in events if e.kind == 'review').data['override'] is True  # the system had offers
    client = workflow.client_decision(app.decision)
    assert '115' not in str(client) and 'aml' not in str(client) and sa.CLIENT_TEXT[Action.DECLINE] in client['reasons']


def test_request_data_moves_to_needs_data(review_case):
    application_id, _ = review_case
    act(application_id, staff_user(), Action.REQUEST_DATA, 'statement_needed', documents=['Выписка за 12 месяцев'])
    assert load(application_id)[0].status == S.NEEDS_DATA
    assert decision_of(application_id).requested_documents == ['Выписка за 12 месяцев']
    with pytest.raises(workflow.IllegalTransition):
        act(application_id, staff_user(), Action.APPROVE, 'flag_explained')


def test_rerun_after_a_source_was_unavailable(world):
    company = world.companies[0]
    application_id = decided(world, company, 300_000, {company.inn: {SourceId.BKI: Scenario('error')}})
    assert decision_of(application_id).score is None
    with pytest.raises(StaffActionError, match='Без скоринга'):
        act(application_id, staff_user(), Action.CHANGE, 'client_request',
            changes=[OfferChange(amount=300_000, term_months=12)])
    act(application_id, staff_user(), Action.RERUN, 'source_restored')
    app, events = load(application_id)
    assert app.status == S.SUBMITTED and app.attempts == 0
    assert events[-1].from_status == 'in_review' and events[-1].to_status == 'submitted'
    drain(pipeline(world))
    assert load(application_id)[0].status != S.SUBMITTED and decision_of(application_id).score is not None


def test_rerun_is_refused_for_a_scored_application(review_case):
    application_id, _ = review_case
    with pytest.raises(StaffActionError):
        act(application_id, staff_user(), Action.RERUN, 'technical_failure')


def test_decline_without_any_decision(world, outcomes):
    """Three failed automatic runs leave `decision = null`; staff can still decline."""
    company, request, _ = first(outcomes, lambda d: d.status == S.OFFERED)
    application_id, _ = new_application(company, status=S.IN_REVIEW, amount=request.amount)
    act(application_id, staff_user(), Action.DECLINE, 'data_unreliable')
    out = decision_of(application_id)
    assert load(application_id)[0].status == S.DECLINED and out.score is None and out.reasons[-1].code == 'staff_data_unreliable'


def test_actions_need_in_review(world, outcomes):
    company, request, _ = first(outcomes, lambda d: d.status == S.OFFERED)
    application_id = decided(world, company, request.amount)
    with pytest.raises(workflow.IllegalTransition):
        act(application_id, staff_user(), Action.DECLINE, 'business_risk')


# --- Committee -------------------------------------------------------------------------------

def test_committee_needs_an_underwriter_and_a_risk_manager(review_case, committee):
    application_id, d = review_case
    underwriter, other_underwriter, risk = staff_user(), staff_user(), staff_user(Position.RISK_MANAGER)
    assert act(application_id, underwriter, Action.APPROVE, 'confirmed_by_review') == 'proposed'
    app, events = load(application_id)
    assert app.status == S.IN_REVIEW and app.pending_review['route'] == 'committee' and events[-1].kind == 'proposal'
    with pytest.raises(workflow.IllegalTransition, match='второй подписи'):
        act(application_id, risk, Action.DECLINE, 'business_risk')
    with pytest.raises(StaffActionError, match='другой сотрудник'):
        sign(application_id, underwriter, approve=True)
    with pytest.raises(StaffActionError, match='риск-менеджер'):
        sign(application_id, other_underwriter, approve=True)

    assert sign(application_id, risk, approve=True) == 'applied'
    app, events = load(application_id)
    out = decision_of(application_id)
    assert app.status == S.OFFERED and app.pending_review is None and out.route == Route.COMMITTEE
    assert all(o.valid_until == AT.date() + timedelta(days=10) for o in out.offers)
    review = next(e for e in events if e.kind == 'review')
    assert [s['position'] for s in review.data['signatures']] == ['underwriter', 'risk_manager']
    assert [e.kind for e in events][-5:] == ['proposal', 'signature', 'review', 'decision', 'status']


def test_risk_manager_may_sign_first(review_case, committee):
    application_id, _ = review_case
    act(application_id, staff_user(Position.RISK_MANAGER), Action.APPROVE, 'confirmed_by_review')
    assert sign(application_id, staff_user(), approve=True) == 'applied'


def test_committee_rejection_returns_the_application_to_review(review_case, committee):
    application_id, _ = review_case
    act(application_id, staff_user(), Action.APPROVE, 'confirmed_by_review')
    with pytest.raises(StaffActionError, match='код причины'):
        sign(application_id, staff_user(Position.RISK_MANAGER), approve=False)
    assert sign(application_id, staff_user(Position.RISK_MANAGER), approve=False,
                reason_code='terms_disagreed') == 'rejected'
    app, events = load(application_id)
    assert app.status == S.IN_REVIEW and app.pending_review is None and events[-1].data['approve'] is False
    act(application_id, staff_user(), Action.DECLINE, 'business_risk')  # a decline needs one signature
    assert load(application_id)[0].status == S.DECLINED


def test_a_smaller_amount_needs_less_authority(review_case, monkeypatch):
    """Staff cut the amount under the committee bound: the underwriter approves alone."""
    application_id, d = review_case
    base = d.offers[0]
    bound = base.amount - 10_000
    monkeypatch.setattr(sa, 'route_application', functools.partial(
        route_application, policy=RoutingPolicy(auto_max_full=0, underwriter_max=bound)))
    assert act(application_id, staff_user(), Action.CHANGE, 'risk_not_in_model',
               changes=[OfferChange(amount=bound, term_months=base.term_months, product=base.product)]) == 'applied'
    assert decision_of(application_id).route == Route.UNDERWRITER


def test_sign_without_a_proposal(review_case):
    application_id, _ = review_case
    with pytest.raises(workflow.IllegalTransition):
        sign(application_id, staff_user(Position.RISK_MANAGER), approve=True)


# --- Overrides and API -----------------------------------------------------------------------

def test_override_stats(review_case, world, outcomes):
    with Session() as s:
        before = sa.override_stats(s)
    application_id, _ = review_case
    act(application_id, staff_user(), Action.APPROVE, 'flag_explained')
    company, request, _ = first(outcomes, lambda d: d.status == S.IN_REVIEW and d.offers and d.route == Route.UNDERWRITER)
    other = decided(world, company, request.amount)
    act(other, staff_user(), Action.DECLINE, 'fraud_suspected')
    with Session() as s:
        after = sa.override_stats(s)
    assert after['decisions'] == before['decisions'] + 2 and after['overrides'] == before['overrides'] + 1
    assert after['by_action']['decline']['overrides'] == before['by_action']['decline']['overrides'] + 1
    assert after['override_reasons']['fraud_suspected'] >= 1 and 0 < after['override_rate'] < 1


def test_migration_adds_the_staff_columns():
    columns = {t: {c['name'] for c in inspect(db.engine).get_columns(t)} for t in ('users', 'loan_applications')}
    assert 'staff_position' in columns['users'] and 'pending_review' in columns['loan_applications']


@pytest.fixture
def staff_api(world):
    from apps.api.main import app
    with TestClient(app) as client:
        yield client


def login(client, position):
    r = client.post('/api/auth/staff', json={'password': os.environ['STAFF_PASSWORD'], 'position': position})
    assert r.status_code == 200, r.text


def test_api(staff_api, review_case, committee):
    application_id, d = review_case
    url = f'/api/v2/applications/{application_id}'
    customer = TestClient(staff_api.app)
    customer.post('/api/auth/demo', json={'name': 'Клиент'})
    assert customer.get('/api/v2/staff/reason-codes').status_code == 403
    assert customer.post(url + '/review', json={'action': 'decline', 'reason_code': 'aml_risk', 'comment': COMMENT,
                                                'revision': 1}).status_code == 403

    underwriter, risk = staff_api, TestClient(staff_api.app)
    login(underwriter, 'underwriter')
    login(risk, 'risk_manager')
    assert underwriter.get('/api/auth/me').json()['staff_position'] == 'underwriter'
    codes = underwriter.get('/api/v2/staff/reason-codes').json()
    assert 'aml_risk' in codes['actions']['decline'] and 'terms_disagreed' in codes['reject']

    revision = underwriter.get(url).json()['revision']
    body = {'action': 'approve', 'reason_code': 'flag_explained', 'comment': COMMENT}
    assert underwriter.post(url + '/review', json={**body, 'revision': revision - 1}).status_code == 409
    assert underwriter.post(url + '/review', json={**body, 'reason_code': 'nope', 'revision': revision}).status_code == 422
    r = underwriter.post(url + '/review', json={**body, 'revision': revision})
    assert r.status_code == 200 and r.json()['outcome'] == 'proposed', r.text
    view = r.json()['application']
    assert view['status'] == 'in_review' and view['pending_review']['signatures'][0]['position'] == 'underwriter'

    assert underwriter.post(url + '/review/sign', json={'approve': True, 'comment': COMMENT,
                                                       'revision': view['revision']}).status_code == 422
    r = risk.post(url + '/review/sign', json={'approve': True, 'comment': COMMENT, 'revision': view['revision']})
    assert r.status_code == 200 and r.json()['outcome'] == 'applied' and r.json()['application']['status'] == 'offered'
    assert risk.post(url + '/review/sign', json={'approve': True, 'comment': COMMENT,
                                                'revision': r.json()['application']['revision']}).status_code == 409
    stats = risk.get('/api/v2/staff/overrides').json()
    assert stats['decisions'] >= 1 and set(stats['by_action']) == {'approve', 'change', 'decline'}
