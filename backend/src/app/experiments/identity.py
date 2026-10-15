"""Stored native verification and original execution proof; no exchange IO."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.blofin_identity import connection_binding, native_uid
from app.core.config import Settings
from app.core.errors import ConflictError, ExchangeDemoInactiveError, NotFoundError
from app.db.blofin_activity import BloFinActivityAccount
from app.db.models import AuditLog, ExecutionAccount, ExecutionCommand
from app.schemas.experiments import ExperimentAccount, ExperimentSource
from app.schemas.trade_plan import AccountMode, ExecutionMode
from app.security.tenant import TenantContext
from app.services.canonical_serialization import canonical_sha256


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def require_account(
    session: Session,
    tenant: TenantContext,
    account: ExperimentAccount,
    settings: Settings,
    *,
    now: datetime,
) -> None:
    row = session.get(ExecutionAccount, account.execution_account_id, populate_existing=True)
    if row is None or (row.organization_id, row.user_id) != (
        tenant.organization_id,
        tenant.user_id,
    ):
        raise NotFoundError("Execution account not found.")
    if (
        not row.enabled
        or row.execution_mode is not ExecutionMode.PAPER
        or row.account_mode is not AccountMode.NET
    ):
        raise ConflictError(
            "Experiment requires an enabled PAPER/NET account.", code="experiment_account_invalid"
        )
    if account.source is ExperimentSource.INTERNAL_SIMULATION:
        return
    identity = session.get(
        BloFinActivityAccount,
        (tenant.organization_id, "demo", account.native_uid),
        populate_existing=True,
    )
    audit = session.get(AuditLog, account.execution_identity_audit_id)
    if identity is None or audit is None:
        raise ConflictError(
            "Stored native account proof missing.", code="experiment_account_unverified"
        )
    proof = dict(audit.redacted_metadata)
    digest = proof.pop("evidence_hash", None)
    operation = proof.pop("operation", None)
    try:
        command_id = UUID(audit.resource_id or "")
        binding = connection_binding(settings, readonly=True)
    except (ValueError, ExchangeDemoInactiveError) as exc:
        # Missing current selectors never permit stale stored identity reuse.
        raise ConflictError(
            "Current demo connection is unverified.", code="experiment_account_unverified"
        ) from exc
    command = session.get(ExecutionCommand, command_id)
    if (
        audit.organization_id != tenant.organization_id
        or audit.user_id != tenant.user_id
        or audit.resource_type != "manual_demo_test"
        or operation != "manual_demo_execution_account_verified"
        or canonical_sha256(proof) != digest
        or native_uid({"uid": account.native_uid}) is None
        or proof.get("native_account_uid") != account.native_uid
        or proof.get("execution_account_id") != str(row.id)
        or proof.get("environment") != "demo"
        or command is None
        or command.account_id != row.id
        or command.organization_id != tenant.organization_id
        or command.user_id != tenant.user_id
        or proof.get("plan_content_hash") != command.plan_content_hash
        or identity.identity_error is not None
        or identity.credential_binding != binding
        or identity.identity_verified_at is None
        or not 0 <= (now - utc(identity.identity_verified_at)).total_seconds() <= 300
    ):
        raise ConflictError(
            "Native UID and execution account proof do not match.",
            code="experiment_account_unverified",
        )


def account_scope(account: ExperimentAccount) -> str:
    return account.native_uid or "paper:" + str(account.execution_account_id)
