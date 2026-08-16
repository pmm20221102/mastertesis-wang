"""Phase 4: Semantic Entity → ANYmal Waypoint 映射（semantic_to_waypoint.json）。

输入 target_ref 兼容现有 LLM+RAG 输出的两种形式（均为 DSG 节点 id）：
  - DSG Room 节点 id（64 位）→ 映射到 room 的 anchor waypoint
  - DSG Place index（P index）→ 映射到其 sparsified 代表 waypoint

anchor 选择（计划 §18.2）：
  优先 Room↔Place 已有拓扑；一个 room 有多个 place 时，选 sparsified 代表中
  距 room position 最近者作为 navigation anchor waypoint。

fail closed（计划 §18.4）：
  room 无 place / 无代表 / 无 room position → 返回 None 并记录 status，不强行映射。
  距离过远：记录 distance_m，提供 max_room_distance 可选硬阈值。
"""
import json
import math

from dsg_reader.loader import decode_symbol, PlacesGraph


def load_dsg_rooms(dsg_path: str) -> tuple[dict, dict]:
    """返回 (rooms, room_place)：
    rooms = {room_node_id: {"index": int, "position": [x,y,z]}}
    room_place = {room_node_id: set[place_index]}（Room↔Place 直接边）
    """
    with open(dsg_path, encoding="utf-8") as f:
        data = json.load(f)

    rooms: dict = {}
    room_place: dict = {}
    place_ids: set = set()
    for n in data.get("nodes", []):
        if not isinstance(n, dict):
            continue
        layer, part = n.get("layer"), n.get("partition")
        if layer == 4 and part == 0:
            nid = int(n["id"])
            _, idx = decode_symbol(nid)
            pos = (n.get("attributes", {}) or {}).get("position") or []
            rooms[nid] = {
                "index": idx,
                "position": [float(v) for v in pos[:3]] if len(pos) >= 3 else [],
            }
        elif layer == 3 and part == 0:
            place_ids.add(int(n["id"]))

    for e in data.get("edges", []):
        if not isinstance(e, dict):
            continue
        s, t = int(e.get("source", 0)), int(e.get("target", 0))
        if s in rooms and t in place_ids:
            _, ti = decode_symbol(t)
            room_place.setdefault(s, set()).add(ti)
        elif t in rooms and s in place_ids:
            _, si = decode_symbol(s)
            room_place.setdefault(t, set()).add(si)
    return rooms, room_place


def build_semantic_index(dsg_path: str, sparsified: PlacesGraph,
                         assigned: dict[int, int],
                         max_room_distance: float | None = None) -> dict:
    """生成 semantic_to_waypoint 索引。

    - place_to_waypoint: {place_index: waypoint_id}（assigned 直接给出）
    - room_to_waypoint: {room_node_id: {...anchor 信息...}}
    """
    rooms, room_place = load_dsg_rooms(dsg_path)

    place_to_wp = {i: int(r) for i, r in assigned.items()}

    room_to_wp: dict = {}
    for rid, info in rooms.items():
        places = room_place.get(rid, set())
        reps = {assigned[p] for p in places if p in assigned}
        if not places:
            room_to_wp[rid] = {"waypoint_id": None, "room_index": info["index"],
                               "status": "no_places_in_room"}
            continue
        if not reps:
            room_to_wp[rid] = {"waypoint_id": None, "room_index": info["index"],
                               "status": "no_representative", "n_places_in_room": len(places)}
            continue
        if not info["position"]:
            room_to_wp[rid] = {"waypoint_id": None, "room_index": info["index"],
                               "status": "no_room_position", "n_places_in_room": len(places)}
            continue
        anchor = min(reps, key=lambda r: math.dist(
            info["position"], sparsified.nodes[r].position))
        dist_m = math.dist(info["position"], sparsified.nodes[anchor].position)
        if max_room_distance is not None and dist_m > max_room_distance:
            room_to_wp[rid] = {"waypoint_id": None, "room_index": info["index"],
                               "status": f"distance_exceeded_{dist_m:.2f}m",
                               "n_places_in_room": len(places)}
            continue
        room_to_wp[rid] = {
            "waypoint_id": int(anchor),
            "room_index": info["index"],
            "mapping_method": "room_places_to_nearest_representative",
            "distance_m": round(dist_m, 3),
            "n_places_in_room": len(places),
            "n_representatives": len(reps),
            "status": "ok",
        }

    return {
        "schema_version": 1,
        "frame": sparsified.frame,
        "room_to_waypoint": room_to_wp,
        "place_to_waypoint": place_to_wp,
    }
