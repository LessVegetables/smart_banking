"""Organizer authorization, persistence, concurrency and live source behavior."""
import asyncio
import os
import tempfile
os.environ.setdefault('DATA_DIR',tempfile.mkdtemp(prefix='organizers-tests-'))
os.environ.setdefault('WORKER_ENABLED','false')
import pytest
from fastapi.testclient import TestClient
from apps.api import main,organizers,workflow
from apps.api.db import Session,MockSourceConfig
from apps.api.contracts import SourceId
from apps.sources import MockSourceService,Settings,SourceUnavailable,SourceTimeout,Scenario
from apps.world import generate_world


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('STAFF_PASSWORD','organizers-test-password')
    monkeypatch.setattr(main,'DEMO',True)
    with TestClient(main.app) as client:
        assert client.post('/api/auth/staff',json={'password':'organizers-test-password'}).status_code==200
        yield client
    with Session.begin() as db:
        row=db.get(MockSourceConfig,1)
        row.settings={}
        row.revision+=1


def test_access_requires_staff_and_demo_mode(client,monkeypatch):
    with TestClient(main.app) as other:
        assert other.get('/api/organizers/sources').status_code==401
        assert other.put('/api/organizers/sources',json={}).status_code==401
        other.post('/api/auth/demo',json={'name':'organizers-customer'})
        assert other.get('/api/organizers/sources').status_code==403
        assert other.put('/api/organizers/sources',json={}).status_code==403
    monkeypatch.setattr(main,'DEMO',False)
    assert client.get('/api/organizers/sources').status_code==404


def payload(client):
    data=client.get('/api/organizers/sources').json()
    return {k:data[k] for k in ('revision','sources')}


def test_persistence_conflict_and_reset(client):
    initial=payload(client)
    assert len(initial['sources'])==8
    initial['sources']['bki']={'outcome':'error','latency_ms':0}
    response=client.put('/api/organizers/sources',json=initial)
    assert response.status_code==200,response.text
    assert response.json()['revision']==initial['revision']+1
    assert response.json()['updated_at']
    assert client.put('/api/organizers/sources',json=initial).status_code==409
    with TestClient(main.app) as another:
        another.post('/api/auth/staff',json={'password':'organizers-test-password'})
        assert payload(another)['sources']['bki']==initial['sources']['bki']
    reset=payload(client)
    reset['sources']={s:{'outcome':'default','latency_ms':None} for s in reset['sources']}
    assert client.put('/api/organizers/sources',json=reset).status_code==200
    assert payload(client)['sources']==reset['sources']


@pytest.mark.parametrize('bad',[{'outcome':'other'}, {'latency_ms':-1}, {'latency_ms':30001}, {'latency_ms':1.5}, {'latency_ms':True}, {'url':'https://example.com'}])
def test_invalid_settings_are_rejected(client,bad):
    before=payload(client)
    body=payload(client)
    body['sources']['bki'].update(bad)
    assert client.put('/api/organizers/sources',json=body).status_code==422
    assert payload(client)==before


def test_all_eight_sources_required(client):
    body=payload(client)
    del body['sources']['accounts']
    assert client.put('/api/organizers/sources',json=body).status_code==422
    body=payload(client)
    body['sources']['statement_upload']={'outcome':'ok'}
    assert client.put('/api/organizers/sources',json=body).status_code==422


def test_live_instances_see_updates_and_default_restores_scenarios(client,monkeypatch):
    world=generate_world(n=1)
    inn=world.companies[0].inn
    defaults=Settings(latency_scale=0,hang_seconds=0,scenarios={inn:{SourceId.BKI:Scenario('error')}})
    services=[MockSourceService(world,defaults),MockSourceService(world,defaults)]
    for service in services:service.overrides=organizers.source_overrides
    delays=[]
    async def sleep(delay):delays.append(delay)
    monkeypatch.setattr('apps.sources.service.asyncio.sleep',sleep)
    def set_mode(source,mode,delay=None):
        body=payload(client)
        body['sources'][source]={'outcome':mode,'latency_ms':delay}
        assert client.put('/api/organizers/sources',json=body).status_code==200
    set_mode('bki','ok',125)
    for service in services:
        assert asyncio.run(service.fetch('bki',inn))==world.companies[0].bki
    assert delays==[.125,.125]
    set_mode('bki','default')
    for service in services:
        with pytest.raises(SourceUnavailable):asyncio.run(service.fetch('bki',inn))
    set_mode('accounts','error',0)
    with pytest.raises(SourceUnavailable):asyncio.run(services[0].fetch('accounts',inn))
    set_mode('bki','timeout',30000)
    with pytest.raises(SourceTimeout):asyncio.run(services[1].fetch('bki',inn))
    assert delays[-1]==0  # hang_seconds, not the latency override


def test_default_pipeline_installs_shared_provider(client,monkeypatch):
    world=generate_world(n=1)
    service=MockSourceService(world,Settings(latency_scale=0))
    monkeypatch.setattr(workflow,'service_from_env',lambda:service)
    workflow.default_pipeline.cache_clear()
    try:
        assert workflow.default_pipeline().client.overrides is organizers.source_overrides
    finally:
        workflow.default_pipeline.cache_clear()


def test_random_company_is_from_the_world_and_hides_hidden_parameters(client,monkeypatch):
    world=workflow.default_pipeline().client.world
    by_inn={company.inn:company for company in world.companies}
    data=client.get('/api/organizers/random-company').json()
    company=by_inn[data['inn']]
    assert data['name']==company.egrul.name and data['director']==company.egrul.director.full_name
    assert data['industry_key']==company.industry
    assert not {'q','archetype','outcome','sample'}&set(data)
    persona=world.companies[0]
    monkeypatch.setattr(organizers.random,'choice',lambda companies:persona)
    from apps.api.demo_companies import company_options
    titles={option['inn']:option['title'] for option in company_options(world)}
    assert client.get('/api/organizers/random-company').json()['demo_persona']==titles.get(persona.inn)
    with TestClient(main.app) as other:
        assert other.get('/api/organizers/random-company').status_code==401
        other.post('/api/auth/demo',json={'name':'organizers-customer'})
        assert other.get('/api/organizers/random-company').status_code==403
    monkeypatch.setattr(main,'DEMO',False)
    assert client.get('/api/organizers/random-company').status_code==404
