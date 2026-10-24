"""CPU regressions for verifiable audit evidence, no DB or actual model IO."""
import sys,json,copy
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from llm.intent_parser import parse_intent_with_llm

CONFIG={'ollama_host':'http://127.0.0.1:11434','intent_audit_in_production':True,
        'intent_audit_protocol':'evidence_v2','bounded_repair_enabled':False,
        'intent_grounding_contract_enabled':True}
UNKNOWN={'action':'navigate','target':{'text':'fountain','label':None}}


def audit(checks,complete=True,issues=None):
    return {'complete':complete,'ordered':True,'issues':issues or [],'checks':checks}


def check(quote,pointer,value,kind='target',status='preserved'):
    return {'source_quote':quote,'intent_pointer':pointer,'intent_value_json':json.dumps(value),
            'kind':kind,'status':status}


def serve(monkeypatch,*payloads):
    import ollama
    calls=[]; queue=iter(payloads)
    def chat(*a,**k):
        calls.append(k); payload=next(queue)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload),thinking=None))
    monkeypatch.setattr(ollama.Client,'chat',chat)
    return calls


def test_unknown_class_review_has_no_ontology_and_preserves_request(monkeypatch):
    calls=serve(monkeypatch,UNKNOWN,audit([check('fountain','/target/text','fountain')]))
    result=parse_intent_with_llm('Go to a fountain.',model='synthetic',scene_ontology=['bed','chair'],config=CONFIG)
    assert result.target.text=='fountain' and result.target.label is None
    assert set(json.loads(calls[1]['messages'][-1]['content']))=={'instruction','intent','evidence_fields'}
    assert 'checks' in calls[1]['format']['required']


@pytest.mark.parametrize('item',[
 check('unmentioned scanner','/target/text','fountain'),
 check('fountain','/target/text','bed'),
 check('fountain','/target/not_a_field','fountain'),
 check('fountain','/target/text','fountain',kind='relation'),
])
def test_fabricated_or_misplaced_evidence_is_rejected_without_retry(monkeypatch,item):
    calls=serve(monkeypatch,UNKNOWN,audit([item]))
    with pytest.raises(ValueError):parse_intent_with_llm('Go to a fountain.',model='synthetic',config=CONFIG)
    assert len(calls)==2


def test_empty_audit_cannot_approve_without_requirement_checks(monkeypatch):
    serve(monkeypatch,UNKNOWN,audit([]))
    with pytest.raises(ValueError):parse_intent_with_llm('Go to a fountain.',model='synthetic',config=CONFIG)


def test_nearest_anchor_cannot_be_approved_by_origin_quantifier_evidence(monkeypatch):
    payload={'action':'navigate','target':{'text':'chair','label':'chair','quantifier':'nearest'}}
    calls=serve(monkeypatch,payload,audit([check('closest to a bed','/target/quantifier','nearest',kind='relation')]))
    with pytest.raises(ValueError):parse_intent_with_llm('Go to the chair closest to a bed.',model='synthetic',
        scene_ontology=['chair','bed'],config={**CONFIG,'reference_position':[0,0,0]})
    assert len(calls)==2


def test_empty_relation_array_cannot_be_reported_preserved(monkeypatch):
    payload={'action':'navigate','target':{'text':'chair','label':'chair','relations':[]}}
    serve(monkeypatch,payload,audit([check('near a bed','/target/relations',[],kind='relation')]))
    with pytest.raises(ValueError):parse_intent_with_llm('Go to the chair near a bed.',model='synthetic',config=CONFIG)


def test_inconsistent_summary_cannot_hide_missing_location(monkeypatch):
    payload={'action':'navigate','target':{'text':'chair','label':'chair'}}
    serve(monkeypatch,payload,audit([check('office','/target/room',None,kind='location',status='missing')]))
    with pytest.raises(ValueError):parse_intent_with_llm('Go to the chair in the office.',model='synthetic',config=CONFIG)


def test_detected_missing_location_reparsed_once_with_original_evidence(monkeypatch):
    bad={'action':'navigate','target':{'text':'chair','label':'chair'}}
    good={'action':'navigate','target':{'text':'chair','label':'chair','room':'office'}}
    first=audit([check('office','/target/room',None,kind='location',status='missing')],False,['Location omitted'])
    last=audit([check('office','/target/room','office',kind='location')])
    calls=serve(monkeypatch,bad,first,good,last); records=[]
    result=parse_intent_with_llm('Go to the chair in the office.',model='synthetic',config={**CONFIG,'bounded_repair_enabled':True},
        attempt_callback=lambda rows:records.__setitem__(slice(None),rows))
    assert result.target.room=='office' and len(calls)==4
    assert records[0]['completeness_audit']==first and len(records)==2
    assert 'office' in str(calls[2]['messages'])


def test_valid_anchor_evidence_survives(monkeypatch):
    relation={'relation':'nearest_to','target_text':'bed','target_label':'bed'}
    payload={'action':'navigate','target':{'text':'chair','label':'chair','relations':[relation]}}
    calls=serve(monkeypatch,payload,audit([check('bed','/target/relations/0/target_text','bed',kind='relation')]))
    result=parse_intent_with_llm('Go to the chair closest to a bed.',model='synthetic',config=CONFIG)
    assert result.target.relations[0].relation=='nearest_to' and len(calls)==2


def test_request_restricts_evidence_to_actual_field_catalog(monkeypatch):
    calls=serve(monkeypatch,UNKNOWN,audit([check('fountain','/target/text','fountain')]))
    parse_intent_with_llm('Go to a fountain.',model='synthetic',config=CONFIG)
    field=calls[1]['format']['$defs']['RequirementCheck']['properties']
    assert '/target/text' in field['intent_pointer']['enum']
    assert '/intent/action' not in field['intent_pointer']['enum']
    assert json.dumps('fountain') in field['intent_value_json']['enum']
    assert json.dumps(UNKNOWN) not in field['intent_value_json']['enum']
    supplied=json.loads(calls[1]['messages'][-1]['content'])
    assert supplied['evidence_fields']['/target/text']==json.dumps('fountain')


@pytest.mark.parametrize('pointer,quote,kind,value',[
 ('/target/attributes/relations_note','bed','relation','bed'),
 ('/target/attributes/room','office','location','office'),
 ('/target/relations/0/target_text','office','location','office'),
])
def test_arbitrary_attribute_names_and_distance_relations_are_not_location_evidence(monkeypatch,pointer,quote,kind,value):
    payload={'action':'navigate','target':{'text':'chair','label':'chair',
       'attributes':{'relations_note':'bed','room':'office'},
       'relations':[{'relation':'nearest_to','target_text':'office'}]}}
    calls=serve(monkeypatch,payload,audit([check(quote,pointer,value,kind=kind)]))
    with pytest.raises(ValueError):parse_intent_with_llm('Go to the chair inside office near bed.',model='synthetic',config=CONFIG)
    assert len(calls)==2


def test_catalog_never_offers_oversize_or_hidden_pose_description_containers():
    from planner_schemas.intent import TaskIntent
    from llm import intent_audit_evidence as evidence
    intent=TaskIntent.model_validate({'action':'navigate','steps':[{'action':'navigate','target':{'text':'chair','label':'chair'}} for _ in range(4)]}).model_dump(mode='json')
    fields=evidence.field_catalog(intent)
    assert '/steps' not in fields
    assert all(len(value)<=1200 for value in fields.values())
    assert '/steps/0/action' in fields
    assert all('"position"' not in value and '"description"' not in value for value in fields.values())
