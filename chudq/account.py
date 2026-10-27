"""Your League account and chudq: signing up from the app, and adding the League account the client is
logged in to. chudq.lol only takes a Riot ID once Riot shows a profile icon it picked on that account
(anyone can type a Riot ID; only its owner can change its icon). On the site the person sets the icon;
here the app does it through the League client, waits for Riot to show it, and puts the old icon back.

Only one proof runs at a time, in a thread of its own; `status` says how it goes for the window.
"""

from __future__ import annotations

import socket
import threading
import time

from . import api, config, league
from .paths import log

GIVE_UP = 10 * 60     # Riot is usually quick to show the icon, but can take minutes
ASK_EVERY = 20        # the server asks Riot at most once a minute anyway


class Proof:
    def __init__(self):
        self.status = {"state": None, "message": "", "riotId": None, "kind": None}
        self.signup: str | None = None      # the server's token for a sign-up waiting on a choice
        self._busy = threading.Lock()

    def _set(self, state: str | None, message: str = "", **kw) -> None:
        if message and message != self.status["message"]:
            log(f"account: {message}")
        self.status = {**self.status, "state": state, "message": message, **kw}

    def running(self) -> bool:
        return self._busy.locked()

    def sign_up(self, name: str, password: str) -> None:
        """A new chudq account for the League account in the client."""
        self._start(self._sign_up, name, password)

    def join(self) -> None:
        """The sign-up's League account isn't the squad's: ask the admin to let it in."""
        if not self.signup:
            raise ValueError("Start signing up again")
        self._start(self._finish, self.signup, True, None)

    def add(self) -> None:
        """The League account in the client, added to the logged-in chudq account."""
        self._start(self._add)

    def _start(self, fn, *args) -> None:
        if not self._busy.acquire(blocking=False):
            raise ValueError("Already checking an account: give it a minute")

        def run():
            try:
                fn(*args)
            except (api.ServerError, ValueError) as e:
                self._set("error", str(e))
            except Exception as e:  # never let it take the app down
                self._set("error", f"{type(e).__name__}: {e}")
            finally:
                self._busy.release()
        threading.Thread(target=run, daemon=True, name="proof").start()

    def _client(self) -> tuple[league.Client, dict]:
        client = league.Client(config.load())
        acc = client.account()
        if not acc:
            raise ValueError("Open League and log in first: chudQ uses the account the client is logged in to")
        return client, acc

    def _sign_up(self, name: str, password: str) -> None:
        client, acc = self._client()
        self._set("working", f"Checking {acc['riotId']}", riotId=acc["riotId"], kind="signup")
        out = api.call("POST", "/api/app/signup/start", {"name": name, "password": password, "riotId": acc["riotId"]})
        self.signup = out["signup"]
        self._finish(out["signup"], False, (client, acc, out["icon"]))

    def _finish(self, signup: str, join: bool, icon_step) -> None:
        def ask():
            return api.call("POST", "/api/app/signup/finish", {"signup": signup, "join": join, "device": socket.gethostname()})
        out = self._prove(icon_step, ask) if icon_step else ask()
        if out.get("needsChoice"):
            # Proven, but not a squad account: the person chooses (ask to join) in the window
            return self._set("choose", f"{out.get('riotId')} isn't one of the squad's accounts", riotId=out.get("riotId"))
        api.keep(out)
        self.signup = None
        self._set("done", "Signed up" + ("" if config.load()["player"] else ": the admin lets you into the squad soon"))

    def _add(self) -> None:
        client, acc = self._client()
        self._set("working", f"Checking {acc['riotId']}", riotId=acc["riotId"], kind="add")
        out = api.authed("POST", "/api/app/accounts/start", {"riotId": acc["riotId"]})
        if out is None:
            raise ValueError("Log in first")
        res = self._prove((client, acc, out["icon"]), lambda: api.authed("POST", "/api/app/accounts/check", {}) or {})
        api.refresh_me()
        self._set("done", f"{res['linked']} is on your account now" if res.get("linked")
                  else f"{res.get('requested')} is waiting for the admin to approve it")

    def _prove(self, icon_step, ask) -> dict:
        """Sets the icon, asks the server until Riot shows it, and puts the old icon back either way."""
        client, acc, icon = icon_step
        old = acc["icon"]
        if not client.set_icon(icon):
            raise ValueError("The League client didn't take the icon change: is it still open?")
        self._set("working", f"Proving {acc['riotId']} is yours: Riot can take a minute to show the change")
        try:
            end = time.monotonic() + GIVE_UP
            while True:
                try:
                    return ask()
                except api.ServerError as e:
                    if "next" not in e.data or time.monotonic() > end:
                        raise
                    wait = max(ASK_EVERY, min(60, int(e.data.get("next") or ASK_EVERY)))
                time.sleep(wait)
        finally:
            if old is not None and client.set_icon(old):
                log("put the profile icon back")
