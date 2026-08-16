# Error Reflection - Khronos 语义推理根因误判

**Date**: 2026-07-10 16:30
**Error Type**: Root cause misdiagnosis
**Severity**: High
**Scope**: Project-specific: Khronos

## Error Description

推断 Khronos 语义推理只产生 24 个节点的根因是"标签空间不匹配 / object_labels 太窄"，但实际上 open-set 路径根本不走 `object_labels` 过滤。

## Root Cause Analysis

- **Immediate cause**: 基于 memory 记录的结论（"connected_semantics.cpp:84-86 的 isObject() 过滤"）直接复述，没有重新验证
- **Contributing factors**:
  1. 没有区分 closed-set 和 open-set 两条路径的差异
  2. 没有读 `instance_forwarding.cpp` 的实际代码就下结论
  3. 依赖之前的 memory 记录，假设它是正确的
- **Systemic issue**: **"先猜测后验证"的反模式** — 应该先加诊断日志看数据，再定位原因

## What I Learned

### 技术发现
1. `open_set_node`（Python）发布 `FeatureImage`，label_image 是**实例 ID**（1, 2, 3...），不是语义类标签
2. `InstanceForwarding` 不调用 `isObject()`，它按实例 ID 分组，用 CLIP embedding 做背景过滤
3. 24 个节点是多层过滤叠加的结果：CLIP 背景过滤 → 像素数过滤 → 体积过滤 → tracker 观测次数 → object_extractor 提取
4. 两个独立问题需要分别诊断：(a) 语义推理帧率 vs rosbag 帧率（丢帧），(b) 各阶段过滤计数

### 方法论教训
- **不要复述 memory 里的结论，要验证** — memory 可能是上次会话的推断，不是事实
- **读代码比猜原因快** — 10 分钟读 `instance_forwarding.cpp` 推翻了之前的所有假设
- **加诊断日志是最低成本的验证方式** — 几行 `SLOG(INFO)` 就能获得真实数据

## Prevention Strategy

- **Before**: 分析问题前，先确认数据流路径（closed-set vs open-set），再读对应代码
- **During**: 加诊断日志看实际数据，不要基于假设推断
- **After**: 验证修复效果时，用数据对比（过滤前后计数），不要只看最终结果

## Action Items

- [ ] 在 `open_set_node` 加帧率统计（订阅帧数 vs 处理帧数 vs 丢帧率）
- [ ] 在 `instance_forwarding.cpp` 加各阶段计数日志
- [ ] 重新编译运行，用数据定位真正的问题
- [ ] 更新 MEMORY-khronos-thesis.md 中的错误结论

## Files/Commands Involved
- `instance_forwarding.cpp` — 需要加诊断日志
- `open_set_node` — 需要加帧率统计
- `ade20k_indoor_label_space.yaml` — 之前错误地认为需要修改此文件
