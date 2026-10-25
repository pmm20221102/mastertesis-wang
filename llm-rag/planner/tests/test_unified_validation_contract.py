"""Common validators, explicit bounded recovery and closed-set interface tests."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main_rag as planner
from llm.intent_parser import parse_intent_with_llm

OK={'complete':True,'ordered':True,'issues':[]}
BAD={'complete':False,'ordered':True,'issues':['Missing an explicitly requested location constraint']}
CONFIG={'ollama_host':'http://127.0.0.1:11434','intent_audit_in_production':True,
        'bounded_repair_enabled':False}


def serve(monkeypatch,*responses):
    import ollama
    calls=[]; queue=iter(responses)
    def chat(*a,**k):
        calls.append(k); payload=next(queue)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload) if not isinstance(payload,str) else payload,
           thinking=None),prompt_eval_count=10,eval_count=5,total_duration=1000000,load_duration=0)
    monkeypatch.setattr(ollama.Client,'chat',chat)
    return calls


def test_no_repair_keeps_model_audit_and_stops_on_coverage_rejection(monkeypatch):
    calls=serve(monkeypatch,{'action':'wait'},BAD,{'action':'wait'},OK)
    with pytest.raises(ValueError):parse_intent_with_llm('Wait.',model='synthetic',config=CONFIG)
    assert len(calls)==2 and 'complete' in calls[1]['format']['properties']


@pytest.mark.parametrize('first',['{"action":',{'action':'wait'}])
def test_no_repair_schema_and_sequence_fail_without_reparse(monkeypatch,first):
    command='Wait, then wait.' if isinstance(first,dict) else 'Wait.'
    calls=serve(monkeypatch,first,{'action':'wait','steps':[{'action':'wait'},{'action':'wait'}]},OK)
    with pytest.raises(ValueError):parse_intent_with_llm(command,model='synthetic',config=CONFIG)
    assert len(calls)==1


def test_no_repair_invalid_audit_fails_after_one_parse_one_audit(monkeypatch):
    calls=serve(monkeypatch,{'action':'wait'},'not JSON',{'action':'wait'},OK)
    with pytest.raises(ValueError):parse_intent_with_llm('Wait.',model='synthetic',config=CONFIG)
    assert len(calls)==2


def test_enabled_recovery_uses_shared_budget_and_preserves_audit_usage(monkeypatch):
    calls=serve(monkeypatch,{'action':'wait'},BAD,{'action':'wait'},OK)
    records=[]
    result=parse_intent_with_llm('Wait.',model='synthetic',config={**CONFIG,'bounded_repair_enabled':True},
        attempt_callback=lambda rows:records.__setitem__(slice(None),rows))
    assert result.action=='wait' and len(calls)==4
    assert records[0]['completeness_audit']==BAD
    assert records[1]['request_counts']=={'parse':2,'audit':2}
    for record in records:
        assert record['parse_usage']['prompt_tokens']==10
        assert record['audit_usage']['output_tokens']==5
        assert record['parse_elapsed_ms']>=0 and record['audit_elapsed_ms']>=0
    prompt=calls[1]['messages'][0]['content']
    assert 'location' in prompt.lower() and 'anchor' in prompt.lower() and 'contains' in prompt.lower()


@pytest.mark.parametrize('bad',[
 {'action':'navigate','target':{'text':'chair closest to bed','label':None,
  'relations':[{'relation':'nearest_to','target_text':'bed','target_label':'bed'}]}},
 {'action':'navigate','target':{'text':'rooms containing beds','label':'rooms','node_type':'object','quantifier':'all'}},
])
def test_known_description_interface_errors_are_reparsed_not_filled(monkeypatch,bad):
    good={'action':'navigate','target':{'text':'chair','label':'chair'}}
    calls=serve(monkeypatch,bad,good)
    records=[]
    result=parse_intent_with_llm('Go there.',model='synthetic',scene_ontology=['chair','bed','unknown'],
        config={'intent_audit_in_production':False,'bounded_repair_enabled':True,'intent_grounding_contract_enabled':True},
        attempt_callback=lambda rows:records.__setitem__(slice(None),rows))
    assert len(calls)==2 and result.target.label=='chair'
    assert records[0]['raw_lmm_output']==bad
    assert records[0]['error_phase']=='intent_grounding_contract'


def test_unknown_classes_still_reach_audit_without_forced_known_substitution(monkeypatch):
    calls=serve(monkeypatch,{'action':'navigate','target':{'text':'fountain','label':None}},OK)
    result=parse_intent_with_llm('Go to a fountain.',model='synthetic',scene_ontology=['bed','chair'],config=CONFIG)
    assert len(calls)==2 and result.target.label is None and result.target.text=='fountain'


def test_generic_room_is_valid_with_type_even_if_room_absent_from_label_ontology(monkeypatch):
    calls=serve(monkeypatch,{'action':'navigate','target':{'text':'rooms containing beds','node_type':'room',
        'label':'room','relations':[{'relation':'contains','target_text':'bed','target_label':'bed'}]}},OK)
    result=parse_intent_with_llm('Go to rooms containing beds.',model='synthetic',scene_ontology=['bed','unknown'],config=CONFIG)
    assert len(calls)==2 and result.target.label=='room' and result.target.node_type=='room'


@pytest.mark.parametrize('bad',['not JSON',json.dumps({'status':'valid','task_chain':[{
 'step':1,'action':'navigate_to','target_type':'object','target_ref':'target','description':'test','executor':'none'}]})])
def test_no_repair_pipeline_still_validates_but_never_calls_repair(monkeypatch,bad):
    from test_planner_correctness import grounding
    from planner_schemas.intent import TaskIntent,EntityReference
    monkeypatch.setattr(planner,'load_config',lambda *a:{'bounded_repair_enabled':False})
    monkeypatch.setattr(planner,'parse_intent_with_llm',lambda *a,**k:TaskIntent(action='navigate',target=EntityReference(text='object')))
    monkeypatch.setattr(planner,'SceneGraphRAG',lambda *a:SimpleNamespace(get_navigation_target=lambda n:None))
    monkeypatch.setattr(planner,'GroundingOrchestrator',lambda *a:SimpleNamespace(ground_intent=lambda intent:grounding()))
    calls=serve(monkeypatch,bad,bad)
    result=planner.run_structured_task_planner('synthetic','fake','not-used')
    assert result.status=='system_error' and len(calls)==1
    assert len(result.planning_attempts)==1 and result.validation_errors


@pytest.mark.parametrize('text',['bed','table lamp','room thermometer'])
def test_contract_preserves_exact_short_text_and_unknown_compounds(monkeypatch,text):
    calls=serve(monkeypatch,{'action':'navigate','target':{'text':text,'label':None}},OK)
    result=parse_intent_with_llm('Go to '+text+'.',model='synthetic',scene_ontology=['bed','table'],
        config={**CONFIG,'intent_grounding_contract_enabled':True})
    assert len(calls)==2 and result.target.text==text


@pytest.mark.parametrize('text,type_',[('rooms containing beds','object'),('bed closest to table','object')])
def test_generic_or_descriptive_role_cannot_bypass_contract_with_any_type(monkeypatch,text,type_):
    calls=serve(monkeypatch,{'action':'navigate','target':{'text':text,'label':None,'node_type':type_}})
    with pytest.raises(ValueError):parse_intent_with_llm('Go there.',model='synthetic',scene_ontology=['bed','table'],
        config={**CONFIG,'intent_audit_in_production':False,'intent_grounding_contract_enabled':True})
    assert len(calls)==1


@pytest.mark.parametrize('phase',['parse','audit'])
def test_failed_transport_has_elapsed_cost_without_invented_token_counts(monkeypatch,phase):
    import ollama
    calls=[]; records=[]
    def chat(*a,**k):
        calls.append(k)
        if phase=='parse' or len(calls)==2:raise RuntimeError('offline test failure')
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({'action':'wait'}),thinking=None))
    monkeypatch.setattr(ollama.Client,'chat',chat)
    with pytest.raises(RuntimeError):parse_intent_with_llm('Wait.',model='synthetic',config=CONFIG,
        attempt_callback=lambda rows:records.__setitem__(slice(None),rows))
    assert len(calls)==(1 if phase=='parse' else 2)
    assert records[0][phase+'_elapsed_ms']>=0
    assert records[0][phase+'_usage']['prompt_tokens'] is None


def test_generic_room_label_without_node_type_is_normalized_and_audited(monkeypatch):
    response = {
        'action': 'navigate',
        'target': {
            'text': 'room with the health monitor',
            'label': 'room',
            'relations': [{
                'relation': 'contains',
                'target_text': 'health monitor',
                'target_label': 'healthmonitor',
                'target_node_type': 'object',
            }],
        },
    }
    calls = serve(monkeypatch, response)
    records = []
    result = parse_intent_with_llm(
        'I need to get to the room with the health monitor.',
        model='synthetic',
        scene_ontology=['healthmonitor'],
        config={'intent_grounding_contract_enabled': True,
                'intent_audit_in_production': False,
                'bounded_repair_enabled': False},
        attempt_callback=lambda rows: records.__setitem__(slice(None), rows),
    )
    assert len(calls) == 1
    assert result.target.node_type == 'room'
    assert result.target.label == 'room'
    assert records[0]['raw_lmm_output']['target'].get('node_type') is None
    assert records[0]['intent_normalizations'] == [{
        'action': 'fill_node_type',
        'text': 'room with the health monitor',
        'label': 'room',
        'node_type': 'room',
    }]


def test_descriptive_room_without_predicate_is_not_normalized_to_any_room(monkeypatch):
    response = {
        'action': 'navigate',
        'target': {
            'text': 'room with a first-aid kit',
            'label': 'room',
        },
    }
    calls = serve(monkeypatch, response)
    records = []
    result = parse_intent_with_llm(
        'Please take me to a room with a first-aid kit.',
        model='synthetic',
        scene_ontology=['healthmonitor'],
        config={'intent_grounding_contract_enabled': False,
                'intent_audit_in_production': False,
                'bounded_repair_enabled': False},
        attempt_callback=lambda rows: records.__setitem__(slice(None), rows),
    )
    assert len(calls) == 1
    assert result.target.text == 'room with a first-aid kit'
    assert result.target.label is None
    assert result.target.node_type is None
    assert records[0]['intent_normalizations'] == [{
        'action': 'discard_unstructured_generic_label',
        'text': 'room with a first-aid kit',
        'label': 'room',
        'node_type': None,
    }]


def test_bare_generic_room_without_predicate_keeps_room_type(monkeypatch):
    calls = serve(monkeypatch, {'action': 'navigate', 'target': {
        'text': 'a room', 'label': 'room'}})
    result = parse_intent_with_llm(
        'Take me to a room.', model='synthetic', scene_ontology=['healthmonitor'],
        config={'intent_grounding_contract_enabled': False,
                'intent_audit_in_production': False,
                'bounded_repair_enabled': False},
    )
    assert len(calls) == 1
    assert result.target.node_type == 'room'
    assert result.target.label == 'room'


def test_top_level_primary_target_is_lifted_before_remaining_navigation_steps(monkeypatch):
    response = {
        'action': 'navigate',
        'target': {
            'text': 'room with an elevator and a sofa',
            'label': 'room',
            'relations': [
                {'relation': 'contains', 'target_text': 'elevator',
                 'target_label': 'elevator', 'target_node_type': 'object'},
                {'relation': 'contains', 'target_text': 'sofa',
                 'target_label': 'sofa', 'target_node_type': 'object'},
            ],
        },
        'steps': [{
            'action': 'navigate',
            'target': {
                'text': 'room with a medical screen',
                'label': 'room',
                'relations': [{'relation': 'contains', 'target_text': 'medical screen',
                               'target_label': 'medicalscreen', 'target_node_type': 'object'}],
            },
        }],
    }
    calls = serve(monkeypatch, response)
    records = []
    result = parse_intent_with_llm(
        'Please take me to the room with an elevator and a sofa, then to the room with the medical screen.',
        model='synthetic',
        scene_ontology=['elevator', 'sofa', 'medicalscreen'],
        config={'intent_grounding_contract_enabled': True,
                'intent_audit_in_production': False,
                'bounded_repair_enabled': False},
        attempt_callback=lambda rows: records.__setitem__(slice(None), rows),
    )
    assert len(calls) == 1
    assert result.target is None
    assert [step.target.text for step in result.steps] == [
        'room with an elevator and a sofa',
        'room with a medical screen',
    ]
    assert result.steps[0].target.node_type == 'room'
    assert result.steps[1].target.node_type == 'room'
    assert records[0]['raw_lmm_output']['target']['text'] == 'room with an elevator and a sofa'
    assert records[0]['intent_normalizations'][0]['action'] == 'lift_primary_target_into_step_zero'
