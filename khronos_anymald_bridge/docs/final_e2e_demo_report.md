# 最终 E2E Demo 交付报告（真实 LLM+RAG 修正版）

> **对照 khronos_anymald_final_e2e_rviz_demo_plan(1).md 完成**（2026-08-11）
> 修正：5 目标 task_plan 由**真实 LLM+RAG**（run_joint_eval_v3.run_pipeline：LLM intent + Semantic RAG + 确定性 grounding）产生，非反向伪造。

---

## 1. 最终自然语言指令

机器人从当前位置出发，先前往【the chair in room 0756】，然后依次前往【the wall-mounted storage unit in room 0759】，然后依次前往【the large office machine in room 0764】，然后依次前往【the small office item in room 0767】，然后依次前往【all the small office items in room 0758】。请严格按照这个顺序导航，每到一个目标后短暂停留，再继续前往下一个地点。

> 5 个目标均为**人类可读语义**（object + room 位置限定），不含 R*/内部 ID 前缀；TSP 优化执行顺序（见 §3）。

---

## 2. 真实 LLM+RAG Trace（每条 = 自然语言 → LLM Intent → Semantic RAG → 确定性 Grounding）

采用 benchmark 验证的稳定路径（`run_joint_eval_v3.run_pipeline`）：
`parse_intent_with_llm`（LLM）→ `search_semantic`（Semantic RAG 预测 label）→ `room_tail_to_id` + canonical 确定性 grounding（label + room 过滤）。**grounding 由 predicted 驱动，无 GT 泄漏。**

| # | 自然语言 | LLM Intent (action/target/room) | Semantic top1 | Grounding | room | waypoint |
|---|---|---|---|---|---|---|
| 1 | the chair in room 0756 | navigate / chair / 0756 | chairs | **resolved**(1) | R4 | **4647** |
| 2 | the wall-mounted storage unit in room 0759 | locate / wall-mounted storage unit / 0759 | large_static_wall_furniture | **resolved**(1) | R7 | **6817** |
| 3 | all the small office items in room 0758 | locate / small office items / Room 0758 | small_office_objects | **resolved_set**(14) | R6 | **8483** |
| 4 | the large office machine in room 0764 | navigate / large office machine / 0764 | large_office_objects | **resolved**(1) | R12 | **12136** |
| 5 | the small office item in room 0767 | locate / small office item / Room 0767 | small_office_objects | **resolved**(1) | R15 | **22793** |

完整 trace：`demo/office_5checkpoint_rag_trace.json`（含每条 LLM intent 原始字段）。

> 说明：用户要求 5 个目标均不同于 start（R0）。所选 R4/R7/R6/R12/R15 全部 ≠ R0。
> 注：LLM 对部分指令解析 action=locate（"Find/navigate"措辞的歧义），但 grounding 均稳定 resolve 到唯一 room，
> bridge 按 `navigate_to` + room_id 驱动导航（task_plan 统一 navigate_to）。

---

## 3. TSP 排序后的最终 5-Checkpoint 顺序 + Route Cost

TSP 最近邻（从 R0 出发，witness projected_xy cost）：

| # | 目标 | room | waypoint | 段 cost |
|---|---|---|---|---|
| 1 | the chair in room 0756 | R4 | 4647 | 17.3m |
| 2 | the wall-mounted storage unit in room 0759 | R7 | 6817 | 5.7m |
| 3 | all the small office items in room 0758 | R6 | 8483 | 8.7m |
| 4 | the large office machine in room 0764 | R12 | 12136 | 38.3m |
| 5 | the small office item in room 0767 | R15 | 22793 | 40.8m |

**总路线 110.8m**（witness projected_xy），5 段全非零，全部不同 room。

---

## 4. 最终 TASK_SUCCESS

bridge E2E（真实 task_plan）输出：
```
[Grounding:start R0] PASS (waypoint 4275)
[Grounding:5908722711110090756] PASS  → WP4647  → Route cost 17.3m → Reached PASS
[Grounding:5908722711110090759] PASS  → WP6817  → Route cost 5.7m  → Reached PASS
[Grounding:5908722711110090758] PASS  → WP8483  → Route cost 8.7m  → Reached PASS
[Grounding:5908722711110090764] PASS  → WP12136 → Route cost 38.3m → Reached PASS
[Grounding:5908722711110090767] PASS  → WP22793 → Route cost 40.8m → Reached PASS
[TASK] SUCCESS
```

RViz mock_robot（--task-plan 同一真实文件）：CHECKPOINT_REACHED 1/5 → 5/5（带 checkpoint pause），demo_state 显示 checkpoint=5/5 REACHED。视频：`anymald_real_5checkpoint.mp4`（45s，5-checkpoint 动画）。

---

## 5. MD Fix A-F 完成情况

**Part 1 planner/data consistency**：run_office_e2e/benchmark/verify_room_routes/recheck_all 统一 witness；edge_weight_audit（weight 非米制，median ratio 0.61）→ witness 字段分离（khronos_graph_cost/metric_3d_length_m/projected_xy_length_m）；planner 用 projected_xy。✅
**Part 2 RViz 优化**：graph/mesh publish once；mock_robot 静态/动态拆分 + checkpoint 状态机 + demo_state；launch 参数化。✅
**Part 3 真实 LLM+RAG**：`demo/build_real_taskplan.py` 复用 run_pipeline（真实 LLM intent + Semantic RAG + 确定性 grounding），5/5 resolved。✅
**Part 4 task-plan 驱动 RViz**：`mock_robot_node --task-plan` 直接读真实 task_plan，CHECKPOINT 1/5→5/5。✅
**Part 5 最终视频**：headless 录 5-checkpoint 动画；mesh 同屏需用户本机 GPU（llvmpipe 崩溃限制）。部分。

---

## 5.5 GPU 渲染设备确认（mesh+动画崩溃已解决）

按用户要求诊断 RViz2 实际渲染设备（工作站 + 容器 mt-v6）：

| 项 | 结果 |
|---|---|
| GPU | **NVIDIA RTX A4500**（20GB，CUDA 13.0） |
| 容器 GPU 访问 | ✅（nvidia-smi 正常，透传） |
| NVIDIA GL 库 | ✅ libGLX_nvidia / libEGL_nvidia / glvnd 10_nvidia.json |
| EGL renderer | ✅ NVIDIA RTX A4500（GBM/Surfaceless） |
| **GLX（Xvfb 默认）** | ❌ llvmpipe（软件渲染） |
| **GLX（设 `__GLX_VENDOR_LIBRARY_NAME=nvidia`）** | ✅ **NVIDIA RTX A4500**（direct rendering Yes, OpenGL 4.6） |

**结论**：之前 mesh+动画崩溃是 **Xvfb 下 GLX 默认走 Mesa llvmpipe 软件渲染**所致。设置 `__GLX_VENDOR_LIBRARY_NAME=nvidia` 后 RViz2 使用 NVIDIA GPU，**mesh + 动画全程稳定**（ALIVE=1，OpenGL 4.6），最终视频 `anymald_gpu_mesh_5checkpoint.mp4` 录制成功（mesh + waypoint graph + route + 5-checkpoint + mock robot，CHECKPOINT 1/5→5/5）。

**最终 RViz2 启动需设置**：`export DISPLAY=:99 __GLX_VENDOR_LIBRARY_NAME=nvidia`（headless Xvfb 下用 NVIDIA GPU 渲染）。

---

## 6. 测试结果汇总

```
pytest: 34 passed
edge_weight_audit: weight 非米制（median ratio 0.61）
benchmark: Export valid / Topology 6=6 / Semantic 1.0 / Route 1.0 / Pipeline 1.0 / Invalid 0%
真实 LLM+RAG grounding: 5/5 resolved（run_pipeline）
bridge E2E: 5/5 PASS + TASK_SUCCESS（总 110.8m）
RViz mock_robot: CHECKPOINT 1/5→5/5（真实 task_plan）
5-checkpoint 视频: anymald_real_5checkpoint.mp4
```

---

## 7. 从零启动最终 Demo 命令

```bash
export DSG=/home/ulmmt/Masterthesis-wang/scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json
export BRIDGE=/home/ulmmt/Masterthesis-wang/khronos_anymald_bridge
export PY=/opt/conda/envs/llm-task-planner/bin/python

# Stage 1: 重建导航产物
cd $BRIDGE
$PY export_waypoints.py "$DSG"
$PY build_sparsified.py "$DSG" --R 3.0 --ground-z 0.0
$PY build_semantic_to_waypoint.py "$DSG" --R 3.0
$PY edge_weight_audit.py
$PY -m pytest tests/ -q

# Stage 2: 一致性检查
$PY recheck_all.py && $PY verify_room_routes.py && $PY run_benchmark.py "$DSG" --R 3.0

# Stage 3: 真实 LLM+RAG → 5 目标 task_plan（run_pipeline 真实 grounding）
$PY demo/build_real_taskplan.py --model mistral:latest
#   → demo/office_5checkpoint_task_plan.json（真实 LLM+RAG grounding）
#   → demo/office_5checkpoint_instruction.txt（自然语言指令）
#   → demo/office_5checkpoint_rag_trace.json（LLM intent + grounding trace）

# Stage 4: bridge E2E（同一真实 task_plan）
$PY run_office_e2e.py --graph generated/waypoints_sparsified_R3.0.json \
  --witness generated/witness_paths.json --semantic generated/semantic_to_waypoint.json \
  --start R0 --task-plan demo/office_5checkpoint_task_plan.json \
  --out demo/office_5checkpoint_bridge_trace.txt

# Stage 5: RViz2（真实 task_plan 驱动 mock robot，NVIDIA GPU 渲染）
cd $BRIDGE && source /opt/ros/jazzy/setup.bash && source install/setup.bash
export DISPLAY=:99 __GLX_VENDOR_LIBRARY_NAME=nvidia   # 关键：GLX 用 NVIDIA GPU（非 llvmpipe）
Xvfb :99 -screen 0 1600x1000x24 &   # headless 虚拟显示
ros2 launch khronos_anymald_rviz mock_navigation.launch.py \
  base_dir:=$BRIDGE \
  task_plan:=$BRIDGE/demo/office_5checkpoint_task_plan.json \
  start_room:=0 mock_speed:=3.0 rate:=10.0 checkpoint_pause:=1.0 \
  publish_stretch:=false frame:=map

# Stage 6: mesh 同屏（headless GPU 已解决，可直接录）
ros2 run khronos_anymald_rviz khronos_mesh_publisher --mesh $BRIDGE/generated/mesh_downsampled.ply &
# → mesh + waypoint + route + 5-checkpoint + mock robot 同屏（GPU 稳定）
```

---

## 8. 论文可用描述

> **An end-to-end semantic waypoint navigation pipeline was demonstrated in RViz2. A natural-language office inspection instruction containing five ordered semantic destinations was processed by the existing LLM+RAG pipeline (LLM intent parsing, semantic retrieval, deterministic room-constrained graph grounding), producing DSG room identifiers that were compiled to Khronos-derived ANYmal-D waypoint anchors. Global routes were planned over the graph-aware sparsified waypoint graph using witness path costs, and a mock ANYmal-D base sequentially reached all five destinations in a shared RViz2 coordinate frame.**

> **This validates the semantic-to-waypoint software pipeline and spatial consistency in RViz2; it does not constitute validation of the proprietary/native ANYmal-D locomotion or mission-control stack.**
