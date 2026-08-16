# Khronos 3DSG RAG 系统代码与评估分析报告

## 1. 分析目标

本报告基于：

-   `RAG/khronos_3dsg_rag/` 全部核心代码
-   `rag_eval/` 评估框架
-   32 轮 RAG evaluation reports
-   office / mock_office groundtruth 测试结果

目标：

1.  找出当前 RAG 性能瓶颈来源；
2.  区分问题来自：
    -   数据解析
    -   document 构建
    -   embedding/index
    -   retrieval strategy
    -   evaluation design
3.  给后续 Agent 提供明确修改方向。

------------------------------------------------------------------------

# 2. 当前系统架构分析

当前 pipeline:

    Khronos DSG JSON
            |
            v
    parse_3dsg.py
            |
            v
    SceneGraph
            |
            v
    build_documents.py
            |
            v
    Entity / Region / Relation / Alias chunks
            |
            v
    Chroma Vector DB
            |
            v
    query_rag.py
            |
            v
    semantic retrieval
            |
            v
    LLM task planning

同时 evaluation:

    Groundtruth
        |
    query_generator
        |
    retrieval_adapter
        |
    metrics
        |
    report_writer

整体架构方向正确，但是目前 retrieval 仍然接近传统 vector RAG，而 Khronos
DSG 本质是 graph database，因此存在结构性匹配问题。

------------------------------------------------------------------------

# 3. 评估结果总结

## 3.1 总体表现

最佳完整 office evaluation:

-   hit@10: 0.545
-   recall@10: 0.532
-   MRR@10: 0.313
-   precision@10: 0.068
-   graph_accuracy: 0.113

说明：

-   能找到相关信息的大概率约 50%
-   但是排序质量较低
-   返回大量无关 chunk
-   图结构利用不足

------------------------------------------------------------------------

# 4. 主要问题分析

# Problem 1: Reverse hierarchy 查询严重失败（最大问题）

例如：

    Where is small_office_objects located?

    Expected:
    room node

    Retrieved:
    relation chunks / object chunks

结果：

office round_21:

    reverse_hierarchy:

    hit:
    0.105

    recall:
    0.1025

    graph_accuracy:
    0.0199

office_improved:

    reverse_hierarchy:

    hit:
    0

这是当前最大瓶颈。

## 根本原因

当前 document:

    object entity chunk
    relation chunk
    room chunk

但是用户问题：

    Where is object X located?

需要：

    object
       |
       belongs_to
       |
    room

而 vector search 更容易匹配：

    object name
    relation text

无法保证 parent room 被召回。

## 修改方向

不要依赖 embedding 找 parent。

应该：

query classification:

    Where is X located?
    Which room contains X?
    What is the parent of X?

直接进入 graph traversal。

新增:

    query
     |
    detect intent
     |
    reverse_hierarchy
     |
    find node_id
     |
    lookup parent_ids
     |
    return parent node

这类查询无需 embedding。

------------------------------------------------------------------------

# Problem 2: Graph metadata 没有真正参与 retrieval

虽然 build_documents.py 添加：

    parent_ids
    child_ids
    neighbor_ids
    related_nodes
    depth

但是 query_rag.py:

核心仍然：

``` python
collection.query()
```

即：

    embedding similarity only

metadata 只是保存，没有影响 ranking。

## 建议

实现 Hybrid Retriever:

    score =
    0.5 semantic similarity
    +
    0.3 graph relation score
    +
    0.2 metadata match

例如：

查询:

    Which room contains chairs?

候选：

chair node:

semantic score 高

room node:

graph parent score 高

最终：

room node 排名第一。

------------------------------------------------------------------------

# Problem 3: Relation chunk设计存在问题

目前：

    one edge -> one relation document

例如:

    A contains B

生成:

    relation_A_B

问题：

大量 relation chunk：

-   文本短
-   语义弱
-   embedding 区分能力低

导致：

reverse hierarchy:

大量 relation 被召回，但是不是目标。

## 修改建议

不要单独存 relation chunk。

改为：

Graph path document:

例如:

    Object:
    chair_01

    Hierarchy:
    chair_01
     -> place_02
     -> room_03
     -> building


    Location:
    room_03

一个 object 一个完整 context。

这样：

    Where is chair?

直接匹配。

------------------------------------------------------------------------

# Problem 4: Chunk设计不适合3D Scene Graph

目前:

## Entity chunk

优点：

-   object 查询有效

缺点：

缺少上下文。

例如:

当前：

    Object:
    chair

    position:
    x,y,z

应该:

    Object:
    chair

    Located in:
    Room A

    Near:
    table

    Parent:
    office

    Position:
    x,y,z

------------------------------------------------------------------------

# 推荐新的 document schema

每个 object:

    {
    id,

    semantic_label,

    type,

    position,

    room,

    place,

    building,

    parents,

    neighbors,

    contained_objects,

    time_state
    }

embedding text:

    Chair object.

    Located in Room 3.

    Near table and desk.

    Position x=1.2 y=3.5.

    Parent hierarchy:
    Building -> Floor -> Room -> Object

------------------------------------------------------------------------

# Problem 5: Query classification不足

当前 retrieval_adapter 已经有：

    _detect_query_type()

但是没有完全控制 retrieval。

应该改成：

## semantic query

例如:

    What objects exist?

使用:

vector search

## hierarchy query

例如:

    What is inside room?

使用:

children traversal

## reverse hierarchy

例如:

    Where is object?

使用:

parent traversal

## spatial

例如:

    object near x,y

使用:

position search

## neighbor

例如:

    What is next to chair?

使用:

neighbor graph

------------------------------------------------------------------------

# Problem 6: Evaluation存在偏差

当前 precision 很低：

    precision@10 = 0.068

原因：

top_k=10 对 graph query 太大。

例如：

一个 object 只有一个 parent:

正确答案:

    room_A

返回10个:

    relation1
    relation2
    object2
    roomA
    ...

precision自然低。

建议增加:

    Hit@1
    Hit@3
    MRR@3

而不是只看 hit@10。

------------------------------------------------------------------------

# 5. 推荐修改优先级

## Priority 1（必须）

实现 Graph-aware retrieval。

修改：

    retrieval_adapter.py
    query_rag.py

新增：

    Query Router

流程:

    Query

     |
    classifier

     |
    +--------------+
    |              |
    vector       graph
    search       search

     |
    fusion ranking

     |
    result

------------------------------------------------------------------------

## Priority 2

重新设计 document。

从：

    node chunk
    relation chunk

改成：

    graph context chunk

重点：

object -\> room -\> building

------------------------------------------------------------------------

## Priority 3

增加 metadata filtering。

例如:

查询:

    Which room contains chairs?

先:

    node_type=room

过滤。

------------------------------------------------------------------------

## Priority 4

增加 reranker。

当前:

    embedding similarity

建议:

    retrieve top50

            |
            v

    cross encoder rerank

            |
            v

    top5

------------------------------------------------------------------------

# 6. 不建议的优化

## 不建议1：继续调整embedding model

原因：

问题不是语义理解。

问题是：

    graph relation retrieval

embedding无法解决。

------------------------------------------------------------------------

## 不建议2：简单增加top_k

会导致：

-   precision下降
-   LLM context污染

------------------------------------------------------------------------

## 不建议3：增加更多relation chunk

当前relation chunk已经过多。

应该减少，而不是增加。

------------------------------------------------------------------------

# 7. 推荐最终架构

                    Query

                      |
                      v

              Query Understanding

                      |
            +---------+---------+

     semantic       graph        spatial

                      |
                      v

              Hybrid Retrieval

                      |
                      v

              Graph-aware Rerank

                      |
                      v

                 LLM Planner

------------------------------------------------------------------------

# 8. 给Agent的修改任务列表

## Task 1

实现 Query Router:

文件:

    rag_eval/retrieval_adapter.py
    RAG/src/query_rag.py

要求：

根据query type选择:

-   vector retrieval
-   parent traversal
-   child traversal
-   neighbor traversal
-   spatial search

------------------------------------------------------------------------

## Task 2

重构 document builder:

文件:

    build_documents.py

新增:

    build_context_chunks()

每个object包含:

-   parent room
-   building
-   neighbors
-   position
-   semantic label

------------------------------------------------------------------------

## Task 3

实现 Hybrid ranking:

公式:

    final_score =
    semantic_score
    +
    graph_score
    +
    metadata_score

------------------------------------------------------------------------

## Task 4

重新evaluation:

新增指标:

    Hit@1
    Hit@3
    MRR@3
    Graph traversal accuracy
    Answer completeness

------------------------------------------------------------------------

# 最终结论

当前 RAG 最大问题不是模型，也不是embedding。

核心问题：

> 使用 vector retrieval 解决 graph query。

Khronos DSG 是结构化时空图，不应该完全按照文本RAG处理。

下一阶段优化方向：

1.  graph query routing
2.  graph traversal retrieval
3.  context-aware chunk
4.  hybrid ranking

完成以上修改后，reverse hierarchy、neighbor、semantic
navigation性能应该会有明显提升。
