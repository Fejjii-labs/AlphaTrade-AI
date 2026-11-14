#!/usr/bin/env python3
"""Non-blocking DeepEval answer-relevancy layer on top of ``evaluate_rag.py``.

Reuses the offline RAG retrieval path from ``evaluate_rag.py`` (ingest -> index ->
vector search -> relevance floor, lexical embeddings by default) and scores the
retrieved answer with ONE metric: DeepEval ``AnswerRelevancyMetric``.

- Golden set: ``golden/answer_relevancy_v1.jsonl`` (versioned, 30-50 cases, every
  expected answer taken from ``datasets/rag_corpus.json``).
- Answer: the text of the top-ranked retrieved document (extractive; no generation
  model). ``retrieval_context`` is the top-3 retrieved documents.
- Judge: ``DEEPEVAL_JUDGE_MODEL`` (default: a pinned, dated snapshot id), temperature 0.
- Scoring: each case is judged ``--runs`` times (default 3); the case score is the
  median of those runs and the suite score is the mean of the case medians.
- Failure rule: exit 1 only if the suite score is more than ``DEEPEVAL_MARGIN``
  (default 0.05) below the stored baseline ``baselines/answer_relevancy_v1.json``.
  An unmeasured baseline (``suite_score: null``) or a different judge model is
  report-only.
- Spend cap: ``DEEPEVAL_MAX_USD`` (default 1.00) and ``DEEPEVAL_MAX_JUDGE_CALLS``
  (default 150). Each judge call is charged at the larger of DeepEval's reported cost
  and ``DEEPEVAL_EST_USD_PER_CALL`` (default 0.005). The run refuses to start if the
  projected spend exceeds the cap, and aborts before the call that would exceed it.

Exit codes: 0 pass / report-only, 1 regression beyond margin, 2 bad config or data,
3 spend cap reached (aborted cleanly; partial results are still written).

This is an LLM-judge eval: it calls the paid OpenAI API (needs OPENAI_API_KEY), is
never part of ci.yml and never a required check. Install its extra dependency with
``uv pip install -r ../evaluation/requirements-deepeval.txt`` from ``backend/``.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

import evaluate_rag  # noqa: E402  (also puts backend/src on sys.path)

GOLDEN_VERSION = "answer_relevancy_v1"
GOLDEN_PATH = EVAL_DIR / "golden" / f"{GOLDEN_VERSION}.jsonl"
BASELINE_PATH = EVAL_DIR / "baselines" / f"{GOLDEN_VERSION}.json"
MIN_CASES, MAX_CASES = 30, 50
CONTEXT_DOCS = 3
NO_SOURCE_ANSWER = "No relevant source was found in the knowledge base."

DEFAULT_JUDGE_MODEL = "gpt-4.1-mini-2025-04-14"
DEFAULT_MAX_USD = 1.00
DEFAULT_MAX_JUDGE_CALLS = 150
DEFAULT_EST_USD_PER_CALL = 0.005
DEFAULT_MARGIN = 0.05
DEFAULT_RUNS = 3

GOLDEN_FIELDS = {
    "version": str,
    "id": str,
    "category": str,
    "input": str,
    "expected_output": str,
    "source_document_keys": list,
}


class GoldenSetError(ValueError):
    """The golden set file is missing, malformed, or out of bounds."""


class SpendCapError(RuntimeError):
    """The next judge call would exceed the configured spend cap."""


@dataclass(frozen=True)
class JudgeConfig:
    model: str = DEFAULT_JUDGE_MODEL
    temperature: float = 0.0
    max_usd: float = DEFAULT_MAX_USD
    max_calls: int = DEFAULT_MAX_JUDGE_CALLS
    est_usd_per_call: float = DEFAULT_EST_USD_PER_CALL
    margin: float = DEFAULT_MARGIN

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> JudgeConfig:
        env = os.environ if env is None else env
        config = cls(
            model=env.get("DEEPEVAL_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL,
            max_usd=float(env.get("DEEPEVAL_MAX_USD") or DEFAULT_MAX_USD),
            max_calls=int(env.get("DEEPEVAL_MAX_JUDGE_CALLS") or DEFAULT_MAX_JUDGE_CALLS),
            est_usd_per_call=float(
                env.get("DEEPEVAL_EST_USD_PER_CALL") or DEFAULT_EST_USD_PER_CALL
            ),
            margin=float(env.get("DEEPEVAL_MARGIN") or DEFAULT_MARGIN),
        )
        if config.max_usd < 0 or config.max_calls < 0 or config.margin < 0:
            raise ValueError("DEEPEVAL_MAX_USD, _MAX_JUDGE_CALLS and _MARGIN must be >= 0")
        if config.est_usd_per_call <= 0:
            raise ValueError("DEEPEVAL_EST_USD_PER_CALL must be > 0")
        return config


@dataclass(frozen=True)
class GoldenCase:
    id: str
    category: str
    input: str
    expected_output: str
    source_document_keys: tuple[str, ...]


@dataclass(frozen=True)
class JudgeVerdict:
    score: float
    cost_usd: float | None = None


class Judge(Protocol):
    def score(
        self, *, input: str, actual_output: str, retrieval_context: list[str]
    ) -> JudgeVerdict: ...


def load_golden_set(
    path: Path = GOLDEN_PATH,
    *,
    version: str = GOLDEN_VERSION,
    known_document_keys: set[str] | None = None,
) -> list[GoldenCase]:
    """Load and validate the golden set: schema, version, unique ids, 30-50 cases."""
    if not path.is_file():
        raise GoldenSetError(f"golden set not found: {path}")
    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenSetError(f"line {lineno}: invalid JSON ({exc.msg})") from exc
        if not isinstance(row, dict) or set(row) != set(GOLDEN_FIELDS):
            raise GoldenSetError(f"line {lineno}: fields must be exactly {sorted(GOLDEN_FIELDS)}")
        for name, kind in GOLDEN_FIELDS.items():
            if not isinstance(row[name], kind) or not row[name]:
                raise GoldenSetError(f"line {lineno}: {name} must be a non-empty {kind.__name__}")
        if row["version"] != version:
            raise GoldenSetError(f"line {lineno}: version {row['version']!r} != {version!r}")
        if row["id"] in seen:
            raise GoldenSetError(f"line {lineno}: duplicate id {row['id']!r}")
        keys = row["source_document_keys"]
        if not all(isinstance(key, str) and key for key in keys):
            raise GoldenSetError(f"line {lineno}: source_document_keys must be non-empty strings")
        if known_document_keys is not None and not set(keys) <= known_document_keys:
            unknown = sorted(set(keys) - known_document_keys)
            raise GoldenSetError(f"line {lineno}: unknown source documents {unknown}")
        seen.add(row["id"])
        cases.append(
            GoldenCase(
                row["id"], row["category"], row["input"], row["expected_output"], tuple(keys)
            )
        )
    if not MIN_CASES <= len(cases) <= MAX_CASES:
        raise GoldenSetError(f"golden set has {len(cases)} cases; expected {MIN_CASES}-{MAX_CASES}")
    return cases


class SpendGuard:
    """Hard cap on judge calls and estimated USD for one run."""

    def __init__(self, config: JudgeConfig) -> None:
        self.config = config
        self.calls = 0
        self.spent_usd = 0.0
        self.per_call_usd = config.est_usd_per_call

    def check_projection(self, planned_calls: int) -> None:
        projected = planned_calls * self.config.est_usd_per_call
        if planned_calls > self.config.max_calls or projected > self.config.max_usd:
            raise SpendCapError(
                f"planned {planned_calls} judge calls (~${projected:.2f}) exceed the cap "
                f"({self.config.max_calls} calls / ${self.config.max_usd:.2f})"
            )

    def before_call(self) -> None:
        next_spend = self.spent_usd + self.per_call_usd
        if self.calls + 1 > self.config.max_calls or next_spend > self.config.max_usd:
            raise SpendCapError(
                f"spend cap reached after {self.calls} judge calls (${self.spent_usd:.4f} of "
                f"${self.config.max_usd:.2f}, max {self.config.max_calls} calls)"
            )

    def after_call(self, reported_cost: float | None) -> None:
        charged = max(reported_cost or 0.0, self.config.est_usd_per_call)
        self.calls += 1
        self.spent_usd += charged
        self.per_call_usd = max(self.per_call_usd, charged)


@dataclass
class CaseScore:
    case_id: str
    runs: list[float]
    median: float


@dataclass
class SuiteResult:
    cases: list[CaseScore] = field(default_factory=list)
    judge_calls: int = 0
    spent_usd: float = 0.0
    aborted: str | None = None

    @property
    def suite_score(self) -> float | None:
        if not self.cases:
            return None
        return statistics.fmean(case.median for case in self.cases)


AnswerFn = Callable[[GoldenCase], tuple[str, list[str]]]


def run_suite(
    cases: Sequence[GoldenCase],
    answer: AnswerFn,
    judge: Judge,
    guard: SpendGuard,
    *,
    runs: int = DEFAULT_RUNS,
) -> SuiteResult:
    """Judge every case ``runs`` times and keep the median; stop cleanly at the spend cap."""
    if runs < 1 or runs % 2 == 0:
        raise ValueError("runs must be a positive odd number so the median is a real run")
    result = SuiteResult()
    try:
        guard.check_projection(len(cases) * runs)
        for case in cases:
            actual_output, context = answer(case)
            scores: list[float] = []
            for _ in range(runs):
                guard.before_call()
                verdict = judge.score(
                    input=case.input, actual_output=actual_output, retrieval_context=context
                )
                guard.after_call(verdict.cost_usd)
                scores.append(verdict.score)
            result.cases.append(CaseScore(case.id, scores, statistics.median(scores)))
    except SpendCapError as exc:
        result.aborted = str(exc)
    result.judge_calls, result.spent_usd = guard.calls, guard.spent_usd
    return result


@dataclass(frozen=True)
class GateDecision:
    failed: bool
    message: str


def gate(
    suite_score: float | None,
    baseline: Mapping[str, Any],
    *,
    margin: float,
    judge_model: str,
) -> GateDecision:
    """Fail only if the suite score drops more than ``margin`` below the baseline."""
    if suite_score is None:
        return GateDecision(False, "no completed cases; nothing to compare")
    stored = baseline.get("suite_score")
    if stored is None:
        return GateDecision(False, "baseline not yet measured; report-only")
    if baseline.get("judge_model") != judge_model:
        return GateDecision(
            False,
            f"baseline judge {baseline.get('judge_model')!r} != {judge_model!r}; report-only",
        )
    floor = float(stored) - margin
    if suite_score < floor:
        return GateDecision(
            True, f"suite score {suite_score:.3f} < baseline {stored:.3f} - margin {margin:.3f}"
        )
    return GateDecision(
        False, f"suite score {suite_score:.3f} >= baseline {stored:.3f} - margin {margin:.3f}"
    )


def load_baseline(path: Path = BASELINE_PATH, *, version: str = GOLDEN_VERSION) -> dict[str, Any]:
    baseline: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if baseline.get("golden_version") != version:
        raise GoldenSetError(
            f"baseline golden_version {baseline.get('golden_version')!r} != {version!r}"
        )
    return baseline


class DeepEvalJudge:
    """``AnswerRelevancyMetric`` with a pinned OpenAI judge at temperature 0."""

    def __init__(self, config: JudgeConfig) -> None:
        from deepeval.metrics import AnswerRelevancyMetric
        from deepeval.models import OpenAIModel

        self._metric_cls = AnswerRelevancyMetric
        self._model = OpenAIModel(model=config.model, temperature=config.temperature)

    def score(
        self, *, input: str, actual_output: str, retrieval_context: list[str]
    ) -> JudgeVerdict:
        from deepeval.test_case import LLMTestCase

        metric = self._metric_cls(model=self._model, include_reason=False, async_mode=False)
        metric.measure(
            LLMTestCase(
                input=input, actual_output=actual_output, retrieval_context=retrieval_context
            )
        )
        return JudgeVerdict(float(metric.score or 0.0), metric.evaluation_cost)


def extractive_answer(
    ranked: list[str], corpus: Mapping[str, Mapping[str, Any]]
) -> tuple[str, list[str]]:
    """Answer with the top retrieved document; context is the top-3 documents."""
    context = [str(corpus[key]["text"]) for key in ranked[:CONTEXT_DOCS]]
    return (context[0] if context else NO_SOURCE_ANSWER), context


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--embeddings", choices=("lexical", "provider"), default="lexical")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--limit", type=int, default=None, help="Judge only the first N cases.")
    parser.add_argument("--output", type=Path, default=None, help="Write a JSON result file.")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Store this run's suite score as the baseline (complete runs only).",
    )
    args = parser.parse_args(argv)

    try:
        config = JudgeConfig.from_env()
        corpus_keys = {doc["key"] for doc in evaluate_rag.load_json("rag_corpus.json")}
        cases = load_golden_set(known_document_keys=corpus_keys)
        baseline = load_baseline()
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.limit is not None:
        cases = cases[: args.limit]
    try:
        judge = DeepEvalJudge(config)
    except ImportError:
        print("ERROR: deepeval is not installed; see requirements-deepeval.txt", file=sys.stderr)
        return 2

    from app.core.config import Settings

    min_score = Settings.model_fields["rag_min_relevance_score"].default
    embeddings = evaluate_rag.build_embeddings(args.embeddings)
    with evaluate_rag.pipeline_rank_fn(embeddings, min_score=min_score) as (rank, corpus):

        def answer(case: GoldenCase) -> tuple[str, list[str]]:
            return extractive_answer(rank({"query": case.input}), corpus)

        result = run_suite(cases, answer, judge, SpendGuard(config), runs=args.runs)

    for case in result.cases:
        runs = ", ".join(f"{score:.3f}" for score in case.runs)
        print(f"{case.case_id}: median={case.median:.3f} runs=[{runs}]")
    score = result.suite_score
    print(
        f"\nAnswer relevancy ({GOLDEN_VERSION}, judge={config.model}, runs={args.runs}): "
        f"suite={'n/a' if score is None else f'{score:.3f}'} cases={len(result.cases)}/"
        f"{len(cases)} judge_calls={result.judge_calls} est_spend=${result.spent_usd:.4f}"
    )
    decision = gate(score, baseline, margin=config.margin, judge_model=config.model)
    print(("REGRESSION: " if decision.failed else "OK: ") + decision.message)

    if args.output:
        payload = {
            "golden_version": GOLDEN_VERSION,
            "judge_model": config.model,
            "runs": args.runs,
            "suite_score": score,
            "judge_calls": result.judge_calls,
            "est_spend_usd": round(result.spent_usd, 4),
            "aborted": result.aborted,
            "gate": asdict(decision),
            "cases": [asdict(case) for case in result.cases],
        }
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if result.aborted:
        print(f"ABORTED: {result.aborted}", file=sys.stderr)
        return 3
    if args.write_baseline and score is not None:
        if args.limit is not None:
            print("ERROR: refusing to write a baseline from a --limit run", file=sys.stderr)
            return 2
        baseline.update(
            suite_score=round(score, 4),
            judge_model=config.model,
            runs=args.runs,
            case_medians={case.case_id: round(case.median, 4) for case in result.cases},
        )
        BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline written to {BASELINE_PATH}")
    return 1 if decision.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
