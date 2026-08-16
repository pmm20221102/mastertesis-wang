#!/usr/bin/env python3
"""Phase 1 审计：Khronos Office DSG / PLACES 层统计。

读取 SPARK-DSG 序列化的 dsg.json，统计 PLACES / MESH_PLACES 节点、边、
连通性、Room↔Place 关系，为 Khronos → ANYmal D waypoint exporter 提供事实依据。

用法:
  python analyze_places.py <dsg.json> [--json <out_stats.json>]
  # 必须仅读不写。frame 信息取自 hydra_config（map_frame: map），此处作为参数传入以便报告。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict


def decode_symbol(nid: int) -> tuple[str, int]:
    """NodeSymbol 解码：高 8 位为层字符 key，低 56 位为 index。"""
    key = (nid >> 56) & 0xFF
    index = nid & ((1 << 56) - 1)
    return chr(key), index


def valid_position(pos) -> bool:
    if not isinstance(pos, (list, tuple)) or len(pos) < 3:
        return False
    try:
        return all(math.isfinite(float(v)) for v in pos[:3])
    except (TypeError, ValueError):
        return False


def dist3(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a[:3], b[:3])))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dsg_json")
    ap.add_argument("--json", dest="out_json", default=None,
                    help="统计结果写到此 JSON（可选）")
    ap.add_argument("--frame", default="map",
                    help="DSG 声明 frame（来自 hydra_config map_frame），默认 map")
    args = ap.parse_args()

    print(f"[1/6] 加载 {args.dsg_json} ...", flush=True)
    with open(args.dsg_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    header = data.get("SPARK_DSG_header", {})
    directed = data.get("directed", False)
    layer_names = data.get("layer_names", {})
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    print(f"[2/6] 总节点 {len(nodes)}, 总边 {len(edges)}, directed={directed}", flush=True)

    # ---- 节点分类 ----
    # 用 json 中的 layer/partition 字段（parse_3dsg 同款），不用 NodeSymbol 解码推测
    by_layer: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for n in nodes:
        if not isinstance(n, dict):
            continue
        by_layer[(n.get("layer", 0), n.get("partition", 0))].append(n)

    layer_summary = {}
    for (layer, part), ns in sorted(by_layer.items()):
        layer_summary[f"layer_{layer}_part_{part}"] = {
            "count": len(ns),
            "layer_name": layer_names.get(f"{layer}.{part}", {}).get("name", "") if isinstance(layer_names.get(f"{layer}.{part}"), dict) else str(layer_names.get(f"{layer}.{part}")),
        }
        print(f"  层 {layer} partition {part}: {len(ns)} 节点")
    print("layer_names:", json.dumps(layer_names)[:500] if layer_names else "(空)")

    # ---- PLACES / MESH_PLACES 节点属性 ----
    def analyze_places(layer: int, partitions: list[int], label: str) -> dict:
        ns = [n for p in partitions for n in by_layer.get((layer, p), [])]
        stats = {
            "label": label,
            "count": len(ns),
            "id_index_min": None, "id_index_max": None,
            "position_valid": 0, "position_invalid": 0,
            "position_xy_range": None, "position_z_range": None,
            "has_orientation": 0, "has_quaternion": 0,
            "attr_keys_top": [],
        }
        positions = []
        attr_keys: Counter = Counter()
        for n in ns:
            key, idx = decode_symbol(int(n.get("id", 0)))
            if stats["id_index_min"] is None or idx < stats["id_index_min"]:
                stats["id_index_min"] = idx
            if stats["id_index_max"] is None or idx > stats["id_index_max"]:
                stats["id_index_max"] = idx
            attrs = n.get("attributes", {})
            for k in attrs.keys():
                attr_keys[k] += 1
            pos = attrs.get("position")
            if valid_position(pos):
                stats["position_valid"] += 1
                positions.append([float(v) for v in pos[:3]])
            else:
                stats["position_invalid"] += 1
            if attrs.get("orientation") is not None:
                stats["has_orientation"] += 1
            if attrs.get("quaternion") is not None:
                stats["has_quaternion"] += 1
        stats["attr_keys_top"] = attr_keys.most_common(15)
        if positions:
            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            zs = [p[2] for p in positions]
            stats["position_xy_range"] = {
                "x_min": round(min(xs), 3), "x_max": round(max(xs), 3),
                "y_min": round(min(ys), 3), "y_max": round(max(ys), 3),
            }
            stats["position_z_range"] = {
                "z_min": round(min(zs), 3), "z_max": round(max(zs), 3),
            }
        print(f"  [{label}] {len(ns)} 节点, 有效position {stats['position_valid']}, "
              f"invalid {stats['position_invalid']}, 有orientation {stats['has_orientation']}")
        return stats

    places_stats = analyze_places(3, [0], "PLACES")
    mesh_places_stats = analyze_places(3, [1], "MESH_PLACES")
    rooms = by_layer.get((4, 0), [])
    objects = by_layer.get((2, 0), [])
    buildings = by_layer.get((5, 0), [])
    print(f"[3/6] ROOMS={len(rooms)}, BUILDINGS={len(buildings)}, OBJECTS={len(objects)}", flush=True)

    # ---- 边：分类 + PLACES 层内边 ----
    # 判断边端点是否在 place 层内：解码 key 为 'P' 且 layer==3
    place_ids = set()
    for n in by_layer.get((3, 0), []):
        place_ids.add(int(n.get("id", 0)))
    mesh_place_ids = set()
    for n in by_layer.get((3, 1), []):
        mesh_place_ids.add(int(n.get("id", 0)))

    edge_summary = {
        "total": len(edges),
        "directed_flag": directed,
        "weight_min": None, "weight_max": None, "weight_mean": None,
        "self_loop": 0,
        "duplicate_directed": 0,
        "intra_place": 0,   # PLACES(3,0) 内
        "intra_mesh_place": 0,  # MESH_PLACES(3,1) 内
        "place_room": 0,    # PLACES <-> ROOMS
        "other": 0,
    }
    weights = []
    seen_pairs: set = set()
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = int(e.get("source", 0)), int(e.get("target", 0))
        info = e.get("info", {}) or {}
        w = info.get("weight")
        if isinstance(w, (int, float)) and math.isfinite(w):
            weights.append(float(w))
        if src == tgt:
            edge_summary["self_loop"] += 1
        pair = (min(src, tgt), max(src, tgt)) if not directed else (src, tgt)
        if pair in seen_pairs:
            edge_summary["duplicate_directed"] += 1
        seen_pairs.add(pair)
        s_place = src in place_ids
        t_place = tgt in place_ids
        s_mesh = src in mesh_place_ids
        t_mesh = tgt in mesh_place_ids
        if s_place and t_place:
            edge_summary["intra_place"] += 1
        elif s_mesh and t_mesh:
            edge_summary["intra_mesh_place"] += 1
        elif (s_place or s_mesh) and (tgt in {n.get("id", 0) for n in rooms}):
            edge_summary["place_room"] += 1
        elif (t_place or t_mesh) and (src in {n.get("id", 0) for n in rooms}):
            edge_summary["place_room"] += 1
        else:
            edge_summary["other"] += 1
    if weights:
        edge_summary["weight_min"] = round(min(weights), 4)
        edge_summary["weight_max"] = round(max(weights), 4)
        edge_summary["weight_mean"] = round(sum(weights) / len(weights), 4)
        # 直方图（0-0.1, 0.1-0.5, 0.5-1, 1-3, 3-10, >10）
        hist = Counter()
        for w in weights:
            if w < 0.1: hist["<0.1"] += 1
            elif w < 0.5: hist["0.1-0.5"] += 1
            elif w < 1.0: hist["0.5-1.0"] += 1
            elif w < 3.0: hist["1.0-3.0"] += 1
            elif w < 10.0: hist["3.0-10"] += 1
            else: hist["≥10"] += 1
        edge_summary["weight_hist"] = dict(sorted(hist.items(), key=lambda kv: float(kv[0].replace("≥", "").split("-")[0])))
    print(f"[4/6] 边: intra_place={edge_summary['intra_place']}, "
          f"intra_mesh_place={edge_summary['intra_mesh_place']}, "
          f"place_room={edge_summary['place_room']}, self_loop={edge_summary['self_loop']}, "
          f"dup={edge_summary['duplicate_directed']}", flush=True)

    # ---- PLACES 连通性（用 PLACES(3,0) 层内边）----
    print(f"[5/6] PLACES 连通性 ...", flush=True)
    adj: dict[int, list[int]] = {pid: [] for pid in place_ids}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = int(e.get("source", 0)), int(e.get("target", 0))
        if src in place_ids and tgt in place_ids:
            adj.setdefault(src, []).append(tgt)
            adj.setdefault(tgt, []).append(src)
    visited: set = set()
    components: list[list[int]] = []
    for pid in place_ids:
        if pid in visited:
            continue
        comp = []
        stack = [pid]
        visited.add(pid)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nb in adj.get(cur, []):
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        components.append(comp)
    components.sort(key=len, reverse=True)
    comp_sizes = [len(c) for c in components]
    isolated = [c for c in components if len(c) == 1]
    conn_stats = {
        "num_components": len(components),
        "largest_component_size": comp_sizes[0] if comp_sizes else 0,
        "component_sizes": comp_sizes[:15],
        "isolated_nodes": len(isolated),
        "singleton_ids": [decode_symbol(c[0]) for c in isolated][:10],
    }
    print(f"  连通分量 {len(components)}, 最大 {comp_sizes[0] if comp_sizes else 0}, "
          f"孤立 {len(isolated)}", flush=True)

    # ---- Room ↔ Place 关系 ----
    print(f"[6/6] Room↔Place ...", flush=True)
    room_ids = {int(n.get("id", 0)): n for n in rooms}
    room_place_count: dict[str, int] = defaultdict(int)
    room_place_ids: dict[str, list[int]] = defaultdict(list)
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = int(e.get("source", 0)), int(e.get("target", 0))
        # Room -> Place (hierarchy)
        if src in room_ids and tgt in place_ids:
            rk, ridx = decode_symbol(src)
            room_place_count[f"{rk}{ridx}"] += 1
            room_place_ids[f"{rk}{ridx}"].append(tgt)
        elif tgt in room_ids and src in place_ids:
            rk, ridx = decode_symbol(tgt)
            room_place_count[f"{rk}{ridx}"] += 1
            room_place_ids[f"{rk}{ridx}"].append(src)
    room_relation = {
        "num_rooms_with_places": len(room_place_count),
        "places_per_room": {
            k: v for k, v in sorted(room_place_count.items(), key=lambda x: -x[1])
        },
        "rooms_without_places": [
            f"{decode_symbol(nid)[0]}{decode_symbol(nid)[1]}" for nid in room_ids
            if f"{decode_symbol(nid)[0]}{decode_symbol(nid)[1]}" not in room_place_count
        ][:10],
    }
    print(f"  有 Place 的房间 {len(room_place_count)} 个")
    for k, v in sorted(room_place_count.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v} places")

    # ---- 汇总 ----
    stats = {
        "source": args.dsg_json,
        "spark_dsg_version": header.get("version", {}),
        "declared_frame": args.frame,
        "directed": directed,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "layer_summary": layer_summary,
        "places": places_stats,
        "mesh_places": mesh_places_stats,
        "rooms": {"count": len(rooms)},
        "buildings": {"count": len(buildings)},
        "objects": {"count": len(objects)},
        "edges": edge_summary,
        "connectivity_places": conn_stats,
        "room_place_relation": room_relation,
    }
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        print(f"\n统计已写: {args.out_json}")
    print("\n=== 审计统计完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
