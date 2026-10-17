"""Existing balance/position reads must prove the currently selected native connection."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.blofin_readonly_access import get_readonly_account_provider
from app.core.errors import NotFoundError
from app.db.models import BloFinDemoSyncSnapshot, User
from app.services.blofin_activity_service import run_activity_sync
from app.services.blofin_sync_service import BloFinSyncService
from tests.test_blofin_activity_postgres import world
from tests.test_blofin_activity_provider import NOW
from tests.test_dashboard_demo_account import transport

__all__ = ["world"]


def test_snapshot_rotation_uid_switch_and_unverified_replacement_preserve_history(
    world, monkeypatch
):
    engine, settings, config, scope, venue = world
    user_id = uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, email="snapshot@example.test", hashed_password="fixture"))
        session.commit()
    monkeypatch.setattr("app.services.blofin_sync_service.get_activity_settings", lambda: config)

    def bind_provider(uid):
        monkeypatch.setattr(
            "app.services.blofin_sync_service.get_readonly_account_provider",
            lambda cfg: get_readonly_account_provider(
                cfg, transport=transport([], identity={"uid": uid, "readOnly": 1})
            ),
        )

    bind_provider(scope.account_uid)
    with Session(engine) as session:
        initial = (
            BloFinSyncService(session, settings)
            .sync(organization_id=scope.organization_id, user_id=user_id)
            .snapshot
        )
        session.commit()
        assert initial.balance_count == initial.position_count == 1
        assert "connection_binding" not in initial.provenance
        old_payload = dict(initial.account_snapshot)

    rotated = settings.model_copy(update={"blofin_readonly_api_key": "rotated-fixture"})
    with Session(engine) as session, pytest.raises(NotFoundError, match="unverified"):
        BloFinSyncService(session, rotated).latest(organization_id=scope.organization_id)

    # A signed UID verification on the replacement key proves A; no snapshot rewrite.
    for kind in ("order", "fill"):
        venue.pages[kind] = {None: []}
    assert (
        run_activity_sync(
            engine, rotated, config, provider=venue.provider(), clock=lambda: NOW
        ).status
        == "bounded"
    )
    with Session(engine) as session:
        recovered = BloFinSyncService(session, rotated).latest(
            organization_id=scope.organization_id
        )
        assert recovered.id == initial.id and recovered.account_snapshot == old_payload

    replacement = rotated.model_copy(update={"blofin_readonly_api_key": "account-B-fixture"})
    config = config.model_copy(update={"expected_uid": "snapshot-native-B"})
    with Session(engine) as session, pytest.raises(NotFoundError, match="unverified"):
        BloFinSyncService(session, replacement).latest(organization_id=scope.organization_id)
    venue.uid = config.expected_uid
    venue.identity = {"uid": venue.uid, "readOnly": 1}
    assert (
        run_activity_sync(
            engine, replacement, config, provider=venue.provider(), clock=lambda: NOW
        ).status
        == "bounded"
    )
    # B is verified, but A's older balances and positions remain excluded.
    with Session(engine) as session, pytest.raises(NotFoundError):
        BloFinSyncService(session, replacement).latest(organization_id=scope.organization_id)
    bind_provider(venue.uid)
    with Session(engine) as session:
        current = (
            BloFinSyncService(session, replacement)
            .sync(organization_id=scope.organization_id, user_id=user_id)
            .snapshot
        )
        session.commit()
        assert current.provenance["native_account_uid"] == venue.uid
        assert (
            BloFinSyncService(session, replacement).latest(organization_id=scope.organization_id).id
            == current.id
        )
        old = session.get(BloFinDemoSyncSnapshot, initial.id)
        # Make A newer than B to prove selection is by account, not newest organization row.
        old.synced_at = current.synced_at + timedelta(seconds=1)
        session.commit()
        assert (
            BloFinSyncService(session, replacement).latest(organization_id=scope.organization_id).id
            == current.id
        )
        assert old.account_snapshot == old_payload
        legacy = BloFinDemoSyncSnapshot(
            organization_id=scope.organization_id,
            user_id=user_id,
            synced_at=current.synced_at + timedelta(seconds=2),
            health_status="ok",
            provider="blofin_demo",
            exchange_mode="paper_internal",
            account_snapshot={"balances": [{"asset": "USDT", "total": "999999"}]},
            positions_snapshot={"items": [{"inst_id": "UNVERIFIED"}]},
            provenance={"provider": "blofin_demo"},
            balance_count=1,
            position_count=1,
        )
        session.add(legacy)
        session.commit()
        assert (
            BloFinSyncService(session, replacement).latest(organization_id=scope.organization_id).id
            == current.id
        )
        assert session.scalar(select(func.count()).select_from(BloFinDemoSyncSnapshot)) == 3
    unverified = replacement.model_copy(update={"blofin_readonly_api_key": "unverified-fixture"})
    with Session(engine) as session:
        with pytest.raises(NotFoundError, match="unverified"):
            BloFinSyncService(session, unverified).latest(organization_id=scope.organization_id)
        assert session.scalar(select(func.count()).select_from(BloFinDemoSyncSnapshot)) == 3
    venue.identity = {"uid": "unexpected-uid", "readOnly": 1}
    assert (
        run_activity_sync(
            engine, replacement, config, provider=venue.provider(), clock=lambda: NOW
        ).error_code
        == "identity_unverified"
    )
    with Session(engine) as session, pytest.raises(NotFoundError, match="unverified"):
        BloFinSyncService(session, replacement).latest(organization_id=scope.organization_id)


@pytest.mark.parametrize(
    "identity",
    [
        {"readOnly": 1},
        {"readOnly": 1, "parentUid": "demo-user-1"},
        {"readOnly": 1, "uid": "different"},
    ],
)
def test_unverified_snapshot_refresh_withholds_native_data(world, monkeypatch, identity):
    engine, settings, config, scope, _ = world
    user_id = uuid4()
    monkeypatch.setattr("app.services.blofin_sync_service.get_activity_settings", lambda: config)
    seen = []
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda cfg: get_readonly_account_provider(
            cfg, transport=transport(seen, identity=identity)
        ),
    )
    with Session(engine) as session:
        session.add(User(id=user_id, email="missing@example.test", hashed_password="fixture"))
        session.flush()
        result = (
            BloFinSyncService(session, settings)
            .sync(organization_id=scope.organization_id, user_id=user_id)
            .snapshot
        )
        session.commit()
        assert result.health_status.value == "unavailable"
        assert result.account_snapshot == {} and result.positions_snapshot["items"] == []
        assert result.balance_count == result.position_count == 0
        with pytest.raises(NotFoundError):
            BloFinSyncService(session, settings).latest(organization_id=scope.organization_id)
    assert seen == [("GET", "/api/v1/user/query-apikey")]
