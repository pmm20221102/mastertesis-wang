# Khronos Hospital 当前代码/日志问题分析与 Agent 执行指南

> 目的：基于当前上传的 debug package，先把“574 nodes / 仅 objects 层”的真正来源追清楚，再修复 `dynamic_object_points` 保存问题。
>
> **当前禁止继续盲改 backend.cpp。**
>
> 当前最重要的结论：
>
> 1. 旧版 Hospital 1200-frame 运行已经明确成功：
>    - 1980 nodes
>    - 9407 edges
>    - 33 objects
>    - 1781 places
>    - 14 rooms
>    - 1 building
> 2. 当前归档中的 `backend.cpp` 与 `backend.cpp.bak` **SHA256 完全相同**。
> 3. 当前归档代码中 **不存在** `dynamic_object_points.clear()`。
> 4. 因此：
>    > 归档中所谓“修改 backend.cpp 后只剩 objects 层”的因果链目前并没有被代码证据支持。
> 5. 最新 `voxel010_full.log` / `voxel010_run3.log` 是**另一类失败运行**：
>    - 配置文件路径不存在
>    - node 在初始化阶段 SIGABRT
>    - 根本没有进入正常 mapping
>    - 不能把这次日志当作“574 nodes degraded run”的证据
> 6. 当前最大问题是：
>    > **不同运行、不同 executable、不同 output directory、不同配置和不同 DSG 文件被混在一起分析。**
>
> 先解决 provenance（运行来源可追踪性），再讨论 strip 修复。

---

# 1. 已确认的成功基线

## 1.1 Smoke 300 frames

旧报告：

```text
793 nodes
3571 edges
9 objects
727 places
9 rooms
1 building
```

结果：

```text
PASS
```

## 1.2 1200 frames / 120 s

旧报告：

```text
1980 nodes
9407 edges
33 objects
1781 places
14 rooms
1 building
```

输出：

```text
reports/khronos_output/2026_08_16-10_45_05/dsg.json
```

这个是当前最重要的 **known-good DSG baseline**。

后续任何新输出都必须和它对比。

---

# 2. 当前归档中的关键代码事实

归档：

```text
src_backend/backend.cpp
src_backend/backend.cpp.bak
```

SHA256：

```text
backend.cpp
6470b6075d9f83b1644e6eb8ab765260feaca4ce40812bb9a5ca8352d9a1e9a3

backend.cpp.bak
6470b6075d9f83b1644e6eb8ab765260feaca4ce40812bb9a5ca8352d9a1e9a3
```

两者完全相同。

并且：

```bash
grep -R "dynamic_object_points.clear" src_backend
```

无结果。

当前 `Backend::save()` 是：

```cpp
void Backend::save(const hydra::DataDirectory& log_setup) {
  const auto path = log_setup.path();
  unmerged_graph_->save(path / "shared_dsg.json", false);
  private_dsg_->graph->save(path / "dsg.json", false);

  const auto mesh = private_dsg_->graph->mesh();
  if (mesh && !mesh->empty()) {
    kimera_pgmo::WriteMesh(path / "mesh.ply", *mesh, *mesh);
  }

  deformation_graph_->save(path / "deformation_graph.dgrf");

  ...
}
```

也就是说：

> 当前归档源码中没有执行任何 lightweight strip。

因此不能继续说：

```text
backend strip modification
→ places/rooms disappeared
```

至少当前证据不支持。

---

# 3. 清空 dynamic_object_points 本身不应删除 Place / Room / Building 层

计划中的修改类似：

```cpp
for (const auto& [id, node] :
     private_dsg_->graph->getLayer(DsgLayers::OBJECTS).nodes()) {
  node->attributes<KhronosObjectAttributes>()
      .dynamic_object_points.clear();
}
```

这个操作只涉及：

```text
Layer 2 OBJECTS
```

中的 attribute field。

它本身不会：

```text
removeNode()
removeLayer()
clear PLACES
clear ROOMS
clear BUILDINGS
```

因此：

> 如果一个新 DSG 真的只有 Layer 2，那么不能第一反应就归因于 `dynamic_object_points.clear()`。

必须寻找真正导致 Layer 3/4/5 不存在的来源。

---

# 4. 最新 voxel010_full.log 是独立启动失败，不是 degraded DSG run

`voxel010_full.log` 显示：

```text
ERROR File '.../tartanground_input.yaml' does not exist
ERROR File '.../mapper_tartanground_01.yaml' does not exist
ERROR File '.../tartanground_hospital_label_space.yaml' does not exist
SIGABRT
exit code -6
```

根因非常明确：

> 启动命令在容器中使用了宿主机 `/home/ulmmt/...` 路径，但这些文件在该容器路径下不存在。

这次运行：

```text
没有正常初始化 Khronos
没有处理 bag
没有生成有效 mapping
```

所以：

**禁止把 `voxel010_full.log` 和 574-node degraded output 关联起来，除非能证明 output directory/timestamp 一致。**

---

# 5. ianvs warning 不是当前主因

日志还有：

```text
ModuleNotFoundError:
No module named 'ianvs._ianvs_bindings'
```

这是 launch extension warning。

之后真正导致 launch abort 的是：

```text
config files do not exist
```

因此当前优先级：

```text
Missing config path = fatal
ianvs warning = secondary
```

不要先去修 ianvs。

---

# 6. 当前最大嫌疑：你们分析了错误的 DSG 文件

当前所谓 degraded output：

```text
574 nodes
480 edges
1 layer
objects only
614 KB
```

这组数字非常异常。

尤其：

```text
574 object nodes
480 edges
```

相比旧数据：

```text
33 objects
1781 places
14 rooms
```

这不像：

```text
同一个完整 DSG 删除了 dynamic_object_points
```

更像：

1. 打开了一个只包含某个 layer 的中间文件
2. 打开了 frontend/private object graph
3. 打开了 incomplete save
4. 分析脚本把 JSON schema 解析错了
5. output directory 指向了另一轮测试
6. 新运行实际没加载完整 mapper config
7. 使用了错误 executable/install overlay

因此第一任务不是改算法。

第一任务是：

> **准确定位 574-node 文件。**

---

# 7. Agent 第一阶段：建立 degraded output provenance

必须找到：

```text
574 nodes / 480 edges / objects-only
```

对应的真实文件。

输出：

```text
reports/degraded_output_provenance.md
```

必须包含：

```text
absolute path
file name
file size
mtime
sha256
run timestamp
output directory
log file
config.txt
experiment_log.txt
executable path
binary sha256
source backend.cpp sha256
```

例如：

```bash
DEGRADED_DSG=/absolute/path/dsg.json

stat "$DEGRADED_DSG"
sha256sum "$DEGRADED_DSG"
```

找到父目录：

```bash
dirname "$DEGRADED_DSG"
```

然后：

```bash
find "$OUTPUT_DIR" -maxdepth 3 -type f -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' | sort
```

---

# 8. 不允许通过“最近的文件”猜是哪次运行

禁止：

```bash
ls -t | head
```

然后直接说：

```text
这个应该就是 degraded run
```

必须使用：

```text
timestamp
experiment_log
config.txt
shell log
output mtime
```

四者互相匹配。

如果不能匹配：

```text
RESULT=INCONCLUSIVE
```

不要继续推断。

---

# 9. Agent 第二阶段：分析所有候选 DSG 的真实 layer

必须对以下文件分别统计：

## Known good

```text
2026_08_16-10_45_05/dsg.json
```

## degraded candidate

实际 574-node 文件。

## 其他同 run 文件

如果存在：

```text
shared_dsg.json
backend/dsg.json
frontend/dsg.json
dsg_with_mesh.json
maps/dsg_*.json
```

全部分别检查。

输出表：

```text
file
size
nodes
edges
layer2_objects
layer3_places
layer4_rooms
layer5_buildings
mesh_attached
```

必须确认：

> 574 nodes 到底来自哪个 DSG 类型。

---

# 10. 不要用旧统计脚本盲信结果

Agent 必须先检查 JSON 的 layer keys。

例如 Python：

```python
import json

with open(path) as f:
    d = json.load(f)

print(d.keys())
```

然后打印：

```text
layer IDs
layer names
node counts by layer
```

如果 JSON 5GB 无法直接加载：

使用 streaming / grep / spark_dsg API。

不要因为分析脚本输出：

```text
layers = 1
```

就直接相信。

先确认脚本对 Khronos/Spark-DSG 1.1.3 schema 的解析方式正确。

---

# 11. Known-good vs degraded 配置必须做真正 diff

找到对应 run 的：

```text
config.txt
```

不要只对比手写 YAML。

执行：

```bash
diff -u \
  KNOWN_GOOD_OUTPUT/config.txt \
  DEGRADED_OUTPUT/config.txt \
  > reports/known_good_vs_degraded_config.diff
```

重点检查：

```text
enable_rooms
freespace_places
frontend configuration
backend type
semantic_layers
room_finder
input
label space
semantic remap
save flags
```

特别注意：

当前 config dump 中出现：

```text
surface_places [Uninitialized Virtual Config]
freespace_places [Uninitialized Virtual Config]
frontier_places [Uninitialized Virtual Config]
```

这本身不一定错误，因为具体 place generation config 可能在 frontend module 层。

但必须与 known-good config.txt 做 **逐行 diff**。

不能靠视觉猜。

---

# 12. 运行 executable provenance 必须记录

这是当前非常重要的一点。

机器上可能同时存在：

```text
/workspace/install
/workspace/src/khronos/install
其他 colcon install
```

所以：

```bash
which ros2
ros2 pkg prefix khronos
ros2 pkg prefix khronos_ros
```

以及：

```bash
readlink -f $(ros2 pkg prefix khronos_ros)/lib/khronos_ros/khronos_node
```

然后：

```bash
sha256sum /actual/path/khronos_node
```

记录：

```text
AMENT_PREFIX_PATH
CMAKE_PREFIX_PATH
LD_LIBRARY_PATH
```

必须回答：

> 574-node run 运行的到底是哪一个 build？

---

# 13. 确认 backend.cpp 修改是否真的进入 executable

不要只：

```text
修改源码
colcon build
```

然后假设运行的是新代码。

建议加一个临时、明确、只出现一次的日志：

```cpp
CLOG(1) << "[HOSPITAL_LIGHT_SAVE_V1] Backend::save() build marker";
```

重新编译。

运行 10–50 frames。

日志必须出现：

```text
[HOSPITAL_LIGHT_SAVE_V1]
```

如果没出现：

> 当前运行没有使用修改后的 binary。

这是当前必须建立的 build provenance。

确认后再删除 marker。

---

# 14. 不能直接在原 graph 上 destructive strip

之前建议：

```cpp
private_dsg_->graph->...dynamic_object_points.clear();
```

这虽然理论上不删除层，但属于：

```text
mutating live backend graph during save
```

工程上不够安全。

尤其 save 之后还有：

```text
map_.update(private_dsg_->graph->clone())
map_.save(...)
maps/*.json
pipeline clone
```

如果直接修改 live graph，会影响后续：

```text
4D map
snapshot
其他保存输出
```

因此新修复不要直接 clear live DSG。

---

# 15. 正确 lightweight save 设计

目标：

```text
保留 live graph 完整不变
↓
只在 serialization 时省略 dynamic_object_points
```

优先级：

## 最优

给 `KhronosObjectAttributes` JSON serializer 增加：

```text
save_dynamic_object_points = false
```

在 lightweight output 时跳过字段。

优点：

```text
不复制5GB
不修改live graph
不影响layers
不影响backend
```

## 次优

构造 lightweight attributes/node copy，只复制：

```text
id
semantic
position
bounding box
mesh summary
trajectory summary
```

不要复制：

```text
dynamic_object_points
```

## 不推荐

```cpp
auto copy = graph->clone();  // clone 5GB
clear points;
```

因为：

```text
先复制5GB
```

仍可能造成内存峰值。

---

# 16. 不要在 Backend::save() 一开始清空两个 live graph

禁止最终方案：

```cpp
for (...) {
  unmerged_graph_...dynamic_object_points.clear();
}

for (...) {
  private_dsg_->graph...dynamic_object_points.clear();
}
```

原因：

1. destructive
2. 会改变后续 map save 输入
3. 会改变 debug/archive state
4. 不易证明算法输出保持一致
5. 后续若需要 dynamic history 已丢失

---

# 17. 推荐新增独立 RAG 输出

最干净的架构：

```text
Khronos full in-memory DSG
        │
        ├── normal/full output（可选）
        │
        └── RAG lightweight exporter
                  ↓
         hospital_rag.sparkdsg
         hospital_rag.json
```

RAG lightweight DSG 保留：

```text
Objects
Places
Rooms
Buildings
Edges
semantic labels
positions
room containment
place topology
is_dynamic
optional trajectory summary
```

不保存：

```text
dynamic_object_points
raw point history
```

这样不会干扰 Khronos 官方保存路径。

---

# 18. 当前最关键的 A/B 不是 full 3178

使用 known-good 小数据：

```text
300 frames
```

先验证 lightweight exporter。

Baseline：

```text
793 nodes
3571 edges
9 objects
727 places
9 rooms
1 building
```

Lightweight 必须：

```text
793 nodes
3571 edges
9 objects
727 places
9 rooms
1 building
```

**必须完全一致。**

唯一允许变化：

```text
file size
dynamic_object_points field
```

---

# 19. A/B 的硬验收条件

输出：

```text
reports/lightweight_graph_equivalence.json
```

必须包含：

```json
{
  "baseline": {
    "nodes": 793,
    "edges": 3571,
    "objects": 9,
    "places": 727,
    "rooms": 9,
    "buildings": 1
  },
  "lightweight": {
    "nodes": 793,
    "edges": 3571,
    "objects": 9,
    "places": 727,
    "rooms": 9,
    "buildings": 1
  },
  "topology_equal": true,
  "semantic_labels_equal": true,
  "positions_equal": true,
  "dynamic_object_points_removed": true
}
```

如果任意：

```text
places
rooms
buildings
edges
```

变化：

```text
FAIL
```

立即停止，不跑 full P1000。

---

# 20. 先用 300-frame known-good，而不是500-frame未知基线

之前用了500-frame测试，但旧项目已经有非常明确的：

```text
300-frame smoke known-good
```

因此现在更合理：

```text
相同300 frames
相同config
相同bag
```

做：

```text
old binary / old output
vs
new lightweight save
```

这样因果最清楚。

---

# 21. 最新 missing-config launch 问题要独立修

当前失败命令使用：

```text
/home/ulmmt/Masterthesis-wang/...
```

但 node 在 container 中运行：

```text
/workspace/...
```

所以配置不存在。

Agent 必须统一路径策略。

推荐：

```text
容器内部所有配置都使用容器路径
```

例如：

```text
/workspace/tartanground_khronos/...
```

运行前：

```bash
test -f "$INPUT_CONFIG" || exit 10
test -f "$MAPPER_CONFIG" || exit 11
test -f "$LABELSPACE_CONFIG" || exit 12
test -f "$REMAP_CONFIG" || exit 13
```

配置不存在时：

```text
不要启动 Khronos
```

---

# 22. 不要再混用 launch 和 ros2 run 流程

Known-good 08-16 是：

```text
ros2 launch tartanground_khronos_bridge ...
```

后续很多实验变成：

```text
ros2 run khronos_ros khronos_node ...
```

两者参数合成方式可能不同。

当前恢复基线时：

> 首先使用 08-16 已验证成功的同一 launch 方式。

只有 baseline 再次复现后，才允许改成 `ros2 run` 简化流程。

---

# 23. 第一个回归任务：复现 300-frame baseline

不要先修 strip。

Agent 先：

```text
当前环境
当前容器
当前源码
当前bag
```

运行已知：

```text
hospital_p2000_smoke
300 frames
```

目标：

```text
793±合理小差异 nodes
Places存在
Rooms存在
Building存在
```

更严格：

如果 deterministic：

```text
793 / 3571 / 9 / 727 / 9 / 1
```

完全复现。

如果当前 baseline 本身只能输出 objects：

> 问题根本不是 strip save。

应该先定位 build/config/environment regression。

---

# 24. 如果当前 baseline 复现成功，再做 lightweight modification

顺序：

```text
A. current unmodified source + 300 frames
   → PASS

B. apply lightweight serialization ONLY
   → rebuild
   → verify build marker
   → same 300 frames
   → compare graph
```

每次只改一个变量。

---

# 25. 不允许直接从 1200/3178 full run调试

当前每次 full run：

```text
耗时长
内存高
保存重
```

且会让 Agent 再次混淆输出。

只有：

```text
300 baseline PASS
300 lightweight PASS
```

后：

```text
1200 frames
```

再：

```text
3178 frames
```

---

# 26. 对 574-node degraded output 的当前判断

当前不能下结论：

```text
strip dynamic_object_points broke DSG
```

当前更合理的候选排序：

1. **分析了错误 output 文件**
2. **run/output/log 时间线混淆**
3. **运行了错误 install overlay / stale binary**
4. **使用了不同启动方式 / config composition**
5. **config path错误导致某轮根本没正常运行**
6. graph analysis script 解析错文件/schema
7. lightweight modification副作用
8. Khronos算法 regression

先按 1→6 排除。

---

# 27. Agent 必须维护 RUN_MANIFEST

以后每次运行都生成：

```text
RUN_MANIFEST.json
```

内容：

```json
{
  "run_id": "",
  "date": "",
  "bag": "",
  "frame_count": 0,
  "launch_mode": "ros2_launch|ros2_run",
  "command": "",
  "output_dir": "",
  "log_file": "",
  "input_config": "",
  "mapper_config": "",
  "labelspace_config": "",
  "remap_config": "",
  "binary": "",
  "binary_sha256": "",
  "backend_cpp_sha256": "",
  "ament_prefix_path": "",
  "result": ""
}
```

没有 manifest 的 run：

> 不再作为正式实验结论依据。

---

# 28. 每次 output 自动生成 graph stats

结束后：

```text
graph_stats.json
```

包含：

```text
nodes
edges
objects
places
rooms
buildings
dynamic nodes
dynamic_object_points bytes
file size
```

这样不会再靠人工猜。

---

# 29. 推荐最终修复方向

当前目标不是修改 Khronos mapping。

最终应该新增：

```text
save_rag_lightweight_dsg
```

而不是 destructive 修改 live state。

建议行为：

```text
Full Khronos Graph
↓
RAG Serializer
↓
skip dynamic_object_points
↓
retain all graph layers
↓
hospital_rag.json / sparkdsg
```

---

# 30. 当前 Agent 严格执行顺序

## Phase 1 — Provenance

```text
1. 找到574-node文件
2. 确认absolute path
3. hash
4. parent output dir
5. matching log
6. matching config
7. matching binary
```

如果无法完整匹配：

```text
STOP = INCONCLUSIVE
```

---

## Phase 2 — Layer audit

分别分析：

```text
known-good 1200 dsg
574-node candidate
同run其他所有DSG文件
```

输出真实 layer stats。

---

## Phase 3 — Baseline reproduction

使用：

```text
300-frame smoke
known-good launch path
unmodified backend
```

确认 Places / Rooms / Building。

---

## Phase 4 — Build provenance

加入：

```text
[HOSPITAL_LIGHT_SAVE_V1]
```

marker。

确认修改源码被实际 binary 使用。

---

## Phase 5 — Lightweight save implementation

禁止 live destructive clear。

实现：

```text
serialization-time skip
或独立 RAG exporter
```

---

## Phase 6 — 300-frame equivalence A/B

要求 graph topology 完全不变。

---

## Phase 7 — 1200 frames

只有 Phase 6 PASS 才允许。

---

## Phase 8 — Full P1000

最后才运行 3178 frames。

---

# 31. 给 Agent 的最高优先级指令

> 当前不要再把“574 nodes only objects”归因于 `dynamic_object_points` strip。
>
> 当前归档源码里 strip 修改根本不存在，`backend.cpp` 与 `.bak` 完全相同。
>
> 同时最新 `voxel010_full.log` 是配置路径错误导致的启动 SIGABRT，和 degraded DSG 不是同一个问题。
>
> 现在第一任务是建立 degraded output provenance：
>
> ```text
> DSG文件
> ↔ output目录
> ↔ experiment log
> ↔ config
> ↔ command
> ↔ executable
> ↔ source hash
> ```
>
> 未建立完整对应关系前，禁止继续修改 Khronos 核心代码。
>
> 然后用 300-frame known-good baseline 重新复现四层 DSG，再做 lightweight serialization A/B。
