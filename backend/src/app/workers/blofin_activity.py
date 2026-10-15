"""Importable single-tick entry point for existing workers; disarmed by default."""

import signal
from dataclasses import asdict
from threading import Event
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings
from app.db.session import get_engine
from app.services.blofin_activity_config import (
    BloFinActivitySettings,
    configured_scope,
    get_activity_settings,
)
from app.services.blofin_activity_service import run_activity_sync

if TYPE_CHECKING:
    from app.workers.paper_worker import CycleOutcome


class BloFinActivityCycle:
    """Optional bounded component of the existing paper-worker supervisor."""

    def __init__(self, settings: Settings, config: BloFinActivitySettings) -> None:
        self.settings = settings.model_copy(deep=True)
        self.config = config.model_copy(deep=True)
        self._stop = Event()

    def __call__(self) -> "CycleOutcome":
        from app.workers.paper_worker import CycleOutcome

        if self._stop.is_set():
            return CycleOutcome("stopped")
        if not self.config.enabled:
            return CycleOutcome("disarmed")
        configured_scope(self.config)
        result = run_activity_sync(
            get_engine(), self.settings, self.config, shutdown=self._stop.is_set
        )
        return CycleOutcome(
            "degraded" if result.status == "failed" else result.status,
            error=result.error_code or "",
        )

    def request_stop(self) -> None:
        self._stop.set()


def main() -> None:
    config = get_activity_settings()
    if not config.enabled:
        print("BloFin activity synchronization disabled.")
        return
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _sig, _frame: stop.set())
    result = run_activity_sync(get_engine(), get_settings(), config, shutdown=stop.is_set)
    print(asdict(result))


if __name__ == "__main__":
    main()
