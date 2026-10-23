import os
import tempfile
import time
import hashlib
import hmac
import json
from urllib.parse import urlencode

os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='alfa-tests-')
os.environ['DATABASE_URL']='sqlite:///'+os.path.join(os.environ['DATA_DIR'],'test.db').replace('\\','/')
os.environ['WORKER_ENABLED']='false'
os.environ['STAFF_PASSWORD']='test-staff-password-only'
os.environ['TELEGRAM_BOT_TOKEN']='test-telegram-token'

import pytest
from fastapi.testclient import TestClient
from apps.api.main import app
from apps.api.worker import run_once

@pytest.fixture
def client():
    with TestClient(app) as c:
        assert c.post('/api/auth/demo',json={'name':'Тест'}).status_code==200
        yield c
        while run_once():pass

def filled(c,persona='healthy'):
    s=c.post('/api/surveys').json()
    r=c.post(f'/api/surveys/{s["id"]}/persona/{persona}')
    assert r.status_code==200,r.text
    return r.json()

def submitted(c,persona='healthy'):
    s=filled(c,persona)
    payload={'survey_id':s['id'],'version':s['version'],'confirmed':True,'idempotency_key':'test-'+s['id']}
    r=c.post('/api/applications',json=payload);assert r.status_code==200,r.text
    return r.json(),payload

def test_full_happy_path_and_replay(client):
    a,payload=submitted(client)
    same=client.post('/api/applications',json=payload).json();assert same['id']==a['id']
    assert run_once()
    a=client.get('/api/applications/'+a['id']).json();assert a['status']=='approved'
    a=client.post(f'/api/applications/{a["id"]}/actions',json={'action':'accept','offer_index':0,'revision':a['revision']}).json();assert a['status']=='signing'
    a=client.post(f'/api/applications/{a["id"]}/actions',json={'action':'sign','revision':a['revision']}).json();assert a['status']=='issued'
    assert client.post(f'/api/applications/{a["id"]}/actions',json={'action':'sign','revision':a['revision']}).status_code==409
    assert len(a['decisions'])==1
    assert a['decision']['input']['name']=='Кофейня «Зёрна»'
    assert a['decision']['policy_snapshot']['version']==a['decision']['rule_version']

def test_application_ownership(client):
    a,_=submitted(client)
    with TestClient(app) as other:
        other.post('/api/auth/demo',json={'name':'Другой'})
        assert other.get('/api/applications/'+a['id']).status_code==404
        assert other.get('/api/applications').json()==[]

def test_optimistic_survey_and_hidden_answers(client):
    s=filled(client,'equipment')
    r=client.patch(f'/api/surveys/{s["id"]}/answers',json={'version':s['version'],'question_id':'purpose','value':'working'})
    assert r.status_code==200;r=r.json()
    assert 'equipment_cost' not in r['answers'];assert 'collateral' not in r['answers']
    assert client.patch(f'/api/surveys/{s["id"]}/answers',json={'version':s['version'],'question_id':'purpose','value':'line'}).status_code==409
    assert client.patch(f'/api/surveys/{s["id"]}/answers',json={'version':r['version'],'question_id':'collateral','value':'yes'}).status_code==422

def test_incomplete_and_unconfirmed_submission(client):
    s=client.post('/api/surveys').json()
    assert client.post(f'/api/surveys/{s["id"]}/recommendations',json={'version':s['version']}).status_code==422
    assert client.post('/api/applications',json={'survey_id':s['id'],'version':s['version'],'confirmed':False,'idempotency_key':'confirm-test-000'}).status_code==422

def test_staff_confirmation_and_rescore(client):
    a,_=submitted(client,'documents');run_once()
    a=client.get('/api/applications/'+a['id']).json();assert a['status']=='needs_data'
    before=a['decision']['score']
    r=client.post(f'/api/applications/{a["id"]}/documents',files={'file':('report.pdf',b'%PDF-1.4\n synthetic report','application/pdf')})
    assert r.status_code==200,r.text;a=r.json()
    assert a['decision']['score']==before
    doc=a['documents'][0]
    assert client.get('/api/documents/'+doc['id']).status_code==200
    with TestClient(app) as employee:
        assert employee.post('/api/auth/staff',json={'password':'test-staff-password-only'}).status_code==200
        payload={'action':'documents','revision':a['revision'],'verified_revenue':36000000,'verified_trend':'stable','comment':'Выручка подтверждена выпиской за 12 месяцев.'}
        r=employee.post('/api/staff/reviews/'+a['id'],json=payload)
        assert r.status_code==200,r.text;a=r.json()
        assert a['status']=='approved';assert len(a['decisions'])==2
        assert a['decisions'][0]['input']['revenue']==12000000
        assert a['decisions'][1]['input']['revenue']==36000000
        assert a['documents'][0]['status']=='verified'
    with TestClient(app) as other:
        other.post('/api/auth/demo',json={'name':'Посторонний'})
        assert other.get('/api/documents/'+doc['id']).status_code==404

def test_staff_role_and_comment_required(client):
    assert client.get('/api/staff/reviews').status_code==403
    assert client.post('/api/auth/staff',json={'password':'wrong'}).status_code==403

def test_invalid_file_not_accepted(client):
    a,_=submitted(client,'manual');run_once()
    assert client.post(f'/api/applications/{a["id"]}/documents',files={'file':('report.pdf',b'<script>alert(1)</script>','application/pdf')}).status_code==422

def test_origin_guard(client):
    assert client.post('/api/surveys',headers={'Origin':'https://evil.invalid'}).status_code==403

def signed_telegram(fields):
    check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    secret=hmac.new(b'WebAppData',b'test-telegram-token',hashlib.sha256).digest()
    return urlencode({**fields,'hash':hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()})

def test_telegram_signature_and_freshness(client):
    fields={'auth_date':str(int(time.time())),'user':json.dumps({'id':123456,'first_name':'Демо'})}
    assert client.post('/api/auth/telegram',json={'init_data':signed_telegram(fields)}).status_code==200
    fields['auth_date']=str(int(time.time())-600)
    assert client.post('/api/auth/telegram',json={'init_data':signed_telegram(fields)}).status_code==401
    assert client.post('/api/auth/telegram',json={'init_data':'hash=invalid'}).status_code==401

def test_assistant_fallback_is_usable_without_model(client):
    r=client.post('/api/assistant/messages',json={'message':'Какие ставки?'}).json()
    assert r['mode']=='template';assert '24%' in r['message']

def test_idempotency_key_stays_bound_after_survey_changes(client):
    a,payload=submitted(client)
    s=client.get('/api/surveys/current').json()
    client.patch(f'/api/surveys/{s["id"]}/answers',json={'version':s['version'],'question_id':'amount','value':2000000})
    replay=client.post('/api/applications',json=payload)
    assert replay.status_code==200;assert replay.json()['id']==a['id']

def test_job_uses_policy_saved_at_submission(client,monkeypatch):
    a,_=submitted(client)
    from apps.api.catalog import RULES
    monkeypatch.setitem(RULES,'version','new-live-version')
    run_once()
    decision=client.get('/api/applications/'+a['id']).json()['decision']
    assert decision['rule_version']=='demo-2026-10-v1'

def test_stale_offer_action_cannot_be_replayed(client):
    a,_=submitted(client);run_once();a=client.get('/api/applications/'+a['id']).json()
    payload={'action':'accept','offer_index':0,'revision':a['revision']}
    assert client.post(f'/api/applications/{a["id"]}/actions',json=payload).status_code==200
    assert client.post(f'/api/applications/{a["id"]}/actions',json=payload).status_code==409

def test_empty_document_confirmation_and_staff_override_blocked(client):
    a,_=submitted(client,'manual');run_once();a=client.get('/api/applications/'+a['id']).json()
    client.post('/api/auth/staff',json={'password':'test-staff-password-only'})
    for action in ('documents','approve'):
        r=client.post('/api/staff/reviews/'+a['id'],json={'action':action,'revision':a['revision'],'comment':'Проверка документов и условий.'})
        assert r.status_code==422

def test_expired_offer_is_not_accepted(client):
    a,_=submitted(client);run_once();a=client.get('/api/applications/'+a['id']).json()
    from apps.api.db import Session,Application
    with Session.begin() as db:db.get(Application,a['id']).offer_expires=time.time()-1
    assert client.post(f'/api/applications/{a["id"]}/actions',json={'action':'accept','revision':a['revision']}).status_code==409

def test_registration_date_is_validated_and_age_is_derived(client):
    s=filled(client)
    r=client.patch(f'/api/surveys/{s["id"]}/answers',json={'version':s['version'],'question_id':'registered_at','value':'2099-01-01'})
    assert r.status_code==422
    a,_=submitted(client);run_once()
    a=client.get('/api/applications/'+a['id']).json()
    assert a['answers']['age']==36
    assert a['answers']['ogrn']=='1000000000000'

def test_form_change_invalidates_both_requisites(client):
    s=filled(client)
    r=client.patch(f'/api/surveys/{s["id"]}/answers',json={'version':s['version'],'question_id':'form','value':'ip'}).json()
    assert 'inn' not in r['answers'];assert 'ogrn' not in r['answers']
