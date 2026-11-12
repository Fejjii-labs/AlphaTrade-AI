# AlphaTrade API and paper worker

The FastAPI backend owns scoped services and canonical paper authority. The same backend package supplies the separate supervised paper worker; the default API container and worker commands have different startup/migration behavior. Real-money execution is permanently refused by the inspected source.

Use [local setup](../docs/local_setup.md) for disposable database/provider configuration, frozen dependency installation, migrations and the development server. Use [deployment](../docs/deployment.md) for hosted API/worker boundaries and [testing/evaluation](../docs/evaluation.md) for checks.

From `backend/`, after configuring the isolated local environment/database:

```sh
uv sync --frozen --extra dev
uv run alembic upgrade head
./scripts/run_dev_server.sh
```

The dev script sets `PYTHONPATH=src`; it does not migrate automatically. Default API container startup applies migrations; an explicit worker command executes directly. Do not use a shared/staging database for local setup or tests.

Shared-worker pause/resume requires an authenticated tenant Owner whose immutable user UUID
is in `PLATFORM_OPERATOR_USER_IDS` (comma-separated). Empty denies everyone. Successful
requests atomically record `worker_paused` / `worker_resumed` audit events with the actor,
organization, worker name and paused value. Controls preserve heartbeat timestamps and
cycle status; a control row created before any worker beat uses a stale Unix epoch
timestamp and `unknown` status. `/worker/health` retains its existing read access.

[Architecture and versions](../docs/architecture.md) · [Agent](../docs/agent_workflow.md) · [Security](../docs/security.md) · [Current status](../docs/current_status.md).
