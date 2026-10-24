from pathlib import Path
import httpx
from apps.api import assistant
from apps.api.assistant import explain

BASE = 'http://llm.test:8080/v1'

def configure(monkeypatch, key='not-a-real-key', base=BASE):
    for name in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('LLM_BASE_URL', base)
    monkeypatch.setenv('LLM_MODEL', 'test-model')
    if key:
        monkeypatch.setenv('LLM_API_KEY', key)

def reply(text, url=BASE + '/chat/completions'):
    return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': text}}]},
                          request=httpx.Request('POST', url))

def test_model_is_off_without_llm_settings(monkeypatch):
    for name in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('no call')))
    assert explain('Привет', 'Справка') is None
    monkeypatch.setenv('LLM_BASE_URL', BASE)
    assert explain('Привет', 'Справка') is None  # a model name is required too

def test_request_goes_to_configured_server_without_tools_and_redacts(monkeypatch):
    configure(monkeypatch, base=BASE + '/')
    def fake_post(url, headers, json, timeout):
        assert url == BASE + '/chat/completions'
        assert headers == {'Authorization': 'Bearer not-a-real-key'}
        assert json['model'] == 'test-model' and 'tools' not in json
        system, user = json['messages']
        assert system['role'] == 'system' and 'Ставка 24%' in system['content']
        assert user['role'] == 'user'
        assert '7700000000' not in user['content'] and 'test@example.com' not in user['content']
        return reply('Ставка в демо — 24%.')
    monkeypatch.setattr(httpx, 'post', fake_post)
    assert explain('ИНН 7700000000, test@example.com', 'Ставка 24%') == 'Ставка в демо — 24%.'

def test_api_key_is_optional(monkeypatch):
    configure(monkeypatch, key=None)
    def fake_post(url, headers, json, timeout):
        assert headers == {}
        return reply('Готово.')
    monkeypatch.setattr(httpx, 'post', fake_post)
    assert explain('Помоги', 'Контекст') == 'Готово.'

def test_invented_numbers_are_replaced_with_template(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: reply('Одобрено 999 миллионов'))
    assert explain('Какие условия?', 'Ставка 24%') is None

def test_provider_timeout_falls_back(monkeypatch):
    configure(monkeypatch)
    def timeout(*a, **kw):
        raise httpx.ReadTimeout('Timeout')
    monkeypatch.setattr(httpx, 'post', timeout)
    assert explain('Помоги', 'Контекст') is None

def test_provider_error_and_malformed_answer_fall_back(monkeypatch):
    configure(monkeypatch)
    request = httpx.Request('POST', BASE + '/chat/completions')
    for response in (httpx.Response(503, request=request),
                     httpx.Response(200, json={'choices': []}, request=request),
                     httpx.Response(200, json={'choices': [{'message': {'content': None}}]}, request=request),
                     httpx.Response(200, text='not json', request=request)):
        monkeypatch.setattr(httpx, 'post', lambda *a, response=response, **kw: response)
        assert explain('Помоги', 'Контекст') is None

def test_no_hard_coded_provider():
    source = Path(assistant.__file__).read_text(encoding='utf-8').lower()
    assert 'openai.com' not in source and 'openai_' not in source
