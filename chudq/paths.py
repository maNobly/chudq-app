"""Where the app keeps things:

%LOCALAPPDATA%\\ChudQ\\
    config.json      settings and the chudq login (config.py)
    app.log          what the app did
    webview\\         the window's browser data
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

APP = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "ChudQ"
LOG = APP / "app.log"
FROZEN = getattr(sys, "frozen", False)
# The app's own files: beside the package when run from a checkout, unpacked by PyInstaller when installed
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAGE = BUNDLE / "page"

APP.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        print(line, flush=True)
    except (OSError, ValueError, AttributeError):
        pass  # no console
    try:
        if LOG.exists() and LOG.stat().st_size > 2_000_000:
            LOG.replace(LOG.with_suffix(".old.log"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
