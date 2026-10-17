"""Bounded synthetic REST transport; exercises the production public acquisition."""

from datetime import timedelta

import httpx
import pytest

from tests.test_trendpulse_1r_adapter import END, path


@pytest.fixture(autouse=True)
def screening_budget(monkeypatch):
    from app.market_contracts.adapters.request_budget import SlidingWeightBudget
    from app.strategy_brain.trendpulse_screening import acquisition

    monkeypatch.setattr(acquisition, "_BUDGET", SlidingWeightBudget(limit=120, max_wait_seconds=0))


def metadata():
    return {
        "futuresType": "U_MARGINED",
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ],
    }


def rest_row(bar):
    return [
        int(bar.interval_start.timestamp() * 1000),
        str(bar.open),
        str(bar.high),
        str(bar.low),
        str(bar.close),
        str(bar.base_volume),
        int(bar.interval_end.timestamp() * 1000) - 1,
        str(bar.quote_volume),
        10,
        "0",
        "0",
        "0",
    ]


class BinanceTransport:
    def __init__(self, *, short=False, end=END, failure=None, inputs=None):
        self.inputs = inputs or path(short, end=end)
        self.calls = []
        self.failure = failure
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        assert request.method == "GET"
        assert request.url.host == "fapi.binance.com"
        if self.failure == "rate_limit" and len(self.calls) == 5:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"code": -1003})
        if request.url.path == "/fapi/v1/exchangeInfo":
            return self.response(metadata())
        assert request.url.path == "/fapi/v1/klines"
        stream = "trend" if request.url.params["interval"] == "15m" else "entry"
        rows = [rest_row(bar) for bar in self.inputs[stream + "_bars"]]
        if self.failure == "row_bound":
            rows *= 10
        if self.failure == "changed_confirmation" and len(self.calls) == 3:
            rows[-1][4] = rows[-1][1]
        return self.response(rows)

    def response(self, data):
        if self.failure == "gzip":
            import gzip
            import json

            return httpx.Response(
                200,
                content=gzip.compress(json.dumps(data).encode()),
                headers={"Content-Encoding": "gzip"},
            )
        return httpx.Response(200, json=data)


class DelayedReplayAcquirer:
    """Canonical synthetic replay envelopes retain their original positive arrivals."""

    mode = "replay"
    provenance = "synthetic_fixture"

    def __init__(self, inputs=None):
        self.inputs = inputs or path()
        self.calls = 0

    def acquire(self, spec, trigger_end):
        from app.schemas.trendpulse_screening import TrendPulseScreeningEvidence

        self.calls += 1
        assert self.inputs["trigger_end"] == trigger_end
        assert spec.symbol == "BTCUSDT"
        return TrendPulseScreeningEvidence.model_validate(
            {k: v for k, v in self.inputs.items() if k not in {"trigger_end", "evaluated_at"}}
        )


def after_close():
    return END + timedelta(seconds=8)
