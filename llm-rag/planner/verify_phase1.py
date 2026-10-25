"""Phase 1 schema 严格化验证脚本。"""
from schema import PlanDraftStep

ok = True
# 1. 空 target_ref 可执行 action 应抛错（任务书 7.2）
try:
    PlanDraftStep(step=1, action="navigate_to", target_type="object", target_ref="", description="x", executor="nav2")
    print("FAIL: empty target_ref did not raise"); ok = False
except ValueError as e:
    print("PASS: empty target_ref ->", str(e)[:70])
# 2. wait 无需 target_ref
try:
    PlanDraftStep(step=1, action="wait", target_type="unknown", target_ref="", description="w", executor="none")
    print("PASS: wait no target_ref ok")
except ValueError as e:
    print("FAIL: wait raised", e); ok = False
# 3. 非法 target_ref 应抛错
try:
    PlanDraftStep(step=1, action="navigate_to", target_type="object", target_ref="badref", description="x", executor="nav2")
    print("FAIL: invalid target_ref did not raise"); ok = False
except ValueError as e:
    print("PASS: invalid target_ref ->", str(e)[:70])
# 4. 合法 target_ref 通过
s = PlanDraftStep(step=1, action="navigate_to", target_type="object", target_ref="target", description="x", executor="nav2")
print("PASS: valid target_ref ok:", s.target_ref)
# 5. role_refs 路径（escort）无需 target_ref
try:
    s2 = PlanDraftStep(step=1, action="escort_to", target_type="person", target_ref="",
                       role_refs={"actor": "actor", "destination": "destination"}, description="e", executor="nav2")
    print("PASS: role_refs ok")
except ValueError as e:
    print("FAIL: role_refs raised", e); ok = False
print("VERIFY_PHASE1", "PASS" if ok else "FAIL")
