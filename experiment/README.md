# experiment：论文数据、评测与结果

这里集中保存数据集、冻结输入、评测脚本和运行结果。实验记录是证据档案，不会被代码重构自动重跑或改写。

从干净环境验证代码、构建带 provenance manifest 的 RAG 索引、启动 Nav2 并运行单条 plan-only 请求，请按 [CODE_REPRODUCTION.md](CODE_REPRODUCTION.md)。该文档也列出当前不能从本仓库完整重放的历史批次及缺失输入。

## 主论文数据

### 60 条任务链消融

- 输入：task-chain-60/public/tasks_en.jsonl，60 条指令。
- 主运行：task-chain-60/runs/surface-v1-llm2-60x4-20261020/。
- 条件：Full、No LLM1、Raw DSG、No LLM2；每个任务每个条件一次，共 240 个任务—条件输出。
- 来源文件：artifact_manifest.json、draft/system_lock.json、run_manifest.json。
- predictions/ 是各条件输出；runtime_inputs/ 保存当次运行的 canonical graph、RAG documents 和索引快照。

不同用途的 final_intent_60、planning_generation_60 等数据保存在 evaluations/taskchain/，不是这次主消融的同一数据集。

### 49 条 Nav2 mission

- 输入：nav2-49/missions/ 中 49 个 mission，准备报告在 missions/preparation_report.json。
- 首轮结果：nav2-49/tasks/ 中 49 个结果 JSON，logs/ 保存首轮日志。
- 诊断重跑：retry-20261023/ 单独保存 6 个失败 mission 的日志与结果；不回写首轮结果。
- 论文分母：首轮 43/49。六个重跑成功属于补充诊断，不替代首轮分数。

这些 mission 是从论文选定的较早 Full 预测中筛选的可执行子集，不是 49/60 的任务链成功率。

### 其他产物

- four-floor-demo/：HC-031 四层 RViz2 单独演示。
- map-generation/：Khronos-only 地图生成脚本、几何清单和测试；生成的 output/ 可重建，不随 Git 提交。
- interface-smoke/：自然语言到 Nav2 接口联调记录，不计入 benchmark。
- evaluations/taskchain/、evaluations/semantic-rag/、evaluations/retrieval/：其他评测案例、脚本和历史结果。
- evaluations/baseline/scene_graph.json：RAG 规划器可选 baseline comparison 使用的 fixture；旧 baseline planner 实现不再保留。

## 完整复跑要求

归档 mission、预测、日志和结果可用于核对论文分母；它们本身不是“重跑证明”。完整重建 RAG 需本地 Hospital 原始 DSG、mesh、78 行审阅 CSV、embedding 模型；完整 Nav2 重跑需 Ollama、已启动的隔离 Nav2 stack、匹配地图 bundle 和 ROS domain。历史 manifest 中的绝对路径保留为运行 provenance，不应当作当前 checkout 的路径。

## 快速核对归档数量

~~~bash
wc -l experiment/task-chain-60/public/tasks_en.jsonl
find experiment/nav2-49/missions -maxdepth 1 -name 'HC-*.mission.json' | wc -l
find experiment/nav2-49/tasks -maxdepth 1 -name 'HC-*.result.json' | wc -l
~~~

当前期望值依次为 60、49、49。数量核对不等于重跑实验。
