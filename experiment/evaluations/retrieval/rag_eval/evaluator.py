"""Graph-Aware RAG Evaluator.

Two-stage evaluation:
1. Retrieval quality - does vector search find relevant chunks?
2. Graph understanding - does metadata contain correct graph structure?
"""

from __future__ import annotations

import logging

from .metrics import compute_metrics, graph_accuracy
from .query_generator import generate_all_graph_cases
from .retrieval_adapter import RetrievalAdapter, _detect_query_type
from .schemas import CaseResult, EvalCase, EvalReport, GraphGroundTruth

logger = logging.getLogger(__name__)


def _match_relevant(retrieved_ids: list[str], relevant_ids: list[str]) -> bool:
    return bool(set(retrieved_ids) & set(relevant_ids))


def _determine_failure_reason(retrieved_ids: list[str], relevant_ids: list[str]) -> str:
    if not retrieved_ids:
        return "no_results"
    if not relevant_ids:
        return "no_groundtruth"
    if not _match_relevant(retrieved_ids, relevant_ids):
        return "no_match_in_top_k"
    return ""


class Evaluator:
    def __init__(
        self,
        adapter: RetrievalAdapter,
        graph_gt: GraphGroundTruth,
        top_k: int = 10,
        seed: int = 42,
    ):
        self.adapter = adapter
        self.graph_gt = graph_gt
        self.top_k = top_k
        self.seed = seed
        self.cases = generate_all_graph_cases(graph_gt, seed=seed)

    def run(self, dataset: str) -> EvalReport:
        logger.info(f"Running evaluation for {dataset}: {len(self.cases)} cases, top_k={self.top_k}")

        case_results = []
        for case in self.cases:
            result = self._evaluate_case(case)
            case_results.append(result)

        failures = [r for r in case_results if r.failure_reason]

        metrics_by_type: dict[str, dict[str, float]] = {}
        for r in case_results:
            qt = r.query_type
            if qt not in metrics_by_type:
                metrics_by_type[qt] = {
                    "hit@1": 0, "hit@3": 0, "hit@10": 0,
                    "mrr@1": 0, "mrr@3": 0, "mrr@10": 0,
                    "recall@10": 0, "precision@10": 0, "f1@10": 0,
                    "count": 0, "graph_accuracy": 0,
                }
            # Aggregate all metric variants
            for key in ["hit@1", "hit@3", "hit@10", "mrr@1", "mrr@3", "mrr@10",
                         "recall@10", "precision@10", "f1@10"]:
                metrics_by_type[qt][key] += r.metrics.get(key, 0)
            metrics_by_type[qt]["graph_accuracy"] += r.graph_accuracy
            metrics_by_type[qt]["count"] += 1

        for qt in metrics_by_type:
            count = metrics_by_type[qt]["count"]
            if count > 0:
                for key in metrics_by_type[qt]:
                    if key != "count":
                        metrics_by_type[qt][key] /= count

        overall_metrics = {}
        if case_results:
            for key in ["hit@1", "hit@3", "hit@10", "mrr@1", "mrr@3", "mrr@10",
                         "recall@10", "precision@10", "f1@10"]:
                overall_metrics[key] = sum(r.metrics.get(key, 0) for r in case_results) / len(case_results)
            overall_metrics["graph_accuracy"] = sum(r.graph_accuracy for r in case_results) / len(case_results)

        total_nodes = len(self.graph_gt.nodes)
        indexed_ids = self.adapter.get_indexed_entity_ids()
        matched = sum(1 for n in self.graph_gt.nodes if n.node_id in indexed_ids)
        index_coverage = matched / total_nodes if total_nodes > 0 else 0.0

        return EvalReport(
            dataset=dataset,
            total_cases=len(case_results),
            metrics_by_type=metrics_by_type,
            overall_metrics=overall_metrics,
            case_results=case_results,
            failures=failures,
            index_coverage=index_coverage,
            total_gt_objects=total_nodes,
            matched_gt_objects=matched,
        )

    def _evaluate_case(self, case: EvalCase) -> CaseResult:
        retrieved_chunks = self.adapter.search_with_strategy(case.query, top_k=self.top_k)

        retrieved_ids = list(dict.fromkeys([chunk.node_id for chunk in retrieved_chunks]))

        metrics = compute_metrics(retrieved_ids, case.relevant_ids, k=self.top_k)
        hit = _match_relevant(retrieved_ids, case.relevant_ids)
        failure_reason = "" if hit else _determine_failure_reason(retrieved_ids, case.relevant_ids)

        # Graph accuracy (query-type aware)
        ga = 0.0
        if case.relevant_ids and retrieved_chunks:
            ga_scores = []
            for chunk in retrieved_chunks[:5]:
                child_ids = chunk.metadata.get("child_ids", [])
                parent_ids = chunk.metadata.get("parent_ids", [])
                neighbor_ids = chunk.metadata.get("neighbor_ids", [])
                has_graph_info = child_ids or parent_ids or neighbor_ids
                if has_graph_info:
                    score = graph_accuracy(chunk.metadata, case.relevant_ids)
                    ga_scores.append(score)
                elif chunk.node_id in case.relevant_ids:
                    ga_scores.append(1.0)
            ga = sum(ga_scores) / len(ga_scores) if ga_scores else 0.0

        return CaseResult(
            case_id=case.case_id,
            query=case.query,
            query_type=case.query_type,
            retrieved_ids=retrieved_ids,
            relevant_ids=case.relevant_ids,
            hit=hit,
            metrics=metrics,
            graph_accuracy=ga,
            failure_reason=failure_reason,
        )
