#!/usr/bin/env python3
"""Generate three-layer data quality report from raw DSG + canonical graph + RAG metadata.

Outputs:
  - raw_data_quality_report.json
  - canonical_data_quality_report.json
  - rag_metadata_quality_report.json
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

# Add src to path — script is at Masterthesis-wang/LLM-.../eval/data_quality/
_workspace_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_workspace_root / "RAG" / "khronos_3dsg_rag" / "src"))


def generate_raw_report(dsg_path: str) -> dict:
    """Analyze raw DSG data quality."""
    from parse_3dsg import parse_3dsg

    graph = parse_3dsg(dsg_path)

    # Node counts by layer/type
    node_types = defaultdict(int)
    layer_counts = defaultdict(int)
    for node in graph.nodes:
        node_types[node.node_type] += 1
        layer_counts[node.layer] += 1

    # Edge counts
    edge_count = len(graph.edges)

    # Position coverage
    nodes_with_position = sum(1 for n in graph.nodes if n.position)
    position_coverage = nodes_with_position / len(graph.nodes) * 100 if graph.nodes else 0

    # Object and place counts (raw DSG uses plural: objects, places, rooms)
    # Distinguish objects (partition=0) from agents (partition=97)
    obj_count = sum(1 for n in graph.nodes if n.node_type in ("object", "objects") and n.partition == 0)
    agent_count = sum(1 for n in graph.nodes if n.node_type == "agent" and n.partition == 97)
    place_count = sum(1 for n in graph.nodes if n.node_type in ("place", "places"))

    # Hierarchy coverage (based on layer comparison)
    obj_place_parents = set()
    agent_place_parents = set()
    place_room_parents = set()

    for edge in graph.edges:
        src = graph.node_index.get(edge.source_id)
        tgt = graph.node_index.get(edge.target_id)
        if not src or not tgt:
            continue

        # Determine parent (higher layer) and child (lower layer)
        if src.layer > tgt.layer:
            parent, child = src, tgt
        elif tgt.layer > src.layer:
            parent, child = tgt, src
        else:
            continue  # Same layer = neighbor

        # L2 -> L3 (object/agent -> place)
        if child.layer == 2 and parent.layer == 3:
            if child.partition == 0:
                obj_place_parents.add(child.node_id)
            elif child.partition == 97:
                agent_place_parents.add(child.node_id)
        # Place (L3) -> Room (L4)
        elif child.layer == 3 and parent.layer == 4:
            place_room_parents.add(child.node_id)

    return {
        "source_file": str(dsg_path),
        "total_nodes": len(graph.nodes),
        "total_edges": edge_count,
        "node_types": dict(node_types),
        "layer_counts": {str(k): v for k, v in sorted(layer_counts.items())},
        "position_coverage_pct": round(position_coverage, 1),
        "object_count": obj_count,
        "agent_count": agent_count,
        "place_count": place_count,
        "object_place_parent_count": len(obj_place_parents),
        "object_place_coverage_pct": round(len(obj_place_parents) / obj_count * 100, 1) if obj_count > 0 else 0,
        "agent_place_parent_count": len(agent_place_parents),
        "agent_place_coverage_pct": round(len(agent_place_parents) / agent_count * 100, 1) if agent_count > 0 else 0,
        "place_room_parent_count": len(place_room_parents),
        "place_room_coverage_pct": round(len(place_room_parents) / place_count * 100, 1) if place_count > 0 else 0,
    }


def generate_canonical_report(canon_path: str) -> dict:
    """Analyze canonical graph v2 quality."""
    with open(canon_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    schema_version = data.get("schema_version", 1)
    nodes = data.get("nodes", {})
    edges = data.get("edges", {})

    total_nodes = len(nodes)
    hierarchy_edges = len(edges.get("hierarchy", edges.get("parents", {})))
    neighbor_edges = len(edges.get("neighbors", []))

    # Type counts
    type_counts = defaultdict(int)
    for n in nodes.values():
        type_counts[n.get("node_type", "unknown")] += 1

    # Hierarchy validity
    VALID_PARENTS = {
        "object": {"place"}, "objects": {"place", "places"},
        "agent": {"place"}, "agents": {"place", "places"},
        "place": {"room"}, "places": {"room", "rooms"},
        "room": {"building"}, "rooms": {"building", "buildings"},
    }

    valid_count = 0
    total_parent_edges = 0
    for nid, n in nodes.items():
        ct = n.get("node_type", "")
        if schema_version >= 2:
            parents = n.get("parent_place_ids", []) + n.get("parent_room_ids", []) + n.get("parent_building_ids", [])
        else:
            parents = n.get("parent_ids", [])

        for pid in parents:
            total_parent_edges += 1
            pn = nodes.get(pid)
            if pn:
                pt = pn.get("node_type", "")
                if pt in VALID_PARENTS.get(ct, set()):
                    valid_count += 1

    validity_pct = valid_count / total_parent_edges * 100 if total_parent_edges > 0 else 0

    # Coverage
    obj_count = type_counts.get("object", 0) + type_counts.get("objects", 0)
    place_count = type_counts.get("place", 0) + type_counts.get("places", 0)

    obj_with_place = sum(1 for n in nodes.values()
                        if n.get("node_type", "") in ("object", "objects")
                        and (n.get("place_id") or n.get("parent_place_ids")))
    obj_with_room = sum(1 for n in nodes.values()
                       if n.get("node_type", "") in ("object", "objects")
                       and n.get("room_id"))
    place_with_room = sum(1 for n in nodes.values()
                         if n.get("node_type", "") in ("place", "places")
                         and (n.get("room_id") or n.get("parent_room_ids")))

    # Position coverage
    with_position = sum(1 for n in nodes.values() if n.get("position"))

    return {
        "source_file": str(canon_path),
        "schema_version": schema_version,
        "total_nodes": total_nodes,
        "node_type_counts": dict(type_counts),
        "total_hierarchy_edges": hierarchy_edges,
        "total_neighbor_edges": neighbor_edges,
        "hierarchy_validity_pct": round(validity_pct, 1),
        "object_place_coverage_pct": round(obj_with_place / obj_count * 100, 1) if obj_count > 0 else 0,
        "object_room_coverage_pct": round(obj_with_room / obj_count * 100, 1) if obj_count > 0 else 0,
        "place_room_coverage_pct": round(place_with_room / place_count * 100, 1) if place_count > 0 else 0,
        "position_coverage_pct": round(with_position / total_nodes * 100, 1) if total_nodes > 0 else 0,
    }


def generate_rag_metadata_report(jsonl_path: str) -> dict:
    """Analyze RAG document metadata quality."""
    chunk_types = defaultdict(int)
    nodes_with_place = 0
    nodes_with_room = 0
    total_entities = 0
    blank_labels = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            doc = json.loads(line)
            meta = doc.get("metadata", {})
            ct = meta.get("chunk_type", "unknown")
            chunk_types[ct] += 1

            if ct == "entity":
                total_entities += 1
                if not meta.get("label"):
                    blank_labels += 1
                if meta.get("room"):
                    nodes_with_room += 1

    return {
        "source_file": str(jsonl_path),
        "total_chunks": sum(chunk_types.values()),
        "chunk_types": dict(chunk_types),
        "total_entities": total_entities,
        "entities_with_room": nodes_with_room,
        "entities_with_room_pct": round(nodes_with_room / total_entities * 100, 1) if total_entities > 0 else 0,
        "blank_labels": blank_labels,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate three-layer data quality reports")
    parser.add_argument("--dsg", default="../RAG/khronos_3dsg_rag/data/dsg.json")
    parser.add_argument("--canonical", default="../RAG/khronos_3dsg_rag/outputs/khronos_office_new/canonical_graph_v2.json")
    parser.add_argument("--rag-jsonl", default="../RAG/khronos_3dsg_rag/outputs/khronos_office_new/rag_documents.jsonl")
    parser.add_argument("-o", "--output-dir", default="eval/data_quality")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Generating raw DSG report...")
    raw_report = generate_raw_report(args.dsg)
    with open(output_dir / "raw_data_quality_report.json", "w") as f:
        json.dump(raw_report, f, indent=2)
    print(f"  Nodes: {raw_report['total_nodes']}, Edges: {raw_report['total_edges']}")
    print(f"  Object→Place: {raw_report['object_place_coverage_pct']}%")
    print(f"  Place→Room: {raw_report['place_room_coverage_pct']}%")
    print(f"  Position: {raw_report['position_coverage_pct']}%")

    print("\nGenerating canonical graph report...")
    canon_report = generate_canonical_report(args.canonical)
    with open(output_dir / "canonical_data_quality_report.json", "w") as f:
        json.dump(canon_report, f, indent=2)
    print(f"  Schema v{canon_report['schema_version']}")
    print(f"  Hierarchy validity: {canon_report['hierarchy_validity_pct']}%")
    print(f"  Object→Place: {canon_report['object_place_coverage_pct']}%")
    print(f"  Object→Room: {canon_report['object_room_coverage_pct']}%")
    print(f"  Place→Room: {canon_report['place_room_coverage_pct']}%")
    print(f"  Position: {canon_report['position_coverage_pct']}%")

    print("\nGenerating RAG metadata report...")
    rag_report = generate_rag_metadata_report(args.rag_jsonl)
    with open(output_dir / "rag_metadata_quality_report.json", "w") as f:
        json.dump(rag_report, f, indent=2)
    print(f"  Total chunks: {rag_report['total_chunks']}")
    print(f"  Entities: {rag_report['total_entities']}")
    print(f"  Blank labels: {rag_report['blank_labels']}")

    print(f"\nAll reports saved to {output_dir}/")


if __name__ == "__main__":
    main()
