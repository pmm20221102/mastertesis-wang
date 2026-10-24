"""Static safeguards for the English Hospital intent prompt."""

import ast
from pathlib import Path


source = Path(__file__).resolve().parents[1] / 'llm' / 'intent_parser.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
INTENT_SYSTEM_PROMPT = next(
    ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == 'INTENT_SYSTEM_PROMPT'
            for target in node.targets)
)


def test_prompt_distinguishes_near_relation_from_aggregate():
    prompt = INTENT_SYSTEM_PROMPT
    assert 'near' in prompt
    assert 'relations' in prompt
    assert 'aggregate' in prompt
    assert 'Only use aggregate for counting or extremum questions' in prompt
    assert 'Never encode a spatial relation as aggregate.location' in prompt


def test_prompt_requires_known_category_label_and_multi_stop_order():
    prompt = INTENT_SYSTEM_PROMPT
    assert 'If the destination category is present in CURRENT SCENE ONTOLOGY' in prompt
    assert 'Use label null only when the category is genuinely absent' in prompt
    assert 'up to five destinations' in prompt
    assert 'Do not invent an extra locate step' in prompt


def test_prompt_examples_use_english_and_no_hospital_task_answers():
    prompt = INTENT_SYSTEM_PROMPT
    assert 'Navigate to a crate near a beacon' in prompt
    assert 'First go to the atrium, then to the observatory' in prompt
    assert 'target_node_id": "explicit numeric ID or null"' in prompt
