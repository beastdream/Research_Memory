"""Unit tests for Bi-Encoder IR evaluation metrics (Recall@K, MRR@K, nDCG@K)."""
import math
import pytest
from scripts.benchmark_biencoders import (
    compute_recall_at_k,
    compute_mrr_at_k,
    compute_ndcg_at_k,
)


def test_recall_at_k():
    relevant = {"c1": 2, "c2": 2, "c3": 1}

    # Perfect recall in top 3
    assert compute_recall_at_k(["c1", "c2", "c3", "c4"], relevant, k=3) == 1.0
    # 2 hits out of 3 relevant
    assert pytest.approx(compute_recall_at_k(["c1", "c4", "c2", "c5"], relevant, k=3)) == 2.0 / 3.0
    # 1 hit out of 3 in top 1
    assert pytest.approx(compute_recall_at_k(["c1", "c4", "c2", "c5"], relevant, k=1)) == 1.0 / 3.0
    # 0 hits
    assert compute_recall_at_k(["c9", "c8"], relevant, k=5) == 0.0
    # Empty relevant
    assert compute_recall_at_k(["c1"], {}, k=5) == 0.0


def test_mrr_at_k():
    relevant = {"target_chunk": 2}

    # Hit at rank 1 -> 1.0
    assert compute_mrr_at_k(["target_chunk", "other"], relevant, k=10) == 1.0
    # Hit at rank 2 -> 0.5
    assert compute_mrr_at_k(["other", "target_chunk"], relevant, k=10) == 0.5
    # Hit at rank 3 -> 1/3
    assert pytest.approx(compute_mrr_at_k(["o1", "o2", "target_chunk"], relevant, k=10)) == 1.0 / 3.0
    # Hit beyond rank 10 -> 0.0
    ranked = [f"o{i}" for i in range(10)] + ["target_chunk"]
    assert compute_mrr_at_k(ranked, relevant, k=10) == 0.0


def test_ndcg_at_k_perfect():
    # Graded relevance: c1 has 2, c2 has 1
    relevant = {"c1": 2, "c2": 1}
    # Ideal ranking: ["c1", "c2"]
    assert pytest.approx(compute_ndcg_at_k(["c1", "c2", "c3"], relevant, k=10)) == 1.0


def test_ndcg_at_k_suboptimal():
    relevant = {"c1": 2, "c2": 1}
    # Inverted ranking: ["c2", "c1"]
    # Ideal:
    # rank 1: c1 (gain = 2^2 - 1 = 3) / log2(2) = 3
    # rank 2: c2 (gain = 2^1 - 1 = 1) / log2(3) = 1 / 1.58496 = 0.6309
    # IDCG = 3 + 0.6309 = 3.6309
    # Actual:
    # rank 1: c2 (gain = 1) / log2(2) = 1
    # rank 2: c1 (gain = 3) / log2(3) = 3 / 1.58496 = 1.8928
    # DCG = 1 + 1.8928 = 2.8928
    # nDCG = 2.8928 / 3.6309 ≈ 0.7967
    expected_ndcg = (1.0 + 3.0 / math.log2(3)) / (3.0 + 1.0 / math.log2(3))
    actual_ndcg = compute_ndcg_at_k(["c2", "c1", "c3"], relevant, k=10)
    assert pytest.approx(actual_ndcg, rel=1e-4) == expected_ndcg

