"""Updates: the app asks chudq.lol which version its download is (GET /api/app/version) at start and
every hour, and whenever the window is opened. A newer one shows "Update available" in the window and the tray, with what every
version since this one adds (the site's CHANGES.json); Update now downloads
that installer from <server>/download and runs it silently, and the app quits so it can be replaced
(the installer starts the new one). Never mid-game: the button waits until the game is over.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__, api, config
from .paths import log

EVERY = 3600


def parse(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (v or "").split("."))
    except ValueError:
        return ()


class Updater:
    def __init__(self, watcher, quit_app, just_updated: bool = False):
        self.watcher = watcher
        self.quit_app = quit_app
        self.status = {"latest": None, "available": False, "state": "idle", "message": None, "changes": [],
                       "updated": None}     # {"from", "to", "changes"} on the first start after an update
        before = config.load().get("last_version")
        if (before and parse(before) < parse(__version__)) or (just_updated and before != __version__):
            # The installer says --updated; a version from before last_version was kept doesn't say which it was
            log(f"updated from {before or 'an older version'}")
            self.status["updated"] = {"from": before, "to": __version__, "changes": []}
        if before != __version__:
            config.save(last_version=__version__)
        self._wake = threading.Event()
        self._busy = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="updates").start()

    def kick(self) -> None:
        """Check now (the window was opened), not only at the hour."""
        self._wake.set()

    def _run(self) -> None:
        while True:
            self.check()
            self._wake.wait(EVERY)
            self._wake.clear()

    def check(self) -> None:
        self.status = {**self.status, "checked": time.time()}
        try:
            out = api.call("GET", "/api/app/version", timeout=10)
        except api.ServerError:
            return  # offline: ask again later
        latest = out.get("version")
        available = parse(latest) > parse(__version__)
        # Everything after the version running here, up to the one on offer: an update can skip a few
        changes = [{"version": str(c.get("version")), "date": str(c.get("date") or ""),
                    "changes": [str(x) for x in c.get("changes") or []][:20]}
                   for c in (out.get("changes") or [])[:30]
                   if isinstance(c, dict) and parse(__version__) < parse(str(c.get("version"))) <= parse(latest)]
        if self.status["updated"]:
            since = self.status["updated"]["from"] or ".".join(map(str, parse(__version__)[:-1] + (parse(__version__)[-1] - 1,)))
            self.status["updated"]["changes"] = [
                {"version": str(c.get("version")), "date": str(c.get("date") or ""), "changes": [str(x) for x in c.get("changes") or []][:20]}
                for c in (out.get("changes") or [])[:30]
                if isinstance(c, dict) and parse(since) < parse(str(c.get("version"))) <= parse(__version__)]
        if available and not self.status["available"]:
            log(f"update available: {latest}")
        self.status = {**self.status, "latest": latest, "available": available, "changes": changes}

    def install(self) -> None:
        """Download the new installer and hand over to it. Raises ValueError with what to show."""
        if self.watcher.status.get("inGame"):
            raise ValueError("Update after your game")
        if not self.status["available"]:
            raise ValueError("chudQ is up to date")
        if not self._busy.acquire(blocking=False):
            raise ValueError("Already updating")
        threading.Thread(target=self._install, daemon=True, name="update").start()

    def _install(self) -> None:
        version = self.status["latest"]
        target = Path(tempfile.gettempdir()) / f"ChudQ-Setup-{version}.exe"
        try:
            self.status = {**self.status, "state": "downloading", "message": f"Downloading chudQ {version}"}
            log(f"updating to {version}")
            cfg = config.load()
            # The login goes along so the site's list of downloads says whose app updated
            auth = {"Authorization": f"Bearer {cfg['token']}"} if cfg["token"] else {}
            req = urllib.request.Request(cfg["server"] + "/download", headers={**api.HEADERS, **auth})
            with urllib.request.urlopen(req, timeout=60) as r, open(target, "wb") as out:
                name = r.headers.get("Content-Disposition") or ""
                if f"ChudQ-Setup-{version}.exe" not in name:
                    raise OSError("the download isn't that version (a newer one came out meanwhile? try again)")
                size, got = int(r.headers.get("Content-Length") or 0), 0
                while chunk := r.read(1 << 18):
                    out.write(chunk)
                    got += len(chunk)
                    if size:
                        self.status = {**self.status, "message": f"Downloading chudQ {version} · {got * 100 // size}%"}
            self.status = {**self.status, "state": "installing", "message": "Installing: chudQ opens again in a moment"}
            # Its own process, so it outlives this one. /SILENT: no questions, only the installer's progress
            # bar, so there's something on screen between this window closing and the new one opening;
            # its [Run] starts the new app after
            subprocess.Popen([str(target), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                             close_fds=True, cwd=os.path.dirname(target))
            threading.Timer(1.5, self.quit_app).start()
        except Exception as e:
            log(f"update failed: {type(e).__name__}: {e}")
            self.status = {**self.status, "state": "error", "message": f"Update failed: {e}"}
            self._busy.release()
