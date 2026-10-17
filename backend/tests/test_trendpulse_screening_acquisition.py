"""Positive REST arrival clocks, bounded requests, closed proof and no fallback."""

from datetime import timedelta

import pytest

from app.strategy_brain.trendpulse_1r.adapter import evaluate_trendpulse
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseStatus
from app.strategy_brain.trendpulse_screening.acquisition import (
    BinanceScreeningAcquirer,
    ScreeningAcquisitionError,
)
from tests.support.trendpulse_screening import BinanceTransport, after_close
from tests.support.trendpulse_screening import screening_budget as _screening_budget  # noqa: F401
from tests.test_trendpulse_1r_adapter import END, spec


@pytest.mark.parametrize("minute", [5, 10, 15])
@pytest.mark.parametrize("short", [False, True])
def test_real_acquisition_invokes_closed_proof_then_detector_with_actual_receipts(minute, short):
    end = END.replace(minute=minute)
    now = end + timedelta(seconds=8)
    provider = BinanceTransport(short=short, end=end)
    evidence = BinanceScreeningAcquirer(transport=provider.transport, clock=lambda: now).acquire(
        spec(short), end
    )
    assert len(provider.calls) == 5
    assert [c[2].get("limit") for c in provider.calls[1:]] == ["252", "252", "62", "62"]
    assert all(
        o.receive_time == now and o.observed_at == now
        for o in evidence.trend_observations + evidence.entry_observations
    )
    assert all(
        o.receive_time > b.interval_end
        for b, o in zip(
            evidence.trend_bars + evidence.entry_bars,
            evidence.trend_observations + evidence.entry_observations,
            strict=True,
        )
    )
    result = evaluate_trendpulse(
        spec(short),
        **{k: getattr(evidence, k) for k in type(evidence).model_fields if k != "instrument_rules"},
        instrument_rules=evidence.instrument_rules,
        trigger_end=end,
        evaluated_at=now,
    )
    assert result.status is TrendPulseStatus.QUALIFIED
    assert result.signal.decision_at == now
    assert result.signal.known_at == now
    assert result.signal.gross_reward_risk == 1
    assert result.signal.instrument_rules_hash
    assert not result.signal.execution_authorized


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("rate_limit", "acquisition_ratelimited"),
        ("row_bound", "acquisition_row_bound"),
        ("changed_confirmation", "acquisition_formingcandle"),
    ],
)
def test_acquisition_failures_are_bounded_and_retain_partial_original_receipts(failure, reason):
    provider = BinanceTransport(failure=failure)
    with pytest.raises(ScreeningAcquisitionError) as error:
        BinanceScreeningAcquirer(transport=provider.transport, clock=after_close).acquire(
            spec(), END
        )
    assert error.value.reason == reason
    assert len(provider.calls) <= 5
    if failure == "rate_limit":
        assert len(provider.calls) == 5  # No retry, sleep or provider fallback.
        assert len(error.value.evidence.trend_observations) == 250
        assert all(o.receive_time == after_close() for o in error.value.evidence.trend_observations)
        assert error.value.evidence.entry_observations == ()


def test_response_bytes_and_acquisition_deadline_are_bounded():
    import httpx

    def too_large(_request):
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))

    with pytest.raises(ScreeningAcquisitionError, match="acquisition_response_bound"):
        BinanceScreeningAcquirer(
            transport=httpx.MockTransport(too_large), clock=after_close
        ).acquire(spec(), END)
    times = iter((0, 1, 16))
    provider = BinanceTransport()
    with pytest.raises(ScreeningAcquisitionError, match="acquisition_deadline"):
        BinanceScreeningAcquirer(
            transport=provider.transport, clock=after_close, monotonic=lambda: next(times)
        ).acquire(spec(), END)
    assert len(provider.calls) == 1


def test_recorded_replay_preserves_original_arrivals_and_rejects_spec_or_size_changes(tmp_path):
    from app.strategy_brain.trendpulse_1r.contracts import exact_hash
    from app.strategy_brain.trendpulse_screening.recorded import (
        RecordedScreeningAcquirer,
        RecordedScreeningWindow,
    )
    from tests.support.trendpulse_screening import DelayedReplayAcquirer

    original = DelayedReplayAcquirer().acquire(spec(), END)
    window = RecordedScreeningWindow(
        provenance="synthetic_fixture",
        spec_hash=exact_hash(spec()),
        trigger_end=END,
        decision_at=after_close(),
        evidence=original,
    )
    file = tmp_path / "window.json"
    file.write_text(window.model_dump_json())
    replay = RecordedScreeningAcquirer.from_file(file)
    assert replay.acquire(spec(), END) == original
    with pytest.raises(ValueError, match="provenance contradicts"):
        RecordedScreeningWindow.model_validate(
            {**window.model_dump(), "provenance": "recorded_public_receipts"}
        )
    with pytest.raises(ScreeningAcquisitionError, match="binding_mismatch"):
        replay.acquire(spec(True), END)
    file.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(ScreeningAcquisitionError, match="size_bound"):
        RecordedScreeningAcquirer.from_file(file)


def test_compressed_public_responses_are_decoded_once_within_the_same_bounds():
    provider = BinanceTransport(failure="gzip")
    evidence = BinanceScreeningAcquirer(transport=provider.transport, clock=after_close).acquire(
        spec(), END
    )
    assert len(provider.calls) == 5
    assert len(evidence.trend_bars) == 250 and len(evidence.entry_bars) == 60
