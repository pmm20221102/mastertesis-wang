"""ANYmal D waypoint graph 数据结构构造。

schema 依据用户提供的 waypoints_sample.json：
- node: {id, pose_stamped:{header:{frame,stamp}, orientation:[w,x,y,z], position:[x,y,z], tolerance:[,]} }
- edge: {id, start_node_id, end_node_id, data:{local_planner, locomotion_controller,
         max_deviation, traversability, perception_parameters, version}}
"""
import copy


def make_node(node_id: int, position, orientation=None, frame="map",
              stamp=0, tolerance=(0, 0)):
    o = orientation or {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "id": int(node_id),
        "pose_stamped": {
            "header": {"frame": frame, "stamp": stamp},
            "orientation": [float(o["w"]), float(o["x"]), float(o["y"]), float(o["z"])],
            "position": [float(v) for v in position[:3]],
            "tolerance": list(tolerance),
        },
    }


def make_edge(edge_id: int, start_node_id: int, end_node_id: int, policy: dict):
    return {
        "id": int(edge_id),
        "start_node_id": int(start_node_id),
        "end_node_id": int(end_node_id),
        "data": copy.deepcopy(policy),
    }
