"""Test 1: Evaluation script imports and compilation."""

import sys
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiment" / "evaluations" / "taskchain"))
RAG_SRC = Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))


def test_eval_module_imports():
    """Test that eval module can be imported."""
    import run_pipeline_eval
    assert hasattr(run_pipeline_eval, 'run_evaluation')
    assert hasattr(run_pipeline_eval, 'evaluate_grounding_case')
    assert hasattr(run_pipeline_eval, 'evaluate_planning_case')
    print("✓ test_eval_module_imports passed")


def test_main_rag_imports():
    """Test that main_rag module can be imported."""
    import main_rag
    assert hasattr(main_rag, 'run_structured_task_planner')
    assert hasattr(main_rag, 'run_legacy_task_planner')
    print("✓ test_main_rag_imports passed")


def test_schema_imports():
    """Test that all schema modules can be imported."""
    from planner_schemas.intent import (
        EntityReference, TaskIntent, TaskConstraint,
        RelationConstraint, AggregateConstraint, IntentStep,
        IntentParseResult,
    )
    from api_schemas import (
        SceneEntity, ResolutionResult, ResolutionCandidate,
        NavigationTarget, SceneRelation, SceneGraphResult,
    )
    print("✓ test_schema_imports passed")


def test_grounding_imports():
    """Test that grounding module can be imported."""
    from grounding.orchestrator import GroundingOrchestrator, GroundingResult
    print("✓ test_grounding_imports passed")


def test_rag_imports():
    """Test that RAG modules can be imported."""
    from canonical_index import CanonicalGraphIndex, get_canonical_index
    from scene_graph_api import SceneGraphRAG
    from entity_resolver import EntityResolver
    print("✓ test_rag_imports passed")


if __name__ == "__main__":
    test_eval_module_imports()
    test_main_rag_imports()
    test_schema_imports()
    test_grounding_imports()
    test_rag_imports()
    print("\n=== All import tests passed ===")
