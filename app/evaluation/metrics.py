from __future__ import annotations

from collections.abc import Iterable


def retrieval_metrics(ranks: Iterable[int | None], k: int = 5) -> dict[str, float]:
    values = list(ranks)
    if not values:
        return {f"recall_at_{k}": 0.0, "mrr": 0.0}
    return {
        f"recall_at_{k}": sum(rank is not None and rank <= k for rank in values) / len(values),
        "mrr": sum(1 / rank for rank in values if rank is not None) / len(values),
    }


def agent_metrics(results: Iterable[dict[str, bool]]) -> dict[str, float]:
    values = list(results)
    if not values:
        return {
            "plan_validity": 0.0,
            "execution_success": 0.0,
            "numeric_groundedness": 0.0,
        }
    return {
        "plan_validity": sum(item["plan_valid"] for item in values) / len(values),
        "execution_success": sum(item["executed"] for item in values) / len(values),
        "numeric_groundedness": sum(item["grounded"] for item in values) / len(values),
    }
