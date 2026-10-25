"""Closed-set interface diagnostics; never fill target classes or graph IDs."""
import re
from semantic_labels import normalize_label
from planner_schemas.intent import IntentStep

GENERIC_LABEL_TO_NODE_TYPE = {
    "room": "room", "rooms": "room",
    "place": "place", "places": "place",
    "object": "object", "objects": "object",
    "agent": "agent", "agents": "agent",
}


def lift_primary_target_into_steps(intent):
    """Make an unrepresented top-level navigation target the first ordered step."""
    if intent.action != "navigate" or not intent.steps or intent.target is None:
        return None

    primary = intent.target.model_dump(mode="json", exclude_none=True)
    for step in intent.steps:
        for reference in (step.target, step.destination):
            if reference is not None and reference.model_dump(
                mode="json", exclude_none=True
            ) == primary:
                return None

    intent.steps.insert(0, IntentStep(
        action="navigate",
        target=intent.target.model_copy(deep=True),
    ))
    intent.target = None
    return {
        "action": "lift_primary_target_into_step_zero",
        "text": primary.get("text", ""),
        "label": primary.get("label"),
        "node_type": primary.get("node_type"),
    }


def normalize_generic_entity_types(references):
    """Normalize generic labels without erasing descriptive grounding predicates."""
    normalizations = []
    for reference in references:
        label = reference.label
        if not isinstance(label, str):
            continue
        label_key = label.strip().lower()
        node_type = GENERIC_LABEL_TO_NODE_TYPE.get(label_key)
        if node_type is None:
            continue

        text = (reference.text or "").strip().lower()
        text = re.sub(r"^(?:the|a|an|any|all|every|each)\s+", "", text)
        is_bare_generic = text in GENERIC_LABEL_TO_NODE_TYPE
        has_structured_predicate = bool(
            reference.relations or reference.locations or reference.aggregate
        )
        if not is_bare_generic and not has_structured_predicate:
            reference.label = None
            normalizations.append({
                "action": "discard_unstructured_generic_label",
                "text": reference.text,
                "label": label,
                "node_type": reference.node_type,
            })
            continue

        if reference.node_type is not None:
            continue
        reference.node_type = node_type
        normalizations.append({
            "action": "fill_node_type",
            "text": reference.text,
            "label": label,
            "node_type": node_type,
        })
    return normalizations


def check_intent_grounding_contract(references, ontology):
    if ontology is None:
        return
    for ref in references:
        head = re.split(r'\b(?:near|closest|nearest|farthest|furthest|inside|containing|contains|that|with)\b',
                        ref.text, maxsplit=1, flags=re.I)[0]
        head = re.sub(r'^\s*(?:the|a|an|any|all|every|each)\s+', '', head, flags=re.I).strip()
        generic = {"room", "rooms", "place", "places", "object", "objects", "agent", "agents"}
        generic_head = head.lower() if head.lower() in generic else None
        declared_generic = ref.label if ref.label in generic else generic_head
        if declared_generic and not ref.label:
            expected = declared_generic[:-1] if declared_generic.endswith('s') else declared_generic
            if ref.node_type != expected:
                raise ValueError("A generic entity description requires its matching explicit node_type")
            continue
        if ref.label in generic and ref.node_type != (ref.label[:-1] if ref.label.endswith('s') else ref.label):
            raise ValueError("A generic type label requires the matching explicit node_type")
        if ref.label is not None:
            continue
        # Entire short category text is already supported by the API; unknown
        # compounds are not classified from local substrings. Only an explicitly
        # described known-category head exposes the long-text interface gap.
        surface = re.sub(r'^\s*(?:the|a|an)\s+', '', ref.text, flags=re.I).strip()
        if normalize_label(surface, ontology) is not None:
            continue
        if normalize_label(head, ontology) is not None:
            raise ValueError("Known-category descriptive reference lacks a usable label supported by grounding; reparse the original instruction with explicit roles and predicates")
