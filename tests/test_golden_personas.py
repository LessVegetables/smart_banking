"""Golden tests (T33): every demo persona through the API and the worker gets the decision in the
expected-outcomes table (`apps/world/demo/personas.json`, T12), and one application goes end to end.

`tests/test_world_personas.py` checks the table against `decide` alone; here the same requests go through
submission, the worker's run, statement upload and staff actions, as the demo does them.
"""
import os
import tempfile

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='alfa-tests-'))
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(os.environ['DATA_DIR'], 'test.db').replace('\\', '/'))
os.environ.setdefault('WORKER_ENABLED', 'false')
os.environ.setdefault('STAFF_PASSWORD', 'test-staff-password-only')

import pytest
from fastapi.testclient import TestClient

from apps.api import db, disbursement, workflow
from apps.api.contracts import Decision
from apps.api.db import Session, User
from apps.api.scoring import default_scorer
from apps.api.workflow import Pipeline
from apps.sources import MockSourceService, Settings
from apps.sources.service import parse_scenarios
from apps.world import generate_world
from apps.world.personas import PERSONAS_FILE, RETRY, STATEMENTS_DIR, expected, read

PERSONAS = read(PERSONAS_FILE).personas
BY_ID = {p.persona_id: p for p in PERSONAS}
COMMENT = 'Проверено по выписке и документам клиента'


@pytest.fixture(scope='module')
def pipeline():
    """The default world the personas were picked on, with every persona's source failures (MOCK_SOURCES_SCENARIOS)."""
    db.init_db()
    world = generate_world()
    scenarios = {p.inn: p.scenarios for p in PERSONAS if p.scenarios}
    service = MockSourceService(world, Settings(latency_scale=0, failures=False, hang_seconds=0.01,
                                                scenarios=parse_scenarios(scenarios)))
    p = Pipeline(client=service, as_of=world.meta.as_of, scorer=default_scorer(), retry=RETRY)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(workflow, 'default_pipeline', lambda: p)  # the API's demo catalog reads this world too
        yield p


@pytest.fixture(scope='module')
def api(pipeline):
    from apps.api.main import app
    with TestClient(app) as client:
        yield client


def drain(pipeline):
    while workflow.run_next(pipeline):
        pass


def customer(api, persona):
    c = TestClient(api.app)
    assert c.post('/api/auth/demo', json={'name': f'Клиент {persona.persona_id}'}).status_code == 200
    if persona.company_id is None:
        # The demo catalog holds only companies of the world, so an unknown ИНН is submitted as a non-demo client.
        with Session.begin() as s:
            s.get(User, c.get('/api/auth/me').json()['id']).identity = f'telegram:golden-{persona.persona_id}'
    return c


def staff(api, position='underwriter'):
    c = TestClient(api.app)
    r = c.post('/api/auth/staff', json={'password': os.environ['STAFF_PASSWORD'], 'position': position})
    assert r.status_code == 200, r.text
    return c


def submit(api, pipeline, persona):
    """The client submits the persona's application and the worker decides it."""
    client = customer(api, persona)
    r = client.post('/api/v2/applications', json={'idempotency_key': f'golden-{persona.persona_id}',
                                                 'request': persona.request})
    assert r.status_code == 200, r.text
    drain(pipeline)
    return client, f'/api/v2/applications/{r.json()["id"]}'


def decided(staff_client, url):
    view = staff_client.get(url).json()
    return view, expected(Decision.model_validate(view['decision']))


def upload(client, url, persona):
    revision = client.get(url).json()['revision']
    data = (STATEMENTS_DIR / persona.statement_file).read_bytes()
    r = client.post(url + '/statements', data={'revision': revision},
                    files=[('files', (persona.statement_file, data, 'application/json'))])
    assert r.status_code == 200 and r.json()['status'] == 'enriching', r.text


@pytest.mark.parametrize('persona', PERSONAS, ids=[p.persona_id for p in PERSONAS])
def test_persona_gets_the_expected_decision(api, pipeline, persona):
    client, url = submit(api, pipeline, persona)
    underwriter = staff(api)
    view, got = decided(underwriter, url)
    assert view['status'] == persona.expected.status
    assert got == persona.expected

    if persona.statement_file is None:
        return
    if view['status'] == 'in_review':
        # PARTIAL: the underwriter asks for the other bank's statement (T20), the client answers it (T23).
        r = underwriter.post(url + '/review', json={
            'action': 'request_data', 'reason_code': 'statement_needed', 'comment': COMMENT,
            'documents': ['Выписка из другого банка'], 'revision': view['revision']})
        assert r.status_code == 200 and r.json()['application']['status'] == 'needs_data', r.text
    upload(client, url, persona)
    drain(pipeline)
    view, got = decided(underwriter, url)
    assert view['status'] == persona.expected_after_statement.status
    assert got == persona.expected_after_statement


# --- End to end --------------------------------------------------------------------------

def test_smoke_auto_approval_to_disbursed(api, pipeline):
    persona = BY_ID['auto_ooo']
    client, url = submit(api, pipeline, persona)
    view = client.get(url).json()
    assert view['status'] == 'offered' and 'facts' not in view  # the client's view keeps staff data out
    offer = view['decision']['offers'][0]
    assert offer['amount'] == persona.expected.offers[0].amount

    r = client.post(url + '/accept', json={'offer_id': offer['offer_id'], 'revision': view['revision']})
    assert r.status_code == 200 and r.json()['status'] == 'documents', r.text
    r = client.post(url + '/sign/code', json={'revision': r.json()['revision']})
    assert r.status_code == 200, r.text
    code, view = r.json()['demo_code'], r.json()['application']
    r = client.post(url + '/sign', json={'code': code, 'revision': view['revision']})
    assert r.status_code == 200 and r.json()['status'] == 'conditions', r.text

    disbursement.tick()  # the worker checks the conditions and pays out
    view = client.get(url).json()
    assert view['status'] == 'disbursed' and view['closing']['payout']['to'] == 'client_account'
    assert [e['status'] for e in view['events']] == ['submitted', 'enriching', 'scoring', 'offered', 'accepted',
                                                     'documents', 'signed', 'conditions', 'disbursed']


def test_smoke_underwriter_approves(api, pipeline):
    persona = BY_ID['underwriter']
    client, url = submit(api, pipeline, persona)
    underwriter = staff(api)
    view = underwriter.get(url).json()
    assert view['status'] == 'in_review'
    r = underwriter.post(url + '/review', json={'action': 'approve', 'reason_code': 'confirmed_by_review',
                                                'comment': COMMENT, 'revision': view['revision']})
    assert r.status_code == 200 and r.json()['outcome'] == 'applied', r.text
    view = client.get(url).json()
    assert view['status'] == 'offered'
    assert [(o['amount'], o['term_months']) for o in view['decision']['offers']] == \
        [(o.amount, o.term_months) for o in persona.expected.offers]
