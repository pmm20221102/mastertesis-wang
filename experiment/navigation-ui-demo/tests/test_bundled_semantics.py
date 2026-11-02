"""Bundled data must preserve Hospital109 semantics, not legacy Office labels."""
from pathlib import Path
import hashlib
import json
import yaml

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "experiment/navigation-ui-demo"


def test_bundled_profiles_preserve_native_and_reviewed_object_labels():
    reference = json.loads((ROOT / "experiment/task-chain-60/runs/surface-v1-llm2-60x4-20261020/runtime_inputs/canonical_graph_v2.json").read_text())["nodes"]
    native = {key: node for key, node in reference.items()
              if node.get("node_type") == "object"
              and node.get("semantic_status") in ("semantic_static", "semantic_dynamic")}
    assert len(native) == 42
    reviewed = json.loads((DEMO / "rag/canonical_graph_v2.json").read_text())["nodes"]
    fresh = json.loads((DEMO / "rag-fresh/canonical_graph_v2.json").read_text())["nodes"]
    for key, node in reference.items():
        if node.get("node_type") == "object":
            assert reviewed[key]["label"] == node["label"], key
    for key, node in native.items():
        assert fresh[key]["label"] == node["label"], key
    for key, node in fresh.items():
        if node.get("node_type") == "object" and key not in native:
            assert node.get("label", "").lower() in ("", "unknown"), key
    mapping = ROOT / "llm-rag/RAG/khronos_3dsg_rag/config/tartanground_hospital_p1000_fine_label_space.yaml"
    expected_hash = hashlib.sha256(mapping.read_bytes()).hexdigest()
    for folder in ("rag", "rag-fresh"):
        config = json.loads((DEMO / folder / "build_config.json").read_text())
        assert config["label_space"]["name"] == "hospital109"
        assert config["label_space"]["source_sha256"] == expected_hash
