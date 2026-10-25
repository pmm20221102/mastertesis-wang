# llm-rag 模块

本模块包含一套 RAG 任务规划器和其 Khronos 3DSG 数据索引构建器：

- planner/：LLM1 意图解析、实体引用、任务链验证，以及自然语言到 Nav2 mission 的总入口。
- RAG/khronos_3dsg_rag/：解析 DSG、导出 canonical graph、生成检索文档和 Chroma vector database。

完整构建环境和依赖来源见 BUILD_ENVIRONMENT.md；仓库根目录 README.md 说明最短端到端启动流程。

## 主要接口

### 构建 RAG index

从仓库根目录调用：

~~~bash
.venv/bin/python llm-rag/RAG/khronos_3dsg_rag/src/main.py \
  --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible --dataset hospital-p1000 \
  --input /path/to/hospital/full-data/dsg.json \
  --review-csv /path/to/hospital-object-review-2026-10-12.csv \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu \
  --output llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5
~~~

输入是原始 DSG 和审阅 CSV；输出目录含 canonical graph、documents、vector_db 和 build manifest。构建不会改写输入或覆盖已存在的输出。

### 运行规划器

自然语言到 Nav2 的正式入口是 planner/plan_nav2.py；根目录的 plan_hospital_nav2.sh 是方便调用的 wrapper。输入为 instruction、RAG index、map bundle 和模型参数；输出是新的 provenance JSON。执行范围默认 plan-only。

~~~bash
bash llm-rag/plan_hospital_nav2.sh \
  --instruction "Please take me to a room with a chair and a door." \
  --ros-domain-id 77 \
  --output /tmp/hospital-nav2-plan.json
~~~

其他重要参数和状态解释见根目录 README；组件内规划器说明见 planner/README.md。

## 测试

~~~bash
.venv/bin/python -m pytest -q llm-rag/planner/tests llm-rag/RAG/khronos_3dsg_rag/tests
~~~
