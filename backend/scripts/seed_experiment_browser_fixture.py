"""Seed a synthetic, loopback-only experiment tenant for focused browser checks.

Uses trusted test fixtures, not native source records. Never enable a runtime.
The output contains short-lived local authentication; keep it outside the repository.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from tests.support.experiment_fixtures import World
from tests.test_trendpulse_1r_adapter import END, spec

from app.core.config import Settings
from app.db.models import (
    Conversation,
    ConversationMessage,
    KillSwitchState,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.schemas.common import ConversationMessageRole, StrategyId
from app.schemas.experiments import ExperimentCreate
from app.schemas.strategy_library import StrategyCard
from app.security.passwords import hash_password
from app.security.tokens import create_access_token


class BrowserWorld(World):
    """Domain fixtures also need a valid public strategy DTO on the actual API."""

    def add_strategy_version(self, spec, number):
        row = UserStrategyVersion(
            id=uuid4(),
            strategy_id=self.strategy.id,
            version=number,
            card=StrategyCard(
                strategy_name="Synthetic authored research",
                entry_conditions=["Closed trigger research observation"],
                invalidation=["Research structure invalidated"],
                stop_loss=["Structural research stop; no execution authority"],
            ).model_dump(mode="json"),
            pattern_spec=spec.model_dump(mode="json"),
        )
        self.session.add(row)
        self.session.flush()
        return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-jwt-secret", required=True)
    parser.add_argument("--long-conversation", action="store_true")
    args = parser.parse_args()
    url = make_url(args.database_url)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.database != "alphatrade_test"
    ):
        parser.error("Only explicitly disposable loopback PostgreSQL alphatrade_test is allowed.")
    if not args.output.resolve().is_relative_to(Path("/tmp")):
        parser.error("Keep the short-lived synthetic authentication file under /tmp.")
    engine = create_engine(url)
    settings = Settings(
        _env_file=None, environment="local", provider_mode="mock", jwt_secret=args.local_jwt_secret
    )
    try:
        with Session(engine, expire_on_commit=False) as session:
            world = BrowserWorld(session)
            world.now = datetime.now(UTC) - timedelta(minutes=3)

            def seed_login(tenant_world):
                fixture_user = session.get(User, tenant_world.tenant.user_id)
                assert fixture_user is not None
                fixture_user.email = f"browser-{uuid4().hex}@example.com"
                fixture_password = f"Synthetic-auth-{uuid4().hex}!"
                fixture_user.hashed_password = hash_password(fixture_password, settings)
                fixture_user.email_verified = True
                tenant_world.tenant = replace(tenant_world.tenant, email=fixture_user.email)
                # Only these newly created disposable tenants: give read-only safety
                # checks an existing conservative row, never toggle an operator's row.
                session.add(
                    KillSwitchState(
                        organization_id=tenant_world.tenant.organization_id,
                        active=True,
                        reason="Synthetic authentication fixture; execution blocked",
                    )
                )
                return fixture_user, fixture_password

            user, password = seed_login(world)
            other_user, other_password = seed_login(BrowserWorld(session))

            def create(name: str, mode: str):
                return world.service.create(
                    world.tenant,
                    ExperimentCreate(
                        name=name,
                        idempotency_key=name,
                        configuration=world.configuration(
                            mode=mode,
                            sample_target={
                                "kind": "setup_observation",
                                "minimum": 30,
                                "maximum": 100,
                            },
                        ),
                    ),
                )

            exploration = world.approve(
                world.transition(create("Nested exploration", "exploration"), "submit")
            )
            validation = world.transition(
                world.approve(
                    world.transition(create("Nested validation", "validation"), "submit")
                ),
                "start",
            )
            world.sample(validation, reference="synthetic-browser-setup")
            world.transition(create("Nested review", "exploration"), "submit")
            # An immutable authored research version for real-login recovery checks.
            # Existing Nested fixtures stay unchanged for their browser consumers.
            world.strategy = UserStrategy(
                id=uuid4(),
                organization_id=world.tenant.organization_id,
                user_id=world.tenant.user_id,
                name="Synthetic TrendPulse auth recovery",
                setup_type=StrategyId.MANUAL_REVIEW,
            )
            session.add(world.strategy)
            session.flush()
            world.add_strategy_version(spec(), 1)
            conversation_id = None
            if args.long_conversation:
                conversation = Conversation(
                    organization_id=world.tenant.organization_id,
                    user_id=world.tenant.user_id,
                    title="Synthetic long viewport conversation",
                )
                session.add(conversation)
                session.flush()
                conversation_id = str(conversation.id)
                for index in range(130):
                    session.add(
                        ConversationMessage(
                            conversation_id=conversation.id,
                            organization_id=world.tenant.organization_id,
                            user_id=world.tenant.user_id,
                            role=ConversationMessageRole.USER
                            if index % 2 == 0
                            else ConversationMessageRole.ASSISTANT,
                            content=f"Synthetic viewport message {index:03d}. "
                            + (
                                "This is a synthetic observation for reading navigation. "
                                "It provides no performance evidence or trading authority. "
                            )
                            * 4,
                            created_at=world.now + timedelta(seconds=index),
                            payload={},
                        )
                    )
            session.commit()
            token, _ = create_access_token(
                user_id=world.tenant.user_id,
                organization_id=world.tenant.organization_id,
                email=user.email,
                settings=settings,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with open(
                args.output,
                "w",
                encoding="utf-8",
                opener=lambda path, flags: os.open(path, flags, 0o600),
            ) as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump(
                    {
                        "token": token,
                        "conversation_id": conversation_id,
                        "email": user.email,
                        "password": password,
                        "other": {"email": other_user.email, "password": other_password},
                        "strategy_id": str(world.strategy.id),
                        "experiment_id": str(exploration.experiment_id),
                        "research_spec": spec().model_dump(mode="json"),
                        "trigger_end": END.isoformat(),
                    },
                    output,
                )
                output.write("\n")
            print("Seeded synthetic tenant; runtime inactive; private local auth file written.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
