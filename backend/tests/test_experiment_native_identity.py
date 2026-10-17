"""Original command evidence + current read-only UID verification, using MockTransport only."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.blofin_identity import connection_binding
from app.core.config import Environment, Settings
from app.core.errors import ConflictError
from app.db.blofin_activity import BloFinActivityAccount
from app.db.models import AuditLog
from app.experiments.identity import require_account
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.schemas.experiments import ExperimentAccount
from app.schemas.manual_demo import ManualDemoConfirmation, ManualDemoPreviewRequest
from app.services.manual_demo_service import ManualDemoService
from tests.support.experiment_fixtures import World
from tests.support.experiment_fixtures import (
    experiment_engine as _experiment_engine,  # noqa: F401
)
from tests.test_manual_blofin_demo import ManualVenue


def original_execution_proof(w):
    settings = Settings(
        _env_file=None,
        environment="local",
        provider_mode="mock",
        execution_mode="paper",
        exchange_mode="paper_exchange_demo",
        blofin_demo_enabled=True,
        manual_blofin_demo_enabled=True,
        governed_blofin_demo_enabled=False,
        enable_real_trading=False,
        global_kill_switch_active=False,
        blofin_api_key="fixture-execution",
        blofin_api_secret="fixture",
        blofin_api_passphrase="fixture",
        blofin_readonly_api_key="fixture-reader-a",
        blofin_readonly_api_secret="fixture",
        blofin_readonly_api_passphrase="fixture",
        blofin_demo_rest_base_url="https://demo-trading-openapi.blofin.com",
        governed_blofin_demo_organization_id=str(w.tenant.organization_id),
        governed_blofin_demo_user_id=str(w.tenant.user_id),
        governed_blofin_demo_account_id=str(w.account.id),
    ).model_copy(
        update={"environment": Environment.STAGING, "perpetual_evidence_source": "binance_usdm"}
    )
    venue = ManualVenue(Decimal("100000"))
    requests = []

    def transport(request):
        requests.append(request.url.path)
        if request.url.path == "/api/v1/user/query-apikey":
            return httpx.Response(200, json={"code": "0", "data": {"readOnly": 0, "uid": "uid-a"}})
        return venue.handle(request)

    provider = GovernedBloFinDemoProvider(
        BloFinClient(
            base_url=settings.blofin_demo_rest_base_url,
            api_key="fixture",
            api_secret="fixture",
            api_passphrase="fixture",
            transport=httpx.MockTransport(transport),
            sleeper=lambda _: None,
            max_retries=0,
        ),
        clock=lambda: w.now,
    )
    service = ManualDemoService(w.session, settings, provider=provider, clock=lambda: w.now)
    plan = service.preview(
        w.tenant, ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000")
    )
    result = service.confirm(
        w.tenant,
        ManualDemoConfirmation(
            revision_id=plan.revision_id,
            content_hash=plan.content_hash,
            confirm=True,
            label="manual demo test",
        ),
    )
    audit = next(
        a
        for a in w.session.scalars(
            select(AuditLog).where(AuditLog.resource_id == str(result.command_id))
        )
        if a.redacted_metadata.get("operation") == "manual_demo_execution_account_verified"
    )
    identity = BloFinActivityAccount(
        organization_id=w.tenant.organization_id,
        environment="demo",
        account_uid="uid-a",
        credential_binding=connection_binding(settings, readonly=True),
        identity_verified_at=w.now,
        identity_error=None,
    )
    w.session.add(identity)
    w.session.flush()
    account = ExperimentAccount(
        source="blofin_demo",
        execution_account_id=w.account.id,
        native_uid="uid-a",
        execution_identity_audit_id=audit.id,
    )
    return settings, identity, account, requests


@pytest.fixture
def native_world(experiment_engine):
    # Existing dispatch checks committed authority through another connection.
    with Session(experiment_engine, expire_on_commit=False) as session:
        yield World(session)


def test_proven_native_account_and_rotated_same_uid_keep_history(native_world):
    w = native_world
    settings, identity, account, requests = original_execution_proof(w)
    before = len(requests)
    require_account(w.session, w.tenant, account, settings, now=w.now)
    w.service.settings = settings
    version = w.running(w.configuration(account=account))
    assert version.configuration.account.native_uid == "uid-a"
    rotated = settings.model_copy(update={"blofin_readonly_api_key": "fixture-reader-rotated"})
    with pytest.raises(ConflictError, match="do not match"):
        require_account(w.session, w.tenant, account, rotated, now=w.now)
    identity.credential_binding = connection_binding(rotated, readonly=True)
    identity.identity_verified_at = w.now
    w.session.flush()
    require_account(w.session, w.tenant, account, rotated, now=w.now)
    assert len(requests) == before  # Domain identity verification never contacts the exchange.
    assert w.session.get(AuditLog, account.execution_identity_audit_id) is not None


@pytest.mark.parametrize("case", ["uid_switch", "unverified", "stale", "execution_audit_other_uid"])
def test_replacement_connection_cannot_reuse_account_proof(native_world, case):
    w = native_world
    settings, identity, account, _ = original_execution_proof(w)
    if case == "uid_switch":
        account = account.model_copy(update={"native_uid": "uid-b"})
        w.session.add(
            BloFinActivityAccount(
                organization_id=w.tenant.organization_id,
                environment="demo",
                account_uid="uid-b",
                credential_binding=identity.credential_binding,
                identity_verified_at=w.now,
            )
        )
    elif case == "unverified":
        identity.identity_verified_at = None
    elif case == "stale":
        identity.identity_verified_at = w.now - timedelta(seconds=301)
    else:
        account = account.model_copy(update={"native_uid": "uid-b"})
    w.session.flush()
    with pytest.raises(ConflictError):
        require_account(w.session, w.tenant, account, settings, now=w.now)
