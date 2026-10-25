"""Independent CPU geometry/ontology regressions, no benchmark or model IO."""
import copy
import json
from types import SimpleNamespace

import pytest
from test_grounding_semantic_contracts import make_api, FIRST, SECOND, ROOM, OTHER_ROOM
from api_schemas import SceneEntity
from planner_schemas.intent import EntityReference, RelationConstraint, TaskIntent
from grounding.orchestrator import GroundingOrchestrator
from llm.intent_parser import parse_intent_with_llm

CLOSE, FAR, EXTRA_BED = '100000000000101', '100000000000102', '100000000000103'


def scene():
    api = make_api()
    api._index.nodes[FIRST].position = [0, 0, 0]
    for identifier, label, position, room in [
        (CLOSE, 'chair', [.2, 0, 0], ROOM), (FAR, 'chair', [20, 0, 0], OTHER_ROOM),
        (EXTRA_BED, 'bed', [10, 0, 0], OTHER_ROOM)]:
        n = SceneEntity(node_id=identifier, node_type='object', label=label,
                        position=position, room_id=room)
        api._index.nodes[identifier] = n
        api._index.labels[label].append(identifier)
        api._index.types['object'].append(identifier)
        api._index.parents[identifier] = [room]
        api._index.children[room].append(identifier)
        api._resolver._index_cache[identifier] = {'label': label, 'node_type': 'object'}
    return api


def near_ref(mode='one', identifier=CLOSE, **kwargs):
    return EntityReference(text=identifier if mode == 'exact' else 'chairs', label='chairs',
        node_type='object', quantifier='one' if mode == 'exact' else mode,
        position=[0, 0, 0], relations=[RelationConstraint(relation='near', target_label='beds',
        target_node_type='object')], **kwargs)


@pytest.mark.parametrize('raw,canonical', [('beds','bed'),('HEALTH_MONITORS','healthmonitor'),
    ('patient monitor','healthmonitor'),('seat','chair'),('rooms','room')])
def test_parser_normalizes_unique_forms_without_losing_raw_evidence(monkeypatch, raw, canonical):
    import ollama
    payload = {'action': 'navigate', 'target': {'text': 'any '+raw, 'label': raw,
               'node_type': 'room' if canonical=='room' else 'object'}}
    monkeypatch.setattr(ollama.Client, 'chat', lambda *a,**k: SimpleNamespace(message=
        SimpleNamespace(content=json.dumps(payload), thinking=None)))
    records=[]
    result=parse_intent_with_llm('Go there.', model='synthetic',
        scene_ontology=['bed','healthmonitor','chair','ward'], config={'intent_audit_in_production':False},
        attempt_callback=lambda rows: records.__setitem__(slice(None), rows))
    assert result.target.label==canonical
    assert records[0]['raw_lmm_output']['target']['label']==raw


@pytest.mark.parametrize('term,labels,expected', [
    ('patient monitor',('bed','healthmonitor'),SECOND),
    ('bedside table',('nightstand','healthmonitor'),FIRST)])
def test_safe_synonyms_use_current_ontology(term,labels,expected):
    result=make_api(labels).resolve_entity(EntityReference(text=term,label=term,node_type='object'))
    assert result.selected and result.selected.node_id==expected


def test_seat_does_not_pick_chair_when_sofa_also_exists():
    api=make_api(('chair','sofa'))
    assert api.resolve_entity(EntityReference(text='seat',label='seat')).status!='resolved'


def test_desk_is_not_blanket_table_alias():
    assert make_api(('table','desk')).resolve_entity(EntityReference(text='desk',label='desk')).selected.node_id==SECOND
    assert make_api(('table','bed')).resolve_entity(EntityReference(text='desk',label='desk')).status=='not_found'


@pytest.mark.parametrize('mode',['exact','one','any','all','nearest'])
def test_near_radius_uses_complete_pool_without_object_edges(mode):
    api=scene(); ref=near_ref(mode); original=copy.deepcopy(ref.model_dump())
    result=api.resolve_entity(ref)
    assert result.selected and result.selected.node_id==CLOSE
    assert 'near:radius3m:raw3d:min_anchor' in result.selected.matched_constraints
    assert ref.model_dump()==original


def test_explicit_far_id_cannot_make_itself_nearest_by_shrinking_pool():
    api=scene(); api._index.neighbors[FIRST].append(FAR)
    ref=near_ref('exact',FAR); ref.relations[0].target_node_id=FIRST
    assert api.resolve_entity(ref).status=='not_found'


def test_location_filters_before_near_radius():
    api=scene(); api._index.nodes[FAR].position=[10.5,0,0]
    result=api.resolve_entity(near_ref(room='office'))
    assert result.selected and result.selected.node_id==FAR


def test_room_candidate_outside_near_radius_is_not_found():
    assert scene().resolve_entity(near_ref(room='office')).status=='not_found'


def test_all_nearest_ties_are_retained():
    api=scene(); api._index.nodes[FAR].position=[10.2,0,0]
    result=api.resolve_entity(near_ref('all'))
    assert {c.node_id for c in result.selected_many}=={CLOSE,FAR}


def test_contains_category_anchor_is_existential_and_keeps_all_rooms():
    ref=EntityReference(text='rooms',label='room',node_type='room',quantifier='all',
        relations=[RelationConstraint(relation='contains',target_label='bed',target_node_type='object')])
    result=scene().resolve_entity(ref)
    assert {c.node_id for c in result.selected_many}=={ROOM,OTHER_ROOM}


@pytest.mark.parametrize('position',[[],[float('nan'),0,0],[0,0], [float('inf'),0,0]])
def test_missing_anchor_coordinates_never_fall_back_to_topology(position):
    api=scene(); api._index.nodes[FIRST].position=position; api._index.nodes[EXTRA_BED].position=position
    api._index.neighbors[FIRST].append(CLOSE)
    assert api.resolve_entity(near_ref()).status!='resolved'


@pytest.mark.parametrize('relation,expected',[('nearest_to',CLOSE),('farthest_from',FAR)])
def test_relative_extrema_use_minimum_distance_to_anchor_set(relation,expected):
    ref=EntityReference(text='chair',label='chair',relations=[RelationConstraint(
        relation=relation,target_label='bed',target_node_type='object')])
    result=scene().resolve_entity(ref)
    assert result.selected and result.selected.node_id==expected


def test_orchestrator_uses_same_relation_contract_as_api():
    api=scene(); intent=TaskIntent(action='navigate',target=near_ref())
    result=GroundingOrchestrator(api).ground_intent(intent)
    assert result.references['target'].selected and result.references['target'].selected.node_id==CLOSE


def test_nearest_and_farthest_origin_forms_remain_supported():
    api=scene()
    for mode,expected in [('nearest',CLOSE),('farthest',FAR)]:
        result=api.resolve_entity(EntityReference(text='chair',label='chair',quantifier=mode,position=[0,0,0]))
        assert result.selected and result.selected.node_id==expected


def test_unobserved_attributes_cannot_disappear_during_class_normalization():
    ref=EntityReference(text=FIRST,label='beds',attributes={'color':'red'})
    assert scene().resolve_entity(ref).status!='resolved'


@pytest.mark.parametrize('relation,word',[('nearest_to','closest'),('farthest_from','farthest')])
def test_parser_keeps_anchored_ranking_separate_from_robot_origin(monkeypatch,relation,word):
    import ollama
    payload={'action':'navigate','target':{'text':'chair','label':'chair','quantifier':'one',
        'relations':[{'relation':relation,'target_text':'bed','target_label':'bed'}]}}
    monkeypatch.setattr(ollama.Client,'chat',lambda *a,**k: SimpleNamespace(message=
        SimpleNamespace(content=json.dumps(payload),thinking=None)))
    parsed=parse_intent_with_llm(f'Go to the chair {word} to the bed.',model='synthetic',
        scene_ontology=['chair','bed'],config={'intent_audit_in_production':False})
    assert parsed.target.quantifier=='one'
    assert scene().resolve_entity(parsed.target).selected is not None


def test_model_cannot_invent_reference_coordinates(monkeypatch):
    import ollama
    payload={'action':'navigate','target':{'text':'chair','label':'chair',
        'quantifier':'nearest','position':[999,999,999]}}
    monkeypatch.setattr(ollama.Client,'chat',lambda *a,**k: SimpleNamespace(message=
        SimpleNamespace(content=json.dumps(payload),thinking=None)))
    parsed=parse_intent_with_llm('Go to the nearest chair.',model='synthetic',config={})
    assert parsed.target.position is None
    parsed=parse_intent_with_llm('Go to the nearest chair.',model='synthetic',
        config={'reference_position':[0,0,0]})
    assert parsed.target.position==[0,0,0]


def test_unknown_aggregate_category_cannot_select_an_unrelated_room():
    from planner_schemas.intent import AggregateConstraint
    ref=EntityReference(text='room with most alien scanners',node_type='room',label='room',
        aggregate=AggregateConstraint(operation='max_count',target_labels=['alien_scanner']))
    result=GroundingOrchestrator(scene()).ground_intent(TaskIntent(action='navigate',target=ref))
    assert result.references['target'].status=='not_found'


def test_origin_and_anchor_ranks_intersect_complete_pool():
    ref=near_ref('nearest'); ref.position=[20,0,0]
    assert scene().resolve_entity(ref).status=='not_found'


def test_api_and_anchor_synonym_repairs_cannot_rescue_contradictory_text():
    api=scene()
    assert api.resolve_entity(EntityReference(text='bed',label='patient monitor')).status=='not_found'
    ref=near_ref(); ref.relations[0].target_label='patient monitor'
    ref.relations[0].target_text='bed'
    assert api.resolve_entity(ref).status=='not_found'


def test_aggregate_ontology_does_not_truncate_at_500_objects():
    from planner_schemas.intent import AggregateConstraint
    api=scene()
    for i in range(495):
        nid=str(100000000001000+i)
        api._index.nodes[nid]=SceneEntity(node_id=nid,node_type='object',label='cabinet',room_id=ROOM)
        api._index.types['object'].append(nid); api._index.labels['cabinet'].append(nid)
    nid='100000000099999'
    api._index.nodes[nid]=SceneEntity(node_id=nid,node_type='object',label='sofa',room_id=ROOM)
    api._index.types['object'].append(nid); api._index.labels['sofa'].append(nid)
    for label,status in [('seat','not_found'),('sofa','resolved')]:
        ref=EntityReference(text='room',label='room',node_type='room',
            aggregate=AggregateConstraint(operation='max_count',target_labels=[label]))
        result=GroundingOrchestrator(api).ground_intent(TaskIntent(action='navigate',target=ref))
        assert result.references['target'].status==status


@pytest.mark.parametrize('relation',['near','farthest_from'])
def test_unsupported_adapter_cannot_fake_complete_geometry(relation):
    from api_schemas import ResolutionResult,ResolutionCandidate
    api=scene()
    class Adapter:
        get_node=api.get_node
        get_neighbors=lambda self,nid:[api.get_node(FAR)]
    ref=near_ref(); ref.relations[0].relation=relation
    ref.relations[0].target_node_id=FIRST
    candidates=[ResolutionCandidate(node_id=nid,node_type='object',label='chair',
        position=api.get_node(nid).position) for nid in [CLOSE,FAR]]
    resolution=ResolutionResult(status='ambiguous',reference=ref,candidates=candidates)
    result=GroundingOrchestrator(Adapter())._apply_relation_filter(ref,resolution)
    assert result.selected is None and not result.candidates


def test_plan_generation_schema_enforces_one_based_steps(monkeypatch):
    import main_rag as planner
    import ollama
    calls=[]
    payload={'status':'valid','task_chain':[{'step':1,'action':'navigate_to','target_type':'object',
        'target_ref':'target','target_id':CLOSE,'description':'synthetic','executor':'nav2'}]}
    def chat(*a,**k):
        calls.append(k); return SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))
    monkeypatch.setattr(ollama.Client,'chat',chat)
    planner._generate_grounded_plan('Go to chair.',{'references':{'target':{'node_id':CLOSE,'node_type':'object'}}},
        'synthetic',{'ollama_host':'http://127.0.0.1:11434'})
    assert calls[0]['format']['$defs']['PlanDraftStep']['properties']['step']['minimum']==1


def test_zero_step_is_rejected_before_set_expansion_can_hide_it():
    from llm.plan_contract import parse_grounded_plan_draft
    raw={'status':'valid','task_chain':[{'step':0,'action':'navigate_to','target_type':'object',
        'target_ref':'target','description':'synthetic','executor':'nav2'}]}
    with pytest.raises(ValueError):
        parse_grounded_plan_draft(json.dumps(raw),{'references':{'target':{'node_ids':[CLOSE,FAR]}}})


@pytest.mark.parametrize('bad',[
 {'action':'navigate','target':{'text':'patient monitor','label':'healthmonitor',
  'locations':[{'text':'null','node_type':'place'}]}},
 {'action':'navigate','target':{'text':'chair','label':'chair','quantifier':'farthest'},
  'source':{'text':'bed','label':'bed'}},
])
def test_malformed_placeholders_and_comparative_source_roles_are_reparsed(monkeypatch,bad):
    import ollama
    command=('Go to the patient monitor.' if bad['target']['text']=='patient monitor'
             else 'Go to the chair farthest from a bed.')
    good=copy.deepcopy(bad)
    good['target'].pop('locations',None)
    if 'source' in good:
        del good['source']; good['target']['quantifier']='one'
        good['target']['relations']=[{'relation':'farthest_from','target_text':'bed','target_label':'bed'}]
    queue=iter([bad,good]); calls=[]
    def chat(*a,**k):
        calls.append(k);return SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(queue)),thinking=None))
    monkeypatch.setattr(ollama.Client,'chat',chat)
    parsed=parse_intent_with_llm(command,model='synthetic',config={})
    assert len(calls)==2 and parsed.source is None and not parsed.target.locations


def test_room_description_does_not_request_internal_locate_action(monkeypatch):
    import ollama
    bad={'action':'navigate','steps':[{'action':'locate','target':{'text':'bed','label':'bed'}},
        {'action':'navigate','target':{'text':'room','node_type':'room','label':'room'}}]}
    good={'action':'navigate','target':{'text':'rooms','node_type':'room','label':'room',
        'quantifier':'all','relations':[{'relation':'contains','target_text':'bed','target_label':'bed'}]}}
    queue=iter([bad,good]);calls=[]
    def chat(*a,**k):
        calls.append(k);return SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(queue)),thinking=None))
    monkeypatch.setattr(ollama.Client,'chat',chat)
    parsed=parse_intent_with_llm('Visit all rooms that contain a bed.',model='synthetic',config={})
    assert len(calls)==2 and not parsed.steps and parsed.target.quantifier=='all'


@pytest.mark.parametrize('multi',[False,True])
def test_adapter_resolved_sets_do_not_bypass_geometry_contract(multi):
    from planner_schemas.intent import IntentStep
    api=scene()
    class Adapter:
        def resolve_entity(self,ref):
            result=api.resolve_entity(ref.model_copy(update={'relations':[]}))
            # Some valid adapter DTOs represent a set without duplicating it
            # into candidates; fail-closed must work in both representations.
            result.candidates=[]
            return result
    ref=near_ref('all')
    intent=(TaskIntent(action='navigate',steps=[IntentStep(action='navigate',target=ref)])
            if multi else TaskIntent(action='navigate',target=ref))
    result=GroundingOrchestrator(Adapter()).ground_intent(intent)
    key='step_0_target' if multi else 'target'
    assert result.references[key].status=='not_found'
    assert not result.references[key].selected_many


@pytest.mark.parametrize('matches',[True,False])
def test_adapter_topology_filter_synchronizes_all_set_fields(matches):
    api=scene()
    class Adapter:
        get_node=api.get_node
        get_neighbors=lambda self,nid:[api.get_node(CLOSE)] if matches else []
        def resolve_entity(self,ref):
            result=api.resolve_entity(ref.model_copy(update={'relations':[]}))
            result.candidates=[]
            return result
    ref=near_ref('all'); ref.relations[0].relation='connected_to'
    ref.relations[0].target_node_id=FIRST
    result=GroundingOrchestrator(Adapter()).ground_intent(TaskIntent(action='navigate',target=ref))
    resolved=result.references['target']
    if matches:
        assert resolved.status=='resolved_set'
        assert {c.node_id for c in resolved.selected_many}=={CLOSE}
        assert result.get_all_resolved_ids()=={CLOSE}
    else:
        assert resolved.status=='not_found' and not resolved.selected_many
