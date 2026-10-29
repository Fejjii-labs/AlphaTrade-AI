"""Hands-free voice: turn setting, auto-response accounting, barge-in and trade safety."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

import app.agent_voice as agent_voice_package
import app.agent_voice.config as voice_config
from app.agent_voice import service as voice_service
from app.agent_voice.accounting import VoiceLedger
from app.agent_voice.config import (
    LEGACY_DEFAULT_TURNS,
    MAXIMUM_HANDS_FREE_AUDIO_SECONDS,
    MAXIMUM_VOICE_TURNS,
    VoiceLimits,
    VoiceUnavailableError,
    apply_turn_setting,
    load_limits,
)
from app.agent_voice.live_config import LiveLimits, load_live_limits
from app.agent_voice.live_protocol import INSTRUCTIONS as LIVE_INSTRUCTIONS
from app.agent_voice.protocol import (
    INSTRUCTIONS,
    TOOL,
    TOOL_NAME,
    auto_responses_off,
    session_update,
)
from app.core.config import Settings
from app.core.errors import TradingPolicyError
from tests.test_agent_live_integration import live_limits  # noqa: F401
from tests.test_agent_realtime_protocol import (  # noqa: F401
    FakeLedger,
    session_for,
    voice_limits,
)


def _settings(**update: object) -> Settings:
    return Settings(_env_file=None).model_copy(update=update)


def _receipt_without_turns(limits: VoiceLimits | LiveLimits, *, cheap: bool = False) -> dict:
    data = json.loads(limits.model_dump_json())
    data.pop("maximum_turns")
    data.pop("maximum_responses", None)
    if cheap:
        # Synthetic low rates so the receipt can reserve more than the legacy turns.
        for route in ("realtime", "reasoning"):
            for key in list(data.get(route) or {}):
                if key.endswith("usd_per_million"):
                    data[route][key] = "0.01"
    return data


def _largest_fitting(limits, settings: Settings, upper: int) -> int:
    ceiling = min(limits.session_ceiling_usd, limits.remaining_budget_usd)
    for turns in range(upper, LEGACY_DEFAULT_TURNS - 1, -1):
        update = {"maximum_turns": turns}
        if isinstance(limits, VoiceLimits):
            update["maximum_responses"] = 2 * turns
        if (
            type(limits).model_validate(limits.model_dump() | update).reservation(settings)
            <= ceiling
        ):
            return turns
    return LEGACY_DEFAULT_TURNS


# --- maximum_turns setting -------------------------------------------------------


def test_turn_setting_defaults_to_twenty_with_a_hard_bound():
    assert Settings(_env_file=None).agent_voice_maximum_turns == 20
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_voice_maximum_turns=MAXIMUM_VOICE_TURNS + 1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_voice_maximum_turns=0)


def test_receipt_relying_on_default_gets_the_setting_and_paired_responses(voice_limits):  # noqa: F811
    receipt = VoiceLimits.model_validate(_receipt_without_turns(voice_limits, cheap=True))
    settings = _settings(agent_voice_maximum_turns=8, agent_max_output_tokens=1024)
    effective = apply_turn_setting(receipt, settings)
    assert effective.maximum_turns == 8
    assert effective.maximum_responses == 16
    assert effective.reservation(settings) <= effective.session_ceiling_usd


def test_explicit_receipt_turns_remain_an_upper_bound(voice_limits):  # noqa: F811
    effective = apply_turn_setting(voice_limits, _settings(agent_voice_maximum_turns=20))
    assert effective.maximum_turns == voice_limits.maximum_turns == 2
    assert effective.maximum_responses == voice_limits.maximum_responses
    lowered = apply_turn_setting(voice_limits, _settings(agent_voice_maximum_turns=1))
    assert lowered.maximum_turns == 1


def test_default_turns_shrink_to_fit_the_ceiling_but_not_below_legacy(voice_limits):  # noqa: F811
    settings = _settings(agent_voice_maximum_turns=24)
    data = _receipt_without_turns(voice_limits, cheap=True)
    ten = VoiceLimits.model_validate(data | {"maximum_turns": 10, "maximum_responses": 20})
    # A ceiling that holds exactly ten turns binds before the setting does.
    receipt = VoiceLimits.model_validate(
        data | {"session_ceiling_usd": str(ten.reservation(settings))}
    )
    effective = apply_turn_setting(receipt, settings)
    assert effective.maximum_turns == 10
    assert effective.maximum_responses == 20
    assert effective.reservation(settings) <= effective.session_ceiling_usd


def test_turn_count_is_hard_capped_at_twenty_four(voice_limits):  # noqa: F811
    # Bypass the Settings validator: the cap must hold inside apply_turn_setting too.
    settings = _settings().model_copy(update={"agent_voice_maximum_turns": 50})
    receipt = VoiceLimits.model_validate(_receipt_without_turns(voice_limits, cheap=True))
    effective = apply_turn_setting(receipt, settings)
    assert effective.maximum_turns == MAXIMUM_VOICE_TURNS == 24
    assert effective.maximum_responses == 2 * MAXIMUM_VOICE_TURNS
    data = _receipt_without_turns(voice_limits, cheap=True)
    with pytest.raises(ValidationError):
        VoiceLimits.model_validate(data | {"maximum_turns": 25, "maximum_responses": 50})


def test_receipt_reserves_one_response_of_slack(voice_limits, monkeypatch):  # noqa: F811
    settings = _settings()
    with_slack = voice_limits.reservation(settings)
    tokens = voice_limits.reserved_tokens(settings)
    monkeypatch.setattr(voice_config, "RESPONSE_SLACK", 0)
    realtime = voice_limits.realtime
    one_response = (
        realtime.maximum_input_tokens
        * max(realtime.input_usd_per_million, realtime.audio_input_usd_per_million)
        + voice_limits.maximum_output_tokens
        * max(realtime.output_usd_per_million, realtime.audio_output_usd_per_million)
    ) / 1000000
    assert with_slack - voice_limits.reservation(settings) == one_response > 0
    assert tokens - voice_limits.reserved_tokens(settings) == (
        realtime.maximum_input_tokens + voice_limits.maximum_output_tokens
    )


def _receipt_without_audio(limits: VoiceLimits) -> dict:
    data = _receipt_without_turns(limits, cheap=True)
    data.pop("maximum_audio_seconds")
    return data | {"maximum_seconds": 180}


def test_hands_free_audio_follows_session_length_when_it_fits(voice_limits):  # noqa: F811
    settings = _settings(agent_voice_maximum_turns=8)
    effective = apply_turn_setting(
        VoiceLimits.model_validate(_receipt_without_audio(voice_limits)), settings
    )
    assert effective.maximum_audio_seconds == MAXIMUM_HANDS_FREE_AUDIO_SECONDS == 180
    assert effective.maximum_turns == 8
    assert effective.reservation(settings) <= effective.session_ceiling_usd
    # An explicit receipt value stays.
    explicit = apply_turn_setting(voice_limits, settings)
    assert explicit.maximum_audio_seconds == voice_limits.maximum_audio_seconds


def test_hands_free_audio_keeps_receipt_default_when_longer_audio_cannot_fit(
    voice_limits,  # noqa: F811
):
    settings = _settings(agent_voice_maximum_turns=20)
    data = _receipt_without_audio(voice_limits)
    legacy = VoiceLimits.model_validate(
        data | {"maximum_turns": LEGACY_DEFAULT_TURNS, "maximum_responses": 12}
    )
    receipt = VoiceLimits.model_validate(
        data | {"session_ceiling_usd": str(legacy.reservation(settings))}
    )
    effective = apply_turn_setting(receipt, settings)
    assert effective.maximum_audio_seconds == 90
    assert effective.maximum_turns == LEGACY_DEFAULT_TURNS
    assert effective.reservation(settings) <= effective.session_ceiling_usd


def test_load_limits_keeps_the_ceiling_check_fail_closed(tmp_path, voice_limits):  # noqa: F811
    settings = _settings(
        agent_realtime_enabled=True, openai_api_key="fixture", provider_mode="live"
    )
    path = tmp_path / "realtime-limits.json"
    # The legacy turn count does not fit this receipt; it is still refused, not shrunk.
    path.write_text(json.dumps(_receipt_without_turns(voice_limits)))
    with pytest.raises(VoiceUnavailableError, match="verified_cap_cannot_cover_session"):
        load_limits(settings.model_copy(update={"agent_realtime_limits_file": str(path)}))
    path.write_text(voice_limits.model_dump_json())
    loaded = load_limits(settings.model_copy(update={"agent_realtime_limits_file": str(path)}))
    assert loaded.maximum_turns == 2
    path.write_text(json.dumps(_receipt_without_turns(voice_limits, cheap=True)))
    loaded = load_limits(settings.model_copy(update={"agent_realtime_limits_file": str(path)}))
    assert loaded.maximum_turns == _largest_fitting(loaded, settings, 20)
    assert loaded.reservation(settings) <= loaded.session_ceiling_usd


def test_live_receipt_uses_the_turn_setting(tmp_path, live_limits):  # noqa: F811
    settings = _settings(
        agent_live_enabled=True,
        openai_api_key="fixture",
        provider_mode="live",
        agent_voice_maximum_turns=9,
        agent_max_output_tokens=1024,
    )
    path = tmp_path / "live-limits.json"
    path.write_text(json.dumps(_receipt_without_turns(live_limits, cheap=True)))
    loaded = load_live_limits(settings.model_copy(update={"agent_live_limits_file": str(path)}))
    assert loaded.maximum_turns == 9
    assert loaded.reservation(settings) <= loaded.session_ceiling_usd


# --- auto-created response accounting --------------------------------------------


def _ledger_with_row(limits: VoiceLimits):
    ledger = VoiceLedger.__new__(VoiceLedger)
    ledger.limits = limits
    row = SimpleNamespace(response_count=0)
    data: dict = {"responses": []}
    ledger.update = lambda change: change(row, data)  # type: ignore[method-assign]
    return ledger, row, data


def test_provider_created_responses_count_toward_the_response_cap(voice_limits):  # noqa: F811
    ledger, row, data = _ledger_with_row(voice_limits)
    assert ledger.provider_response("auto-1") == voice_limits.maximum_responses - 1
    # Duplicate delivery is admitted once.
    assert ledger.provider_response("auto-1") == voice_limits.maximum_responses - 1
    assert row.response_count == 1
    assert data["responses"][0]["response_id"] == "auto-1"
    assert data["responses"][0]["origin"] == "server_vad"


def test_over_cap_provider_response_is_recorded_then_refused(voice_limits):  # noqa: F811
    ledger, row, data = _ledger_with_row(voice_limits)
    for index in range(voice_limits.maximum_responses):
        ledger.provider_response(f"auto-{index}")
    with pytest.raises(VoiceUnavailableError, match="voice_response_cap_exhausted"):
        ledger.provider_response("over-cap")
    # Recorded (may be billed), so the reservation closes uncertain, never refunded.
    assert row.response_count == voice_limits.maximum_responses + 1
    assert data["responses"][-1]["response_id"] == "over-cap"
    assert data["responses"][-1]["state"] != "done"


class CapLedger(FakeLedger):
    """FakeLedger with the real VoiceLedger response admission on an in-memory row."""

    def __init__(self, limits):
        super().__init__(limits)
        self.real, self.row, self.data = _ledger_with_row(limits)

    def response(self):
        super().response()
        return self.real.response()

    def response_started(self, key):
        super().response_started(key)
        self.real.response_started(key)

    def provider_response(self, response_id):
        super().provider_response(response_id)
        return self.real.provider_response(response_id)


@pytest.mark.asyncio
async def test_barge_in_during_a_tool_at_the_cap_admits_no_over_cap_response(
    voice_limits,  # noqa: F811
):
    # Griller PR390 B1: cap 4, user keeps barging in while a delegate runs.
    session, _ledger, _sent, upstream = session_for(voice_limits)
    session.ledger = ledger = CapLedger(voice_limits)
    session.tool_task = asyncio.create_task(asyncio.sleep(30))
    admitted = 0
    with pytest.raises(VoiceUnavailableError, match="voice_response_cap_exhausted"):
        for index in range(20):
            await session.provider({"type": "input_audio_buffer.speech_started"})
            # This fake provider ignores session.update: worst-case in-flight race.
            await session.provider({"type": "response.created", "response": {"id": f"a{index}"}})
            admitted += 1
            if session.responses_used == voice_limits.maximum_responses - 1:
                # Auto responses are switched off despite the running tool.
                assert upstream == [auto_responses_off()]
            await session.provider(
                {"type": "response.done", "response": {"id": f"a{index}", "status": "cancelled"}}
            )
    session.tool_task.cancel()
    assert admitted == voice_limits.maximum_responses
    assert ledger.row.response_count == voice_limits.maximum_responses
    assert upstream == [auto_responses_off()]
    # Even the worst case (one more already in flight) stays within the reservation.
    assert ledger.row.response_count + voice_config.RESPONSE_SLACK > admitted


@pytest.mark.asyncio
async def test_last_admission_turns_auto_responses_off_and_reconfirms(voice_limits):  # noqa: F811
    session, ledger, sent, upstream = session_for(voice_limits)
    session.responses_used = voice_limits.maximum_responses - 2
    await session._respond(grounded=True)
    assert upstream[0] == auto_responses_off()
    assert upstream[1]["type"] == "response.create"
    assert not session.auto_responses
    effective = session_update(voice_limits)["session"]
    effective["audio"]["input"]["turn_detection"]["create_response"] = False
    await session.provider({"type": "session.updated", "session": effective})
    assert "active" not in ledger.events and not sent  # no second activation/ready
    effective["audio"]["input"]["turn_detection"]["create_response"] = True
    with pytest.raises(VoiceUnavailableError, match="provider_audio_limits_not_confirmed"):
        await session.provider({"type": "session.updated", "session": effective})


@pytest.mark.asyncio
async def test_tool_call_with_an_unknown_name_is_refused(voice_limits):  # noqa: F811
    session, ledger, _sent, upstream = session_for(voice_limits)
    await session.provider({"type": "response.created", "response": {"id": "r"}})
    with pytest.raises(VoiceUnavailableError, match="unsupported_provider_tool"):
        await session.provider(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "r",
                "name": "place_order",
                "call_id": "call-x",
                "arguments": json.dumps({"message": "buy"}),
            }
        )
    assert session.tool_task is None and not upstream
    assert not [e for e in ledger.events if e[0] == "pending"]


@pytest.mark.asyncio
async def test_interrupted_auto_response_is_counted_and_forwarded(voice_limits):  # noqa: F811
    session, ledger, sent, upstream = session_for(voice_limits)
    await session.provider({"type": "response.created", "response": {"id": "auto"}})
    await session.provider({"type": "input_audio_buffer.speech_started"})
    await session.provider(
        {"type": "response.done", "response": {"id": "auto", "status": "cancelled"}}
    )
    assert ("provider_response", "auto") in ledger.events
    assert ("done", "auto") in ledger.events
    assert session.responses_used == 1
    assert [e["type"] for e in sent] == [
        "playback.clear",
        "speech.started",
        "response.interrupted",
        "response.done",
    ]
    assert not [e for e in upstream if e["type"] == "response.cancel"]


@pytest.mark.asyncio
async def test_literal_response_cancelled_forwards_an_interruption(voice_limits):  # noqa: F811
    session, _ledger, sent, _ = session_for(voice_limits)
    await session.provider({"type": "response.cancelled"})
    assert sent == [{"type": "response.interrupted"}]


@pytest.mark.asyncio
async def test_new_speech_ends_session_when_a_turn_no_longer_fits(voice_limits):  # noqa: F811
    session, _ledger, sent, _ = session_for(voice_limits)
    session.responses_used = voice_limits.maximum_responses - 1
    with pytest.raises(VoiceUnavailableError, match="voice_response_cap_exhausted"):
        await session.provider({"type": "input_audio_buffer.speech_started"})
    assert not sent


@pytest.mark.asyncio
async def test_second_tool_call_while_one_runs_is_answered_busy_not_run(voice_limits):  # noqa: F811
    calls = []
    release = asyncio.Event()

    def turn(_ledger, key, body):
        calls.append(key)
        return {"reply": "Canonical.", "conversation_id": body["conversation_id"]}

    session, ledger, _sent, upstream = session_for(voice_limits, turn)
    session.tool_task = asyncio.create_task(release.wait())  # a running delegated turn
    await session.provider({"type": "response.created", "response": {"id": "again"}})
    await session.provider(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "again",
            "name": TOOL_NAME,
            "call_id": "call-again",
            "arguments": json.dumps({"message": "and also this"}),
        }
    )
    busy = upstream[-1]
    assert busy["type"] == "conversation.item.create"
    assert busy["item"]["call_id"] == "call-again"
    assert json.loads(busy["item"]["output"])["status"] == "busy"
    assert not calls and not [e for e in ledger.events if e[0] == "pending"]
    release.set()
    await session.tool_task


# --- trade safety audit ----------------------------------------------------------

_EXECUTION_MODULES = (
    "app.providers.exchange",
    "app.services.agent_paper_execution",
    "app.services.canonical_paper_execution",
    "app.services.execution",
    "app.services.governed_blofin",
    "app.services.manual_demo",
    "app.services.paper_bot_engine",
    "app.services.paper_portfolio",
    "app.api.routes.execution",
    "app.api.routes.exchange",
)


def test_voice_exposes_only_the_read_propose_agent_delegate(voice_limits):  # noqa: F811
    session = session_update(voice_limits)["session"]
    assert session["tools"] == [TOOL]
    assert session["tool_choice"] == "auto"
    assert TOOL["name"] == TOOL_NAME == "alpha_trade_turn"
    assert TOOL["parameters"]["properties"].keys() == {"message"}
    assert TOOL["parameters"]["additionalProperties"] is False
    for text in (INSTRUCTIONS, LIVE_INSTRUCTIONS):
        assert "Never place, modify, close or cancel an order" in text
        assert "on-screen button" in text
        assert "Never state a price, position, balance, P&L" in text
    assert "unless it came from an alpha_trade_turn result" in INSTRUCTIONS


def test_no_voice_module_imports_an_execution_path():
    root = Path(agent_voice_package.__file__).parent
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                assert not name.startswith(_EXECUTION_MODULES), (path.name, name)


# Shared modules through which execution code is (transitively) importable from voice.
# Import reachability is not a call path: each entry is audited. The delegate runs
# without a paper executor (tested below and in the delegate test); the others are
# read-only grounding, config, auth, quota, usage and provider plumbing. A new voice
# import that reaches execution code must be reviewed and added here.
_AUDITED_EXECUTION_REACHABLE = frozenset(
    {
        "app.agent_capture.service",
        "app.core.config",
        "app.db.session",
        "app.interactive_agent.turn_runtime",
        "app.interactive_agent.vector_adapter",
        "app.providers.embeddings",
        "app.providers.factory",
        "app.security.rbac",
        "app.services.auth_service",
        "app.services.blofin_activity_config",
        "app.services.blofin_activity_service",
        "app.services.quota_service",
        "app.services.turn_coordinator",
        "app.services.usage_service",
    }
)


def _module_file(src: Path, module: str) -> Path | None:
    base = src.joinpath(*module.split("."))
    for candidate in (base / "__init__.py", base.with_suffix(".py")):
        if candidate.exists():
            return candidate
    return None


def _app_imports(src: Path, module: str) -> set[str]:
    path = _module_file(src, module)
    if path is None:
        return set()
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parts, *([base] if base else [])])
            found |= {base, *(f"{base}.{alias.name}" for alias in node.names)}
    return {name for name in found if name.startswith("app.") and _module_file(src, name)}


def test_voice_reaches_execution_code_only_through_audited_modules():
    root = Path(agent_voice_package.__file__).parent
    src = root.parents[1]
    voice = {f"app.agent_voice.{path.stem}" for path in root.glob("*.py")}
    closure: dict[str, bool] = {}

    def reaches_execution(module: str) -> bool:
        if module not in closure:
            seen, queue = {module}, [module]
            while queue:
                for name in _app_imports(src, queue.pop()):
                    if name not in seen:
                        seen.add(name)
                        queue.append(name)
            closure[module] = any(name.startswith(_EXECUTION_MODULES) for name in seen)
        return closure[module]

    reachable = {
        name
        for module in voice
        for name in _app_imports(src, module)
        if not name.startswith("app.agent_voice") and reaches_execution(name)
    }
    assert reachable  # the walk really follows transitive imports
    assert reachable <= _AUDITED_EXECUTION_REACHABLE, sorted(
        reachable - _AUDITED_EXECUTION_REACHABLE
    )


def test_spoken_prepare_paper_trade_is_refused_without_an_executor():
    from app.interactive_agent.action_registry import resolve_action, route_action
    from app.interactive_agent.contracts import AgentTurnRequest
    from app.interactive_agent.orchestration import propose_action

    # A well-formed spoken trade is inferred as prepare_execution; the voice delegate
    # passes no paper executor, so orchestration refuses before any prepare call.
    action = route_action(
        AgentTurnRequest(
            message=f"prepare paper trade candidate_id={uuid4()} account_id={uuid4()} "
            "symbol=BTCUSDT venue=paper market=SPOT timeframe=15m direction=long "
            "entry=100000 stop=99000 targets=102000",
            conversation_id=uuid4(),
        )
    )
    assert action is not None and action.name == "paper_trade.prepare_execution"
    tool, inputs = resolve_action(action)
    with pytest.raises(TradingPolicyError, match="paper execution authority is unavailable"):
        propose_action(
            None,  # type: ignore[arg-type]
            tool=tool,
            inputs=inputs,
            conversation=SimpleNamespace(id=uuid4(), organization_id=uuid4(), user_id=uuid4()),  # type: ignore[arg-type]
            source_message_id=uuid4(),
            paper_execution=None,
        )


@pytest.mark.asyncio
async def test_voice_delegate_turn_carries_no_action_and_no_paper_executor(
    voice_limits,  # noqa: F811
    monkeypatch,
):
    bodies = []

    def turn(_ledger, key, body):
        bodies.append(body)
        return {"reply": "Use the on-screen button.", "conversation_id": body["conversation_id"]}

    session, _ledger, sent, _ = session_for(voice_limits, turn)
    await session.provider({"type": "response.created", "response": {"id": "trade"}})
    await session.provider(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "trade",
            "name": TOOL_NAME,
            "call_id": "call-trade",
            "arguments": json.dumps({"message": "buy one BTC now"}),
        }
    )
    await session.provider(
        {"type": "response.done", "response": {"id": "trade", "status": "completed"}}
    )
    for _ in range(200):
        pending = [e for e in sent if e["type"] == "turn.pending"]
        if pending:
            break
        await asyncio.sleep(0.001)
    await session.client({"type": "turn.accept", "request_key": pending[0]["request_key"]})
    await session.tool_task
    assert bodies and bodies[0].get("action") is None
    # Only the tool's free-text message reaches the Agent; no typed action can be smuggled.
    with pytest.raises(VoiceUnavailableError, match="invalid_provider_tool_arguments"):
        await session.provider(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "smuggle",
                "name": TOOL_NAME,
                "call_id": "call-smuggle",
                "arguments": json.dumps(
                    {"message": "x", "action": {"name": "proposal.confirm", "arguments": {}}}
                ),
            }
        )

    # shared_turn runs the interactive Agent without a paper execution factory.
    captured: dict = {}

    class Coordinator:
        def __init__(self, _sessions):
            pass

        def run(self, **kwargs):
            return kwargs["work"](SimpleNamespace(conversation_id=uuid4()))

    def run_interactive(*args, **kwargs):
        captured["args"], captured["kwargs"] = args, kwargs
        raise RuntimeError("stop after capture")

    class Sessions:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    ledger = SimpleNamespace(
        sessions=lambda: Sessions(),
        settings=Settings(_env_file=None),
        limits=voice_limits,
        principal=lambda _s: SimpleNamespace(organization_id=uuid4(), user_id=uuid4()),
    )
    monkeypatch.setattr(voice_service, "read_grounding", lambda *a: "")
    monkeypatch.setattr(voice_service, "TurnCoordinator", Coordinator)
    monkeypatch.setattr(voice_service, "run_interactive", run_interactive)
    monkeypatch.setattr(
        voice_service, "resolve_providers", lambda _s: SimpleNamespace(llm=None, embeddings=None)
    )
    monkeypatch.setattr(voice_service, "voice_llm", lambda provider, _s: object())
    monkeypatch.setattr(voice_service, "VoiceReasoningProvider", lambda provider, _l: provider)
    monkeypatch.setattr(voice_service, "embedding_limitation", lambda _l: "off")
    with pytest.raises(RuntimeError, match="stop after capture"):
        voice_service.shared_turn(
            ledger,  # type: ignore[arg-type]
            str(uuid4()),
            {"message": "buy one BTC now", "conversation_id": str(uuid4())},
        )
    assert len(captured["args"]) == 4  # coordinator, settings, reservation, request
    assert "paper_factory" not in captured["kwargs"]
    assert captured["args"][3].action is None
