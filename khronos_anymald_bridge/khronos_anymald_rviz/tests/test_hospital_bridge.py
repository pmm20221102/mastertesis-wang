#!/usr/bin/env python3
"""Small real-artifact check for Hospital 3D RViz route fusion."""
import os
import sys
from pathlib import Path


def main():
    bridge = Path(os.environ.get("BRIDGE_ROOT", "/workspace/khronos_anymald_bridge"))
    pipeline = Path(os.environ.get(
        "HOSPITAL_PIPELINE", "/workspace/outputs/hospital_p1000_pipeline"))
    sys.path[:0] = [str(bridge), str(bridge / "khronos_anymald_rviz")]

    from khronos_anymald_rviz.data_loader import (
        BridgeData, floor_offset, floor_z_bounds)
    from khronos_anymald_rviz.khronos_mesh_publisher import clip_segment_to_z
    from navigation_goal_compiler import NavigationGoalCompiler
    from waypoint_graph.planner import WaypointGraph, shortest_path

    data = BridgeData(
        str(bridge), data_dir=str(pipeline / "waypoint_v2"),
        canonical_graph=str(pipeline / "canonical_graph_v2.json"),
        vertical_transitions=str(pipeline / "vertical_transitions_v2.json"),
        project_z=None)
    graph = WaypointGraph.load(
        str(data.gen / "waypoints_sparsified_R3.0.json"),
        str(data.gen / "witness_paths.json"),
        str(data.vertical_transitions_path))

    assert len(data.waypoint_ids()) == 192
    assert data.dense_pos(34) is not None  # canonical dense witness point
    assert data.witness_of(9454, 35440).get("vertical_transition_id") == "VT1"
    route, _ = shortest_path(graph, 26, 31728)
    assert route and any((a, b) in graph.vertical_edges for a, b in zip(route, route[1:]))
    points = data.route_positions(route)
    assert len(points) > len(route)
    assert max(p[2] for p in points) - min(p[2] for p in points) > 5.0

    exploded = BridgeData(
        str(bridge), data_dir=str(pipeline / "waypoint_v2"),
        canonical_graph=str(pipeline / "canonical_graph_v2.json"),
        vertical_transitions=str(pipeline / "vertical_transitions_v2.json"),
        floor_model=str(pipeline / "floor_model_v2.json"),
        floor_spacing=42.0, floor_columns=2, z_offset=0.6, project_z=None)
    raw = data.spars_pos(26)
    shifted = exploded.spars_pos(26)
    assert exploded.floor_of_waypoint(26) == 1
    assert floor_offset(3, 42.0, 2) == (42.0, -42.0)
    assert abs(shifted[0] - raw[0] - 42.0) < 1e-9
    assert abs(shifted[1] - raw[1]) < 1e-9
    assert abs(shifted[2] - raw[2] - 0.6) < 1e-9

    bounds = floor_z_bounds({0: 0.0, 1: 4.0, 2: 8.0})
    assert bounds[1] == (2.0, 6.0)
    assert clip_segment_to_z((0, 0, 0), (0, 0, 10), *bounds[1]) == (
        (0.0, 0.0, 2.0), (0.0, 0.0, 6.0))
    assert clip_segment_to_z((0, 0, 7), (1, 0, 7), *bounds[1]) is None

    reachable = exploded.reachable_waypoint_ids(26)
    display_edges = exploded.display_edges()
    assert len(display_edges) < len(exploded.edges())
    assert set(exploded.vertical_edges()) <= set(display_edges)
    assert all(exploded.floor_of_waypoint(a) == exploded.floor_of_waypoint(b)
               or (a, b) in exploded.vertical_edges() for a, b in display_edges)
    compiler = NavigationGoalCompiler(str(data.gen / "semantic_to_waypoint.json"))
    refs = ["5908722711110090753", "5908722711110090758",
            "5908722711110090762", "5908722711110090764",
            "5908722711110090766"]
    goals = [compiler.compile(ref) for ref in refs]
    assert goals == [9259, 32573, 41479, 48453, 51889]
    assert set(goals) <= reachable
    sequence = [26]
    for goal in goals:
        leg, _ = shortest_path(graph, sequence[-1], goal)
        assert leg
        sequence.extend(leg[1:])
    exploded_points = exploded.route_positions(sequence)
    assert len(exploded_points) > len(sequence)
    assert {exploded.floor_of_waypoint(wid) for wid in goals} == {0, 1, 2, 3}
    assert max(p[0] for p in exploded_points) - min(p[0] for p in exploded_points) > 35
    assert max(p[1] for p in exploded_points) - min(p[1] for p in exploded_points) > 35
    print(f"hospital-rviz-self-check: PASS reachable={len(reachable)} "
          f"route5={len(sequence)} dense={len(exploded_points)} "
          f"display_x_span={max(p[0] for p in exploded_points) - min(p[0] for p in exploded_points):.2f}m")


if __name__ == "__main__":
    main()
