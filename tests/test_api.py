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
from apps.api.db import Session, User
from apps.world.generator import generate_world
from apps.api.demo_companies import company_options

@pytest.fixture
def client():
    with TestClient(app) as c:
        assert c.post('/api/auth/demo',json={'name':'Тест'}).status_code==200
        yield c


def test_demo_company_catalog_is_available_and_restricts_unknown_inn(client):
    options=client.get('/api/v2/demo/companies').json()
    assert options
    assert all({'inn','company_name','title','persona_id'} <= item.keys() for item in options)
    assert client.get('/api/v2/companies/0000000000').status_code==403
    r=client.post('/api/v2/applications',json={'idempotency_key':'unknown-company','request':{'inn':'0000000000'}})
    assert r.status_code==422 and 'демо-списка' in r.text

def test_demo_company_catalog_falls_back_to_current_world(tmp_path):
    world=generate_world(n=1)
    personas=tmp_path/'personas.json'
    options=company_options(world,personas)
    assert len(options)==1
    assert options[0]['inn']==world.companies[0].inn
    assert 'archetype' not in options[0] and 'q' not in options[0]
    personas.write_text('{broken',encoding='utf-8')
    assert company_options(world,personas)[0]['inn']==world.companies[0].inn


def test_staff_role_and_comment_required(client):
    assert client.get('/api/v2/staff/overrides').status_code==403
    assert client.post('/api/auth/staff',json={'password':'wrong'}).status_code==403

def test_model_validation_is_available_only_to_risk_managers(client):
    with TestClient(app) as underwriter:
        assert underwriter.post('/api/auth/staff',json={'password':'test-staff-password-only','position':'underwriter'}).status_code==200
        assert underwriter.get('/api/v2/staff/model-validation').status_code==403
    with TestClient(app) as risk_manager:
        assert risk_manager.post('/api/auth/staff',json={'password':'test-staff-password-only','position':'risk_manager'}).status_code==200
        response=risk_manager.get('/api/v2/staff/model-validation')
        assert response.status_code==200,response.text
        report=response.json()
        assert report['scorecard_version']=='scorecard_v1'
        assert report['gini']['true_pd_ceiling']>report['gini']['holdout']
        assert report['limitations']
        assert any(grade['predicted_vs_actual']=='below' for grade in report['calibration']['grades'])


def test_origin_guard(client):
    assert client.post('/api/auth/demo',json={},headers={'Origin':'https://evil.invalid'}).status_code==403

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
    assert r['mode']=='template';assert 'индивидуально' in r['message'];assert '24%' not in r['message']


def test_v2_company_lookup_returns_registry_card_and_consent_subjects(client,monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from apps.api import main as main_module
    from apps.api.contracts import EgrulPayload,SourceId
    from apps.sources import SourceNotFound

    user_id=client.get('/api/auth/me').json()['id']
    with Session.begin() as db:
        db.get(User,user_id).identity='telegram:test-company-lookup'

    fixtures=Path('tests/fixtures/sources')
    records={}
    for file in ('egrul.json','egrip_ip.json'):
        payload=json.loads((fixtures/file).read_text(encoding='utf-8'))['payload']
        record=EgrulPayload.model_validate(payload)
        records[record.inn]=record

    class Client:
        async def fetch(self,source,inn):
            assert source==SourceId.EGRUL
            if inn=='7703999999':raise SourceNotFound(source,inn,'not found')
            return records[inn]

    monkeypatch.setattr(main_module.workflow,'default_pipeline',lambda:SimpleNamespace(client=Client()))
    company=client.get('/api/v2/companies/5406000001')
    assert company.status_code==200,company.text
    data=company.json()
    assert data['name']=='ООО «Зёрна»'
    assert data['legal_form']=='ooo'
    assert data['owner_name']=='Иванов Алексей Петрович'
    assert data['owner_inn']=='540600000001'
    assert 'archetype' not in data and 'q' not in data and 'account_profile' not in data

    entrepreneur=client.get('/api/v2/companies/540700000002')
    assert entrepreneur.status_code==200,entrepreneur.text
    assert entrepreneur.json()['legal_form']=='ip'
    assert entrepreneur.json()['owner_inn']==entrepreneur.json()['inn']

    assert client.get('/api/v2/companies/7703999999').status_code==404
    assert client.get('/api/v2/companies/not-an-inn').status_code==422

def test_v2_company_lookup_requires_login(client):
    from fastapi.testclient import TestClient
    from apps.api.main import app
    with TestClient(app) as anonymous:
        assert anonymous.get('/api/v2/companies/5406000001').status_code==401

def test_default_pipeline_uses_world_meta_as_of(monkeypatch):
    from datetime import date
    from types import SimpleNamespace
    from apps.api import workflow

    expected=date(2026,10,1)
    service=SimpleNamespace(world=SimpleNamespace(meta=SimpleNamespace(as_of=expected)))
    monkeypatch.setattr(workflow,'service_from_env',lambda:service)
    workflow.default_pipeline.cache_clear()
    try:
        assert workflow.default_pipeline().as_of==expected
    finally:
        workflow.default_pipeline.cache_clear()


def stored_v2_application(client, decision_file='decision_offered.json'):
    from pathlib import Path
    from apps.api.db import LoanApplication, uid
    user_id=client.get('/api/auth/me').json()['id']
    application_id=uid()
    decision=json.loads((Path('tests/fixtures')/decision_file).read_text(encoding='utf-8')) if decision_file else None
    request=json.loads(Path('tests/fixtures/application_request.json').read_text(encoding='utf-8'))
    with Session.begin() as db:
        db.add(LoanApplication(id=application_id,user_id=user_id,idempotency_key=uid(),
                               status=decision['status'] if decision else 'submitted',request=request,decision=decision))
    return application_id,decision


def test_demo_profile_keeps_v2_application_history(client):
    client.post('/api/auth/demo',json={'name':'Stable v2 profile'})
    application_id,_=stored_v2_application(client)
    user=client.get('/api/auth/me').json()
    client.post('/api/auth/logout')
    assert client.post('/api/auth/demo',json={'name':'  Stable v2 profile  '}).json()['id']==user['id']
    assert application_id in [a['id'] for a in client.get('/api/v2/applications').json()]


@pytest.mark.parametrize('decision_file',['decision_offered.json','decision_declined.json','decision_in_review.json',None])
def test_assistant_explains_v2_without_staff_details(client,monkeypatch,decision_file):
    from apps.api import main
    captured=[]
    monkeypatch.setattr(main,'explain',lambda message,reference:captured.append(reference))
    application_id,decision=stored_v2_application(client,decision_file)
    response=client.post('/api/assistant/messages',json={'message':'Какая ставка?', 'application_id':application_id})
    assert response.status_code==200,response.text
    text=response.json()['message']
    assert captured==[text]
    assert 'Статус заявки:' in text
    if decision:
        for reason in decision['reasons']+decision['stop_factors']:
            assert reason['client_text'] in text
            if reason['staff_text'] != reason['client_text']:
                assert reason['staff_text'] not in text
        for offer in decision['offers']:
            assert str(offer['amount']) in text
            assert f"{offer['rate']*100:.2f}%" in text
        assert 'challenger' not in text and 'expected_profit' not in text
    with TestClient(app) as other:
        other.post('/api/auth/demo',json={'name':'Other assistant user'})
        assert other.post('/api/assistant/messages',json={'message':'Условия?', 'application_id':application_id}).status_code==404


@pytest.mark.parametrize('path',['/api/catalog','/api/personas','/api/surveys/current','/api/applications','/api/staff/reviews'])
def test_retired_routes_are_not_exposed(client,path):
    assert client.get(path).status_code==404
