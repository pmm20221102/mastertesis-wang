# Khronos 3DSG Graph-aware RAG 后续优化任务说明

## 1. 文档目的

本文档用于指导 Agent 后续修改 Khronos 3D Dynamic Scene Graph (DSG) + RAG
系统。

当前版本已经完成：

-   Vector RAG 基础检索
-   Graph-aware retrieval
-   Query Router
-   Hierarchy / Neighbor / Spatial 查询分类
-   Graph metadata 利用
-   Evaluation framework 扩展

当前重点不再是增加简单 RAG 功能，而是进一步提升：

1.  Scene Graph 结构化查询能力
2.  Object / Entity 定位能力
3.  Graph reasoning 能力
4.  面向 LLM task planning 的可靠性

------------------------------------------------------------------------

# 2. 当前系统架构

当前 pipeline：

    Khronos DSG JSON
            |
            v
    parse_3dsg.py
            |
            v
    Scene Graph
            |
            v
    build_documents.py
            |
            v
    Vector Database
            |
            v
    Query Router
            |
            +----------------+
            |                |
            v                v
     Graph Retrieval     Vector Retrieval
            |
            v
    Ranking
            |
            v
    LLM Planner

当前版本相比旧版本最大的改进：

-   不再完全依赖 embedding similarity
-   开始利用：
    -   parent_ids
    -   child_ids
    -   neighbor_ids
    -   node_type
    -   spatial information

------------------------------------------------------------------------

# 3. 当前优化效果总结

新版 evaluation 显示：

## 总体提升

相比纯 Vector RAG：

-   hit@10 提升
-   precision 提升
-   graph accuracy 提升

说明：

Graph-aware retrieval 方向正确。

------------------------------------------------------------------------

# 4. 当前主要问题

------------------------------------------------------------------------

# Problem 1: Entity Resolution 缺失（最高优先级）

## 现象

Reverse hierarchy 查询仍然失败。

例如：

用户：

    Where is small office chair located?

Graph:

    node_id:
    a83f92xxxx


    semantic_label:
    chair

当前流程：

    query
     |
    embedding/entity search
     |
    find node
     |
    parent traversal

问题：

第一步无法稳定找到正确 node。

导致：

    parent traversal

没有执行。

------------------------------------------------------------------------

## 根本原因

Khronos DSG 使用：

-   UUID node id
-   semantic label
-   object attributes

而用户使用：

-   自然语言名称
-   描述性表达
-   同义词

二者没有映射。

------------------------------------------------------------------------

## 修改方案

新增：

    entity_resolver.py

实现：

    User Query

          |
          v

    Entity Extraction

          |
          v

    Candidate Node Search

          |
          v

    Node Ranking

          |
          v

    Resolved Node ID

匹配信息：

-   semantic_label
-   object name
-   aliases
-   attributes
-   spatial relation
-   room context

------------------------------------------------------------------------

## 示例

输入：

    small office chair

输出：

    candidate nodes:

    chair_001
    chair_002

    scores:

    chair_001 0.92
    chair_002 0.65

然后进入：

    chair_001
     |
    parent_ids
     |
    office_room

------------------------------------------------------------------------

# Problem 2: Reverse Hierarchy 查询仍然低效

## 当前表现

Reverse hierarchy:

    Where is object X?

仍然是最低指标。

原因：

Graph traversal 本身没有问题。

问题：

node resolution 不可靠。

------------------------------------------------------------------------

## 修改方案

重新设计流程：

旧：

    query
     |
    node
     |
    parent

新：

    query

     |
    entity resolver

     |
    candidate nodes

     |
    graph traversal

     |
    parent candidates

     |
    ranking

     |
    answer

------------------------------------------------------------------------

# Problem 3: Document Chunk 设计仍然存在污染

## 当前问题

目前存在：

    entity chunk

    relation chunk

    context chunk

    alias chunk

同一个 graph node 可能生成多个 document。

导致：

Vector database:

    same information
    appears multiple times

影响：

-   retrieval ranking
-   precision
-   context quality

------------------------------------------------------------------------

## 修改方案

减少单独 relation chunk。

推荐：

Object Context Document:

    Object:

    chair


    Hierarchy:

    Building
     |
    Floor
     |
    Room
     |
    Chair


    Relations:

    Near table

    Position:

    x,y,z


    Attributes:

    color,size,type

一个 object 一个完整语义上下文。

------------------------------------------------------------------------

# Problem 4: Graph Retrieval Ranking 不充分

## 当前问题

Graph retrieval 可以找到候选。

但是排序不足。

例如：

Neighbor query:

    What is near chair?

返回：

    table
    wall
    floor
    chair

距离可能类似。

------------------------------------------------------------------------

## 修改方案

增加 graph-aware score:

    final_score =
    0.5 semantic similarity

    +
    0.3 graph relation

    +
    0.2 spatial distance

不同任务使用不同权重。

------------------------------------------------------------------------

# Problem 5: Neighbor Retrieval 缺少关系权重

当前：

    near
    adjacent
    connected

没有区分。

------------------------------------------------------------------------

## 修改方案

建立 relation weight:

    contains:
    1.0

    inside:
    1.0

    adjacent:
    0.8

    near:
    0.6

    far:
    0.2

用于 ranking。

------------------------------------------------------------------------

# Problem 6: Spatial Retrieval 排序不足

当前 spatial retrieval：

优势：

-   可以过滤空间范围

问题：

距离相同时：

    wall
    chair
    table

无法判断。

------------------------------------------------------------------------

## 修改方案

增加：

    spatial score

    +

    semantic score

    +

    node_type filtering

例如：

查询：

    object at position

优先：

    object node

    not room/wall node

------------------------------------------------------------------------

# Problem 7: Query Router 需要增强

当前：

    semantic
    hierarchy
    reverse hierarchy
    neighbor
    spatial

已经正确。

但是分类还可以增加：

## 新增 Query Type

### Object location

    Where is X?

使用：

Entity Resolver + Parent traversal

### Containment

    What objects are inside room?

使用：

children traversal

### Spatial relation

    What is near X?

使用：

neighbor graph

### Scene understanding

    What exists in the office?

使用：

vector retrieval

------------------------------------------------------------------------

# 5. 推荐最终架构

目标架构：

                     User Query

                          |

                  Query Understanding

                          |

                 Entity Resolution Layer

                          |

              +-----------+------------+

              |                        |

       Graph Retrieval          Semantic Retrieval


              |

              v

          Graph-aware Ranking


              |

              v

            LLM Planner

------------------------------------------------------------------------

# 6. Agent 后续修改任务列表

# Task 1 (Highest Priority)

## Implement Entity Resolver

新增：

    entity_resolver.py

功能：

-   query entity extraction
-   alias matching
-   semantic label matching
-   candidate generation
-   candidate ranking

目标：

提升：

    reverse hierarchy accuracy

------------------------------------------------------------------------

# Task 2

## Rewrite reverse hierarchy pipeline

修改：

    query_rag.py

    retrieval_adapter.py

流程：

    query

    -> entity resolver

    -> node candidates

    -> parent traversal

    -> ranking

    -> answer

------------------------------------------------------------------------

# Task 3

## Improve Graph Ranking

新增：

    graph_ranker.py

支持：

    semantic score

    graph score

    distance score

    node type score

------------------------------------------------------------------------

# Task 4

## Simplify Document Generation

修改：

    build_documents.py

减少：

relation-only chunks

增加：

context-rich graph documents

------------------------------------------------------------------------

# Task 5

## Extend Evaluation

增加：

    Entity resolution accuracy

    Node matching accuracy

    Parent retrieval accuracy

    Graph traversal success rate

不要只评价最终 retrieval。

------------------------------------------------------------------------

# 7. 不建议继续做的事情

## 不建议 1

继续更换 embedding model。

原因：

当前瓶颈不是语义 embedding。

------------------------------------------------------------------------

## 不建议 2

继续增加 top_k。

原因：

会降低 precision。

------------------------------------------------------------------------

## 不建议 3

增加大量 relation documents。

原因：

会污染 vector database。

------------------------------------------------------------------------

# 8. 最终目标

最终系统应该从：

    Vector RAG over Scene Graph

升级为：

    Graph Reasoning RAG over Khronos DSG

满足：

-   找到目标物体
-   理解物体所在房间
-   理解空间关系
-   支持 LLM task decomposition
-   支持 semantic navigation

当前版本已经完成第一阶段。

下一阶段核心：

> Entity Resolution + Graph Reasoning + Graph-aware Ranking

这是提升系统性能的关键。
