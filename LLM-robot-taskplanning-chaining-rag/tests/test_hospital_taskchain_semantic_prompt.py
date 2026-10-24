from llm.intent_parser import (
    HOSPITAL_TASKCHAIN_SEMANTIC_PROFILE,
    HOSPITAL_TASKCHAIN_SEMANTIC_V6_PROFILE,
    HOSPITAL_TASKCHAIN_SEMANTIC_V5_PROFILE,
    HOSPITAL_TASKCHAIN_SEMANTIC_V3_PROFILE,
    HOSPITAL_TASKCHAIN_SEMANTIC_V4_PROFILE,
    HOSPITAL_TASKCHAIN_SEMANTIC_V3_PROFILE,
    SimpleTaskChainIntent,
    assemble_intent_system_prompt,
    get_intent_prompt_artifact,
    simple_taskchain_to_task_intent,
)


def test_semantic_taskchain_profile_uses_compact_ordered_stop_schema():
    prompt, schema = get_intent_prompt_artifact(HOSPITAL_TASKCHAIN_SEMANTIC_PROFILE)

    assert schema == SimpleTaskChainIntent.model_json_schema()
    assert '"stops"' in prompt
    assert "canonical object label" in prompt.lower()
    assert "visit order" in prompt.lower()
    assert '"clarification"' in prompt


def test_semantic_taskchain_profile_receives_runtime_canonical_ontology():
    prompt = assemble_intent_system_prompt(
        HOSPITAL_TASKCHAIN_SEMANTIC_PROFILE,
        ["chair", "doorway", "stairs", "table", "wall"],
    )

    assert "CURRENT SCENE ONTOLOGY" in prompt
    assert '"chair"' in prompt and '"doorway"' in prompt
    assert "Use an exact listed label" in prompt


def test_semantic_taskchain_profile_treats_next_stop_cues_as_new_ordered_destinations():
    prompt, _ = get_intent_prompt_artifact(HOSPITAL_TASKCHAIN_SEMANTIC_PROFILE)

    assert "next stop" in prompt.lower()
    assert "separate stop" in prompt.lower()
    assert "count the requested destinations" in prompt.lower()


def test_semantic_taskchain_profile_shows_two_stop_output_order():
    prompt, _ = get_intent_prompt_artifact(HOSPITAL_TASKCHAIN_SEMANTIC_PROFILE)

    assert "My next stop is the second named area" in prompt
    assert '"order":1,"destination":"first named area"' in prompt
    assert '"order":2,"destination":"second named area"' in prompt


def test_semantic_taskchain_profile_treats_objects_as_room_selectors_not_destinations():
    prompt, _ = get_intent_prompt_artifact(HOSPITAL_TASKCHAIN_SEMANTIC_PROFILE)

    assert "never set destination to an object label" in prompt.lower()
    assert '"destination":"room","objects":["bed"]' in prompt


def test_v3_preserves_exact_labels_and_normalizes_common_semantic_categories():
    prompt, schema = get_intent_prompt_artifact(HOSPITAL_TASKCHAIN_SEMANTIC_V3_PROFILE)

    assert schema == SimpleTaskChainIntent.model_json_schema()
    assert "preserve an exact ontology term" in prompt.lower()
    assert "counter-height work surface" in prompt.lower()
    assert "solid divider" in prompt.lower()
    assert "passage or opening between spaces" in prompt.lower()
    assert "do not list alternative labels" in prompt.lower()


def test_v4_asks_for_missing_area_or_history_without_rejecting_named_ood_objects():
    prompt, schema = get_intent_prompt_artifact(HOSPITAL_TASKCHAIN_SEMANTIC_V4_PROFILE)

    assert schema == SimpleTaskChainIntent.model_json_schema()
    assert "qualitative area" in prompt.lower()
    assert "no room or area label" in prompt.lower()
    assert "historical reference" in prompt.lower()
    assert "preserve a concise unknown object" in prompt.lower()


def test_v5_keeps_location_names_distinct_and_does_not_replace_missing_functional_devices():
    prompt, schema = get_intent_prompt_artifact(HOSPITAL_TASKCHAIN_SEMANTIC_V5_PROFILE)

    assert schema == SimpleTaskChainIntent.model_json_schema()
    assert "reception area is not the entrance" in prompt.lower()
    assert "printer" in prompt.lower() and "vending machine" in prompt.lower()
    assert "do not substitute a desk" in prompt.lower()


def test_v6_disambiguates_reception_and_pulse_monitor_from_nearby_categories():
    prompt, schema = get_intent_prompt_artifact(HOSPITAL_TASKCHAIN_SEMANTIC_V6_PROFILE)

    assert schema == SimpleTaskChainIntent.model_json_schema()
    assert "reception area is not the entrance" in prompt.lower()
    assert "pulse-reading device" in prompt.lower()
    assert "healthmonitor" in prompt.lower()


def test_canonical_object_labels_flow_into_existing_grounding_contract():
    payload = SimpleTaskChainIntent.model_validate({
        "action": "navigate",
        "stops": [
            {"order": 1, "destination": "room", "objects": ["chair", "wall"]},
            {"order": 2, "destination": "room", "objects": ["stairs"]},
        ],
    })

    intent = simple_taskchain_to_task_intent(payload)

    assert len(intent.steps) == 2
    assert intent.steps[0].target.relations[0].target_label == "chair"
    assert intent.steps[0].target.relations[1].target_label == "wall"
    assert intent.steps[1].target.relations[0].target_label == "stairs"


def test_surface_preserving_profile_extracts_verbatim_clues_without_ontology():
    prompt, schema = get_intent_prompt_artifact("hospital_taskchain_surface_v1")
    assembled = assemble_intent_system_prompt(
        "hospital_taskchain_surface_v1", ["bed", "desk", "stairs"]
    )

    assert schema == SimpleTaskChainIntent.model_json_schema()
    assert "copy each useful object or area clue" in prompt.lower()
    assert "do not translate, paraphrase, singularize, categorize" in prompt.lower()
    assert "CURRENT SCENE ONTOLOGY" not in assembled
    assert "connector or access object" in prompt.lower()
    assert "historical reference" in prompt.lower()
    assert "several objects or clues describing the same room belong in that one stop" in prompt.lower()
    assert "new stop only when the user requests a separate place" in prompt.lower()
    assert "one item per verb/function" in prompt.lower()
    assert "which tool helps a visitor check their temperature" in prompt.lower()
    assert 'output ["x","y"], not one combined clue' in prompt.lower()
    assert "those areas are context for one connector stop" in prompt.lower()


def test_surface_preserving_conversion_keeps_clue_text_and_leaves_labels_unresolved():
    payload = SimpleTaskChainIntent.model_validate({
        "action": "navigate",
        "stops": [{
            "order": 1,
            "destination": "room",
            "objects": ["spread out charts and make notes"],
        }],
    })

    intent = simple_taskchain_to_task_intent(payload, preserve_surface_objects=True)
    relation = intent.steps[0].target.relations[0]

    assert relation.target_text == "spread out charts and make notes"
    assert relation.target_label is None
