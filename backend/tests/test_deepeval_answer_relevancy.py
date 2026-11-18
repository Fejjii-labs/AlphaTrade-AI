"""DeepEval answer-relevancy layer: golden set, median/margin gate, spend cap (judge mocked)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))

import deepeval_answer_relevancy as dar
import evaluate_rag

ROOT = Path(__file__).resolve().parents[2]
CORPUS = {doc["key"]: doc for doc in evaluate_rag.load_json("rag_corpus.json")}


class ScriptedJudge:
    """Returns scripted scores in order; records every call. Never touches the network."""

    def __init__(self, scores: list[float], cost: float | None = None) -> None:
        self.scores = list(scores)
        self.cost = cost
        self.calls: list[str] = []

    def score(
        self, *, input: str, actual_output: str, retrieval_context: list[str]
    ) -> dar.JudgeVerdict:
        self.calls.append(input)
        return dar.JudgeVerdict(self.scores.pop(0), self.cost)


def _cases(n: int) -> list[dar.GoldenCase]:
    return [dar.GoldenCase(f"c{i}", "risk", f"q{i}", "a", ("risk_stop_loss",)) for i in range(n)]


def _answer(case: dar.GoldenCase) -> tuple[str, list[str]]:
    return "answer", ["context"]


def _config(**overrides: object) -> dar.JudgeConfig:
    return dar.JudgeConfig(**overrides)  # type: ignore[arg-type]


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "golden.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _row(i: int, **overrides: object) -> dict:
    row = {
        "version": dar.GOLDEN_VERSION,
        "id": f"case_{i}",
        "category": "risk",
        "input": "When must the stop loss be placed?",
        "expected_output": "Before entry.",
        "source_document_keys": ["risk_stop_loss"],
    }
    row.update(overrides)
    return row


# --- golden set ---------------------------------------------------------------


def test_shipped_golden_set_is_valid_versioned_and_sized() -> None:
    cases = dar.load_golden_set(known_document_keys=set(CORPUS))
    assert dar.MIN_CASES <= len(cases) <= dar.MAX_CASES
    assert len({case.id for case in cases}) == len(cases)
    assert dar.GOLDEN_PATH.name == f"{dar.GOLDEN_VERSION}.jsonl"


def test_shipped_expected_answers_are_grounded_in_their_source_documents() -> None:
    """Expected answers reuse the corpus wording: no invented facts."""
    word = re.compile(r"[a-z0-9%.']+")
    for case in dar.load_golden_set(known_document_keys=set(CORPUS)):
        source = " ".join(CORPUS[key]["text"] for key in case.source_document_keys).lower()
        source_words = {w.strip(".") for w in word.findall(source)}
        content = [w.strip(".") for w in word.findall(case.expected_output.lower()) if len(w) > 3]
        grounded = [w for w in content if w in source_words or w.rstrip("s") in source_words]
        assert len(grounded) / len(content) >= 0.7, (case.id, set(content) - set(grounded))
        for figure in re.findall(r"\d+(?:\.\d+)?[%xR]?", case.expected_output):
            assert figure in CORPUS[case.source_document_keys[0]]["text"], (case.id, figure)


def test_shipped_baseline_matches_golden_version() -> None:
    baseline = dar.load_baseline()
    assert baseline["golden_version"] == dar.GOLDEN_VERSION
    assert baseline["judge_model"] == dar.DEFAULT_JUDGE_MODEL


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([_row(i) for i in range(29)], "29 cases"),
        ([_row(i) for i in range(51)], "51 cases"),
        ([_row(0), _row(0)] + [_row(i) for i in range(1, 30)], "duplicate id"),
        ([_row(0, version="answer_relevancy_v0")] + [_row(i) for i in range(1, 31)], "version"),
        ([_row(0, input="")] + [_row(i) for i in range(1, 31)], "input"),
        ([{**_row(0), "extra": 1}] + [_row(i) for i in range(1, 31)], "fields"),
        ([_row(0, source_document_keys=["nope"])] + [_row(i) for i in range(1, 31)], "unknown"),
    ],
)
def test_golden_set_validation_rejects_bad_files(
    tmp_path: Path, rows: list[dict], message: str
) -> None:
    with pytest.raises(dar.GoldenSetError, match=message):
        dar.load_golden_set(_write(tmp_path, rows), known_document_keys=set(CORPUS))


def test_golden_set_rejects_invalid_json_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(dar.GoldenSetError, match="invalid JSON"):
        dar.load_golden_set(path)
    with pytest.raises(dar.GoldenSetError, match="not found"):
        dar.load_golden_set(tmp_path / "missing.jsonl")


# --- median of 3 and margin gate ------------------------------------------------


def test_case_score_is_median_of_three_runs_and_suite_is_mean_of_medians() -> None:
    judge = ScriptedJudge([0.9, 0.1, 0.8, 0.5, 0.6, 0.4])
    result = dar.run_suite(_cases(2), _answer, judge, dar.SpendGuard(_config()))
    assert [case.median for case in result.cases] == [0.8, 0.5]
    assert result.cases[0].runs == [0.9, 0.1, 0.8]
    assert result.suite_score == pytest.approx(0.65)
    assert result.judge_calls == 6 and result.aborted is None


def test_one_outlier_run_does_not_move_the_median() -> None:
    judge = ScriptedJudge([0.9, 0.0, 0.9])
    result = dar.run_suite(_cases(1), _answer, judge, dar.SpendGuard(_config()))
    assert result.suite_score == pytest.approx(0.9)


def test_even_run_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="odd"):
        dar.run_suite(_cases(1), _answer, ScriptedJudge([]), dar.SpendGuard(_config()), runs=2)


BASELINE = {"golden_version": dar.GOLDEN_VERSION, "judge_model": "judge-x", "suite_score": 0.80}


@pytest.mark.parametrize(
    ("score", "failed"),
    [(0.80, False), (0.76, False), (0.75, False), (0.7499, True), (0.10, True), (0.95, False)],
)
def test_gate_fails_only_beyond_margin_below_baseline(score: float, failed: bool) -> None:
    decision = dar.gate(score, BASELINE, margin=0.05, judge_model="judge-x")
    assert decision.failed is failed


def test_gate_margin_is_configurable() -> None:
    assert dar.gate(0.70, BASELINE, margin=0.05, judge_model="judge-x").failed
    assert not dar.gate(0.70, BASELINE, margin=0.15, judge_model="judge-x").failed


def test_gate_is_report_only_without_baseline_or_with_other_judge() -> None:
    unmeasured = {**BASELINE, "suite_score": None}
    assert not dar.gate(0.0, unmeasured, margin=0.05, judge_model="judge-x").failed
    assert not dar.gate(0.0, BASELINE, margin=0.05, judge_model="judge-y").failed
    assert not dar.gate(None, BASELINE, margin=0.05, judge_model="judge-x").failed


# --- spend cap ----------------------------------------------------------------


def test_projection_over_cap_aborts_before_any_judge_call() -> None:
    judge = ScriptedJudge([0.9] * 300)
    config = _config(max_usd=1.0, est_usd_per_call=0.01)
    result = dar.run_suite(_cases(40), _answer, judge, dar.SpendGuard(config))
    assert judge.calls == [] and result.cases == []
    assert result.aborted and "exceed the cap" in result.aborted


def test_call_cap_aborts_before_projected_overrun() -> None:
    judge = ScriptedJudge([0.9] * 30)
    config = _config(max_calls=5)
    result = dar.run_suite(_cases(2), _answer, judge, dar.SpendGuard(config))
    assert judge.calls == [] and result.aborted


def test_reported_cost_above_estimate_hits_cap_mid_run_and_keeps_partial_results() -> None:
    # Projection: 6 calls x $0.01 = $0.06 fits $0.10, but each call really costs $0.04.
    judge = ScriptedJudge([0.9] * 6, cost=0.04)
    config = _config(max_usd=0.10, est_usd_per_call=0.01)
    result = dar.run_suite(_cases(2), _answer, judge, dar.SpendGuard(config))
    # After two calls ($0.08) the guard predicts $0.04 for the next one: $0.12 > $0.10.
    assert len(judge.calls) == 2
    assert result.aborted and "spend cap reached" in result.aborted
    assert result.cases == []  # case 1 never finished its 3 runs, so it is not scored
    assert result.spent_usd <= config.max_usd


def test_spend_is_charged_at_least_the_estimate_when_cost_is_unreported() -> None:
    guard = dar.SpendGuard(_config(est_usd_per_call=0.02))
    guard.after_call(None)
    guard.after_call(0.001)
    assert guard.calls == 2 and guard.spent_usd == pytest.approx(0.04)


def test_judge_config_reads_env_with_pinned_defaults() -> None:
    default = dar.JudgeConfig.from_env({})
    assert default.model == dar.DEFAULT_JUDGE_MODEL
    assert re.search(r"\d{4}-\d{2}-\d{2}$", default.model), "judge default must be a dated snapshot"
    assert default.temperature == 0.0 and default.max_usd == 1.0
    custom = dar.JudgeConfig.from_env(
        {
            "DEEPEVAL_JUDGE_MODEL": "m-2026-01-01",
            "DEEPEVAL_MAX_USD": "0.25",
            "DEEPEVAL_MARGIN": "0.1",
        }
    )
    assert (custom.model, custom.max_usd, custom.margin) == ("m-2026-01-01", 0.25, 0.1)
    with pytest.raises(ValueError):
        dar.JudgeConfig.from_env({"DEEPEVAL_EST_USD_PER_CALL": "0"})


# --- wiring -------------------------------------------------------------------


def test_extractive_answer_uses_top_document_and_top3_context() -> None:
    ranked = ["risk_leverage", "mistake_overleverage", "risk_stop_loss", "risk_daily_loss"]
    answer, context = dar.extractive_answer(ranked, CORPUS)
    assert answer == CORPUS["risk_leverage"]["text"]
    assert context == [CORPUS[key]["text"] for key in ranked[:3]]
    assert dar.extractive_answer([], CORPUS) == (dar.NO_SOURCE_ANSWER, [])


def test_workflow_is_manual_and_nightly_only_and_ci_does_not_run_it() -> None:
    workflow_path = ROOT / ".github/workflows/deepeval-answer-relevancy.yml"
    workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch", "schedule"}
    assert "deepeval" not in (ROOT / ".github/workflows/ci.yml").read_text().lower()


def test_deepeval_is_not_a_runtime_dependency() -> None:
    assert "deepeval" not in (ROOT / "backend/pyproject.toml").read_text().lower()
    assert "deepeval==" in (ROOT / "evaluation/requirements-deepeval.txt").read_text()
