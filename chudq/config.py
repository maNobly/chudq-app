"""The app's settings and login: config.json, with defaults for anything it doesn't say. The
window changes it through check(); the watcher and the streamer read it fresh every round."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .paths import APP

CONFIG = APP / "config.json"
_lock = threading.Lock()

# Stream quality: name -> (width, height, fps, kbps)
PRESETS = {"720p30": (1280, 720, 30, 2500), "720p60": (1280, 720, 60, 4000), "1080p60": (1920, 1080, 60, 6000)}

DEFAULTS = {
    "server": "https://chudq.lol",   # http://localhost:8780 to test against a local squad server
    "token": None,                   # from POST /api/app/login; None while logged out
    "name": None,                    # the chudq login name, and the squad player it is
    "player": None,
    "accounts": [],                  # the League accounts on the player: only their games are sent or streamed
    "request": None,                 # "pending" while the admin hasn't let a new account into the squad
    "send_live": True,               # "Send live game data"
    "stream": False,                 # "Stream my games"
    "preset": "720p30",
    "mic": False,
    "other_audio": False,            # "All PC sound": the whole desktop mix instead of only League's
    "capture": "game",               # "window" once game capture showed black here (a laptop's two GPUs)
    "start_at_logon": True,
    "league_dir": r"C:\Riot Games\League of Legends",
    "obs_path": r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
    "obs_port": 4455,
    "last_version": None,            # the version that ran last: a newer one says "Updated"
    "port": 47180,                   # the window's page, on 127.0.0.1 (an unusual port: dev servers sit on 87xx)
}


def load() -> dict:
    with _lock:
        cfg = dict(DEFAULTS)
        if CONFIG.exists():
            try:
                cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
            except ValueError:
                pass  # a broken file: the defaults, until the next save
        cfg["server"] = str(cfg["server"]).rstrip("/")
        return cfg


def _write(cfg: dict) -> None:
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)


def save(**changes) -> dict:
    """Set some values as they are (the login, after the server said yes)."""
    with _lock:
        cfg = dict(DEFAULTS)
        if CONFIG.exists():
            try:
                cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
            except ValueError:
                pass
        cfg.update(changes)
        _write(cfg)
    return load()


def check(new: dict) -> dict:
    """The window's changes, checked; raises ValueError on anything that isn't right."""
    out = {}
    for k in ("send_live", "stream", "mic", "other_audio", "start_at_logon"):
        if k in new:
            out[k] = bool(new[k])
    if "preset" in new:
        if new["preset"] not in PRESETS:
            raise ValueError("Quality: one of " + ", ".join(PRESETS))
        out["preset"] = new["preset"]
    if "obs_path" in new:
        p = str(new["obs_path"]).strip().strip('"')
        # The app starts this program, so it must be OBS and nothing else
        if Path(p).name.lower() != "obs64.exe" or not Path(p).is_absolute():
            raise ValueError("OBS path: the full path of obs64.exe, like C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe")
        if not Path(p).is_file():
            raise ValueError(f"There is no {p}")
        out["obs_path"] = p
    return save(**out)
