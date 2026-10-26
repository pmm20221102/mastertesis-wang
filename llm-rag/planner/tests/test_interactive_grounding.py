import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main_rag
from api_schemas import EntityReference, ResolutionCandidate, ResolutionResult
from grounding.orchestrator import GroundingResult


def _ambiguous_grounding():
    reference = EntityReference(reference_id="step_0_target", text="room with a bed",
                                node_type="room")
    candidates = [
        ResolutionCandidate(node_id="room-a", node_type="room", label="room", room_id="room-a"),
        ResolutionCandidate(node_id="room-b", node_type="room", label="room", room_id="room-b"),
    ]
    result = ResolutionResult(status="ambiguous", reference=reference,
                              candidates=candidates)
    grounding = GroundingResult()
    grounding.add_result("step_0_target", result, is_required=True)
    return grounding, candidates


def test_user_selection_resolves_only_a_candidate_from_that_reference():
    grounding, candidates = _ambiguous_grounding()

    main_rag.apply_grounding_selections(grounding, {"step_0_target": "room-b"})

    resolved = grounding.references["step_0_target"]
    assert grounding.status == "ready"
    assert resolved.status == "resolved"
    assert resolved.selected is candidates[1]
    assert [candidate.node_id for candidate in resolved.candidates] == ["room-b"]


def test_user_selection_rejects_ids_outside_the_candidate_set_without_mutating():
    grounding, _ = _ambiguous_grounding()

    with pytest.raises(ValueError, match="is not a candidate"):
        main_rag.apply_grounding_selections(
            grounding, {"step_0_target": "invented-room"})

    assert grounding.status == "ambiguous"
    assert grounding.references["step_0_target"].status == "ambiguous"


def test_user_selection_rejects_unknown_reference_keys():
    grounding, _ = _ambiguous_grounding()

    with pytest.raises(ValueError, match="unknown grounding reference"):
        main_rag.apply_grounding_selections(grounding, {"step_9_target": "room-a"})
