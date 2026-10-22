"""Refuse pytest collection of the salvage tree.

Loaded when collection enters ``archive/``. The hook stays specific to that
directory so the rest of the suite is unchanged.
"""

from __future__ import annotations

from pathlib import Path


def pytest_ignore_collect(collection_path: Path, config: object) -> bool:
    """Skip every file under archive/, including an explicit path argument."""
    return "archive" in collection_path.parts
