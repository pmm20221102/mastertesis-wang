"""witness path 展开（纯 python，无 rclpy 依赖）。

route（waypoint id 序列）→ 沿 dense witness path 展开的坐标序列。
若 route 方向与 stored witness（min→max）相反，则 reverse witness path
（phase85_reaudit §2 Fix 2）。
"""


def expand_route(route, witness, spars_pos, dense_pos):
    """展开 route。witness: {'(a,b)': {'path': [a,...,b]}}。
    spars_pos/dense_pos: id -> (x,y,z)（z 已 ground 投影）。"""
    pts = [spars_pos(route[0])]
    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        w = witness.get(f"({min(a, b)},{max(a, b)})")
        if w and w.get("path"):
            path = w["path"]
            if a != path[0]:   # 反向 traversal：stored 是 min→max，需要 reverse
                path = list(reversed(path))
            for pid in path[1:]:
                p = dense_pos(pid)
                if p is not None:
                    pts.append(p)
        else:
            p = spars_pos(b)
            if p is not None:
                pts.append(p)
    return pts
