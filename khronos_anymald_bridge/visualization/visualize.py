#!/usr/bin/env python3
"""Phase 7: waypoint graph 可视化 + 空间重合验证。

用系统 python3（有 matplotlib）。生成:
  generated/visualization/places_vs_waypoints.png   Khronos PLACES vs sparsified waypoints
  generated/visualization/route_viz.png              example route + room anchors
输出空间重合率（waypoint 落在 Khronos PLACES 位置的比例）。

用法:
  python3 visualization/visualize.py
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
FULL = BASE / "generated" / "waypoints.json"
SPARS = BASE / "generated" / "waypoints_sparsified_R3.0.json"
SEM = BASE / "generated" / "semantic_to_waypoint.json"
OUT_DIR = BASE / "generated" / "visualization"


def load_nodes(p):
    return json.load(open(p, encoding="utf-8"))["nodes"]


def xy(nodes):
    return [n["pose_stamped"]["position"][0] for n in nodes], \
           [n["pose_stamped"]["position"][1] for n in nodes]


def verify_coincidence(full_nodes, spars_nodes) -> float:
    """空间重合率：sparsified waypoint 位置落在全量 PLACES 位置集合中的比例。"""
    full_pos = {(round(n["pose_stamped"]["position"][0], 3),
                 round(n["pose_stamped"]["position"][1], 3)) for n in full_nodes}
    spars_pos = {(round(n["pose_stamped"]["position"][0], 3),
                  round(n["pose_stamped"]["position"][1], 3)) for n in spars_nodes}
    overlap = len(spars_pos & full_pos)
    return overlap / len(spars_pos)


def plot_places_vs_waypoints(full_nodes, spars_nodes, sem, out):
    fig, ax = plt.subplots(figsize=(12, 10))
    fx, fy = xy(full_nodes)
    ax.scatter(fx, fy, s=4, c="lightgray", alpha=0.7,
               label=f"Khronos PLACES (n={len(full_nodes)})")
    sx, sy = xy(spars_nodes)
    ax.scatter(sx, sy, s=14, c="blue",
               label=f"ANYmal waypoints R=3.0 (n={len(spars_nodes)})")
    posmap = {n["id"]: n["pose_stamped"]["position"] for n in spars_nodes}
    for e in json.load(open(SPARS, encoding="utf-8"))["edges"]:
        a, b = posmap[e["start_node_id"]], posmap[e["end_node_id"]]
        ax.plot([a[0], b[0]], [a[1], b[1]], c="blue", lw=0.4, alpha=0.25)
    anchors = {v["room_index"]: v["waypoint_id"]
               for v in sem["room_to_waypoint"].values() if v.get("status") == "ok"}
    for r, w in sorted(anchors.items()):
        p = posmap[w]
        ax.scatter(p[0], p[1], s=110, c="red", marker="*", edgecolors="k",
                   zorder=5)
        ax.annotate(f"R{r}", (p[0], p[1]), textcoords="offset points",
                    xytext=(8, 8), fontsize=9)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal")
    ax.set_title("Khronos PLACES vs ANYmal sparsified waypoints (spatial coincidence)")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_route(spars_nodes, sem, route, out):
    fig, ax = plt.subplots(figsize=(12, 10))
    posmap = {n["id"]: n["pose_stamped"]["position"] for n in spars_nodes}
    # 全 waypoint 图
    for e in json.load(open(SPARS, encoding="utf-8"))["edges"]:
        a, b = posmap[e["start_node_id"]], posmap[e["end_node_id"]]
        ax.plot([a[0], b[0]], [a[1], b[1]], c="blue", lw=0.4, alpha=0.2)
    for r, w in sorted({v["room_index"]: v["waypoint_id"]
                        for v in sem["room_to_waypoint"].values()
                        if v.get("status") == "ok"}.items()):
        p = posmap[w]
        ax.scatter(p[0], p[1], s=100, c="red", marker="*", edgecolors="k", zorder=5)
        ax.annotate(f"R{r}", (p[0], p[1]), textcoords="offset points",
                    xytext=(8, 8), fontsize=9)
    # route 高亮
    rpts = [posmap[w] for w in route]
    ax.plot([p[0] for p in rpts], [p[1] for p in rpts], c="green", lw=2.5,
            label=f"route ({len(route)} waypoints)")
    ax.scatter([rpts[0][0]], [rpts[0][1]], s=160, c="green", marker="o",
               edgecolors="k", label="start", zorder=6)
    ax.scatter([rpts[-1][0]], [rpts[-1][1]], s=160, c="darkgreen", marker="D",
               edgecolors="k", label="goal", zorder=6)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal")
    ax.set_title("Example route: R0 -> R12")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    full_nodes = load_nodes(FULL)
    spars_nodes = load_nodes(SPARS)
    sem = json.load(open(SEM, encoding="utf-8"))

    coincidence = verify_coincidence(full_nodes, spars_nodes)
    print(f"空间重合率: {coincidence * 100:.1f}% "
          f"({len(spars_nodes)} waypoints 都在 Khronos PLACES 位置)")

    plot_places_vs_waypoints(full_nodes, spars_nodes, sem, OUT_DIR / "places_vs_waypoints.png")
    print("已生成:", OUT_DIR / "places_vs_waypoints.png")

    # route 示例：R0 → R12
    from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402
    sys.path.insert(0, str(BASE))
    g = WaypointGraph.load(str(SPARS))
    anchors = {v["room_index"]: v["waypoint_id"] for v in sem["room_to_waypoint"].values()
               if v.get("status") == "ok"}
    route, _ = shortest_path(g, anchors[0], anchors[12])
    plot_route(spars_nodes, sem, route, OUT_DIR / "route_viz.png")
    print("已生成:", OUT_DIR / "route_viz.png")
    print(f"route R0→R12: {' → '.join(str(w) for w in route)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
