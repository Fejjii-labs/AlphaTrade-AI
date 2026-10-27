"""A7b: gated apply of the BloFin demo journal backfill plan (A6 §6 rule 3, §10)."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.db.models import JournalTrade, Membership, Organization, User
from app.schemas.common import JournalTradeStatus, MembershipRole
from app.services.blofin_demo_journal_backfill import (
    ApplyRefusedError,
    GetOnlyClient,
    apply_plan,
    build_plan,
    stored_rows,
)
from tests.support.postgres_persistence import requires_postgres
from tests.test_blofin_demo_journal_backfill import _pages
from tests.test_blofin_demo_manual_mirror import (  # noqa: F401 (autouse pin fixture)
    _FIXTURE,
    ORG,
    USER,
    _apply,
    _Client,
    _engine,
    _paths,
    _pinned_mirror_account,
    _settings,
)


@pytest.fixture
def maker() -> Any:
    factory = _engine()
    with factory() as current:
        current.add(Organization(id=ORG, name="Apply Org"))
        current.add(User(id=USER, email="apply@test.example", hashed_password="not-a-hash"))
        current.flush()
        current.add(Membership(user_id=USER, organization_id=ORG, role=MembershipRole.OWNER))
        current.commit()
    return factory


@pytest.fixture
def session(maker: Any) -> Iterator[Session]:
    with maker() as current:
        yield current


def _digest(session: Session, pages: Any) -> str:
    rows, annotated = stored_rows(session, organization_id=ORG, user_id=USER)
    return build_plan(pages, rows, annotated=annotated, user_id=USER)[1]["plan_digest"]


def _journal(session: Session) -> list[JournalTrade]:
    return list(session.scalars(select(JournalTrade).order_by(JournalTrade.external_ref)).all())


def _record(session: Session) -> tuple[list[str], Any]:
    statements: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", record)
    return statements, record


def test_apply_inserts_one_row_per_lifetime_via_live_cycle_grouping(session: Session) -> None:
    pages = _pages(_FIXTURE["partial_flat"])
    report = apply_plan(
        session, pages, organization_id=ORG, user_id=USER, approved_digest=_digest(session, pages)
    )
    session.commit()
    assert report.created == 1
    (row,) = _journal(session)
    assert row.external_ref == "blofin-demo-history:h-part-1"
    assert row.status is JournalTradeStatus.CLOSED
    assert format(row.size, "f").rstrip("0") == "0.02"
    assert format(row.net_pnl, "f").rstrip("0") == "0.145"
    assert row.exit_reason == "closed"
    assert {"blofin-history:h-part-1", "blofin-history:h-part-2"} <= set(row.tags)
    assert "blofin-position:p-part:1792195200000" in row.tags


def test_apply_is_idempotent_rerun_plans_zero_creates(session: Session) -> None:
    pages = _pages(_FIXTURE["partial_flat"])
    first = _digest(session, pages)
    apply_plan(session, pages, organization_id=ORG, user_id=USER, approved_digest=first)
    session.commit()
    rows, annotated = stored_rows(session, organization_id=ORG, user_id=USER)
    lines, summary = build_plan(pages, rows, annotated=annotated, user_id=USER)
    assert (summary["creates"], summary["updates"]) == (0, 0)
    assert [line["action"] for line in lines] == ["skip"]
    # The approved digest no longer matches the re-planned (no-op) plan.
    with pytest.raises(ApplyRefusedError) as refused:
        apply_plan(session, pages, organization_id=ORG, user_id=USER, approved_digest=first)
    assert refused.value.code == "digest_mismatch"
    again = apply_plan(
        session, pages, organization_id=ORG, user_id=USER, approved_digest=_digest(session, pages)
    )
    assert again.created == 0
    assert len(_journal(session)) == 1


def test_apply_refuses_a_digest_mismatch_and_writes_nothing(session: Session) -> None:
    pages = _pages(_FIXTURE["partial_flat"])
    statements, record = _record(session)
    try:
        with pytest.raises(ApplyRefusedError) as refused:
            apply_plan(
                session,
                pages,
                organization_id=ORG,
                user_id=USER,
                approved_digest="sha256:" + "0" * 64,
            )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", record)
    assert refused.value.code == "digest_mismatch"
    assert {s.split()[0].upper() for s in statements} <= {"SELECT"}
    session.rollback()
    assert _journal(session) == []


def test_apply_issues_no_delete_and_no_update_of_journal_rows(session: Session) -> None:
    pages = _pages(_FIXTURE["partial_flat"])
    digest = _digest(session, pages)
    statements, record = _record(session)
    try:
        apply_plan(session, pages, organization_id=ORG, user_id=USER, approved_digest=digest)
        session.commit()
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", record)
    verbs = [s.split()[0].upper() for s in statements]
    assert "INSERT" in verbs
    assert "DELETE" not in verbs
    assert not [s for s in statements if s.split()[0].upper() == "UPDATE" and "journal_trades" in s]


def test_statement_guard_refuses_a_delete_during_apply(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.blofin_demo_journal_backfill as module

    pages = _pages(_FIXTURE["partial_flat"])
    digest = _digest(session, pages)
    original = module.stored_rows
    calls = {"n": 0}

    def sneaky(current: Session, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # inside the guarded write window
            current.execute(text("DELETE FROM journal_trades"))
        return original(current, **kwargs)

    monkeypatch.setattr(module, "stored_rows", sneaky)
    with pytest.raises(Exception, match="DELETE is not allowed"):
        apply_plan(session, pages, organization_id=ORG, user_id=USER, approved_digest=digest)
    session.rollback()
    assert _journal(session) == []


def test_apply_refuses_a_plan_that_would_update_existing_rows(session: Session) -> None:
    _apply(session, _FIXTURE["partial_open"])
    _apply(session, _FIXTURE["partial_flat"])
    before = {(row.id, row.status, row.exit_reason) for row in _journal(session)}
    pages = _pages(_FIXTURE["partial_flat"])
    with pytest.raises(ApplyRefusedError) as refused:
        apply_plan(
            session,
            pages,
            organization_id=ORG,
            user_id=USER,
            approved_digest=_digest(session, pages),
        )
    assert refused.value.code == "unsupported_action"
    session.rollback()
    assert {(row.id, row.status, row.exit_reason) for row in _journal(session)} == before


def test_old_leg_applies_unverified_with_null_funding(session: Session) -> None:
    pages = _pages(_FIXTURE["long"], funding_begin=datetime(2026, 10, 17, 6, tzinfo=UTC))
    apply_plan(
        session, pages, organization_id=ORG, user_id=USER, approved_digest=_digest(session, pages)
    )
    session.commit()
    (row,) = _journal(session)
    assert row.funding is None and row.net_pnl is None
    assert row.exit_price is not None
    assert "pnl-unverified" in row.tags


def test_get_only_client_refuses_any_other_method() -> None:
    inner = _Client(_paths(_FIXTURE["long"]))  # type: ignore[arg-type]
    client = GetOnlyClient(inner)
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(ApplyRefusedError):
            client.request(method, "/api/v1/trade/order", params=None, signed=True)
    assert inner.calls == []
    client.request("GET", "/api/v1/market/instruments", params=None, signed=False)
    assert [call[0] for call in inner.calls] == ["GET"]


def _run_script(monkeypatch: pytest.MonkeyPatch, maker: Any, client: Any, argv: list[str]) -> int:
    import scripts.blofin_demo_journal_backfill as script

    monkeypatch.setattr(script, "get_settings", lambda: _settings())
    monkeypatch.setattr(script, "get_session_factory", lambda: maker)
    monkeypatch.setattr(
        "app.core.blofin_readonly_access.get_readonly_client", lambda *_a, **_k: client
    )
    monkeypatch.setattr(sys, "argv", ["backfill", "--org", str(ORG), "--user", str(USER), *argv])
    return script.main()


def _capture(tmp_path: Path) -> Path:
    path = tmp_path / "venue.json"
    path.write_text(json.dumps(_pages(_FIXTURE["partial_flat"]).capture()))
    return path


def test_script_without_apply_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, maker: Any, tmp_path: Path, capsys: Any
) -> None:
    client = _Client(_paths(_FIXTURE["partial_flat"]))  # type: ignore[arg-type]
    code = _run_script(monkeypatch, maker, client, ["--replay", str(_capture(tmp_path))])
    assert code == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["summary"]
    assert summary["creates"] == 1
    assert client.calls == []
    with maker() as current:
        assert _journal(current) == []


def test_script_apply_requires_digest_and_refuses_mismatch(
    monkeypatch: pytest.MonkeyPatch, maker: Any, tmp_path: Path, capsys: Any
) -> None:
    client = _Client(_paths(_FIXTURE["partial_flat"]))  # type: ignore[arg-type]
    replay = str(_capture(tmp_path))
    with pytest.raises(SystemExit):
        _run_script(monkeypatch, maker, client, ["--replay", replay, "--apply"])
    code = _run_script(
        monkeypatch,
        maker,
        client,
        ["--replay", replay, "--apply", "--plan-digest", "sha256:" + "0" * 64],
    )
    assert code == 3
    assert '"digest_mismatch"' in capsys.readouterr().err
    assert {method for method, _path in client.calls} == {"GET"}
    with maker() as current:
        assert _journal(current) == []


def test_script_apply_commits_then_rerun_shows_zero_creates(
    monkeypatch: pytest.MonkeyPatch, maker: Any, tmp_path: Path, capsys: Any
) -> None:
    client = _Client(_paths(_FIXTURE["partial_flat"]))  # type: ignore[arg-type]
    replay = str(_capture(tmp_path))
    _run_script(monkeypatch, maker, client, ["--replay", replay])
    digest = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["summary"]["plan_digest"]
    code = _run_script(
        monkeypatch, maker, client, ["--replay", replay, "--apply", "--plan-digest", digest]
    )
    assert code == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["created"] == 1 and result["rerun_creates"] == 0
    # The #304 pin ran before the write, GET only.
    assert client.calls and {method for method, _path in client.calls} == {"GET"}
    _run_script(monkeypatch, maker, client, ["--replay", replay])
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["summary"]
    assert (summary["creates"], summary["updates"]) == (0, 0)
    with maker() as current:
        assert len(_journal(current)) == 1


OTHER_ORG = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
OTHER_USER = uuid.UUID("00000000-0000-4000-8000-0000000000b3")


def _refused_code(session: Session, pages: Any, *, org: Any, user: Any, digest: str) -> str:
    with pytest.raises(ApplyRefusedError) as refused:
        apply_plan(session, pages, organization_id=org, user_id=user, approved_digest=digest)
    session.rollback()
    return refused.value.code


@pytest.mark.parametrize(
    ("role", "active", "code"),
    [
        (None, True, "user_not_member"),
        (MembershipRole.VIEWER, True, "user_role_refused"),
        (MembershipRole.TRADER, False, "user_inactive"),
    ],
)
def test_apply_refuses_a_wrong_user_before_any_write(
    session: Session, role: Any, active: bool, code: str
) -> None:
    pages = _pages(_FIXTURE["partial_flat"])
    digest = _digest(session, pages)
    session.add(User(id=OTHER_USER, email="o@test.example", hashed_password="x", is_active=active))
    session.flush()
    if role is not None:
        session.add(Membership(user_id=OTHER_USER, organization_id=ORG, role=role))
    session.commit()
    # Same pages give the same digest for any user, so only the member check refuses.
    assert _refused_code(session, pages, org=ORG, user=OTHER_USER, digest=digest) == code
    assert _journal(session) == []


def test_apply_refuses_the_right_user_in_a_wrong_org(session: Session) -> None:
    pages = _pages(_FIXTURE["partial_flat"])
    digest = _digest(session, pages)
    session.add(Organization(id=OTHER_ORG, name="Other Org"))
    session.commit()
    code = _refused_code(session, pages, org=OTHER_ORG, user=USER, digest=digest)
    assert code == "user_not_member"
    assert _journal(session) == []


def test_apply_accepts_a_trader_member(session: Session) -> None:
    pages = _pages(_FIXTURE["partial_flat"])
    session.add(User(id=OTHER_USER, email="t@test.example", hashed_password="x"))
    session.flush()
    session.add(Membership(user_id=OTHER_USER, organization_id=ORG, role=MembershipRole.TRADER))
    session.commit()
    rows, annotated = stored_rows(session, organization_id=ORG, user_id=OTHER_USER)
    digest = build_plan(pages, rows, annotated=annotated, user_id=OTHER_USER)[1]["plan_digest"]
    report = apply_plan(
        session, pages, organization_id=ORG, user_id=OTHER_USER, approved_digest=digest
    )
    assert report.created == 1


def test_live_cycle_takes_the_journal_lock_before_it_writes(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.blofin_demo_manual_mirror as mirror

    order: list[str] = []
    real_persist = mirror.BloFinDemoManualMirror._persist
    monkeypatch.setattr(mirror, "lock_journal_mirror", lambda _s, org: order.append(f"lock:{org}"))

    def persist(self: Any, *args: Any, **kwargs: Any) -> Any:
        order.append("persist")
        return real_persist(self, *args, **kwargs)

    monkeypatch.setattr(mirror.BloFinDemoManualMirror, "_persist", persist)
    _apply(session, _FIXTURE["long"])
    assert order == [f"lock:{ORG}", "persist"]


@requires_postgres
def test_apply_refuses_while_the_live_cycle_holds_the_org_lock() -> None:
    from app.services.blofin_demo_manual_mirror import (
        journal_mirror_lock_key,
        lock_journal_mirror,
    )
    from tests.support.postgres_persistence import phase7_plan_session_factory

    factory = phase7_plan_session_factory()
    with factory() as seed:
        seed.add(Organization(id=ORG, name="Lock Org"))
        seed.add(Organization(id=OTHER_ORG, name="Other Lock Org"))
        seed.add(User(id=USER, email="lock@test.example", hashed_password="x"))
        seed.flush()
        seed.add(Membership(user_id=USER, organization_id=ORG, role=MembershipRole.OWNER))
        seed.commit()
    pages = _pages(_FIXTURE["partial_flat"])
    with factory() as live, factory() as backfill:
        digest = _digest(backfill, pages)
        backfill.rollback()
        lock_journal_mirror(live, ORG)  # the live cycle mid-transaction
        # Another org's lock is independent.
        lock_journal_mirror(live, OTHER_ORG)
        with pytest.raises(ApplyRefusedError) as refused:
            apply_plan(backfill, pages, organization_id=ORG, user_id=USER, approved_digest=digest)
        assert refused.value.code == "mirror_cycle_busy"
        backfill.rollback()
        assert _journal(backfill) == []
        live.commit()  # releases the transaction-scoped lock
        report = apply_plan(
            backfill, pages, organization_id=ORG, user_id=USER, approved_digest=digest
        )
        assert report.created == 1
        # While the backfill transaction is open, the live cycle would wait on it.
        assert (
            live.scalar(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": journal_mirror_lock_key(ORG)},
            )
            is False
        )
        backfill.commit()
        live.rollback()
