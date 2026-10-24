"""Documents, signing with a confirmation code, pre-disbursement conditions and payout (T21)."""
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='alfa-tests-'))
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(os.environ['DATA_DIR'], 'test.db').replace('\\', '/'))
os.environ.setdefault('WORKER_ENABLED', 'false')

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from apps.api import db, disbursement as dsb, workflow
from apps.api.contracts import ApplicationStatus as S
from apps.api.db import LoanApplication, Session
from apps.api.offers import rub

FIXTURES = Path(__file__).parent / 'fixtures'
SUPPLIER_INN = '7701000009'
EQUIPMENT = {'description': 'Станок ЧПУ', 'price': 2_000_000, 'down_payment': 400_000, 'supplier_inn': SUPPLIER_INN}


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def investment(offer):
    return {**offer, 'offer_id': 'app-0001-investment', 'kind': 'with_collateral', 'product': 'investment',
            'collateral': ['equipment_pledge', 'msp_guarantee'], 'guarantee_fee': 0.01, 'disbursement': 'supplier'}


def offered(*, offers=None, equipment=None, legal_form=None, user_id=None) -> tuple[str, str]:
    """An `offered` application built from the fixtures, with offers valid from today."""
    db.init_db()
    decision = fixture('decision_offered.json')
    valid_until = (workflow.today() + timedelta(days=10)).isoformat()
    decision['offers'] = [{**o, 'valid_until': valid_until} for o in (offers or decision['offers'])]
    request = fixture('application_request.json')
    request.update(equipment=equipment, **({'legal_form': legal_form} if legal_form else {}))
    with Session.begin() as s:
        if user_id is None:
            user = db.User(name='Клиент')
            s.add(user)
            s.flush()
            user_id = user.id
        app = LoanApplication(id=db.uid(), user_id=user_id, idempotency_key=db.uid(), status=S.OFFERED.value,
                              request=request, decision=decision, offer_valid_until=valid_until,
                              enrichment={'responses': [fixture('sources/egrul.json')]}, attempts=1)
        s.add(app)
        return app.id, user_id


def load(application_id):
    with Session() as s:
        return s.get(LoanApplication, application_id), workflow.events(s, application_id)


def accept(application_id, offer_id=None):
    with Session.begin() as s:
        app = s.get(LoanApplication, application_id)
        dsb.accept(s, app, offer_id or app.decision['offers'][0]['offer_id'], actor='client:x')


def code_for(application_id, at=None):
    with Session.begin() as s:
        return dsb.issue_code(s, s.get(LoanApplication, application_id), actor='client:x', at=at)


def sign(application_id, code, at=None):
    with Session.begin() as s:
        return dsb.sign(s, s.get(LoanApplication, application_id), code, actor='client:x', at=at)


def wrong(code):
    return '0' * 6 if code != '0' * 6 else '1' * 6


# --- Documents ----------------------------------------------------------------------------

def test_accept_generates_the_documents_of_the_offer():
    application_id, _ = offered()
    accept(application_id)
    app, events = load(application_id)
    assert app.status == 'documents' and [e.to_status for e in events[-2:]] == ['accepted', 'documents']
    docs = {d['doc_id']: d for d in app.closing['documents']}
    assert set(docs) == {'loan_agreement', 'guarantee_agreement'}  # working capital with the owner's guarantee
    assert rub(1_000_000) in docs['loan_agreement']['text'] and 'зачисление на расчётный счёт' in docs['loan_agreement']['text']
    founders = fixture('sources/egrul.json')['payload']['founders']
    owner = max(founders, key=lambda f: f['share'])
    assert owner['inn'] in docs['guarantee_agreement']['signer']
    assert [c['code'] for c in app.closing['conditions']] == ['guarantee_signed']
    assert all(d['signed_at'] is None for d in docs.values())


def test_investment_loan_gets_pledge_msp_and_supplier_conditions():
    base = fixture('decision_offered.json')['offers'][0]
    application_id, _ = offered(offers=[investment(base)], equipment=EQUIPMENT)
    accept(application_id)
    app, _ = load(application_id)
    docs = {d['doc_id']: d for d in app.closing['documents']}
    assert set(docs) == {'loan_agreement', 'pledge_agreement', 'msp_guarantee_application'}
    assert SUPPLIER_INN in docs['loan_agreement']['text'] and 'Станок ЧПУ' in docs['pledge_agreement']['text']
    assert [c['code'] for c in app.closing['conditions']] == ['pledge_registered', 'supplier_invoice_checked',
                                                              'msp_guarantee_issued']


def test_an_expired_offer_gets_no_documents():
    application_id, _ = offered()
    with Session.begin() as s:
        app = s.get(LoanApplication, application_id)
        dsb.accept(s, app, app.decision['offers'][0]['offer_id'], actor='client:x',
                   on=workflow.today() + timedelta(days=11))
    app, _ = load(application_id)
    assert app.status == 'expired' and app.closing is None


# --- Signing ------------------------------------------------------------------------------

def test_signing_with_the_code_moves_to_conditions():
    application_id, _ = offered()
    accept(application_id)
    assert 'запросите код' in sign(application_id, '123456')
    code = code_for(application_id)
    assert len(code) == dsb.CODE_DIGITS and code not in json.dumps(load(application_id)[0].closing)
    assert sign(application_id, code) is None
    app, events = load(application_id)
    assert app.status == 'conditions' and [e.to_status for e in events[-2:]] == ['signed', 'conditions']
    assert all(d['signed_at'] for d in app.closing['documents'])
    assert app.closing['signature']['method'] == 'pep_code' and app.closing['code'] is None
    with pytest.raises(workflow.IllegalTransition):
        sign(application_id, code)


def test_wrong_codes_use_up_the_attempts():
    application_id, _ = offered()
    accept(application_id)
    code = code_for(application_id)
    for left in range(dsb.CODE_ATTEMPTS - 1, 0, -1):
        assert f'Осталось попыток: {left}' in sign(application_id, wrong(code))
    assert 'Попытки закончились' in sign(application_id, wrong(code))
    assert 'запросите код' in sign(application_id, code)  # the right code no longer works either
    assert sign(application_id, code_for(application_id)) is None
    assert load(application_id)[0].status == 'conditions'


def test_an_expired_code_and_the_code_limit():
    application_id, _ = offered()
    accept(application_id)
    code = code_for(application_id, at=1000.0)
    assert 'истёк' in sign(application_id, code, at=1000.0 + dsb.CODE_TTL_SECONDS + 1)
    for _ in range(dsb.MAX_CODES - 1):
        code_for(application_id)
    with pytest.raises(dsb.SigningError, match='Лимит'):
        code_for(application_id)


def test_withdrawal_up_to_documents_but_not_after_signing():
    application_id, _ = offered()
    accept(application_id)
    with Session.begin() as s:
        workflow.withdraw(s, s.get(LoanApplication, application_id), actor='client:x')
    assert load(application_id)[0].status == 'withdrawn'

    application_id, _ = offered()
    accept(application_id)
    sign(application_id, code_for(application_id))
    with Session.begin() as s, pytest.raises(workflow.IllegalTransition):
        workflow.withdraw(s, s.get(LoanApplication, application_id), actor='client:x')


# --- Conditions and payout ----------------------------------------------------------------

def signed(**kwargs):
    application_id, _ = offered(**kwargs)
    accept(application_id)
    sign(application_id, code_for(application_id))
    return application_id


def test_conditions_then_payout_to_the_client_account():
    application_id = signed(legal_form='ooo')
    assert dsb.tick() >= 1
    app, events = load(application_id)
    assert app.status == 'disbursed'
    assert [c['status'] for c in app.closing['conditions']] == ['met']
    payout = app.closing['payout']
    assert payout['to'] == 'client_account' and payout['kind'] == 'payment' and payout['amount'] == 1_000_000
    assert payout['account'].startswith('40702810') and len(payout['account']) == 20
    assert events[-1].to_status == 'disbursed' and payout['account'] in events[-1].message
    assert any(e.data and e.data.get('condition') == 'guarantee_signed' for e in events)


def test_investment_loan_is_paid_to_the_supplier():
    base = fixture('decision_offered.json')['offers'][0]
    application_id = signed(offers=[investment(base)], equipment=EQUIPMENT)
    dsb.tick()
    app, _ = load(application_id)
    assert app.status == 'disbursed'
    assert all(c['status'] == 'met' and c['detail'] for c in app.closing['conditions'])
    assert app.closing['payout']['to'] == 'supplier' and app.closing['payout']['supplier_inn'] == SUPPLIER_INN


def test_overdraft_sets_a_limit_instead_of_a_payment():
    base = fixture('decision_offered.json')['offers'][0]
    overdraft = {**base, 'offer_id': 'app-0001-overdraft', 'product': 'overdraft', 'collateral': [], 'term_months': 12}
    application_id = signed(offers=[overdraft])
    dsb.tick()
    payout = load(application_id)[0].closing['payout']
    assert payout['kind'] == 'limit' and 'лимит' in payout['text']


def test_a_failed_condition_keeps_the_application_in_conditions():
    base = fixture('decision_offered.json')['offers'][0]
    too_much = {**EQUIPMENT, 'down_payment': 1_500_000}  # 1 000 000 > 2 000 000 − 1 500 000
    application_id = signed(offers=[investment(base)], equipment=too_much)
    dsb.tick()
    app, events = load(application_id)
    assert app.status == 'conditions' and app.closing['payout'] is None
    failed = [c for c in app.closing['conditions'] if c['status'] == 'failed']
    assert [c['code'] for c in failed] == ['supplier_invoice_checked']
    logged = len(events)
    dsb.tick()
    assert len(load(application_id)[1]) == logged  # not checked again on every tick


def test_migration_adds_the_closing_column():
    db.init_db()
    assert 'closing' in {c['name'] for c in inspect(db.engine).get_columns('loan_applications')}


# --- API ----------------------------------------------------------------------------------

@pytest.fixture
def client():
    from apps.api.main import app
    with TestClient(app) as c:
        c.post('/api/auth/demo', json={'name': 'Клиент'})
        yield c


def test_api_from_accept_to_disbursed(client):
    user_id = client.get('/api/auth/me').json()['id']
    application_id, _ = offered(user_id=user_id)
    url = f'/api/v2/applications/{application_id}'
    view = client.get(url).json()
    r = client.post(url + '/accept', json={'offer_id': view['decision']['offers'][0]['offer_id'],
                                           'revision': view['revision']})
    assert r.status_code == 200 and r.json()['status'] == 'documents', r.text
    view = r.json()
    assert {d['doc_id'] for d in view['closing']['documents']} == {'loan_agreement', 'guarantee_agreement'}
    assert client.get(url + '/documents/loan_agreement').json()['kind'] == 'loan_agreement'
    assert client.get(url + '/documents/nope').status_code == 404

    r = client.post(url + '/sign/code', json={'revision': view['revision']})
    assert r.status_code == 200, r.text
    code, view = r.json()['demo_code'], r.json()['application']
    assert view['closing']['code']['attempts_left'] == dsb.CODE_ATTEMPTS and 'hash' not in view['closing']['code']

    r = client.post(url + '/sign', json={'code': wrong(code), 'revision': view['revision']})
    assert r.status_code == 422 and 'Осталось попыток' in r.json()['detail']
    revision = r.json()['revision']
    assert client.get(url).json()['closing']['code']['attempts_left'] == dsb.CODE_ATTEMPTS - 1  # committed

    r = client.post(url + '/sign', json={'code': code, 'revision': revision})
    assert r.status_code == 200 and r.json()['status'] == 'conditions', r.text
    assert client.post(url + '/withdraw', json={'revision': r.json()['revision']}).status_code == 409

    dsb.tick()
    view = client.get(url).json()
    assert view['status'] == 'disbursed' and view['closing']['payout']['to'] == 'client_account'
    assert set(view['closing']['conditions'][0]) == {'code', 'title', 'status'}  # details stay with staff
    assert view['events'][-1]['status'] == 'disbursed'
