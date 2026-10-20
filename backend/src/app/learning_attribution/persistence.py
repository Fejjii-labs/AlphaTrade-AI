"""Exact later persistence contract. This wave does not emit Alembic."""

from __future__ import annotations

from app.learning_attribution.contracts import (
    DurableAttributionIntegrationRequirement,
    DurableColumnSpec,
)

AGENT_1_ATTRIBUTION_INTEGRATION = DurableAttributionIntegrationRequirement(
    record_columns=(
        DurableColumnSpec(
            name="id",
            sql_type="UUID PRIMARY KEY",
            nullable=False,
            purpose="Deterministic uuid5(org, candidate_id) attribution aggregate id.",
        ),
        DurableColumnSpec(
            name="organization_id",
            sql_type="UUID NOT NULL REFERENCES organizations(id)",
            nullable=False,
            purpose="Tenant scope. Cross-tenant writes fail closed.",
        ),
        DurableColumnSpec(
            name="user_id",
            sql_type="UUID NOT NULL REFERENCES users(id)",
            nullable=False,
            purpose="Acting user for audit; not a second tenant key.",
        ),
        DurableColumnSpec(
            name="account_id",
            sql_type="UUID NOT NULL",
            nullable=False,
            purpose="Execution account scope copied from journal lifecycle events.",
        ),
        DurableColumnSpec(
            name="candidate_id",
            sql_type="UUID NOT NULL",
            nullable=False,
            purpose="Canonical Phase 6 Candidate id. UNIQUE with organization_id.",
        ),
        DurableColumnSpec(
            name="assessment_id",
            sql_type="UUID NOT NULL",
            nullable=False,
            purpose="SetupAssessment id. Learning must not rewrite this row's market truth.",
        ),
        DurableColumnSpec(
            name="evidence_window_hash",
            sql_type="CHAR(64) NOT NULL",
            nullable=False,
            purpose="CanonicalEvidenceWindowV1 hash copied from Candidate/SetupAssessment.",
        ),
        DurableColumnSpec(
            name="execution_lifecycle_id",
            sql_type="UUID NULL",
            nullable=True,
            purpose="Binds to journal_trades.execution_lifecycle_id. UNIQUE per org when set.",
        ),
        DurableColumnSpec(
            name="journal_trade_id",
            sql_type="UUID NULL REFERENCES journal_trades(id)",
            nullable=True,
            purpose="Projector-owned JournalTrade. REJECT/SKIP remain NULL for outcomes.",
        ),
        DurableColumnSpec(
            name="trade_plan_revision_id",
            sql_type="UUID NULL",
            nullable=True,
            purpose="TradePlan revision identity. Do not duplicate TradePlan terms here.",
        ),
        DurableColumnSpec(
            name="facts_hash",
            sql_type="CHAR(64) NOT NULL",
            nullable=False,
            purpose="Canonical hash of AttributionFacts excluding narrative_explanation.",
        ),
        DurableColumnSpec(
            name="executed_trade_outcome",
            sql_type="BOOLEAN NOT NULL",
            nullable=False,
            purpose="False for REJECT/SKIP/planned-only. True only after fill/close facts.",
        ),
        DurableColumnSpec(
            name="learning_venue_mode",
            sql_type="VARCHAR(32) NOT NULL",
            nullable=False,
            purpose="paper_internal or paper_exchange_demo cohort. Live/real is not a venue.",
        ),
        DurableColumnSpec(
            name="setup_quality_axis",
            sql_type="VARCHAR(32) NOT NULL",
            nullable=False,
            purpose="Copied planned setup quality. Independent of PnL.",
        ),
        DurableColumnSpec(
            name="execution_quality_axis",
            sql_type="VARCHAR(32) NOT NULL",
            nullable=False,
            purpose="Fill/close quality versus plan. Independent of setup truth.",
        ),
        DurableColumnSpec(
            name="risk_adherence_axis",
            sql_type="VARCHAR(32) NOT NULL",
            nullable=False,
            purpose="Stop/size plan adherence. Independent of setup, slippage, and PnL.",
        ),
        DurableColumnSpec(
            name="trader_behavior_axis",
            sql_type="VARCHAR(32) NOT NULL",
            nullable=False,
            purpose="REJECT/SKIP/APPROVE/autonomous approval/EXECUTED. Never an outcome by itself.",
        ),
    ),
    event_columns=(
        DurableColumnSpec(
            name="source identity columns",
            sql_type="mirrors journal_projection_receipts uniqueness",
            nullable=False,
            purpose="organization_id, account_id, source_system, source_aggregate, event_type, "
            "source_event_id, source_event_version, supersession.",
        ),
        DurableColumnSpec(
            name="event_content_hash",
            sql_type="CHAR(64) NOT NULL",
            nullable=False,
            purpose="Must match JournalLifecycleEvent.content_hash for the same source identity.",
        ),
        DurableColumnSpec(
            name="payload_lineage",
            sql_type="JSONB NOT NULL",
            nullable=False,
            purpose="Copy of payload.lineage already stored on journal_lifecycle_events.",
        ),
        DurableColumnSpec(
            name="narrative_explanation",
            sql_type="TEXT NULL",
            nullable=True,
            purpose="LLM wording only. Excluded from facts_hash. Cannot update fact columns.",
        ),
    ),
    optional_journal_trade_columns=(
        DurableColumnSpec(
            name="candidate_id",
            sql_type="UUID NULL",
            nullable=True,
            purpose="Query helper on journal_trades. Identity remains payload.lineage until added.",
        ),
        DurableColumnSpec(
            name="assessment_id",
            sql_type="UUID NULL",
            nullable=True,
            purpose="Query helper. Must remain consistent with sticky payload.lineage.",
        ),
        DurableColumnSpec(
            name="evidence_window_hash",
            sql_type="CHAR(64) NULL",
            nullable=True,
            purpose="Copied evidence hash. Must not be recomputed from later market data.",
        ),
        DurableColumnSpec(
            name="trade_plan_revision_id",
            sql_type="UUID NULL",
            nullable=True,
            purpose="Plan revision pointer. TradePlan core implementation stays unchanged.",
        ),
    ),
    reuse_now=(
        "Reuse JournalLifecycleProjector, journal_lifecycle_events.payload.lineage, "
        "journal_projection_receipts, and JournalTrade as the only trade aggregate."
    ),
    must_not=(
        "Do not create a second JournalTrade writer, do not let learning UPDATE SetupAssessment "
        "or HistoricalCandle rows, and do not store LLM narrative inside facts_hash."
    ),
)
