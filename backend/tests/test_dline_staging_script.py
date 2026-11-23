"""scripts/approve_dline_demo_staging.py: staging only, dry run rolls back. SQLite only."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
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


def _dry_run(monkeypatch, capsys, *extra):
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
    argv = ["--organization-id", str(org), "--user-id", str(user), "--symbols", "BTCUSDT"]
    code = module.main([*argv, "--timeframes", "1h", *extra])
    printed = capsys.readouterr().out
    assert code == 0, printed
    return printed


def test_entry_mode_defaults_to_breakout(monkeypatch, capsys):
    printed = _dry_run(monkeypatch, capsys)
    assert '"entry_mode": "breakout"' in printed.split("DLINE|approved|", 1)[0]
    assert printed.count('"entry_mode": "breakout"') == 3  # flags + long + short
    assert '"entry_mode": "split"' not in printed


def test_entry_mode_split_and_breakout_reach_the_spec(monkeypatch, capsys):
    from app.strategy_brain import service

    seen = []
    original = service.create_template

    def spy(session, **kwargs):
        seen.append(kwargs["spec"].parameters.entry_mode)
        return original(session, **kwargs)

    monkeypatch.setattr(service, "create_template", spy)
    for mode in ("split", "breakout"):
        seen.clear()
        printed = _dry_run(monkeypatch, capsys, "--entry-mode", mode)
        assert seen == [mode, mode]
        approved = [line for line in printed.splitlines() if line.startswith("DLINE|approved|")]
        assert len(approved) == 2
        assert all(f'"entry_mode": "{mode}"' in line for line in approved)
        assert "DLINE|DRY_RUN_ROLLED_BACK|true" in printed


def test_refuses_retest_entry_mode(monkeypatch, capsys):
    module = load()
    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: (_ for _ in ()).throw(AssertionError)
    )
    argv = ["--organization-id", str(uuid4()), "--user-id", str(uuid4())]
    with pytest.raises(SystemExit) as exc:
        module.main([*argv, "--entry-mode", "retest"])
    assert exc.value.code == 2
    assert "invalid choice: 'retest'" in capsys.readouterr().err
