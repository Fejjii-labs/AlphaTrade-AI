"""Qualify retained data with a migration-aware older application package.

This creates isolated PostgreSQL schemas and archived source trees only. It never
deploys, downgrades, starts a scanner or contacts an exchange/provider.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db.models import KillSwitchState, Organization, User, UserStrategy, UserStrategyVersion
from app.db.voice_realtime import VoiceSessionReservation
from app.db.watcher_assignments import WatcherAssignmentReceiptRow, WatcherStrategyAssignmentRow
from app.schemas.usage import OrganizationQuotaUpdate
from app.services.quota_service import QuotaService

BASELINE = "b165b92276346f0e0fe3ccdbd2bec3443dc75d40"
ROLLBACK_BASE = "bda597c2fffbf1a49beadc64d757100808094ca2"
REVIEWED_BASE = "a7a843f7c27e8bdcea5f89c802c808472477a969"
MIGRATIONS = (
    "src/app/db/migrations/versions/a10blofinactivity001_account_scoped_native_blofin_activity.py",
    "src/app/db/migrations/versions/a11experiments001_generic_experiment_domain.py",
    "src/app/db/migrations/versions/a12trendpulsescreen001_bounded_trendpulse_research_screening.py",
    "src/app/db/migrations/versions/a13voicemonitor001_bounded_voice_and_strategy_assignments.py",
)
QUOTA_COMPATIBILITY = (
    "src/app/db/voice_realtime.py",
    "src/app/services/quota_service.py",
    "src/app/schemas/usage.py",
)
TABLES = (
    "alembic_version",
    "organizations",
    "users",
    "documents",
    "chunks",
    "knowledge_indexing_jobs",
    "blofin_activity_accounts",
    "blofin_activity_facts",
    "blofin_activity_cursors",
    "experiments",
    "experiment_versions",
    "experiment_events",
    "experiment_samples",
    "trendpulse_screening_runs",
    "watcher_strategy_assignments",
    "watcher_assignment_receipts",
    "voice_session_reservations",
    "watcher_watchlists",
    "kill_switch_states",
    "organization_quotas",
)


def _archive(root: Path, revision: str, target: Path) -> Path:
    archive = subprocess.check_output(["git", "archive", revision, "backend"], cwd=root)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(target, filter="data")
    return target / "backend"


def _snapshot(engine):
    with engine.connect() as connection:
        return {
            table: list(
                connection.scalars(
                    text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY to_jsonb(t)::text")
                )
            )
            for table in TABLES
        }


def _seed(engine):
    org, user = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org, name="Synthetic rollback tenant"))
        session.add(
            User(id=user, email=f"rollback-{user}@example.com", hashed_password="synthetic")
        )
        session.commit()
    with engine.begin() as connection:
        for index, status in enumerate((None, "pending", "processing", "ready", "failed")):
            document, job = uuid4(), uuid4()
            connection.execute(
                text(
                    "INSERT INTO documents (id,organization_id,user_id,source_type,title,"
                    "source_hash,version,tags,ingestion_metadata,indexing_generation) VALUES "
                    "(:id,:org,:user,'GENERAL_NOTE',:title,:hash,2,'[\"synthetic\"]',"
                    "CAST(:metadata AS json),:generation)"
                ),
                {
                    "id": document,
                    "org": org,
                    "user": user,
                    "title": f"Synthetic retained document {index}",
                    "hash": str(index) * 64,
                    "metadata": json.dumps({"indexing": {"status": status or "unknown"}}),
                    "generation": job if status else None,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO chunks (id,document_id,organization_id,user_id,ordinal,"
                    "content,text_hash,metadata) VALUES (:id,:doc,:org,:user,0,:content,:hash,'{}')"
                ),
                {
                    "id": uuid4(),
                    "doc": document,
                    "org": org,
                    "user": user,
                    "content": f"Synthetic retained source passage {index}",
                    "hash": str(index) * 64,
                },
            )
            if status:
                connection.execute(
                    text(
                        "INSERT INTO knowledge_indexing_jobs (id,document_id,organization_id,"
                        "user_id,document_version,source_hash,operation,status,attempts,available_at,"
                        "claimed_until,claim_token,error_code) VALUES (:id,:doc,:org,:user,2,"
                        ":hash,'upsert',:status,2,now(),"
                        "CASE WHEN :processing THEN now()+interval '5 minutes' END,"
                        ":claim,:error)"
                    ),
                    {
                        "id": job,
                        "doc": document,
                        "org": org,
                        "user": user,
                        "hash": str(index) * 64,
                        "status": status,
                        "processing": status == "processing",
                        "claim": uuid4() if status == "processing" else None,
                        "error": "synthetic_provider_unavailable" if status == "failed" else None,
                    },
                )
        # A deletion tombstone must survive without a corresponding document row.
        connection.execute(
            text(
                "INSERT INTO knowledge_indexing_jobs (id,document_id,organization_id,user_id,"
                "document_version,operation,status,attempts,available_at) "
                "VALUES (:id,:doc,:org,:user,3,'delete','pending',1,now())"
            ),
            {"id": uuid4(), "doc": uuid4(), "org": org, "user": user},
        )
        connection.execute(
            text(
                "INSERT INTO blofin_activity_accounts (organization_id,environment,account_uid,"
                "credential_binding,identity_verified_at) VALUES (:org,'demo','synthetic-uid',"
                "repeat('a',64),now())"
            ),
            {"org": org},
        )
        for kind, native in (("order", "order-1"), ("fill", "fill-1"), ("fill", "fill-2")):
            connection.execute(
                text(
                    "INSERT INTO blofin_activity_facts (organization_id,environment,account_uid,"
                    "kind,native_id,order_id,occurred_at_ms,content_hash,payload,"
                    "first_observed_at) "
                    "VALUES (:org,'demo','synthetic-uid',:kind,:native,'order-1',123456789,"
                    "repeat('b',64),CAST(:payload AS json),now())"
                ),
                {
                    "org": org,
                    "kind": kind,
                    "native": native,
                    "payload": json.dumps(
                        {"quantity_contracts": "1.00000000", "fee": None, "pnl": None}
                    ),
                },
            )
        for kind in ("order", "fill"):
            connection.execute(
                text(
                    "INSERT INTO blofin_activity_cursors (organization_id,environment,account_uid,"
                    "kind,window_begin_ms,window_end_ms,native_cursor,seen_cursors,window_complete,"
                    "gap_detected,last_successful_sync) VALUES (:org,'demo','synthetic-uid',:kind,"
                    "123456000,123457000,:cursor,'[\"retained-page\"]',false,true,now())"
                ),
                {"org": org, "kind": kind, "cursor": f"retained-{kind}-cursor"},
            )
    # Trusted synthetic source only: preserve immutable configuration, lifecycle and
    # one setup sample as well as prior-release documents/jobs/native facts.
    from tests.support.experiment_fixtures import World

    with Session(engine, expire_on_commit=False) as session:
        world = World(session)
        version = world.running()
        world.sample(version, reference="retained-synthetic-setup")
        session.commit()
        from app.schemas.trendpulse_screening import TrendPulseScreeningCreate
        from app.strategy_brain.trendpulse_screening.service import TrendPulseScreeningService
        from tests.support.trendpulse_screening import DelayedReplayAcquirer, after_close
        from tests.test_trendpulse_1r_adapter import END
        from tests.test_trendpulse_1r_domain import configure

        world.now = after_close()
        research = world.create(configure(world))
        settings = world.settings.model_copy(update={"trendpulse_screening_enabled": True})
        service = TrendPulseScreeningService(
            session, settings, acquirer=DelayedReplayAcquirer(), clock=lambda: world.now
        )
        body = TrendPulseScreeningCreate(
            request_id=uuid4(), variant_key="baseline", trigger_end=END
        )
        first = service.screen(world.tenant, research.experiment_id, research.id, body)
        session.commit()
        assert first.receipt_provenance == "synthetic_fixture"
        assert first.status == "qualified_research_signal" and first.trend_receipts == 250
        duplicate = service.screen(
            world.tenant,
            research.experiment_id,
            research.id,
            body.model_copy(update={"request_id": uuid4()}),
        )
        session.commit()
        assert duplicate.status == "duplicate" and duplicate.duplicate_of == first.id
        from datetime import timedelta

        world.now = END + timedelta(seconds=60)
        rejected = service.screen(
            world.tenant,
            research.experiment_id,
            research.id,
            body.model_copy(update={"request_id": uuid4()}),
        )
        session.commit()
        assert rejected.status == "refused" and rejected.reason == "trigger_expired"


def _seed_feature_retention(engine):
    """Preserve exact future configuration and uncertain spending across rollback."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import select

    from app.db.watcher_watchlist import WatcherWatchlistRow

    with Session(engine) as session:
        strategy = session.scalars(select(UserStrategy).order_by(UserStrategy.id)).first()
        assert strategy is not None
        version = session.scalars(
            select(UserStrategyVersion)
            .where(
                UserStrategyVersion.strategy_id == strategy.id,
            )
            .order_by(UserStrategyVersion.version)
        ).first()
        assert version is not None
        now = datetime.now(UTC)
        org, user = strategy.organization_id, strategy.user_id
        session.add(
            KillSwitchState(
                organization_id=org,
                active=True,
                reason="Retained synthetic operator state",
                version=7,
            )
        )
        session.add(
            WatcherWatchlistRow(
                organization_id=org,
                revision=3,
                updated_at=now,
                slots=[{"position": 1, "symbol": "BTCUSDT", "enabled": True}],
                runtime_summary={"paper_only": True, "synthetic": True},
            )
        )
        session.add(
            WatcherStrategyAssignmentRow(
                id=uuid4(),
                organization_id=org,
                user_id=user,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                symbol="BTCUSDT",
                version_content_hash="a" * 64,
                timeframes=["15m"],
                parameters=version.pattern_spec,
                monitoring_enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            WatcherAssignmentReceiptRow(
                organization_id=org,
                user_id=user,
                request_key="retained-request-key",
                payload_hash="b" * 64,
                configuration_revision=3,
                response={"revision": 3, "exact": ["retained-body-and-key"]},
                created_at=now,
            )
        )
        session.add(
            VoiceSessionReservation(
                id=uuid4(),
                organization_id=org,
                user_id=user,
                conversation_id=None,
                budget_reference="synthetic-retained-reservation",
                model="synthetic-model",
                status="uncertain",
                reserved_cost=Decimal("1"),
                reserved_tokens=100,
                accounted_cost=None,
                response_count=1,
                ledger={"responses": [{"state": "unknown"}]},
                created_at=now,
                updated_at=now,
            )
        )
        QuotaService(session).update_quota(
            org,
            OrganizationQuotaUpdate(
                monthly_token_limit=100,
                monthly_cost_limit=Decimal("1"),
            ),
        )
        session.commit()
        return org


BOOT_PROBE = """
import faulthandler
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.core.config import get_settings
from app.db.models import Document, KnowledgeIndexingJob
from app.db.session import get_session_factory
from app.main import create_app
import app.main
from app.providers.qdrant import InMemoryVectorStore
from app.workers.paper_worker import run_paper_worker_process
from app.workers.watcher_activation import expected_migration_head, read_migration_revision

faulthandler.dump_traceback_later(20)
print('rollback_probe_imports_complete', flush=True)
assert Path(app.main.__file__).resolve().is_relative_to(Path(os.environ['PYTHONPATH']).resolve())
app.main.get_process_vector_store = lambda: InMemoryVectorStore()
settings = get_settings()
assert settings.provider_mode == 'mock'
assert not settings.knowledge_indexing_enabled
with TestClient(create_app(settings)) as client:
    print('rollback_probe_startup_complete', flush=True)
    health = client.get('/health')
    assert health.status_code == 200
    assert health.json()['real_trading_enabled'] is False
print('rollback_probe_shutdown_complete', flush=True)
with get_session_factory()() as session:
    assert (
        expected_migration_head() == read_migration_revision(session)
        == os.environ['ROLLBACK_EXPECTED_HEAD']
    )
    docs = list(session.scalars(select(Document)))
    jobs = list(session.scalars(select(KnowledgeIndexingJob)))
    assert len(docs) == len(jobs) == 5
    assert all(doc.user_id and doc.organization_id for doc in docs)
    assert any(job.operation == 'delete' for job in jobs)
    from uuid import UUID
    from app.services.quota_service import QuotaService
    if os.environ.get('ROLLBACK_RESERVATION_ORG'):
        # The reviewed older package needs the forward-compatible quota fence;
        # it must not discard potentially billed voice work during rollback.
        status = QuotaService(session).get_status(UUID(os.environ['ROLLBACK_RESERVATION_ORG']))
        assert status.hard_limit_reached
        assert status.usage.monthly_cost_reserved == 1
        assert status.usage.monthly_tokens_reserved == 100
# The worker's disarmed boot role is specifically a staging posture. The API
# above uses local/mock settings; neither modifies deployed operator settings.
os.environ.update({
    'ENVIRONMENT': 'staging',
    'PROVIDER_MODE': 'fallback',
    'AUTH_REFRESH_COOKIE_ENABLED': 'true',
    'AUTH_COOKIE_SECURE': 'true',
    'CORS_ORIGINS': 'https://app.example.com',
    'RATE_LIMIT_USE_REDIS': 'true',
    'RATE_LIMIT_ALLOW_IN_MEMORY_FALLBACK': 'false',
    'ACCESS_TOKEN_DENYLIST_USE_REDIS': 'true',
    'TRUSTED_PROXY_HOPS': '1',
})
# The real disarmed worker must not open operational dependencies. Fallback is
# only its validated configuration token; no provider is constructed or called.
import app.db.session
def forbidden_dependency(*args, **kwargs):
    raise AssertionError('disarmed rollback worker opened the database')
app.db.session.get_session_factory = forbidden_dependency
assert run_paper_worker_process(once=True) == 'disarmed'
print(json.dumps({
    'api_health': 200, 'worker': 'disarmed', 'head': os.environ['ROLLBACK_EXPECTED_HEAD'],
}))
"""


def test_migration_aware_rollback_retains_documents_jobs_and_native_history(tmp_path, monkeypatch):
    raw = os.environ.get("PR237_ROLLBACK_POSTGRES_URL") or os.environ.get("PHASE1_POSTGRES_URL")
    if not raw:
        pytest.skip("Set PR237_ROLLBACK_POSTGRES_URL to disposable loopback PostgreSQL.")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql" and url.host in {"127.0.0.1", "localhost"}
    assert url.database in {"alphatrade_test", "pr237_rollback_test", "alphatrade_voice_monitoring"}
    backend = Path(__file__).resolve().parents[1]
    admin = create_engine(url)
    schema = "pr237_rollback_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    dsn = scoped.render_as_string(hide_password=False)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", dsn)
    engine = create_engine(scoped)
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "src/app/db/migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head == "a13voicemonitor001"
    try:
        command.upgrade(config, "head")
        _seed(engine)
        retained_org = _seed_feature_retention(engine)
        retained = _snapshot(engine)
        assert len(retained["trendpulse_screening_runs"]) == 3
        assert len(retained["experiment_versions"]) == 2
        assert len(retained["watcher_strategy_assignments"]) == 1
        assert len(retained["watcher_assignment_receipts"]) == 1
        assert len(retained["voice_session_reservations"]) == 1
        with pytest.raises(RuntimeError, match="requires retained schema rollback"):
            command.downgrade(config, "a12trendpulsescreen001")
        assert _snapshot(engine) == retained
        env = {
            **os.environ,
            "ALEMBIC_DATABASE_URL": dsn,
            "DATABASE_URL": dsn,
            "ENVIRONMENT": "local",
            "PROVIDER_MODE": "mock",
            "WORKER_ENABLED": "false",
            "MARKET_DATA_ENABLED": "false",
            "JOURNAL_RAG_SYNC_ENABLED": "false",
            "EXECUTION_MODE": "paper",
            "EXCHANGE_MODE": "paper_internal",
            "ENABLE_REAL_TRADING": "false",
            "WATCHER_ORCHESTRATION_ENABLED": "false",
            "WATCHER_PAPER_STAGING_ACTIVATION": "false",
            "MARKET_WATCHER_ENABLED": "false",
            "MARKET_WATCHER_BRIDGE_ENABLED": "false",
            "MARKET_WATCHER_BRIDGE_AUTO_TICK": "false",
            "TELEGRAM_ALERTS_ENABLED": "false",
            "TELEGRAM_INTERACTION_ENABLED": "false",
            "AUTOMATIC_TELEGRAM_DELIVERY_ENABLED": "false",
            "TELEGRAM_PAPER_ACTIVATION_ARMED": "false",
            "TELEGRAM_NETWORK_PERMITTED": "false",
            "TELEGRAM_INBOUND_MODE": "off",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_WEBHOOK_SECRET": "",
            "KNOWLEDGE_INDEXING_ENABLED": "false",
            "BLOFIN_ACTIVITY_ENABLED": "false",
            "RATE_LIMIT_USE_REDIS": "false",
            "ACCESS_TOKEN_DENYLIST_USE_REDIS": "false",
            "MARKET_DATA_CACHE_USE_REDIS": "false",
            "ROLLBACK_EXPECTED_HEAD": head,
            "ROLLBACK_RESERVATION_ORG": str(retained_org),
        }
        for label, revision in (
            ("plain-b165", BASELINE),
            ("plain-bda", ROLLBACK_BASE),
            ("plain-reviewed", REVIEWED_BASE),
            ("rollback", REVIEWED_BASE),
        ):
            target = _archive(backend.parent, revision, tmp_path / label)
            if label == "rollback":
                for migration in MIGRATIONS:
                    (target / migration).write_bytes((backend / migration).read_bytes())
                for compatibility in QUOTA_COMPATIBILITY:
                    (target / compatibility).write_bytes((backend / compatibility).read_bytes())
            env["PYTHONPATH"] = str(target / "src")
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=target,
                env=env,
                capture_output=True,
                text=True,
                timeout=40,
            )
            if label.startswith("plain-"):
                assert result.returncode != 0
                assert f"Can't locate revision identified by '{head}'" in (
                    result.stdout + result.stderr
                )
            else:
                assert result.returncode == 0, result.stdout + result.stderr
                for _restart in range(2):
                    try:
                        boot = subprocess.run(
                            [sys.executable, "-c", BOOT_PROBE],
                            cwd=target,
                            env=env,
                            capture_output=True,
                            text=True,
                            timeout=45,
                        )
                    except subprocess.TimeoutExpired as exc:
                        pytest.fail(
                            f"Bounded rollback probe timed out: {exc.stdout!r} {exc.stderr!r}"
                        )
                    assert boot.returncode == 0, boot.stdout + boot.stderr
                    assert _snapshot(engine) == retained
            assert _snapshot(engine) == retained
        command.upgrade(config, "head")  # Return to the candidate; no schema rollback.
        env["PYTHONPATH"] = str(backend / "src")
        forward = subprocess.run(
            [sys.executable, "-c", BOOT_PROBE],
            cwd=backend,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert forward.returncode == 0, forward.stdout + forward.stderr
        assert _snapshot(engine) == retained
        fingerprint = hashlib.sha256(json.dumps(retained, sort_keys=True).encode()).hexdigest()
        print(f"Preserved row counts: { {table: len(rows) for table, rows in retained.items()} }")
        print(f"Exact retained data SHA256: {fingerprint}")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
