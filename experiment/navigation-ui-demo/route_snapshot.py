"""Selected Nav2 route snapshots; no generated geometry and no ROS imports."""
from copy import deepcopy
import math


class RouteHistory:
    """A frozen plan snapshot, independent of process and left-panel lifetimes."""
    def __init__(self, session_id, map_bundle_id):
        self.data = {"schema_version": "hospital-selected-route-v1", "session_id": session_id,
                     "map_bundle_id": map_bundle_id, "segments": [], "execution_segments": []}

    @classmethod
    def from_plan(cls, result, session_id, map_bundle_id):
        history = cls(session_id, map_bundle_id)
        selection = (result.get("nav2_plan") or {}).get("route_selection") or {}
        for segment in selection.get("segment_results") or []:
            floor = segment.get("floor_id")
            path = segment.get("path")
            if type(floor) is not int or not isinstance(path, list) or not path:
                continue
            if not all(isinstance(p, dict) and all(type(p.get(k)) in (int, float)
                       and math.isfinite(p[k]) for k in ("x", "y")) for p in path):
                continue
            history.data["segments"].append({
                "floor_id": floor, "kind": segment.get("kind"),
                "stop_index": segment.get("stop_index"),
                "path": [{"x": p["x"], "y": p["y"]} for p in path]})
        return history

    def snapshot(self):
        return deepcopy(self.data)

    @staticmethod
    def segments_on_floor(snapshot, floor):
        if type(floor) is not int:
            return []
        # Keep separate Nav2 legs separate; floor transitions are never travel lines.
        return [deepcopy(segment) for segment in snapshot.get("segments", [])
                if segment.get("floor_id") == floor and segment.get("path")]
