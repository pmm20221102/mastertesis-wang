"""独立语义向量检索接口（v3）。

只做：自然语言短语 -> ChromaDB 向量检索 -> 语义最接近的 canonical label / 实体候选。

v3 变更（依据审查建议）：
- Label-level ranking：对候选按 canonical label 聚合（每 label 取最高相似度）
- SemanticSearchResult：status = matched / ambiguous / not_found（与 Evaluation 共用拒识逻辑）
- 正确读取 ChromaDB ``documents`` 字段作为 document_text
- 多路召回（全量 + objects + places + rooms）
- 过滤 alias 文档（无 node_id），unknown / 空 label 视为无效
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from build_vector_db import load_vector_db

logger = logging.getLogger(__name__)

DEFAULT_DB_DIR = "outputs/office_dense/vector_db"

# 用户层 node_type（单数）-> ChromaDB metadata node_type（复数）
NT_MAP = {
    "object": "objects",
    "place": "places",
    "room": "rooms",
    "building": "buildings",
}

_collection_cache = None
_db_cache_key: str | None = None


@dataclass
class SemanticCandidate:
    """一条节点级候选结果。"""

    node_id: str
    canonical_label: str
    node_type: str
    similarity_score: float  # 1 - cosine distance，越高越相似
    document_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "canonical_label": self.canonical_label,
            "node_type": self.node_type,
            "similarity_score": round(self.similarity_score, 4),
            "document_text": self.document_text[:200],
        }


@dataclass
class LabelScore:
    """Label-level 聚合结果。"""

    label: str
    node_type: str
    score: float
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_type": self.node_type,
            "score": round(self.score, 4),
            "count": self.count,
        }


@dataclass
class SemanticSearchResult:
    """语义检索结果（含状态判定）。"""

    status: str  # matched / ambiguous / not_found
    candidates: list[SemanticCandidate] = field(default_factory=list)
    label_ranking: list[LabelScore] = field(default_factory=list)


def _is_valid_label(label: str) -> bool:
    """canonical label 是否有效（unknown / 空视为无效）。"""
    return bool(label) and label.strip().lower() != "unknown"


def _get_collection(db_dir: str | Path):
    global _collection_cache, _db_cache_key
    key = str(db_dir)
    if _collection_cache is None or _db_cache_key != key:
        _collection_cache = load_vector_db(key)
        _db_cache_key = key
    return _collection_cache


def search_semantic(
    query: str,
    top_k: int = 5,
    node_type: str | None = None,
    db_dir: str | Path = DEFAULT_DB_DIR,
    threshold: float | None = None,
) -> SemanticSearchResult:
    """对查询短语做多路向量检索，返回实体候选 + label 聚合。

    Args:
        query: 自然语言查询短语。
        top_k: 返回的候选数量。
        node_type: 可选过滤，只返回该 node_type（object / place / room / building）。
        db_dir: 向量库目录。
        threshold: not_found 判定阈值（< threshold 判定为 not_found）。

    Returns:
        SemanticSearchResult：含 status、节点候选、label 聚合排名。
    """
    if not query or not query.strip():
        raise ValueError("query 不能为空")

    collection = _get_collection(db_dir)
    # 召回更多以支持 label 聚合（label 去重后 top_k 有效）
    n_results = min(max(top_k * 6, 40), collection.count())
    merged: dict[str, SemanticCandidate] = {}

    def _query_once(where: dict[str, Any] | None = None) -> None:
        kwargs: dict[str, Any] = {"query_texts": [query], "n_results": n_results, "include": ["metadatas", "documents", "distances"]}
        if where:
            kwargs["where"] = where
        res = collection.query(**kwargs)
        ids = res.get("ids", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        docs = res.get("documents", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for i in range(len(ids)):
            meta = metas[i] if i < len(metas) else {}
            nid = meta.get("node_id")
            if not nid:
                continue
            ntype = str(meta.get("node_type") or "")
            if node_type:
                want = NT_MAP.get(node_type, node_type)
                if ntype != want:
                    continue
            score = 1.0 - float(dists[i]) if i < len(dists) else 0.0
            doc_text = str(docs[i]) if i < len(docs) and docs[i] else ""
            cand = SemanticCandidate(
                node_id=str(nid),
                canonical_label=str(meta.get("label") or ""),
                node_type=ntype,
                similarity_score=score,
                document_text=doc_text,
            )
            key = str(nid)
            if key not in merged or cand.similarity_score > merged[key].similarity_score:
                merged[key] = cand

    _query_once()  # 全量
    if node_type:
        _query_once({"node_type": NT_MAP.get(node_type, node_type)})
    else:
        for nt in ("objects", "places", "rooms"):
            _query_once({"node_type": nt})

    ranked = sorted(merged.values(), key=lambda c: c.similarity_score, reverse=True)
    with_label = [c for c in ranked if _is_valid_label(c.canonical_label)]
    no_label = [c for c in ranked if not _is_valid_label(c.canonical_label)]
    candidates = (with_label + no_label)[:top_k]

    # Label-level 聚合：每 label 取最高相似度（仅有效 label）
    label_map: dict[str, LabelScore] = {}
    for c in ranked:
        if not _is_valid_label(c.canonical_label):
            continue
        lb = c.canonical_label
        if lb not in label_map or c.similarity_score > label_map[lb].score:
            label_map[lb] = LabelScore(label=lb, node_type=c.node_type, score=c.similarity_score, count=1)
        else:
            label_map[lb].count += 1
    label_ranking = sorted(label_map.values(), key=lambda x: x.score, reverse=True)

    # 状态判定
    status = "not_found"
    if label_ranking:
        top = label_ranking[0].score
        if threshold is None or top >= threshold:
            status = "matched"
            # ambiguous：top1 与 top2 接近（差距 < 0.05 且都 >= threshold）
            if len(label_ranking) >= 2:
                second = label_ranking[1].score
                if top - second < 0.05 and second >= (threshold or 0):
                    status = "ambiguous"

    return SemanticSearchResult(status=status, candidates=candidates, label_ranking=label_ranking)


def available_labels(db_dir: str | Path = DEFAULT_DB_DIR) -> dict[str, int]:
    """统计向量库中的真实 canonical label 及其数量。"""
    collection = _get_collection(db_dir)
    from collections import Counter

    labels: Counter = Counter()
    for meta in collection.peek(limit=collection.count())["metadatas"]:
        if meta.get("node_id") and meta.get("label"):
            labels[meta["label"]] += 1
    return dict(labels)
