# Hospital P1000：从自然语言到 Nav2 规划

这是论文最终实现与评测归档。最短运行链为：

自然语言指令 → LLM 意图解析 → Khronos-RAG 语义锚定 → 有序 mission → ROS 2/Nav2 路径规划 → 可追溯 JSON。

默认自然语言入口是软件级 plan-only：结果中的 planned 表示 Nav2 对任务链中的停靠点找到了可行路径，execution_performed 为 false；它不会证明真实 ANYmal 行走、楼梯/电梯通行或实体安全。跨楼层使用预配置抽象通道、地图加载和位姿重置。真实 Nav2 mission runner 是另一个接口，只有在隔离的模拟 ROS domain 中才可执行。

## 模块与说明

| 模块 | 职责 | 详细说明 |
|---|---|---|
| llm-rag/ | 唯一 LLM-RAG 规划器与 Khronos DSG 索引构建器 | llm-rag/README.md、llm-rag/BUILD_ENVIRONMENT.md |
| khronos/ | Khronos 源码与 TartanGround 输入适配工具 | khronos/README.md |
| khronos-nav2-bridge/ | Hospital P1000 ROS 2/Nav2 package | khronos-nav2-bridge/README.md |
| experiment/ | 论文数据、评测结果、地图与演示归档 | experiment/README.md |

仓库根目录只保留这一个 README。模块内 README 解释各自的构建、依赖和接口。

按干净环境重建 Python/ROS 环境、验证测试、构建 RAG 索引并运行 Nav2 plan-only 的逐步操作见 [代码复现流程](experiment/CODE_REPRODUCTION.md)。该指南会区分可重跑的流程与仅保存历史结果的实验归档。

## 环境依赖

- 推荐环境：WSL 2 / Ubuntu 24.04、Python 3.12、ROS 2 Jazzy、colcon、rosdep。
- Python 直接依赖由 [llm-rag/requirements.txt](llm-rag/requirements.txt) 固定；Nav2 ROS 依赖由 khronos-nav2-bridge/khronos_nav2_bridge/package.xml 声明并通过 rosdep 安装。
- 本地模型服务使用 Ollama，默认模型为 gemma4:e2b-it-qat。
- 原始 Hospital P1000 DSG、mesh 和人工审阅 CSV（约 27 GB 数据集）是本地输入，不在 Git 中。构建 RAG 索引时必须提供自己的路径。
- 完整依赖来源、版本约束、安装顺序和验证命令见 llm-rag/BUILD_ENVIRONMENT.md。

以下命令均假定当前工作目录是仓库根目录。先安装 ROS 2 Jazzy，再准备 Python 环境：

~~~bash
python3 -m venv .venv
.venv/bin/pip install -r llm-rag/requirements.txt
ollama pull gemma4:e2b-it-qat
~~~

让 rosdep 安装 bridge 声明的依赖并编译 Hospital package：

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep update
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y
cd khronos-nav2-bridge
colcon build --packages-select khronos_nav2_bridge --symlink-install
source install/setup.bash
cd ..
~~~

PATH 设置选择 ROS Jazzy 的系统 Python，避免用户 Conda Python 覆盖 ROS Python。

## 构建 Hospital RAG 索引

builder 只读取 DSG 与审阅 CSV，不修改原始输入。输出目录必须不存在；下例使用版本化目录 v5，和自然语言规划器默认的 --db-dir 相同。

~~~bash
.venv/bin/python llm-rag/RAG/khronos_3dsg_rag/src/main.py \
  --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible \
  --dataset hospital-p1000 \
  --input /path/to/hospital/full-data/dsg.json \
  --review-csv /path/to/hospital-object-review-2026-10-12.csv \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu \
  --output llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5
~~~

成功后该目录包含 canonical graph、RAG documents、vector_db 和 build manifest。若使用自定义输出路径，调用规划器时必须把该目录下的 vector_db 显式传给 --db-dir。现有目录不会被覆盖；要重建时请指定新的版本化输出目录。

## 启动完整自然语言到 Nav2 流程

终端 1：启动隔离的 Hospital F0 Nav2 stack。先等待日志报告 Managed nodes are active；不要使用真实机器人 ROS domain。

~~~bash
source /opt/ros/jazzy/setup.bash
source khronos-nav2-bridge/install/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export ROS_DOMAIN_ID=77
ros2 launch khronos_nav2_bridge hospital_nav2.launch.py \
  map:=khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only/floor_0/map.yaml \
  params_file:=khronos-nav2-bridge/khronos_nav2_bridge/config/nav2_hospital_point.yaml
~~~

终端 2：提交一条自然语言指令。脚本默认读取 v5 RAG 索引，并且不会覆盖已有结果文件。

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export ROS_DOMAIN_ID=77
export HOSPITAL_NAV2_PYTHON="$PWD/.venv/bin/python"
bash llm-rag/plan_hospital_nav2.sh \
  --instruction "Please take me into the room with the bed." \
  --ros-domain-id 77 \
  --output /tmp/hospital-nav2-plan.json
~~~

主要参数：

- --model：覆盖 Ollama 模型。
- --db-dir：覆盖 RAG vector_db。
- --bundle：覆盖楼层地图及抽象通道配置。
- --task-chain-backend grounded_deterministic 或 llm2：默认确定性编译，llm2 供受控比较。
- --num-ctx、--num-predict、--request-timeout-s：覆盖单次 LLM 请求资源与超时。
- --robot-model point 或 footprint：选择 Nav2 footprint 配置。

查看全部选项：

~~~bash
.venv/bin/python llm-rag/planner/plan_nav2.py --help
~~~

输出 JSON 记录 instruction、模型与代码摘要、RAG/map provenance、目标候选、路径几何、阶段耗时和 execution_performed。候选目标不表示已到达；plan-only 的成功也不表示真实机器人运动。

## Nav2 mission 接口

低层 runner 消费一个冻结的 mission JSON、地图 bundle 和输出路径。默认模式可能发送 NavigateToPose；只验证路径时加 --plan-only。

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export ROS_DOMAIN_ID=77
/usr/bin/python3 khronos-nav2-bridge/khronos_nav2_bridge/scripts/run_task_chain_nav2.py \
  --mission experiment/nav2-49/missions/HC-001.mission.json \
  --bundle khronos-nav2-bridge/khronos_nav2_bridge/config/hospital_p1000_floor_transitions.yaml \
  --output /tmp/HC-001-result.json \
  --plan-only \
  --robot-model point
~~~

该命令使用 ROS Jazzy 系统 Python，以确保能导入 rclpy。输入是有序停靠点和候选目标；输出是包含状态、路径和阶段信息的单任务 JSON。实际导航只允许连接隔离的模拟 domain。

## 数据与论文分母

- experiment/task-chain-60/public/tasks_en.jsonl：60 条输入；论文选定的 surface-v1-llm2-60x4-20261020 运行包含 Full、No LLM1、Raw DSG、No LLM2 四个条件，共 240 个任务—条件输出。
- experiment/nav2-49/missions：从较早的 Full 预测筛出的 49 个可执行 mission；preparation_report.json 同时记录被排除的无效输出。tasks/ 保存 49 个首轮结果，retry-20261023/ 是六个故障任务的独立诊断重跑。
- 论文报告的 43/49 是原始首轮结果；六个重跑成功不替代首轮分数。任务链成功数、Nav2 任务分母和 HC-031 演示是不同证据，不能合并成一次端到端成功率。
- 其他任务链、语义 RAG、检索评测保存在 experiment/evaluations/；历史运行 manifest 保留原始绝对路径作为 provenance，不代表当前机器路径。

原始任务链、Nav2 mission 与结果目录是从本地论文实验档案复制的；完整 RAG 重建和历史实验重跑仍需对应的外部数据、模型与 ROS 环境。

## 测试与故障排查

Python、RAG、地图生成和接口契约测试：

~~~bash
.venv/bin/python -m pytest -q \
  llm-rag/planner/tests \
  llm-rag/RAG/khronos_3dsg_rag/tests \
  experiment/map-generation/test_generate_khronos_only.py \
  experiment/tests
~~~

ROS package 测试：

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
cd khronos-nav2-bridge
colcon test --packages-select khronos_nav2_bridge --event-handlers console_direct+
colcon test-result --verbose
~~~

- 找不到 RAG vector_db：先执行上面的 build 命令；自定义目录时用 --db-dir 指定。
- Ollama 连接失败：确认服务运行，并检查 http://127.0.0.1:11434。
- Nav2 action/service 不可用：先等 Managed nodes are active，并确认两个终端使用同一个隔离 ROS_DOMAIN_ID。
- 找不到原始 DSG、mesh 或审阅 CSV：这些输入不随 Git 提供，需要本机准备后显式传入路径。
