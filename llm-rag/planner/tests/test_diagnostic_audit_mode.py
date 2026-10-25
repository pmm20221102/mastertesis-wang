"""Diagnostic self-audit must not become a hidden safety gate or repair trigger."""
import sys,json
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from llm.intent_parser import parse_intent_with_llm

CONFIG={'ollama_host':'http://127.0.0.1:11434','intent_audit_in_production':True,
        'intent_audit_mode':'diagnostic','bounded_repair_enabled':True}
INTENT={'action':'navigate','target':{'text':'fountain','label':None}}
BAD={'complete':False,'ordered':True,'issues':['Unknown target should not exist']}


def serve(monkeypatch,*payloads):
    import ollama
    calls=[];responses=iter(payloads)
    def chat(*a,**k):
        calls.append(k);p=next(responses)
        if isinstance(p,Exception):raise p
        return SimpleNamespace(message=SimpleNamespace(content=p if isinstance(p,str) else json.dumps(p),thinking=None))
    monkeypatch.setattr(ollama.Client,'chat',chat)
    return calls


@pytest.mark.parametrize('repair',[True,False])
def test_negative_diagnostic_audit_is_retained_without_block_or_reparse(monkeypatch,repair):
    calls=serve(monkeypatch,INTENT,BAD);records=[]
    result=parse_intent_with_llm('Go to a fountain.',model='synthetic',config={**CONFIG,'bounded_repair_enabled':repair},
        attempt_callback=lambda rows:records.__setitem__(slice(None),rows))
    assert result.target.text=='fountain' and result.target.label is None and len(calls)==2
    assert records[0]['completeness_audit']==BAD
    assert records[0]['audit_disposition']=='diagnostic_rejected'
    assert records[0]['audit_mode']=='diagnostic'


@pytest.mark.parametrize('protocol',['legacy','evidence_v2'])
def test_bad_diagnostic_output_is_retained_without_block(monkeypatch,protocol):
    calls=serve(monkeypatch,INTENT,'not JSON');records=[]
    result=parse_intent_with_llm('Go to a fountain.',model='synthetic',config={**CONFIG,'intent_audit_protocol':protocol},
        attempt_callback=lambda rows:records.__setitem__(slice(None),rows))
    assert result.target.text=='fountain' and len(calls)==2
    assert records[0]['audit_status']=='error' and records[0]['audit_raw_content']=='not JSON'
    assert records[0]['audit_disposition']=='diagnostic_error'
    assert records[0]['error_phase']=='audit_schema'


def test_diagnostic_transport_error_records_cost_without_block_or_retry(monkeypatch):
    calls=serve(monkeypatch,INTENT,RuntimeError('offline synthetic audit error'));records=[]
    result=parse_intent_with_llm('Go to a fountain.',model='synthetic',config=CONFIG,
        attempt_callback=lambda rows:records.__setitem__(slice(None),rows))
    assert result.target.text=='fountain' and len(calls)==2
    assert records[0]['audit_elapsed_ms']>=0 and records[0]['audit_usage']['output_tokens'] is None
    assert records[0]['error_phase']=='audit_transport' and records[0]['audit_disposition']=='diagnostic_error'


def test_diagnostic_mode_does_not_suppress_required_parse_failure(monkeypatch):
    calls=serve(monkeypatch,'{"action":','{"action":')
    with pytest.raises(ValueError):parse_intent_with_llm('Go to a fountain.',model='synthetic',config=CONFIG)
    assert len(calls)==2 and all('action' in c['format']['properties'] for c in calls)


def test_parse_recovery_and_diagnostic_failure_share_existing_counts(monkeypatch):
    calls=serve(monkeypatch,'{"action":',INTENT,'not JSON');records=[]
    result=parse_intent_with_llm('Go to a fountain.',model='synthetic',config=CONFIG,
        attempt_callback=lambda rows:records.__setitem__(slice(None),rows))
    assert result.target.text=='fountain' and len(calls)==3
    assert records[1]['request_counts']=={'parse':2,'audit':1}
    assert records[0]['error_phase']=='parse_schema'


def test_callback_error_is_not_swallowed_as_diagnostic_issue(monkeypatch):
    calls=serve(monkeypatch,INTENT,BAD)
    def callback(rows):
        if rows[-1].get('audit_status')=='completed':raise RuntimeError('callback test')
    with pytest.raises(RuntimeError):parse_intent_with_llm('Go to a fountain.',model='synthetic',config=CONFIG,attempt_callback=callback)
    assert len(calls)==2


def test_gate_mode_still_enforces_rejection(monkeypatch):
    calls=serve(monkeypatch,INTENT,BAD)
    with pytest.raises(ValueError):parse_intent_with_llm('Go to a fountain.',model='synthetic',
        config={**CONFIG,'intent_audit_mode':'gate','bounded_repair_enabled':False})
    assert len(calls)==2


def test_invalid_audit_mode_is_not_silently_accepted(monkeypatch):
    calls=serve(monkeypatch,INTENT,BAD)
    with pytest.raises(ValueError):parse_intent_with_llm('Go to a fountain.',model='synthetic',config={**CONFIG,'intent_audit_mode':'typo'})
    assert not calls
