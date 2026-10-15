"""One bounded GET-only Binance USD-M acquisition; never backdate REST receipts."""

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

import httpx

from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.adapters.request_budget import SlidingWeightBudget
from app.market_contracts.catalog import PerpetualInstrumentCatalog
from app.market_contracts.enums import FreshnessState
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import binance_usdm_perpetual
from app.market_contracts.observation import observation_from_ohlcv
from app.market_contracts.provider_contracts import contract_from_binance_exchange_info
from app.schemas.common import Timeframe
from app.schemas.trade_plan import InstrumentRules
from app.schemas.trendpulse_screening import TrendPulseScreeningEvidence
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseSpec, exact_hash

# Shared fail-fast budget. Separate from the watcher, with no wait, retries or fallback.
_BUDGET = SlidingWeightBudget(limit=120, max_wait_seconds=0)


class ScreeningAcquisitionError(ValueError):
    def __init__(self, reason: str, evidence: TrendPulseScreeningEvidence | None = None):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence or TrendPulseScreeningEvidence()


class ScreeningAcquirer(Protocol):
    @property
    def mode(self) -> Literal["public_rest", "replay"]: ...

    @property
    def provenance(
        self,
    ) -> Literal["live_public_rest", "recorded_public_receipts", "synthetic_fixture"]: ...

    def acquire(
        self, spec: TrendPulseSpec, trigger_end: datetime
    ) -> TrendPulseScreeningEvidence: ...


class _BoundedClient(ReadOnlyHttpGetClient):
    def __init__(self, *, monotonic: Callable[[], float], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls = 0
        self.monotonic = monotonic
        self.deadline = monotonic() + 15

    def _exchange_get(self, url: str, params: Mapping[str, str | int] | None) -> httpx.Response:
        # Base request_json already checked HTTPS host/path/GET and fail-fast weight.
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise ScreeningAcquisitionError("acquisition_deadline")
        with self._client.stream(
            "GET", url, params=dict(params or {}), timeout=min(3, remaining)
        ) as response:
            payload = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if self.monotonic() > self.deadline:
                    raise ScreeningAcquisitionError("acquisition_deadline")
                if len(payload) + len(chunk) > 2 * 1024 * 1024:
                    raise ScreeningAcquisitionError("acquisition_response_bound")
                payload.extend(chunk)
            return httpx.Response(
                response.status_code,
                # iter_bytes already decoded HTTP compression; do not decode it twice.
                headers={
                    key: value
                    for key, value in response.headers.items()
                    if key not in {"content-encoding", "content-length", "transfer-encoding"}
                },
                content=bytes(payload),
                request=response.request,
            )

    def get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> object:
        if self.calls >= 5:
            raise ScreeningAcquisitionError("acquisition_request_bound")
        self.calls += 1
        payload: object = super().get_json(path, params)
        if path == "/fapi/v1/klines" and (
            not isinstance(payload, list) or len(payload) > int((params or {}).get("limit", 0))
        ):
            raise ScreeningAcquisitionError("acquisition_row_bound")
        return payload


class BinanceScreeningAcquirer:
    """Five GETs maximum: metadata and two confirmation reads per timeframe.

    Candle evaluated_at selects fixed boundaries; it is NEVER receipt time.
    Envelope arrivals use the actual clock after each confirmed stream returns.
    """

    mode: Literal["public_rest"] = "public_rest"
    provenance: Literal["live_public_rest"] = "live_public_rest"

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://fapi.binance.com",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clock, self.transport, self.base_url = clock, transport, base_url
        self.monotonic = monotonic

    def acquire(self, spec: TrendPulseSpec, trigger_end: datetime) -> TrendPulseScreeningEvidence:
        evidence = TrendPulseScreeningEvidence()
        client = _BoundedClient(
            base_url=self.base_url,
            timeout_seconds=3,
            max_retries=0,
            max_backoff_seconds=0,
            budget=_BUDGET,
            transport=self.transport,
            monotonic=self.monotonic,
        )
        try:
            instrument = binance_usdm_perpetual(spec.symbol)
            raw = client.get_json("/fapi/v1/exchangeInfo", {"symbol": spec.symbol})
            if not isinstance(raw, dict) or not isinstance(raw.get("symbols"), list):
                raise ScreeningAcquisitionError("provider_contract_invalid")
            if len(raw["symbols"]) > 5000:
                raise ScreeningAcquisitionError("acquisition_metadata_bound")
            contract = contract_from_binance_exchange_info(raw, requested_symbol=spec.symbol)
            rows = [
                r for r in raw["symbols"] if isinstance(r, dict) and r.get("symbol") == spec.symbol
            ]
            if (
                len(rows) != 1
                or contract.base_asset != instrument.base_asset
                or rows[0].get("marginAsset") != "USDT"
            ):
                raise ScreeningAcquisitionError("provider_contract_invalid")
            filters = {f["filterType"]: f for f in rows[0]["filters"]}
            rules = InstrumentRules(
                contract_type="LINEAR",
                contract_multiplier=instrument.contract_multiplier,
                base_currency=instrument.base_asset,
                quote_currency=instrument.quote_asset,
                settlement_currency=instrument.settlement_asset,
                tick_size=Decimal(filters["PRICE_FILTER"]["tickSize"]),
                lot_size=Decimal(filters["LOT_SIZE"]["stepSize"]),
                minimum_quantity=Decimal(filters["LOT_SIZE"]["minQty"]),
                minimum_notional=Decimal(filters["MIN_NOTIONAL"]["notional"]),
                rules_version=exact_hash(
                    {"source": "binance-public-research/v1", "contract": rows[0]}
                ),
            )
            evidence = evidence.model_copy(update={"instrument_rules": rules})
            source = BinanceUsdmPerpetualSource(
                client=client, catalog=PerpetualInstrumentCatalog((instrument,)), max_retries=0
            )
            opening = (trigger_end - timedelta(minutes=5)).astimezone(UTC)
            trend_end = opening.replace(minute=opening.minute // 15 * 15, second=0, microsecond=0)
            for stream, timeframe, end, count in (
                ("trend", Timeframe.M15, trend_end, 250),
                ("entry", Timeframe.M5, trigger_end, 60),
            ):
                identity = first_slice_identity(
                    timeframe=timeframe, replay=False, is_live=True, instrument=instrument
                )
                series = source.fetch_closed_ohlcv(
                    identity=identity,
                    instrument=instrument,
                    timeframe=timeframe,
                    min_final_bars=count,
                    evaluated_at=end + timedelta(seconds=5),
                )
                arrived = self.clock()
                observations = tuple(
                    observation_from_ohlcv(
                        bar,
                        identity=identity,
                        observed_at=arrived,
                        receive_time=arrived,
                        freshness_state=FreshnessState.FRESH,
                    )
                    for bar in series.bars
                )
                evidence = TrendPulseScreeningEvidence.model_validate(
                    {
                        **evidence.model_dump(),
                        stream + "_bars": series.bars,
                        stream + "_observations": observations,
                    }
                )
            return evidence
        except ScreeningAcquisitionError as exc:
            raise ScreeningAcquisitionError(exc.reason, evidence) from exc
        except (ValueError, TypeError, KeyError, httpx.HTTPError) as exc:
            # Safe bounded reason, never raw upstream URLs, bodies or headers.
            reason = "acquisition_" + type(exc).__name__.removesuffix("Error").lower()
            raise ScreeningAcquisitionError(reason, evidence) from exc
        finally:
            client.close()
