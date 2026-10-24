"""Evidence-grounded instruction audit protocol, no ontology or graph lookup."""
import json
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

PROTOCOL='evidence_v2'


class RequirementCheck(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    source_quote:str=Field(min_length=1,max_length=160)
    kind:Literal['action','target','location','relation','quantifier','order','constraint','attribute']
    intent_pointer:str=Field(min_length=1,max_length=120)
    intent_value_json:str=Field(max_length=1200)
    status:Literal['preserved','missing','contradictory']


class EvidenceAudit(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    complete:bool
    ordered:bool
    issues:list[str]=Field(max_length=3)
    checks:list[RequirementCheck]=Field(min_length=1,max_length=32)


PROMPT="""Check ONLY whether an instruction's meaning is preserved in the predicted intent.
You are not a planner, world model, graph resolver, or capability verifier. Do not create an intent or executable plan.
No scene inventory is provided. Whether a target exists is NOT your decision. An unknown target described faithfully is preserved and must not be rejected for absence, unfamiliarity, or missing canonical category. Room names and object descriptions need no map IDs. A single action does not require multiple steps. Do not require grounding facts, room IDs, routes, coordinates, or navigation execution details.

Enumerate every requested action, destination, room/location restriction, relation reference, ordering, quantity and constraint. Use exact short quotes from the original instruction; never invent a requirement. For each quote select a JSON pointer into the supplied intent, copy the actual pointed value as JSON encoded inside intent_value_json, and mark preserved/missing/contradictory. This is a concise evidence table, not a reasoning transcript.

Respect field meaning: entity room/location restrictions belong to room, locations or inside relations; a task condition is NOT an entity contains/inside filter. Nearest/farthest to an object needs a relation with that reference. Quantifier nearest/farthest alone means relative to a caller's origin, NOT relative to a mentioned object. Free-text restatement alone cannot stand in for structured relation/location predicates. Target identity is not satisfied by an unrelated category. Objects/rooms described by contained objects are destinations, not extra locate actions. Check every requested destination even when two share a class.

Use an actual field pointer, e.g. /target/text, /target/room, /target/relations, /steps/0/action. A missing predicate may point to its existing empty/null collection; copy []/null accurately. For preserved predicates point to the actual nonempty predicate or leaf value, never a free-text summary. Do not invent values or pointers.

Return ONLY the requested JSON object with complete, ordered, issues and checks. complete is true only if every check is preserved; ordered is false only if requested order is omitted or contradicted. Approval has issues=[]; rejection has up to 3 brief concrete omissions/contradictions, at most160 characters each. Never reject merely because a target is not in a scene ontology. Do not demand navigation steps to interpret one instruction.
"""


def field_catalog(intent):
    """Project actual fields only, never extract requested answers or graph IDs."""
    result={}
    def noisy(value):
        if isinstance(value,dict):
            return any(key in ('position','description') or noisy(item) for key,item in value.items())
        if isinstance(value,list):
            return any(noisy(item) for item in value)
        return False
    def store(path,value):
        serialized=json.dumps(value,sort_keys=True,ensure_ascii=False)
        if len(path)<=120 and len(serialized)<=1200:
            result[path]=serialized
    def visit(value,path):
        if isinstance(value,dict):
            if not value:
                store(path,value)
            for key,item in value.items():
                # Positions are caller state, not evidence of an instruction predicate.
                if key != 'position' and (key != 'description' or '/constraints/' in path):
                    visit(item,path+'/'+key.replace('~','~0').replace('/','~1'))
        elif isinstance(value,list):
            if not noisy(value):
                store(path,value)
            for index,item in enumerate(value):visit(item,path+'/'+str(index))
        else:
            store(path,value)
    visit(intent,'')
    return result


def schema(intent=None):
    value=EvidenceAudit.model_json_schema()
    if intent is not None:
        fields=field_catalog(intent)
        props=value['$defs']['RequirementCheck']['properties']
        props['intent_pointer']['enum']=list(fields)
        props['intent_value_json']['enum']=list(dict.fromkeys(fields.values()))
    return value


def messages(instruction,intent):
    return [{'role':'system','content':PROMPT+'\nUse evidence_fields as a literal path-to-value catalog. '
        'Copy one listed JSON value string, not the entire intent. Root pointer /action describes a single action; '
        'do not invent /intent or steps when steps is null. ordered=true when no multi-action order was requested. '
        'Evidence field examples are a format specification, not approval: source_quote="wait", kind="action", '
        'intent_pointer="/action", intent_value_json="\\\"wait\\\"", status="preserved".'},
            {'role':'user','content':json.dumps({'instruction':instruction,'intent':intent,
                                               'evidence_fields':field_catalog(intent)},ensure_ascii=False)}]


def _pointer(value,path):
    if not path.startswith('/'):
        raise ValueError('Intent evidence pointer must be an absolute JSON pointer')
    for token in path[1:].split('/'):
        if re.search(r'~(?![01])',token):
            raise ValueError('Invalid JSON pointer escape')
        token=token.replace('~1','/').replace('~0','~')
        if isinstance(value,dict) and token in value:
            value=value[token]
        elif isinstance(value,list) and re.fullmatch(r'0|[1-9][0-9]*',token) and int(token)<len(value):
            value=value[int(token)]
        else:
            raise ValueError('Audit evidence pointer does not exist in predicted intent')
    return value


def validate(raw,instruction,intent):
    verdict=EvidenceAudit.model_validate_json(raw)
    for item in verdict.checks:
        if item.source_quote not in instruction:
            raise ValueError('Audit source quote is not present in original instruction')
        actual=_pointer(intent,item.intent_pointer)
        quoted=json.loads(item.intent_value_json)
        if json.dumps(actual,sort_keys=True)!=json.dumps(quoted,sort_keys=True):
            raise ValueError('Audit evidence value disagrees with actual predicted intent')
        if item.status=='preserved':
            pointer=item.intent_pointer
            root=r'/(?:actor|target|source|destination)|/steps/(?:0|[1-9][0-9]*)/(?:actor|target|source|destination)'
            entity=re.match(r'^('+root+r')(/.*)$',pointer)
            suffix=entity.group(2) if entity else ''
            relation_field=bool(re.fullmatch(r'/relations(?:/(?:0|[1-9][0-9]*)(?:/(?:relation|target_text|target_label|target_node_id|target_node_type))?)?',suffix))
            inside_evidence=False
            if entity and relation_field:
                reference=_pointer(intent,entity.group(1))
                tail=suffix.split('/')
                members=(reference['relations'] if len(tail)==2 else [reference['relations'][int(tail[2])]])
                inside_evidence=bool(members) and all(m.get('relation')=='inside' for m in members)
            allowed={
                'action': bool(re.fullmatch(r'/(?:action|steps/(?:0|[1-9][0-9]*)/action)',pointer)),
                'target': bool(re.fullmatch(r'/(?:text|label|node_type|name)',suffix)),
                'relation': relation_field,
                'location': bool(re.fullmatch(r'/room|/locations(?:/(?:0|[1-9][0-9]*)(?:/(?:text|node_id|node_type))?)?',suffix)) or inside_evidence,
                'quantifier': bool(re.fullmatch(r'/quantifier|/aggregate/(?:operation|value)',suffix)),
                'order': bool(re.fullmatch(r'/steps/(?:0|[1-9][0-9]*)/action',pointer))
                    or bool(entity and entity.group(1).startswith('/steps/') and re.fullmatch(r'/(?:text|label|name)',suffix)),
                'constraint': bool(re.fullmatch(r'/(?:constraints|steps/(?:0|[1-9][0-9]*)/constraints)(?:/(?:0|[1-9][0-9]*)(?:/(?:type|description|target_text|target_label))?)?',pointer)),
                'attribute': bool(entity and re.fullmatch(r'/attributes(?:/[^/]+)?',suffix)),
            }
            if not allowed[item.kind] or actual is None or actual=='' or actual==[] or actual=={}:
                raise ValueError('Preserved audit evidence must use a nonempty field with matching semantic role')
    complete=all(item.status=='preserved' for item in verdict.checks)
    if verdict.complete!=complete:
        raise ValueError('Audit summary disagrees with requirement evidence')
    if verdict.complete and (not verdict.ordered or verdict.issues):
        raise ValueError('Approved audit must have ordered=true and no issues')
    if not verdict.complete and (not verdict.issues or any(len(issue)>160 for issue in verdict.issues)):
        raise ValueError('Rejected audit must explain the concrete missing/contradictory requirement')
    return verdict.model_dump(mode='json')
