"""Synthetic long fixture for the short hosted evidence proof; never imports the app."""

import json
import os
import time
from pathlib import Path

root = Path(os.environ["BACKEND_RELEASE_DIAGNOSTICS"])
(root / "active.json").write_text(
    json.dumps({"nodeid": "synthetic_stuck_fixture", "phase": "setup", "started": time.time()})
)
(root / "pytest.log").write_text("Synthetic fixture entered; waiting for the deliberate timeout.\n")
time.sleep(120)
