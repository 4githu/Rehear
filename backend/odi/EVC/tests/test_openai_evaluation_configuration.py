from types import SimpleNamespace
import sys
import os
import subprocess

import pytest

from odi.EVC.evaluation import OpenAISegmentEvaluationProvider
from odi.EVC.schema import SegmentEvaluation


def test_reasoning_and_schema_are_explicit_without_fallback(monkeypatch):
    calls = []
    result = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed='synthetic'))])
    def parse(**kwargs):
        calls.append(kwargs)
        return result
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))))
    provider = OpenAISegmentEvaluationProvider(api_key='synthetic', model='gpt-6-luna', reasoning_effort='none')
    assert provider.evaluate({'text': 'synthetic'}) == 'synthetic'
    assert len(calls) == 1
    assert calls[0]['model'] == 'gpt-6-luna'
    assert calls[0]['reasoning_effort'] == 'none'
    assert calls[0]['response_format'] is SegmentEvaluation


def test_reasoning_is_omitted_for_existing_models(monkeypatch):
    calls = []
    result = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed='synthetic'))])
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=lambda **kwargs: (calls.append(kwargs), result)[1])))))
    OpenAISegmentEvaluationProvider(api_key='synthetic', model='gpt-4.1-mini').evaluate({})
    assert 'reasoning_effort' not in calls[0]


def test_missing_model_maps_to_safe_configuration_json(monkeypatch):
    from odi.EVC.router import _http_error
    def parse(**kwargs):
        error = RuntimeError('secret provider details')
        error.status_code = 404
        error.code = 'model_not_found'
        raise error
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))))
    with pytest.raises(Exception) as caught:
        OpenAISegmentEvaluationProvider(api_key='synthetic', model='missing').evaluate({})
    response = _http_error(caught.value)
    assert response.status_code == 503
    assert response.detail['code'] == 'evaluation_provider_configuration_error'
    assert 'secret' not in str(response.detail)


def test_environment_configures_default_provider_without_real_calls():
    script = '''
import sys
from types import SimpleNamespace
calls=[]
result=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed="ok"))])
sys.modules["openai"]=SimpleNamespace(OpenAI=lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=lambda **kw: (calls.append(kw), result)[1]))))
from odi.EVC.evaluation import default_segment_provider
provider=default_segment_provider()
assert provider.evaluate({}) == "ok"
assert calls[0]["model"] == "gpt-6-luna"
assert calls[0]["reasoning_effort"] == "none"
'''
    env = {'PATH': os.environ.get('PATH', ''), 'OPENAI_API_KEY': 'synthetic',
           'OPENAI_EVC_MODEL': 'gpt-6-luna', 'OPENAI_EVC_REASONING_EFFORT': 'none'}
    subprocess.run([sys.executable, '-c', script], env=env, check=True)


def test_report_uses_same_explicit_reasoning_setting(monkeypatch):
    from odi.EVC.report_v2.report_generation import OpenAIReportInsightProvider
    calls = []
    result = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed='synthetic'))])
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=lambda **kwargs: (calls.append(kwargs), result)[1])))))
    provider = OpenAIReportInsightProvider(api_key='synthetic', model='gpt-6-luna', reasoning_effort='none')
    assert provider.generate({}) == 'synthetic'
    assert calls[0]['model'] == 'gpt-6-luna'
    assert calls[0]['reasoning_effort'] == 'none'
