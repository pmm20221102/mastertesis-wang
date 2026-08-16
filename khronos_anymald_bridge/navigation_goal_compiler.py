"""Phase 6: target_ref → waypoint 编译薄层（NavigationGoalCompiler）。

只消费 LLM+RAG 的 target_ref，不做任何语义理解（计划 §29 Rule 1）。
target_ref 兼容现有系统输出格式：
  - DSG Room 节点 id（64 位，现有 RAG task_plan 的 target_id）
  - DSG Place node id（64 位）
  - "R12" / 纯 room index（可读形式）
"""
import json
import re
from pathlib import Path

from dsg_reader.loader import decode_symbol


class InvalidGoal(Exception):
    pass


class NavigationGoalCompiler:
    def __init__(self, semantic_index_path: str, place_to_waypoint: dict | None = None):
        with open(semantic_index_path, encoding="utf-8") as f:
            idx = json.load(f)
        self.room_to_wp = idx["room_to_waypoint"]
        # place_to_waypoint 键是字符串 place index
        self.place_to_wp = place_to_waypoint if place_to_waypoint is not None else idx["place_to_waypoint"]

    def _lookup_room_index(self, room_index: int) -> int | None:
        for v in self.room_to_wp.values():
            if v.get("room_index") == room_index and v.get("status") == "ok":
                return v["waypoint_id"]
        return None

    def compile(self, target_ref) -> int:
        """target_ref → waypoint id。解析失败抛 InvalidGoal（fail closed）。"""
        if isinstance(target_ref, int):
            nid = target_ref
        elif isinstance(target_ref, str) and target_ref.isdigit():
            nid = int(target_ref)
        else:
            nid = None

        if nid is not None and nid > (1 << 40):
            # 64 位 DSG node id
            key, idx = decode_symbol(nid)
            if key == "R":
                entry = self.room_to_wp.get(str(nid))
                if entry and entry.get("status") == "ok":
                    return entry["waypoint_id"]
                raise InvalidGoal(f"Room {nid} 无有效 waypoint 映射: {entry}")
            if key == "P":
                wp = self.place_to_wp.get(str(idx))
                if wp is not None:
                    return int(wp)
                raise InvalidGoal(f"Place {idx} 无 waypoint 映射")

        # "R12" 格式
        if isinstance(target_ref, str):
            m = re.match(r"^[Rr](\d+)$", target_ref)
            if m:
                wp = self._lookup_room_index(int(m.group(1)))
                if wp is not None:
                    return wp
                raise InvalidGoal(f"room {target_ref} 无有效 anchor")
            if target_ref.isdigit():
                wp = self._lookup_room_index(int(target_ref))
                if wp is not None:
                    return wp
                raise InvalidGoal(f"room index {target_ref} 无有效 anchor")

        raise InvalidGoal(f"无法解析 target_ref: {target_ref!r}")
