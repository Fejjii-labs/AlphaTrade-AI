"""The offline RAG eval can fail: metrics, thresholds and failing variants (review item 2)."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))

import evaluate_rag
from lexical_embeddings import lexical_vector
from rag_metrics import mean, recall_at_k, reciprocal_rank

CASES = evaluate_rag.load_json("rag_cases.json")
CORPUS = {doc["key"]: doc for doc in evaluate_rag.load_json("rag_corpus.json")}


def test_recall_at_k_on_hand_built_rankings() -> None:
    ranked = ["b", "a", "c", "d"]
    assert recall_at_k(ranked, ["a"], 1) == 0.0
    assert recall_at_k(ranked, ["a"], 2) == 1.0
    assert recall_at_k(ranked, ["a", "d"], 3) == 0.5
    assert recall_at_k(ranked, ["a", "d"], 5) == 1.0
    assert recall_at_k([], ["a"], 3) == 0.0
    with pytest.raises(ValueError):
        recall_at_k(ranked, [], 3)
    with pytest.raises(ValueError):
        recall_at_k(ranked, ["a"], 0)


def test_reciprocal_rank_and_mrr_on_hand_built_rankings() -> None:
    assert reciprocal_rank(["a", "b"], ["a"]) == 1.0
    assert reciprocal_rank(["b", "c", "a"], ["a"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(["b", "c"], ["a"]) == 0.0
    assert reciprocal_rank(["b", "x", "a"], ["a", "x"]) == 0.5
    assert mean([1.0, 0.5, 0.0]) == pytest.approx(0.5)
    assert mean([]) == 0.0


def test_dataset_shape_meets_review_requirements() -> None:
    kinds = [case["kind"] for case in CASES]
    assert len(CASES) >= 10
    assert kinds.count("no_relevant_source") >= 1
    assert all("source_types" not in case for case in CASES), "no per-case type pre-filter"
    by_type: dict[str, int] = {}
    for doc in CORPUS.values():
        by_type[doc["source_type"]] = by_type.get(doc["source_type"], 0) + 1
    assert min(by_type.values()) >= 3, "every source type needs same-type distractors"
    expected = {key for case in CASES for key in case["expected_document_keys"]}
    assert expected <= set(CORPUS)


def test_lexical_vectors_are_deterministic_and_normalized() -> None:
    first = lexical_vector("SOLUSDT trades per session")
    assert first == lexical_vector("SOLUSDT trades per session")
    assert sum(v * v for v in first) == pytest.approx(1.0)
    assert not any(lexical_vector("the and of"))


def test_lexical_baseline_passes_thresholds() -> None:
    report = evaluate_rag.run("lexical", min_score=0.55, thresholds=evaluate_rag.Thresholds())
    assert report.passed, report.failures
    assert report.recall_at[3] >= evaluate_rag.MIN_RECALL_AT_3
    assert report.mrr >= evaluate_rag.MIN_MRR
    assert report.no_relevant_accuracy == 1.0


@pytest.mark.parametrize("min_score", [0.55, 0.0])
def test_hash_embedding_scores_below_threshold(min_score: float) -> None:
    report = evaluate_rag.run("hash", min_score=min_score, thresholds=evaluate_rag.Thresholds())
    assert not report.passed
    assert report.recall_at[3] < evaluate_rag.MIN_RECALL_AT_3 or report.mrr < evaluate_rag.MIN_MRR


def test_runner_exits_non_zero_for_hash_embedding() -> None:
    assert evaluate_rag.main(["--embeddings", "hash"]) == 1
    assert evaluate_rag.main([]) == 0


def test_shuffled_retriever_fails() -> None:
    rng = random.Random(7)
    keys = sorted(CORPUS)

    def shuffled(case: dict) -> list[str]:
        ranked = keys[:]
        rng.shuffle(ranked)
        return ranked[: evaluate_rag.TOP_K]

    report = evaluate_rag.evaluate(CASES, shuffled, CORPUS)
    assert not report.passed
    assert report.mrr < evaluate_rag.MIN_MRR
    assert report.no_relevant_accuracy == 0.0


def test_constant_retriever_fails() -> None:
    report = evaluate_rag.evaluate(CASES, lambda case: ["risk_stop_loss"], CORPUS)
    assert not report.passed
    assert report.recall_at[3] < evaluate_rag.MIN_RECALL_AT_3


def test_perfect_retriever_passes_and_filter_scope_is_checked() -> None:
    perfect = evaluate_rag.evaluate(
        CASES, lambda case: list(case["expected_document_keys"]), CORPUS
    )
    assert perfect.passed and perfect.mrr == 1.0

    def leaky_filter(case: dict) -> list[str]:
        extra = ["tpl_eth_pullback"] if case["kind"] == "filter" else []
        return [*case["expected_document_keys"], *extra]

    leaky = evaluate_rag.evaluate(CASES, leaky_filter, CORPUS)
    assert "filter case strategy_tag_filter failed" in leaky.failures
