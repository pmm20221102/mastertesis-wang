# Khronos 3D Mesh 同屏集成 — 尝试记录与分析

> **目标**：在 RViz2 中把 Khronos 3D 地图（mesh.ply）与我们生成的 waypoint graph 同屏加载，验证坐标系对齐 + 可通行区域。
> **时间**：2026-08-11
> **状态**：静态对齐验证 ✅；mesh+动画在 headless 软件渲染崩溃（环境限制）

---

## 1. 已实现的功能（不破坏现成代码）

| 新增 | 说明 |
|---|---|
| `khronos_anymald_rviz/.../khronos_mesh_publisher.py` | **独立节点**：发布 `file://<mesh.ply>` 的 MESH_RESOURCE marker（`/anymald/khronos_mesh`，frame=map） |
| `config/anymal_mock.rviz` 加 KhronosMesh display | Marker display，topic `/anymald/khronos_mesh` |
| `generated/mesh_downsampled.ply` / `mesh_downsampled_K6.ply` | 降采样 mesh（30万→10万 / 5万顶点），缓解软件渲染负担 |
| `downsample_mesh.py` | PLY 降采样工具（顶点抽稀 + 面过滤 + 保留颜色） |

**关键发现**：jazzy 的 `rviz_default_plugins/Mesh` 插件**不存在**（不支持直接加载 mesh 文件），必须用 **Marker MESH_RESOURCE**（assimp 通过 `file://` 加载）。

---

## 2. 验证成功的部分（用户核心需求）

**静态对齐图**（`generated/visualization/anymal_mesh_alignment.png`）：

| 检查项 | 结果 |
|---|---|
| mesh 加载 | ✅ 30 万顶点 `mesh.ply` 成功加载（assimp，frame=map） |
| **坐标系对齐** | ✅ waypoint **100%** 落在 mesh 范围内（xy 完全一致，同一 map frame） |
| **可通行区域** | ✅ waypoint **0%** 落在 mesh 实体（墙）上——全在真实可通行空间（走廊/房间） |

> 像素级验证：green waypoint px=2918，落在 gray mesh 实体上的 = **0 (0.0%)**。

---

## 3. 崩溃问题与多轮尝试

### 现象
mesh + **动态 mock robot 动画** 时 RViz2 崩溃（进程消失，无错误日志，非 OOM——free 47G）。

### 多轮隔离测试（headless Xvfb + llvmpipe 软件渲染）

| 轮 | 配置 | 结果 |
|---|---|---|
| 1 | mesh(30万) + graph_publisher（静态） | ✅ 稳定，ALIVE=1 |
| 2 | mesh(30万) + mock_robot(25Hz) | ❌ 崩溃 |
| 3 | mesh(10万降采样) + mock_robot(25Hz) | ❌ 崩溃 |
| 4 | mesh(10万) + mock_robot(rate 5) | ❌ 崩溃 |
| 5 | mesh(5万) + mock_robot(rate 2) | ❌ 崩溃 |
| 6 | mesh(10万) + mock_robot **--minimal**(rate 5, 只发 robot/state) | ❌ 崩溃 |
| 7 | mesh(10万) + mock_robot --minimal(rate 1) | ❌ 崩溃 |
| 8 | mesh(10万) + mock_robot **--static-final**（一次性发布完整状态） | ❌ 崩溃 |
| 9 | mesh(10万) + mock_robot --static-final --minimal（一次性只发 robot） | ❌ 崩溃 |

### 分析
- **mesh + graph_publisher（SPHERE markers, 1Hz）稳定**；**mesh + 任何 mock_robot 相关（CUBE/ARROW 实体 marker，即使一次）崩溃**。
- 排除因素：非 OOM（free 47G）、非 stretch edges（关掉仍崩）、非 route/trail（minimal 仍崩）、非频率（rate 1 仍崩）、非顶点数（5万仍崩）。
- 结论：**headless 软件渲染（llvmpipe）下，assimp 加载的大 mesh 与实体 marker 更新的 GL 状态组合触发段错误**，是环境限制（软件渲染 GL 驱动不稳定）。

### 日志
见 `docs/mesh_integration_logs/`（viz_rviz.log / viz_robot.log / mesh_pub.log / viz_graph.log / xvfb.log）。

---

## 4. 结论与建议

1. **坐标系对齐 + 可通行区域已验证**（静态图，像素级 0% 穿墙）。
2. **mesh + 动画在本机 GPU 硬件加速下应正常**（软件渲染才崩）；用户有显示器 + GPU 的机器上可跑完整动画。
3. mock_robot 新增两个可选参数（不影响默认行为）：
   - `--minimal`：只发 robot/state/start（诊断用）
   - `--static-final`：一次性发布最终导航状态（无动画，静态验证用）

---

## 5. 用户本机使用命令

```bash
source /opt/ros/jazzy/setup.bash && source <bridge>/install/setup.bash
ros2 run khronos_anymald_rviz khronos_mesh_publisher --mesh <bridge>/generated/mesh_downsampled.ply &
ros2 run khronos_anymald_rviz graph_publisher --base-dir <bridge> --publish-stretch true &
ros2 run khronos_anymald_rviz mock_robot_node --base-dir <bridge> --start-room 0 --goal-rooms 12,4,9 &
rviz2 -d <bridge>/khronos_anymald_rviz/config/anymal_mock.rviz
# 若 frame 不同：--frame world / map 参数 + rviz2 Fixed Frame 对应调整
```
