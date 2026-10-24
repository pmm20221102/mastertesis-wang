#!/usr/bin/env python3
import ast, math, pathlib, sys
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'scripts'))
from kinematic_pose_source import PoseState, yaw_quaternion
s=PoseState(max_v=1,max_w=2,max_accel=2,timeout=.5); s.command(4,4,0); x,y,a,v,w=s.step(.1,.1); assert v<=.2+1e-9 and w<=.2+1e-9
for i in range(10): s.step(.1,.2+i*.1)
assert s.x>0 and abs(yaw_quaternion(a)[3])<=1 and all(math.isfinite(q) for q in yaw_quaternion(a))
s2=PoseState(max_accel=10,timeout=.5); s2.command(1,0,0); s2.step(.1,.1); before=s2.v; s2.step(.1,1.0); assert s2.v<before
s2.command(.5,.5,2.0); s2.step(.1,2.1); s2.reset(2.0,3.0,4.0,now=3.0)
assert s2.x==2.0 and s2.y==3.0 and -math.pi <= s2.yaw <= math.pi
assert s2.v==0.0 and s2.w==0.0 and s2.target_v==0.0 and s2.target_w==0.0 and s2.last_cmd==3.0
try: s2.reset(float('nan'),0,0); raise AssertionError('nonfinite reset accepted')
except ValueError: pass
try: s.command(float('nan'),0,2.0); raise AssertionError('nonfinite accepted')
except ValueError: pass
def has_base_link_assignment(source):
 return any(isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and n.value.value=='base_link' and any(isinstance(t,ast.Attribute) and t.attr=='child_frame_id' for t in n.targets) for n in ast.walk(ast.parse(source)))
raw=(pathlib.Path(__file__).parents[1]/'scripts/kinematic_pose_source.py').read_text(); assert '/cmd_vel' in raw and '/initialpose' in raw and '/odom' in raw and 'joint_states' in raw and has_base_link_assignment(raw)
assert has_base_link_assignment("t.child_frame_id='base_link'")
assert has_base_link_assignment('o.child_frame_id = "base_link"')
assert not has_base_link_assignment("t.child_frame_id='wrong'")
print('kinematic pose source: PASS')
