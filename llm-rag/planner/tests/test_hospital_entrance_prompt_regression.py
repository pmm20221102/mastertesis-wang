import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "llm" / "intent_parser.py"
MODULE = ast.parse(SOURCE.read_text(encoding="utf-8"))
PROMPT = next(
    ast.literal_eval(node.value)
    for node in MODULE.body
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name) and target.id == "INTENT_SYSTEM_PROMPT"
        for target in node.targets
    )
)


def test_escort_example_does_not_bias_entrance_as_place():
    assert '"Escort the person to the entrance"' not in PROMPT
    assert '"Escort the person to the waiting area"' in PROMPT
