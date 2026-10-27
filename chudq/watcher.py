"""The live data: while you're logged in, "Send live game data" is on and a game (or champ select) is on, the game's Live Client API every three
seconds, cut down (gamedata.py) and posted to chudq.lol. A post that would only move the clock is
skipped, but never for more than 15 s; when the game is over (or sending is turned off
mid-game) the site is told so. Nothing the
server or the game does stops the loop: it logs, says so in status, and tries again."""

from __future__ import annotations

import json
import threading
import time

from . import api, config, gamedata, league
from .paths import log

EVERY = 3        # seconds between readings
AT_MOST = 15     # seconds without a post, even when nothing changed


class Game:
    """The game being sent: which one it is, and what was last posted."""

    def __init__(self):
        self.info = {"matchId": None, "queue": None}
        self.asked = 0.0
        self.last_key = None
        self.last_post = 0.0
        self.posted = False


class Watcher:
    """Its own thread. status is what the window and the streamer read: whether the client is open,
    a game is on and loaded (the Live Client API answering for you), and how the sending goes."""

    def __init__(self):
        self.status = {"client": False, "phase": None, "inGame": False, "loaded": False, "champion": None,
                       "champSelect": False,        # in champ select: its picks and bans are sent too
                       "matchId": None, "lastPost": None, "posts": 0, "error": None,
                       "account": None,         # the Riot ID the League client is logged in to
                       "notConnected": None}    # ...when it isn't one of the chudq account's: nothing is sent
        self.me_at = 0.0
        self.draft = None         # champ select as last read, kept through the loading screen after it
        self.draft_queue = None
        self.draft_key, self.draft_at, self.draft_posted = None, 0.0, False
        self._stop = threading.Event()
        self._wake = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="watcher").start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def kick(self) -> None:
        """A setting changed: act on it now rather than at the next reading."""
        self._wake.set()

    def _set(self, **kw) -> None:
        self.status = {**self.status, **kw}

    def _run(self) -> None:
        log("watching for games")
        game: Game | None = None
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                game = self._round(game)
            except Exception as e:  # no console to die in: log it and carry on
                log(f"watcher error: {type(e).__name__}: {e}")
                self._set(error=f"{type(e).__name__}: {e}")
            self._wake.wait(max(0.5, EVERY - (time.monotonic() - started)))
            self._wake.clear()

    def _round(self, game: Game | None) -> Game | None:
        cfg = config.load()
        client = league.Client(cfg)
        phase = client.phase()
        if cfg["token"] and time.monotonic() - self.me_at > (600 if cfg["accounts"] else 60) and phase not in league.IN_GAME:
            self.me_at = time.monotonic()      # accounts added on the site meanwhile
            cfg = api.refresh_me() or cfg
        acc = client.account() if phase is not None else None
        if acc or phase is None:
            rid = acc["riotId"] if acc else None
            self._set(account=rid, notConnected=rid if rid and cfg["token"] and not connected(cfg, rid) else None)
        data = league.all_game_data()
        # A client that crashed or restarted mid-game: the game itself says whether it is still on
        in_game = phase in league.IN_GAME or (phase is None and data is not None)
        live = gamedata.trim(data) if in_game and data else None
        draft = league.champ_select(client) if phase == "ChampSelect" else None
        self._set(client=phase is not None, phase=phase, inGame=in_game, loaded=live is not None, champSelect=draft is not None,
                  champion=next((p["champName"] for p in live["players"] if p["riotId"] == live["me"]["riotId"]), None)
                  if live else None)
        if draft is not None or (in_game and live is None and self.draft is not None):
            # Champ select, and the loading screen after it: the Live page shows the picks and bans as they
            # happen, until the game's own data takes over
            if draft is not None:
                if self.draft is None:
                    log("champ select")
                    self.draft_queue = league.game_info(client)["queue"]
                self.draft = draft
            self._post_draft(cfg, self.status["account"], "champSelect" if draft is not None else "loading")
        elif self.draft is not None:
            if not in_game:
                log("champ select ended without a game")
                self._end_draft()
            self.draft, self.draft_key, self.draft_posted = None, None, False   # the game's own data takes over
        if not in_game:
            if game is not None:
                self._end(game)
            return None
        if game is None:
            game = Game()
            log(f"a game is on (phase {phase})")
        if not game.info["matchId"] and time.monotonic() - game.asked > 30:
            game.asked = time.monotonic()
            game.info = league.game_info(client)
            self._set(matchId=game.info["matchId"])
        if live is None or not cfg["token"]:
            return game  # still loading, a replay or spectating, or logged out
        if not connected(cfg, live["me"]["riotId"]):
            # Only games on the chudq account's own League accounts: the window offers to add this one
            self._set(notConnected=live["me"]["riotId"])
            return game
        # With live data off but streaming on, it still goes, marked private: the server lets a stream
        # out only beside the app's word for which (connected) account plays; the site doesn't show it
        if not cfg["send_live"] and not cfg["stream"]:
            if game.posted:  # turned off mid-game: the site drops what it had
                log("sending live data turned off")
                self._send_end(game)
            return game
        live["matchId"], live["queue"], live["private"] = game.info["matchId"], game.info["queue"], not cfg["send_live"]
        key = gamedata.without_clock(live)
        if key == game.last_key and time.monotonic() - game.last_post < AT_MOST:
            return game
        try:
            if api.authed("POST", "/api/app/live", live) is not None:
                game.last_key, game.last_post, game.posted = key, time.monotonic(), True
                self._set(lastPost=time.time(), posts=self.status["posts"] + 1, error=None)
                if self.status["posts"] == 1 or self.status["posts"] % 100 == 0:
                    log(f"sent live data ({self.status['posts']} posts, {live['matchId'] or 'match id unknown'})")
        except api.ServerError as e:
            if self.status.get("error") != str(e):
                log(f"live data not sent: {e}")
            self._set(error=str(e))
            if e.data.get("notConnected"):
                self.me_at = 0.0        # the site disagrees about the accounts: ask again
        return game

    def _post_draft(self, cfg: dict, rid: str | None, stage: str) -> None:
        if not cfg["token"] or not cfg["send_live"] or not connected(cfg, rid):
            if self.draft_posted:
                self._end_draft()
            return
        body = {"version": 1, "stage": stage, "queue": self.draft_queue, "me": {"riotId": rid},
                "champSelect": self.draft}
        # Like the game: a post when something changed, and at least every 15 s. The timer counting down
        # isn't a change, but it starting again (every turn) is: when it ends, to a few seconds
        ends = round((time.monotonic() + self.draft["left"]) / 3)
        key = json.dumps({**body, "champSelect": {**self.draft, "left": ends}}, sort_keys=True)
        if key == self.draft_key and time.monotonic() - self.draft_at < AT_MOST:
            return
        try:
            if api.authed("POST", "/api/app/live", body) is not None:
                self.draft_key, self.draft_at, self.draft_posted = key, time.monotonic(), True
                self._set(lastPost=time.time(), error=None)
        except api.ServerError as e:
            if self.status.get("error") != str(e):
                log(f"champ select not sent: {e}")
            self._set(error=str(e))

    def _end_draft(self) -> None:
        self.draft_key, self.draft_posted = None, False
        try:
            api.authed("POST", "/api/app/live/end", {})
        except api.ServerError as e:
            log(f"couldn't tell the site champ select is over: {e}")

    def _end(self, game: Game) -> None:
        log(f"the game is over ({game.info['matchId'] or 'match id unknown'})")
        self._set(matchId=None, champion=None)
        if game.posted:
            self._send_end(game)

    def _send_end(self, game: Game) -> None:
        game.posted, game.last_key = False, None
        for _ in range(3):
            try:
                api.authed("POST", "/api/app/live/end", {"matchId": game.info["matchId"]})
                return
            except api.ServerError as e:
                log(f"couldn't tell the site the game is over: {e}")
                self._stop.wait(5)


def connected(cfg: dict, riot_id: str | None) -> bool:
    return bool(riot_id) and riot_id.strip().lower() in {a.lower() for a in cfg.get("accounts") or []}
