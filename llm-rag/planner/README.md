# LLM-RAG planner

这是仓库唯一的自然语言任务规划器。它依赖同一模块下的 Khronos 3DSG RAG builder/retriever（../RAG/khronos_3dsg_rag/），并与根目录的 Hospital Nav2 bridge 通过 mission JSON 和 Nav2 bundle 对接。

## 主入口

自然语言到 Nav2 的主入口是 plan_nav2.py；从仓库根目录推荐通过 wrapper 调用：

~~~bash
bash llm-rag/plan_hospital_nav2.sh \
  --instruction "Please take me to a room with a chair and a door." \
  --ros-domain-id 77 \
  --output /tmp/hospital-nav2-plan.json
~~~

运行前需先构建 RAG v5 index、启动隔离的 Hospital Nav2 stack，并设置 ROS 2 Jazzy 环境。完整操作顺序见根 README.md。

## 参数与输入输出

plan_nav2.py 输入：

- 必需：自然语言 --instruction 和新输出路径 --output。
- 默认：gemma4:e2b-it-qat、Hospital v5 vector_db、Hospital floor bundle 和 plan-only Nav2 adapter。
- 可覆盖：--model、--db-dir、--bundle、--defaults、--intent-prompt-profile、--task-chain-backend、--num-ctx、--num-predict、--request-timeout-s、--floor-tolerance-m、--robot-model、--ros-domain-id。

输出 JSON 记录 intent、实体候选、任务链/mission、RAG 与 map provenance、路径、分阶段状态和耗时。成功规划不会自动代表已执行导航；plan-only 时 execution_performed=false。

~~~bash
.venv/bin/python llm-rag/planner/plan_nav2.py --help
.venv/bin/python llm-rag/planner/main_rag.py --help
~~~

main_rag.py 提供较低层的 intent、grounding 和 task-chain 模块接口；主论文运行应使用 plan_nav2.py，把相同 RAG、mission 和 ROS domain 契约保持在一条总入口上。

## 配置和依赖

- Python 直接运行/测试依赖见仓库级 `llm-rag/requirements.txt`；`pyproject.toml` 保留包元数据和版本下限。当前从源码目录运行，不做 editable install。
- Ollama 地址、默认 instruction、模型表和 RAG index 路径见 config_rag.yaml。
- 默认数据库路径应与根 README 中的 v5 build 输出一致；自定义 build 输出需要显式 --db-dir。
- 可选 baseline 对照使用 experiment/evaluations/baseline/scene_graph.json；它是数据 fixture，不是第二套规划器实现。

环境依赖详见 ../BUILD_ENVIRONMENT.md。

## 测试

~~~bash
.venv/bin/python -m pytest -q llm-rag/planner/tests
~~~
