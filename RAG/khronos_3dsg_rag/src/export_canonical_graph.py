"""Export canonical graph directly from raw DSG.

This is the ONLY authoritative canonical graph source.
It reads parsed SceneGraph and outputs canonical_graph.json
without going through ChromaDB or RAG chunk metadata.

Hierarchy rules:
  Object / Agent -> Place
  Place          -> Room
  Room           -> Building (if edges exist)
  Same-layer     -> neighbors ONLY (never parents/children)

Schema v2 includes:
  - Full position data from raw DSG
  - parent_place_ids / parent_room_ids (list, supports multiple parents)
  - Type-aware ancestor traversal
  - Separate hierarchy and neighbor edge lists
  - Source DSG hash for reproducibility
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from schemas import SceneGraph, SceneNode

logger = logging.getLogger(__name__)

# Valid parent-child type pairs (child_type -> allowed parent types)
VALID_HIERARCHY: dict[str, set[str]] = {
    "object": {"place"},
    "objects": {"place", "places"},
    "agent": {"place"},
    "agents": {"place", "places"},
    "place": {"room"},
    "places": {"room", "rooms"},
    "room": {"building"},
    "rooms": {"building", "buildings"},
}

# Canonical node_type normalization
NODE_TYPE_NORM: dict[str, str] = {
    "objects": "object",
    "places": "place",
    "rooms": "room",
    "buildings": "building",
    "agents": "agent",
}


def _norm_type(t: str) -> str:
    return NODE_TYPE_NORM.get(t.lower(), t.lower())


def _compute_file_hash(path: str | Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _is_valid_parent(child_type: str, parent_type: str) -> bool:
    """Check if parent_type is a valid parent of child_type."""
    ct = _norm_type(child_type)
    pt = _norm_type(parent_type)
    allowed = VALID_HIERARCHY.get(ct, set())
    return pt in allowed


def export_canonical_graph(
    graph: SceneGraph,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Export canonical graph directly from parsed SceneGraph.

    Args:
        graph: Parsed SceneGraph from parse_3dsg.py.
        source_path: Path to source DSG file (for hash).

    Returns:
        Canonical graph dict ready to be saved as JSON.
    """
    source_hash = _compute_file_hash(source_path) if source_path else "unknown"

    # Step 1: Build adjacency index using layer comparison
    adj: dict[str, dict[str, list[str]]] = {}
    for node in graph.nodes:
        adj[node.node_id] = {"parents": [], "children": [], "neighbors": []}

    for edge in graph.edges:
        src = graph.node_index.get(edge.source_id)
        tgt = graph.node_index.get(edge.target_id)
        if not src or not tgt:
            continue

        if src.node_id not in adj or tgt.node_id not in adj:
            continue

        if src.layer > tgt.layer:
            # src is higher layer (potential parent of tgt)
            adj[src.node_id]["children"].append(tgt.node_id)
            adj[tgt.node_id]["parents"].append(src.node_id)
        elif tgt.layer > src.layer:
            # tgt is higher layer (potential parent of src)
            adj[tgt.node_id]["children"].append(src.node_id)
            adj[src.node_id]["parents"].append(tgt.node_id)
        else:
            # Same layer -> neighbor
            adj[src.node_id]["neighbors"].append(tgt.node_id)
            adj[tgt.node_id]["neighbors"].append(src.node_id)

    # Step 2: Build canonical nodes with type-aware hierarchy
    canonical_nodes: dict[str, dict[str, Any]] = {}
    node_type_counts: dict[str, int] = defaultdict(int)

    for node in graph.nodes:
        ct = _norm_type(node.node_type)
        node_type_counts[ct] += 1

        # Get position from bbox center or position field
        position = node.position or []
        if not position and node.bbox:
            position = node.bbox.center or []

        # Get bbox data
        bbox_center = node.bbox.center if node.bbox else []
        bbox_dimensions = node.bbox.dimensions if node.bbox else []

        # Type-aware parent filtering
        raw_parents = adj[node.node_id]["parents"]
        parent_place_ids = []
        parent_room_ids = []
        parent_building_ids = []
        valid_parents = []
        valid_children = []

        for pid in raw_parents:
            parent_node = graph.node_index.get(pid)
            if parent_node:
                pt = _norm_type(parent_node.node_type)
                if _is_valid_parent(ct, pt):
                    valid_parents.append(pid)
                    if pt == "place":
                        parent_place_ids.append(pid)
                    elif pt == "room":
                        parent_room_ids.append(pid)
                    elif pt == "building":
                        parent_building_ids.append(pid)

        # Filter children to valid hierarchy only
        raw_children = adj[node.node_id]["children"]
        for cid in raw_children:
            child_node = graph.node_index.get(cid)
            if child_node:
                child_t = _norm_type(child_node.node_type)
                if _is_valid_parent(child_t, ct):
                    valid_children.append(cid)

        # Remove duplicate neighbors
        neighbor_ids = list(dict.fromkeys(adj[node.node_id]["neighbors"]))

        canonical_nodes[node.node_id] = {
            "node_id": node.node_id,
            "layer": node.layer,
            "partition": node.partition,
            "node_type": ct,
            "label": node.label,
            "name": node.name or node.label,
            "position": position,
            "bbox_center": bbox_center,
            "bbox_dimensions": bbox_dimensions,
            "is_active": node.is_active,
            "first_seen_ns": node.first_seen_ns,
            "last_seen_ns": node.last_seen_ns,
            "parent_place_ids": parent_place_ids,
            "parent_room_ids": parent_room_ids,
            "parent_building_ids": parent_building_ids,
            "child_ids": valid_children,
            "neighbor_ids": neighbor_ids,
            "hierarchy_path_ids": [],  # Computed below
        }

    # Step 3: Compute hierarchy paths and room_id/place_id
    for nid, node_data in canonical_nodes.items():
        ct = node_data["node_type"]

        # Compute hierarchy path by walking up
        path = []
        room_id = ""
        place_id = ""
        building_id = ""

        if ct in ("object", "objects", "agent", "agents"):
            # Walk: object -> place -> room -> building
            for pid in node_data["parent_place_ids"]:
                place_id = pid
                place_data = canonical_nodes.get(pid)
                if place_data:
                    for rid in place_data.get("parent_room_ids", []):
                        room_id = rid
                        room_data = canonical_nodes.get(rid)
                        if room_data:
                            for bid in room_data.get("parent_building_ids", []):
                                building_id = bid
                                break
                        break
                break  # Use first valid place parent

            # Build path
            if place_id:
                path.append(place_id)
                if room_id:
                    path.append(room_id)
                    if building_id:
                        path.append(building_id)

        elif ct in ("place", "places"):
            for rid in node_data["parent_room_ids"]:
                room_id = rid
                room_data = canonical_nodes.get(rid)
                if room_data:
                    for bid in room_data.get("parent_building_ids", []):
                        building_id = bid
                        break
                break
            if room_id:
                path.append(room_id)
                if building_id:
                    path.append(building_id)

        elif ct in ("room", "rooms"):
            for bid in node_data["parent_building_ids"]:
                building_id = bid
                break
            if building_id:
                path.append(building_id)

        node_data["hierarchy_path_ids"] = path
        node_data["room_id"] = room_id
        node_data["place_id"] = place_id
        node_data["building_id"] = building_id

    # Step 4: Build edge lists
    hierarchy_edges = []
    neighbor_edges = []

    for node_data in canonical_nodes.values():
        nid = node_data["node_id"]
        for pid in node_data["parent_place_ids"] + node_data["parent_room_ids"] + node_data["parent_building_ids"]:
            hierarchy_edges.append({"parent": pid, "child": nid})
        for nid2 in node_data["neighbor_ids"]:
            pair = tuple(sorted([nid, nid2]))
            neighbor_edges.append({"source": pair[0], "target": pair[1]})

    # Deduplicate edges
    hierarchy_edges = list({(e["parent"], e["child"]): e for e in hierarchy_edges}.values())
    neighbor_edges = list({(e["source"], e["target"]): e for e in neighbor_edges}.values())

    # Step 5: Compute statistics
    total_nodes = len(canonical_nodes)
    total_hierarchy = len(hierarchy_edges)
    total_neighbors = len(neighbor_edges)

    # Hierarchy validity check
    valid_count = 0
    for node_data in canonical_nodes.values():
        ct = node_data["node_type"]
        for pid in node_data["parent_place_ids"] + node_data["parent_room_ids"] + node_data["parent_building_ids"]:
            pn = canonical_nodes.get(pid)
            if pn and _is_valid_parent(ct, pn["node_type"]):
                valid_count += 1

    validity_rate = valid_count / total_hierarchy * 100 if total_hierarchy > 0 else 0

    # Coverage stats
    obj_with_place = sum(1 for n in canonical_nodes.values()
                        if n["node_type"] == "object" and n["parent_place_ids"])
    obj_count = node_type_counts.get("object", 0)
    obj_place_coverage = obj_with_place / obj_count * 100 if obj_count > 0 else 0

    place_with_room = sum(1 for n in canonical_nodes.values()
                         if n["node_type"] == "place" and n["parent_room_ids"])
    place_count = node_type_counts.get("place", 0)
    place_room_coverage = place_with_room / place_count * 100 if place_count > 0 else 0

    position_coverage = sum(1 for n in canonical_nodes.values() if n["position"]) / total_nodes * 100 if total_nodes > 0 else 0

    manifest = {
        "schema_version": 2,
        "source_dsg_hash": source_hash,
        "total_nodes": total_nodes,
        "node_type_counts": dict(node_type_counts),
        "total_hierarchy_edges": total_hierarchy,
        "total_neighbor_edges": total_neighbors,
        "hierarchy_validity_pct": round(validity_rate, 1),
        "object_place_coverage_pct": round(obj_place_coverage, 1),
        "place_room_coverage_pct": round(place_room_coverage, 1),
        "position_coverage_pct": round(position_coverage, 1),
    }

    result = {
        "schema_version": 2,
        "source_dsg_hash": source_hash,
        "total_nodes": total_nodes,
        "nodes": canonical_nodes,
        "edges": {
            "hierarchy": hierarchy_edges,
            "neighbors": neighbor_edges,
        },
        "manifest": manifest,
    }

    logger.info(
        f"Canonical export: {total_nodes} nodes, "
        f"{total_hierarchy} hierarchy edges, {total_neighbors} neighbor edges, "
        f"validity={validity_rate:.1f}%, "
        f"obj→place={obj_place_coverage:.1f}%, place→room={place_room_coverage:.1f}%"
    )

    return result


def save_canonical_graph(
    graph_data: dict[str, Any],
    output_dir: str | Path,
) -> tuple[Path, Path]:
    """Save canonical graph and manifest to files.

    Args:
        graph_data: Output of export_canonical_graph().
        output_dir: Directory to save files.

    Returns:
        Tuple of (graph_path, manifest_path).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    graph_path = output_dir / "canonical_graph_v2.json"
    manifest_path = output_dir / "canonical_graph_v2.manifest.json"

    # Save graph (without manifest embedded)
    graph_save = {k: v for k, v in graph_data.items() if k != "manifest"}
    with open(graph_path, "w", encoding="utf-8") as f:
        json.dump(graph_save, f, indent=2, ensure_ascii=False)

    # Save manifest separately
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(graph_data["manifest"], f, indent=2, ensure_ascii=False)

    logger.info(f"Saved canonical graph to {graph_path}")
    logger.info(f"Saved manifest to {manifest_path}")

    return graph_path, manifest_path


def main():
    """CLI entry point: export canonical graph from DSG file."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Export canonical graph from raw DSG")
    parser.add_argument("dsg_path", help="Path to dsg.json")
    parser.add_argument("-o", "--output-dir", default="outputs",
                       help="Output directory (default: outputs)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Add src to path for imports
    sys.path.insert(0, str(Path(__file__).parent))

    from parse_3dsg import parse_3dsg

    dsg_path = Path(args.dsg_path)
    if not dsg_path.exists():
        print(f"Error: DSG file not found: {dsg_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing DSG: {dsg_path}")
    graph = parse_3dsg(dsg_path)
    print(f"  Nodes: {len(graph.nodes)}, Edges: {len(graph.edges)}")

    print("Exporting canonical graph...")
    result = export_canonical_graph(graph, source_path=dsg_path)

    graph_path, manifest_path = save_canonical_graph(result, args.output_dir)

    m = result["manifest"]
    print(f"\nCanonical graph exported:")
    print(f"  Nodes: {m['total_nodes']}")
    print(f"  Hierarchy edges: {m['total_hierarchy_edges']}")
    print(f"  Neighbor edges: {m['total_neighbor_edges']}")
    print(f"  Hierarchy validity: {m['hierarchy_validity_pct']}%")
    print(f"  Object→Place coverage: {m['object_place_coverage_pct']}%")
    print(f"  Place→Room coverage: {m['place_room_coverage_pct']}%")
    print(f"  Position coverage: {m['position_coverage_pct']}%")
    print(f"\nSaved: {graph_path}")
    print(f"Saved: {manifest_path}")


if __name__ == "__main__":
    main()
