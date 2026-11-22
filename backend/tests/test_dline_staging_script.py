"""scripts/approve_dline_demo_staging.py: staging only, dry run rolls back. SQLite only."""

import importlib.util
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import Organization, User, UserStrategy

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "approve_dline_demo_staging.py"


def load():
    spec = importlib.util.spec_from_file_location("approve_dline_demo_staging", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def settings(**changes):
    base = {
        "environment": "staging",
        "enable_real_trading": False,
        "governed_demo_data_mode_enabled": True,
        "risk_model_v2_enabled": True,
        "governed_demo_partial_exits_enabled": True,
        "watcher_paper_max_scopes_per_cycle": 60,
    }
    from app.core.config import Settings

    return Settings(_env_file=None).model_copy(update={**base, **changes})


def test_refuses_outside_staging_or_with_real_trading(monkeypatch, capsys):
    module = load()
    for bad in (settings(environment="production"), settings(enable_real_trading=True)):
        monkeypatch.setattr("app.core.config.get_settings", lambda bad=bad: bad)
        monkeypatch.setattr(
            "app.db.session.get_session_factory", lambda: (_ for _ in ()).throw(AssertionError)
        )
        assert module.main(["--organization-id", str(uuid4()), "--user-id", str(uuid4())]) == 1
    assert "DLINE|ABORT" in capsys.readouterr().out


def test_refuses_when_derived_real_trading_flag_is_on(monkeypatch, capsys):
    from app.core.config import Settings

    module = load()
    # real_trading_enabled is a fail-closed property (always False today); guard on it too.
    monkeypatch.setattr(Settings, "real_trading_enabled", property(lambda self: True))
    monkeypatch.setattr("app.core.config.get_settings", settings)
    monkeypatch.setattr(
        "app.db.session.get_session_factory", lambda: (_ for _ in ()).throw(AssertionError)
    )
    assert module.main(["--organization-id", str(uuid4()), "--user-id", str(uuid4())]) == 1
    assert "DLINE|ABORT" in capsys.readouterr().out


def test_dry_run_approves_every_combination_then_rolls_back(monkeypatch, capsys):
    from tests.test_watcher_paper_runtime import _sqlite_factory

    factory = _sqlite_factory()
    org, user = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Organization(id=org, name="D-line staging script"),
                User(id=user, email=f"{user}@example.com", hashed_password="unused"),
            ]
        )
        session.commit()
    module = load()
    monkeypatch.setattr("app.core.config.get_settings", settings)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    code = module.main(["--organization-id", str(org), "--user-id", str(user)])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert printed.count("DLINE|approved|") == 30
    assert "DLINE|DRY_RUN_ROLLED_BACK|true" in printed
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(UserStrategy)) == 0
