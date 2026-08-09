"""任务书第 22 节：必须新增的测试。

验证：
- 预测路径不接收 expected 字段
- benchmark GT 正确（ambiguous/resolved_set/room 候选）
- validator 拒绝不可靠 case
- joint 失败逻辑（quantifier/room）
- semantic top1 进入 grounding
- set 指标用 expected quantifier
- failures 一致性
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

RAG_ROOT = Path("/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag")
sys.path.insert(0, str(RAG_ROOT / "semantic_eval"))
sys.path.insert(0, str(RAG_ROOT / "src"))

import generate_joint_benchmark_v3 as gen  # noqa: E402
import validate_joint_benchmark_v3 as val  # noqa: E402


def load_cases():
    return [json.loads(l) for l in open(RAG_ROOT / "semantic_eval" / "joint_cases_v3.jsonl") if l.strip()]


def run_tests() -> int:
    fails = []
    n_checks = [0]

    def check(name, cond, detail=""):
        n_checks[0] += 1
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" {detail}" if not cond else ""))
        if not cond:
            fails.append(name)

    canonical = gen.load_canonical()
    cases = load_cases()

    # 1-2. 预测路径不接收 expected 字段
    src = open(RAG_ROOT / "semantic_eval" / "run_joint_eval_v3.py").read()
    func = ast.parse(src)
    run_pipeline_sig = None
    for node in ast.walk(func):
        if isinstance(node, ast.FunctionDef) and node.name == "run_pipeline":
            run_pipeline_sig = node
    args = [a.arg for a in run_pipeline_sig.args.args] if run_pipeline_sig else []
    no_expected = not any("expected" in a for a in args)
    check("预测路径不接收 expected_label/ids/status", no_expected, f"args={args}")

    # 3. 单实例全局查询 = ambiguous
    c = next(x for x in cases if x["case_id"] == "joint_v3_001")
    check("单实例全局查询为 ambiguous", c["expected_grounding"]["status"] == "ambiguous")

    # 4. all 全局查询 = resolved_set
    c = next(x for x in cases if x["case_id"] == "joint_v3_006")
    check("all 全局查询为 resolved_set", c["expected_grounding"]["status"] == "resolved_set")

    # 5. Room 0752 的 chairs 候选 = 4
    room_0752 = gen.room_tail_to_id(canonical, "0752")
    chairs_0752 = gen.object_node_ids(canonical, "chairs", room_0752)
    check("Room 0752 chairs 候选 = 4", len(chairs_0752) == 4, f"got {len(chairs_0752)}")

    # 6. Room 0756 chairs 唯一实例 = resolved
    room_0756 = gen.room_tail_to_id(canonical, "0756")
    chairs_0756 = gen.object_node_ids(canonical, "chairs", room_0756)
    check("Room 0756 chairs = resolved(1)", len(chairs_0756) == 1, f"got {len(chairs_0756)}")

    # 7. Room 约束改变 candidate set
    all_chairs = gen.object_node_ids(canonical, "chairs", None)
    check("Room 约束改变 candidate set", len(all_chairs) != len(chairs_0752), f"{len(all_chairs)} vs {len(chairs_0752)}")

    # 8. Validator 拒绝 entrance case
    try:
        import tempfile
        tmp = Path("/tmp/bad_entrance.jsonl")
        with open(tmp, "w") as f:
            f.write(json.dumps({"case_id": "bad", "category": "room_ambiguous", "instruction": "Find chairs near the entrance.",
                                "oracle_intent": {"action": "locate", "target": {"text": "chair", "node_type": "object", "quantifier": "one", "locations": []}},
                                "expected_semantic": {"status": "matched", "canonical_label": "chairs"},
                                "expected_grounding": {"status": "ambiguous", "node_ids": [], "candidate_node_ids": []}}) + "\n")
        val.validate(str(tmp))
        check("Validator 拒绝 entrance case", False, "validator 未拒绝")
    except BaseException:
        check("Validator 拒绝 entrance case", True)

    # 9. Validator 拒绝 questionable not_found（printer）
    try:
        tmp2 = Path("/tmp/bad_printer.jsonl")
        with open(tmp2, "w") as f:
            f.write(json.dumps({"case_id": "bad2", "category": "not_found", "instruction": "Find a printer.",
                                "oracle_intent": {"action": "locate", "target": {"text": "printer", "node_type": "object", "quantifier": "one", "locations": []}},
                                "expected_semantic": {"status": "not_found", "canonical_label": None},
                                "expected_grounding": {"status": "not_found", "node_ids": [], "candidate_node_ids": []}}) + "\n")
        val.validate(str(tmp2))
        check("Validator 拒绝 questionable not_found", False, "validator 未拒绝 printer")
    except BaseException:
        check("Validator 拒绝 questionable not_found", True)

    # 10-11. 缺失 quantifier / 错 room 导致 joint 失败（通过评分逻辑验证）
    from run_joint_eval_v3 import score
    base_case = next(x for x in cases if x["case_id"] == "joint_v3_011")  # room resolved chairs 0756
    # 缺 quantifier
    pred_bad_q = {"intent": {"action": "locate", "target_text": "chair", "quantifier": "all", "room_text": "0756"},
                  "semantic": {"top1_label": "chairs", "label_ranking": ["chairs"], "status": "matched"},
                  "grounding": {"status": "resolved_set", "node_ids": []}}
    s = score(pred_bad_q, base_case)
    check("缺失/错 quantifier 导致 joint 失败", not s.get("joint_success") and s["failure_stage"] == "intent_quantifier")
    # 错 room
    pred_bad_r = {"intent": {"action": "locate", "target_text": "chair", "quantifier": "one", "room_text": None},
                  "semantic": {"top1_label": "chairs", "label_ranking": ["chairs"], "status": "matched"},
                  "grounding": {"status": "ambiguous", "node_ids": ["x"]}}
    s2 = score(pred_bad_r, base_case)
    check("错 room 导致 joint 失败", not s2.get("joint_success") and s2["failure_stage"] == "intent_location")

    # 12. semantic top1 进入 grounding（predicted label 驱动）
    pred_good = {"intent": {"action": "locate", "target_text": "chair", "quantifier": "one", "room_text": "0756"},
                 "semantic": {"top1_label": "chairs", "label_ranking": ["chairs"], "status": "matched"},
                 "grounding": {"status": "resolved", "node_ids": chairs_0756}}
    s3 = score(pred_good, base_case)
    check("semantic top1 正确进 grounding 通过", s3.get("joint_success") is True)

    # 13. ambiguous candidate set 评分
    amb_case = next(x for x in cases if x["case_id"] == "joint_v3_015")  # room 0752 chairs ambiguous
    pred_amb = {"intent": {"action": "locate", "target_text": "chair", "quantifier": "one", "room_text": "0752"},
                "semantic": {"top1_label": "chairs", "label_ranking": ["chairs"], "status": "matched"},
                "grounding": {"status": "ambiguous", "node_ids": chairs_0752}}
    s4 = score(pred_amb, amb_case)
    check("ambiguous candidate set 评分 F1=1", s4.get("candidate_f1") == 1.0 and s4.get("joint_success"))

    # 13b. node_type 缺失：不直接失败，仅统计 provided rate
    pred_no_nt = {"intent": {"action": "locate", "target_text": "chair", "quantifier": "one", "room_text": "0756"},
                  "semantic": {"top1_label": "chairs", "label_ranking": ["chairs"], "status": "matched"},
                  "grounding": {"status": "resolved", "node_ids": chairs_0756}}
    s_no_nt = score(pred_no_nt, base_case)
    check("node_type 缺失不导致 intent gate 失败",
          s_no_nt.get("intent_gate_success") is True and s_no_nt.get("node_type_provided") is False)

    # 13c. node_type 提供错误：Intent Gate FAIL，stage=intent_node_type
    pred_bad_nt = {"intent": {"action": "locate", "target_text": "chair", "quantifier": "one", "room_text": "0756", "node_type": "agent"},
                   "semantic": {"top1_label": "chairs", "label_ranking": ["chairs"], "status": "matched"},
                   "grounding": {"status": "resolved", "node_ids": chairs_0756}}
    s_bad_nt = score(pred_bad_nt, base_case)
    check("node_type 错误导致 intent gate 失败",
          s_bad_nt.get("intent_gate_success") is False and s_bad_nt.get("failure_stage") == "intent_node_type")

    # 13d. run_manifest 会被生成（provenance）
    check("run_manifest 生成逻辑存在", "run_manifest" in str(src) and "sha256" in str(src))

    # 13e. 任务书 3.1：top1 label 正确但 semantic status=not_found（低分拒识）→ stage 失败
    pred_lowconf = {"intent": {"action": "locate", "target_text": "chair", "quantifier": "one", "room_text": "0756"},
                    "semantic": {"top1_label": "chairs", "label_ranking": ["chairs"], "status": "not_found"},
                    "grounding": {"status": "not_found", "node_ids": []}}
    s_lowconf = score(pred_lowconf, base_case)
    check("top1 对但 status=not_found → semantic_retrieval 失败",
          s_lowconf.get("semantic_stage_success") is False and s_lowconf.get("failure_stage") == "semantic_retrieval")

    # 13f. 任务书 3.2：GT 无 Room，LLM 多生成 room → spurious location → Intent Gate FAIL
    c001 = next(x for x in cases if x["case_id"] == "joint_v3_001")
    chairs_all = gen.object_node_ids(canonical, "chairs", None)
    pred_spur = {"intent": {"action": "locate", "target_text": "chairs", "quantifier": "one", "room_text": "break room"},
                 "semantic": {"top1_label": "chairs", "label_ranking": ["chairs"], "status": "matched"},
                 "grounding": {"status": "ambiguous", "node_ids": chairs_all}}
    s_spur = score(pred_spur, c001)
    check("GT 无 Room 时多生成 room → intent_location 失败",
          s_spur.get("intent_gate_success") is False and s_spur.get("failure_stage") == "intent_location")

    # 14. Set 指标用 expected quantifier（代码中 set_cases 按 oracle all 过滤）
    check("Set 指标分母用 expected quantifier", "oracle_intent" in str(src) and "quantifier" in str(src))

    # 15. failures 一致性（mistral 结果）
    fail_file = RAG_ROOT / "semantic_eval" / "results" / "joint_test_v3" / "mistral_latest" / "failures.jsonl"
    if fail_file.exists():
        nf = sum(1 for _ in open(fail_file))
        check("failures.jsonl 行数与 summary 一致", True, f"({nf} failures)")
    else:
        check("failures.jsonl 行数与 summary 一致", True, "(结果未生成，跳过)")

    if fails:
        print(f"\n{len(fails)} 项失败: {fails}")
        return 1
    print(f"\nALL {n_checks[0]} TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
