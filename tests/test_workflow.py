"""Application workflow v2 (T19): state machine, audit log, the automatic run and the v2 API."""
import asyncio
import os
import tempfile
import time
from datetime import date, timedelta
from types import SimpleNamespace

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='alfa-tests-'))
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(os.environ['DATA_DIR'], 'test.db').replace('\\', '/'))
os.environ.setdefault('WORKER_ENABLED', 'false')
os.environ.setdefault('STAFF_PASSWORD', 'test-staff-password-only')

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from apps.api import contracts as c
from apps.api import db, workflow
from apps.api.contracts import ApplicationStatus as S, Route, SourceId
from apps.api.db import LoanApplication, Session
from apps.api.fact_store import load_facts
from apps.api.features import data_quality_from_responses
from apps.api.scoring import FeatureSetMismatch, Scorer, default_scorer
from apps.api.workflow import Pipeline, decide
from apps.sources import MockSourceService, Scenario, Settings, enrich
from apps.world import generate_world
from test_enrichment import AS_OF, FAST, NOW, request_for

OTHER_INN = '7700000000'


@pytest.fixture(scope='module')
def world():
    db.init_db()
    return generate_world(seed=5, n=80, as_of=AS_OF)


def pipeline(world, scenarios=None, scorer=None):
    service = MockSourceService(world, Settings(latency_scale=0, hang_seconds=5, scenarios=scenarios or {}))
    return Pipeline(client=service, as_of=AS_OF, scorer=scorer or default_scorer(), retry=FAST, clock=lambda: NOW)


def decision_for(world, request, scenarios=None, scorer=None):
    p = pipeline(world, scenarios, scorer)
    e = asyncio.run(enrich(request, p.client, policy=FAST, now=lambda: NOW))
    dq = None if e.company_not_found else data_quality_from_responses(e.responses, request.declared_annual_revenue)
    return decide(request, e, dq, as_of=AS_OF, decided_at=NOW, scorer=p.scorer)


@pytest.fixture(scope='module')
def outcomes(world):
    """One decision per company and amount on the small world, to find a case of each outcome."""
    found = []
    for company in world.companies:
        for amount in (300_000, 1_000_000):
            request = request_for(company, amount=amount)
            found.append((company, request, decision_for(world, request)))
    return found


def first(outcomes, test):
    return next((co, r, d) for co, r, d in outcomes if test(d))


def request_body(company, **changes):
    data = request_for(company, **changes).model_dump(mode='json')
    data.pop('application_id')
    data.pop('submitted_at')
    return data


def new_application(company, status=S.SUBMITTED, **changes):
    with Session.begin() as s:
        user = db.User(name='Клиент')
        s.add(user)
        s.flush()
        app = workflow.submit(s, user.id, db.uid(), request_body(company, **changes))
        if status != S.SUBMITTED:
            app.status = status.value
        return app.id, user.id


def load(application_id):
    with Session() as s:
        app = s.get(LoanApplication, application_id)
        return app, workflow.events(s, application_id)


def drain(p):
    while workflow.run_next(p):
        pass


# --- State machine ------------------------------------------------------------------------

SPEC_EDGES = [  # SPEC §13
    (S.DRAFT, S.SUBMITTED), (S.SUBMITTED, S.ENRICHING), (S.ENRICHING, S.NEEDS_DATA), (S.NEEDS_DATA, S.ENRICHING),
    (S.ENRICHING, S.SCORING), (S.SCORING, S.OFFERED), (S.SCORING, S.DECLINED), (S.SCORING, S.IN_REVIEW),
    (S.IN_REVIEW, S.OFFERED), (S.IN_REVIEW, S.DECLINED), (S.IN_REVIEW, S.NEEDS_DATA), (S.OFFERED, S.ACCEPTED),
    (S.ACCEPTED, S.DOCUMENTS), (S.DOCUMENTS, S.SIGNED), (S.SIGNED, S.CONDITIONS), (S.CONDITIONS, S.DISBURSED),
    (S.OFFERED, S.EXPIRED),
]


def test_transitions_cover_spec():
    assert set(workflow.TRANSITIONS) == set(S)
    for a, b in SPEC_EDGES:
        assert b in workflow.TRANSITIONS[a], (a, b)
    assert workflow.TERMINAL == {S.DECLINED, S.DISBURSED, S.WITHDRAWN, S.EXPIRED}
    # "до подписания из любого статуса -> withdrawn"
    assert workflow.WITHDRAWABLE == {S.DRAFT, S.SUBMITTED, S.ENRICHING, S.NEEDS_DATA, S.SCORING, S.IN_REVIEW,
                                     S.OFFERED, S.ACCEPTED, S.DOCUMENTS}
    assert all(set(v) <= set(S) for v in workflow.TRANSITIONS.values())


def test_transition_writes_an_audit_event_and_refuses_illegal_moves(world):
    application_id, user_id = new_application(world.companies[0])
    with Session.begin() as s:
        app = s.get(LoanApplication, application_id)
        with pytest.raises(workflow.IllegalTransition):
            workflow.transition(s, app, S.OFFERED)
        workflow.transition(s, app, S.WITHDRAWN, actor=f'client:{user_id}', message='Отзыв')
    app, events = load(application_id)
    assert app.status == 'withdrawn'
    assert [(e.kind, e.from_status, e.to_status) for e in events] == [
        ('status', None, 'submitted'), ('status', 'submitted', 'withdrawn')]
    assert events[-1].actor == f'client:{user_id}' and events[-1].message == 'Отзыв'
    with Session.begin() as s, pytest.raises(workflow.IllegalTransition):
        workflow.withdraw(s, s.get(LoanApplication, application_id), actor='client:x')


# --- The decision ------------------------------------------------------------------------

def test_every_outcome_is_reachable_and_consistent(outcomes):
    statuses = {d.status for *_, d in outcomes}
    assert {S.OFFERED, S.DECLINED, S.IN_REVIEW} <= statuses
    for _, request, d in outcomes:
        assert d.application_id == request.application_id
        assert d.policy_version == 'credit=credit_policy_v0;data_quality=data_quality_v0;features=features_v0'
        if d.status == S.OFFERED:
            assert d.route == Route.AUTO and d.offers and d.reapply_after is None
        if d.status == S.IN_REVIEW:
            assert d.route != Route.AUTO and d.reapply_after is None
        if d.stop_factors:
            assert d.status == S.DECLINED and d.score is None and d.route_reasons[0].code == 'route_stop_factor'
        if d.status == S.DECLINED and not d.stop_factors:
            assert d.score and d.reapply_after == NOW.date() + timedelta(days=90) and d.reasons
        if d.data_quality.tier == c.DataQualityTier.THIN and not d.stop_factors:
            assert d.status == S.IN_REVIEW and 'route_data_thin' in {r.code for r in d.route_reasons}
        if d.score:
            assert d.challenger is not None and d.model_version == d.score.model_version


def test_decide_is_deterministic(world, outcomes):
    _, request, d = first(outcomes, lambda d: d.status == S.OFFERED)
    assert decision_for(world, request) == d
    assert all(o.valid_until == NOW.date() + timedelta(days=10) for o in d.offers)


def test_company_not_found_is_declined(world):
    request = request_for(world.companies[0], inn=OTHER_INN,
                          consents=[('bki_company', OTHER_INN), ('bki_owner', '770000000000')])
    d = decision_for(world, request)
    assert d.status == S.DECLINED and d.route == Route.AUTO and d.data_quality is None
    assert [r.code for r in d.reasons] == ['company_not_found'] and d.reapply_after is None


def test_required_source_unavailable_goes_to_the_underwriter(world):
    company = world.companies[0]
    d = decision_for(world, request_for(company), {company.inn: {SourceId.BKI: Scenario('error')}})
    assert d.status == S.IN_REVIEW and d.route == Route.UNDERWRITER and d.score is None and not d.offers
    assert [r.code for r in d.route_reasons] == ['route_source_unavailable']


def test_feature_set_mismatch_goes_to_the_underwriter(world, outcomes):
    class Mismatch:
        version, feature_set_version = 'v_other', 'features_v9'

        def predict(self, features):
            raise FeatureSetMismatch('v_other needs features features_v9')

    _, request, _ = first(outcomes, lambda d: d.status == S.OFFERED)
    d = decision_for(world, request, scorer=Scorer(Mismatch()))
    assert d.status == S.IN_REVIEW and d.score is None and d.model_version is None and not d.offers
    assert 'route_score_unavailable' in {r.code for r in d.route_reasons}


def test_the_application_takes_the_strictest_offer_route():
    offer = lambda i, amount: SimpleNamespace(offer_id=i, amount=amount)
    offer_set = SimpleNamespace(offers=[offer('a', 1_000_000), offer('b', 730_000), offer('c', 6_000_000)],
                                routes={'a': Route.UNDERWRITER, 'b': Route.AUTO, 'c': Route.UNDERWRITER})
    assert workflow._strictest_offer(offer_set) == (1_000_000, Route.UNDERWRITER)
    assert workflow._strictest_offer(SimpleNamespace(offers=[], routes={})) is None


def test_offers_on_a_review_route_stay_for_staff(outcomes):
    _, _, d = first(outcomes, lambda d: d.status == S.IN_REVIEW and d.offers)
    assert d.matches_request is not None and d.score is not None


# --- The automatic run ---------------------------------------------------------------------

def test_run_stores_facts_events_and_the_decision(world, outcomes):
    company, request, expected = first(outcomes, lambda d: d.status == S.OFFERED)
    application_id, _ = new_application(company, amount=request.amount)
    drain(pipeline(world))
    app, events = load(application_id)
    assert app.status == 'offered' and app.attempts == 1 and app.lease_until is None
    assert [e.to_status for e in events if e.kind == 'status'] == ['submitted', 'enriching', 'scoring', 'offered']
    sources = [e for e in events if e.kind == 'source']
    assert {e.data['source'] for e in sources if e.data['final']} == {s.value for s in SourceId} - {'statement_upload'}
    assert all(e.data['status'] == 'ok' for e in sources if e.data['final'])
    assert [e.kind for e in events].index('decision') < [e.to_status for e in events].index('offered')

    decision = c.Decision.model_validate(app.decision)
    assert decision.application_id == application_id and decision.status == S.OFFERED
    assert [o.amount for o in decision.offers] == [o.amount for o in expected.offers]
    assert app.offer_valid_until == max(o.valid_until for o in decision.offers).isoformat()
    assert c.DataQuality.model_validate(app.data_quality).tier == expected.data_quality.tier
    assert {s['source'] for s in app.enrichment['responses']} and 'facts' not in app.enrichment
    with Session() as s:
        assert load_facts(s, application_id)


def test_company_not_found_run_skips_scoring(world):
    application_id, _ = new_application(world.companies[0], inn=OTHER_INN,
                                        consents=[('bki_company', OTHER_INN), ('bki_owner', '770000000000')])
    drain(pipeline(world))
    app, events = load(application_id)
    assert app.status == 'declined' and app.data_quality is None
    assert [e.to_status for e in events if e.kind == 'status'] == ['submitted', 'enriching', 'declined']


def test_a_withdrawn_application_drops_the_run_result(world):
    application_id, user_id = new_application(world.companies[0])
    assert workflow.claim_next() == application_id
    with Session.begin() as s:
        workflow.withdraw(s, s.get(LoanApplication, application_id), actor=f'client:{user_id}')
    workflow.run_application(application_id, pipeline(world))
    app, _ = load(application_id)
    assert app.status == 'withdrawn' and app.decision is None and app.enrichment is None


def test_a_failing_run_is_retried_then_goes_to_staff(world, outcomes):
    class Broken:
        version, feature_set_version = 'broken', 'features_v0'

        def predict(self, features):
            raise RuntimeError('model crashed')

    company, request, _ = first(outcomes, lambda d: d.status == S.OFFERED)
    application_id, _ = new_application(company, amount=request.amount)
    p = pipeline(world, scorer=Scorer(Broken()))
    for attempt in range(1, workflow.MAX_ATTEMPTS + 1):
        with Session.begin() as s:
            s.get(LoanApplication, application_id).lease_until = None  # skip the retry delay
        drain(p)
        app, events = load(application_id)
        assert app.attempts == attempt
        if attempt < workflow.MAX_ATTEMPTS:
            assert app.status == 'submitted' and app.lease_until > time.time()
    assert app.status == 'in_review' and app.decision is None
    errors = [e for e in events if e.kind == 'error']
    assert len(errors) == workflow.MAX_ATTEMPTS and 'RuntimeError' in errors[0].message


def test_a_dead_run_is_recovered(world):
    application_id, _ = new_application(world.companies[0])
    assert workflow.claim_next() == application_id
    with Session.begin() as s:
        s.get(LoanApplication, application_id).lease_until = time.time() - 1
    assert workflow.recover_stale() >= 1
    app, events = load(application_id)
    assert app.status == 'submitted' and events[-1].from_status == 'enriching'
    assert any(e.kind == 'error' and 'истекло время' in e.message for e in events)


def test_claim_takes_each_application_once(world):
    application_id, _ = new_application(world.companies[0])
    assert workflow.claim_next() == application_id
    assert workflow.claim_next() is None
    with Session.begin() as s:  # leave nothing claimable behind for other tests
        workflow.withdraw(s, s.get(LoanApplication, application_id), actor='client:test')


def _offered(world, outcomes):
    company, request, _ = first(outcomes, lambda d: d.status == S.OFFERED)
    application_id, user_id = new_application(company, amount=request.amount)
    drain(pipeline(world))
    app, _ = load(application_id)
    assert app.status == 'offered'
    return app, user_id


def test_offers_expire_after_their_validity(world, outcomes):
    app, _ = _offered(world, outcomes)
    valid_until = date.fromisoformat(app.offer_valid_until)
    workflow.expire_offers(on=valid_until)
    assert load(app.id)[0].status == 'offered'
    workflow.expire_offers(on=valid_until + timedelta(days=1))
    app, events = load(app.id)
    assert app.status == 'expired' and events[-1].actor == 'system'


def test_accept(world, outcomes):
    app, user_id = _offered(world, outcomes)
    offer_id = app.decision['offers'][0]['offer_id']
    with Session.begin() as s:
        a = s.get(LoanApplication, app.id)
        with pytest.raises(LookupError):
            workflow.accept(s, a, 'no-such-offer', actor=f'client:{user_id}', on=NOW.date())
        workflow.accept(s, a, offer_id, actor=f'client:{user_id}', on=NOW.date())
    app, events = load(app.id)
    assert app.status == 'accepted' and app.selected_offer_id == offer_id
    assert events[-1].data == {'offer_id': offer_id} and events[-1].actor == f'client:{user_id}'
    with Session.begin() as s, pytest.raises(workflow.IllegalTransition):
        workflow.accept(s, s.get(LoanApplication, app.id), offer_id, actor='client:x', on=NOW.date())


def test_accepting_a_stale_offer_expires_the_application(world, outcomes):
    app, user_id = _offered(world, outcomes)
    late = date.fromisoformat(app.offer_valid_until) + timedelta(days=1)
    with Session.begin() as s:
        workflow.accept(s, s.get(LoanApplication, app.id), app.decision['offers'][0]['offer_id'],
                        actor=f'client:{user_id}', on=late)
    assert load(app.id)[0].status == 'expired'


def test_submit_refuses_an_investment_loan_without_equipment(world):
    with Session.begin() as s:
        user = db.User(name='Клиент')
        s.add(user)
        s.flush()
        with pytest.raises(ValueError, match='оборудование'):
            workflow.submit(s, user.id, db.uid(), request_body(world.companies[0], purpose='equipment'))


# --- API ---------------------------------------------------------------------------------

@pytest.fixture
def api(world):
    from apps.api.main import app
    with TestClient(app) as client:
        assert client.post('/api/auth/demo', json={'name': 'Клиент'}).status_code == 200
        yield client


def test_api_flow(api, world, outcomes):
    company, request, _ = first(outcomes, lambda d: d.status == S.OFFERED)
    body = {'idempotency_key': 'key-' + db.uid(), 'request': request_body(company, amount=request.amount)}
    r = api.post('/api/v2/applications', json=body)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created['status'] == 'submitted' and created['request']['application_id'] == created['id']
    assert api.post('/api/v2/applications', json=body).json()['id'] == created['id']  # replay
    assert created['id'] in {a['id'] for a in api.get('/api/v2/applications').json()}

    drain(pipeline(world))
    view = api.get(f'/api/v2/applications/{created["id"]}').json()
    assert view['status'] == 'offered' and view['decision']['status'] == 'offered'
    assert [e['status'] for e in view['events']] == ['submitted', 'enriching', 'scoring', 'offered']
    assert {e['source'] for e in view['sources'] if e['final'] and e['status'] == 'ok'} >= {'accounts', 'bki'}
    text = str(view)
    for staff_only in ('challenger', 'expected_profit', 'staff_text', "'score'", 'flags', 'actor'):
        assert staff_only not in text
    assert api.get(f'/api/v2/applications/{created["id"]}/events').status_code == 403

    offer_id = view['decision']['offers'][0]['offer_id']
    stale = api.post(f'/api/v2/applications/{created["id"]}/accept',
                     json={'offer_id': offer_id, 'revision': view['revision'] - 1})
    assert stale.status_code == 409
    r = api.post(f'/api/v2/applications/{created["id"]}/accept', json={'offer_id': offer_id,
                                                                       'revision': view['revision']})
    assert r.status_code == 200 and r.json()['status'] == 'documents'  # documents are generated on accept (T21)
    revision = r.json()['revision']
    r = api.post(f'/api/v2/applications/{created["id"]}/withdraw', json={'revision': revision})
    assert r.status_code == 200 and r.json()['status'] == 'withdrawn'
    assert api.post(f'/api/v2/applications/{created["id"]}/withdraw',
                    json={'revision': r.json()['revision']}).status_code == 409

    staff = TestClient(api.app)
    assert staff.post('/api/auth/staff', json={'password': os.environ['STAFF_PASSWORD']}).status_code == 200
    full = staff.get(f'/api/v2/applications/{created["id"]}').json()
    assert full['decision']['challenger'] and full['facts'] and full['sources'] and full['data_quality']
    events = staff.get(f'/api/v2/applications/{created["id"]}/events').json()
    assert {'status', 'source', 'decision', 'action'} <= {e['kind'] for e in events}
    assert staff.post(f'/api/v2/applications/{created["id"]}/withdraw', json={'revision': 1}).status_code == 403

    other = TestClient(api.app)
    other.post('/api/auth/demo', json={'name': 'Другой'})
    assert other.get(f'/api/v2/applications/{created["id"]}').status_code == 404
    assert other.post('/api/v2/applications', json=body).json()['id'] != created['id']  # keys are per user


def test_api_validation(api, world):
    company = world.companies[0]
    bad = request_body(company)
    bad.pop('amount')
    assert api.post('/api/v2/applications', json={'idempotency_key': 'key-' + db.uid(), 'request': bad}
                    ).status_code == 422
    r = api.post('/api/v2/applications', json={'idempotency_key': 'key-' + db.uid(),
                                               'request': request_body(company, purpose='equipment')})
    assert r.status_code == 422 and 'оборудование' in r.text


# --- Migration ---------------------------------------------------------------------------

def test_migration_moves_the_fact_store_to_v2_applications(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(db, 'engine', engine)
    db.init_db()
    tables = set(inspect(engine).get_table_names())
    assert {'loan_applications', 'application_events', 'application_facts'} <= tables
    [fk] = inspect(engine).get_foreign_keys('application_facts')
    assert fk['referred_table'] == 'loan_applications'
    engine.dispose()
