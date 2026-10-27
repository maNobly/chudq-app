"""chudq.lol's app API (squad/STREAMING.md, "App API"): login, and the live data. Every call
after login carries the token; a 401 means it is gone, and the app shows itself logged out."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from . import __version__, config
from .paths import log

HEADERS = {"User-Agent": f"chudQ/{__version__}", "Accept": "application/json"}


class LoggedOut(Exception):
    """The server no longer knows the token."""


class ServerError(Exception):
    """The server said no, or couldn't be reached; the message is fit to show, `data` is the rest of
    the server's answer (say, how long until it asks Riot again)."""

    def __init__(self, msg: str, data: dict | None = None):
        super().__init__(msg)
        self.data = data or {}


def call(method: str, path: str, body: dict | None = None, token: str | None = None, timeout: float = 8) -> dict:
    cfg = config.load()
    headers = dict(HEADERS)
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(cfg["server"] + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 401 and token:
            raise LoggedOut() from None
        try:
            data = json.loads(e.read())
        except (ValueError, AttributeError, OSError):
            data = {}
        data = data if isinstance(data, dict) else {}
        raise ServerError(data.get("error") or f"{cfg['server']} answered {e.code}", data) from None
    except (OSError, ValueError) as e:
        raise ServerError(f"can't reach {cfg['server']}: {getattr(e, 'reason', e)}") from None


def login(name: str, password: str) -> dict:
    """Log in and keep the token; raises ServerError with the server's reason."""
    return keep(call("POST", "/api/app/login", {"name": name, "password": password, "device": socket.gethostname()}), name)


def keep(out: dict, name: str = "") -> dict:
    """A token from logging in or signing up, kept; then who it is."""
    if not out.get("token"):
        raise ServerError("the server sent no token")
    log(f"logged in as {out.get('name')} (player {out.get('player')})")
    config.save(token=out["token"], name=out.get("name") or name, player=out.get("player"))
    return refresh_me() or config.load()


def refresh_me() -> dict | None:
    """Who the token is and which League accounts are theirs (they can add some on the site meanwhile)."""
    try:
        me = authed("GET", "/api/app/me")
    except ServerError:
        return None
    if me is None:
        return None
    return config.save(name=me.get("name"), player=me.get("player"), accounts=me.get("accounts") or [], request=me.get("request"))


def logout() -> None:
    cfg = config.load()
    if cfg["token"]:
        try:
            call("POST", "/api/app/logout", {}, token=cfg["token"], timeout=4)
        except (LoggedOut, ServerError):
            pass  # forgotten here either way
    forget()


def forget() -> None:
    config.save(token=None)
    log("logged out")


def authed(method: str, path: str, body: dict | None = None, timeout: float = 8) -> dict | None:
    """A call with the saved token; None while logged out. A 401 logs the app out."""
    token = config.load()["token"]
    if not token:
        return None
    try:
        return call(method, path, body, token=token, timeout=timeout)
    except LoggedOut:
        log(f"{path}: the server no longer knows this login")
        forget()
        return None
