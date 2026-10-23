import httpx
import pytest
from apps.api.assistant import explain

def configure(monkeypatch):
    monkeypatch.setenv('ASSISTANT_LLM_ENABLED','true');monkeypatch.setenv('OPENAI_API_KEY','not-a-real-key');monkeypatch.setenv('OPENAI_MODEL','test-model')

def test_opt_in_is_required(monkeypatch):
    monkeypatch.delenv('ASSISTANT_LLM_ENABLED',raising=False)
    assert explain('Привет','Справка') is None

def test_model_contract_redacts_identifiers_and_does_not_store(monkeypatch):
    configure(monkeypatch)
    def fake_post(url,headers,json,timeout):
        assert url=='https://api.openai.com/v1/responses'
        assert json['store'] is False;assert 'tools' not in json
        assert '7700000000' not in json['input'];assert 'test@example.com' not in json['input']
        return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'Ставка в демо — 24%.'}]}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',fake_post)
    assert explain('ИНН 7700000000, test@example.com','Ставка 24%')=='Ставка в демо — 24%.'

def test_invented_numbers_are_replaced_with_template(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'Одобрено 999 миллионов'}]}]},request=httpx.Request('POST','https://api.openai.com/v1/responses')))
    assert explain('Какие условия?','Ставка 24%') is None

def test_provider_timeout_falls_back(monkeypatch):
    configure(monkeypatch)
    def timeout(*a,**kw):raise httpx.ReadTimeout('Timeout')
    monkeypatch.setattr(httpx,'post',timeout)
    assert explain('Помоги','Контекст') is None
