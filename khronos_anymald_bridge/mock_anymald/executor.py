"""Phase 6: Mock ANYmal waypoint executor（模拟执行 route 到 goal）。

状态机（计划文档）：
  IDLE / PLANNING / MOVING / REACHED / NO_PATH / INVALID_GOAL / CANCELLED

验证的是"waypoint graph 可执行、goal 可达"，不是 ANYmal 腿部物理控制。
"""
from __future__ import annotations

from waypoint_graph.planner import WaypointGraph, shortest_path


class MockExecutor:
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    MOVING = "MOVING"
    REACHED = "REACHED"
    NO_PATH = "NO_PATH"
    INVALID_GOAL = "INVALID_GOAL"
    CANCELLED = "CANCELLED"

    def __init__(self, graph: WaypointGraph):
        self.graph = graph
        self.state = self.IDLE
        self.current_waypoint: int | None = None
        self.route: list[int] = []
        self._cursor = 0
        self.goal: int | None = None

    def submit_goal(self, start: int, goal: int) -> str:
        """提交导航目标：从 start 规划到 goal。返回初始状态。"""
        self.goal = int(goal)
        if start not in self.graph.nodes or goal not in self.graph.nodes:
            self.state = self.INVALID_GOAL
            return self.state
        self.state = self.PLANNING
        self.route, _cost = shortest_path(self.graph, int(start), int(goal))
        if self.route is None:
            self.state = self.NO_PATH
            return self.state
        self.current_waypoint = int(start)
        self._cursor = 0
        self.state = self.MOVING
        return self.state

    def step(self) -> str:
        """沿 route 推进一个 waypoint。到达终点返回 REACHED。"""
        if self.state not in (self.MOVING, self.PLANNING):
            return self.state
        if self.route is None or not self.route:
            self.state = self.NO_PATH
            return self.state
        if self._cursor >= len(self.route) - 1:
            self.current_waypoint = self.route[-1]
            self.state = self.REACHED
            return self.state
        self._cursor += 1
        self.current_waypoint = self.route[self._cursor]
        self.state = self.MOVING
        return self.state

    def run(self, start: int, goal: int) -> dict:
        """执行完整 route。返回执行摘要。"""
        init = self.submit_goal(start, goal)
        if init in (self.INVALID_GOAL, self.NO_PATH):
            return {"state": init, "route": None, "start": start, "goal": goal}
        visited = [start]
        while self.state != self.REACHED:
            self.step()
            if self.current_waypoint not in visited:
                visited.append(self.current_waypoint)
        return {
            "state": self.REACHED,
            "route": visited,
            "n_waypoints": len(visited),
            "start": start,
            "goal": goal,
        }
