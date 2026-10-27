"""League on this PC: the client's API (from its lockfile) and the game's Live Client API. Both
answer on 127.0.0.1 with self-signed certificates, and only while they run."""

from __future__ import annotations

import base64
import json
import os
import ssl
import urllib.request
from pathlib import Path

IN_GAME = {"GameStart", "InProgress", "Reconnect"}
LIVE = "https://127.0.0.1:2999/liveclientdata"
REGION_PLATFORM = {"EUW": "EUW1", "EUNE": "EUN1", "NA": "NA1", "KR": "KR", "TR": "TR1", "OC1": "OC1", "OCE": "OC1",
                   "BR": "BR1", "JP": "JP1", "LA1": "LA1", "LA2": "LA2", "LAN": "LA1", "LAS": "LA2", "RU": "RU"}
# Riot's own list of where its games are installed
RIOT_INSTALLS = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Riot Games" / "RiotClientInstalls.json"

NO_TLS_CHECK = ssl.create_default_context()
NO_TLS_CHECK.check_hostname = False
NO_TLS_CHECK.verify_mode = ssl.CERT_NONE  # the client and the game use self-signed certificates on 127.0.0.1


def http_json(url: str, auth: str | None = None, timeout: float = 3, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Content-Type": "application/json"}, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=NO_TLS_CHECK if url.startswith("https") else None) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except (OSError, ValueError):
        return None


def league_dirs(cfg: dict) -> list[Path]:
    """Where League may be installed: the setting, Riot's list, and the usual folder on each drive."""
    dirs = [Path(cfg["league_dir"])]
    try:
        installs = json.loads(RIOT_INSTALLS.read_text(encoding="utf-8"))
        dirs += [Path(p) for p in (installs.get("associated_client") or {}) if "league" in p.lower()]
    except (OSError, ValueError, AttributeError):
        pass
    dirs += [Path(f"{d}:\\Riot Games\\League of Legends") for d in "CDEFG"]
    return list(dict.fromkeys(dirs))


class Client:
    """The League client's API; None for everything while the client is closed."""

    def __init__(self, cfg: dict):
        self.dirs = league_dirs(cfg)

    def get(self, path: str, method: str = "GET", body: dict | None = None):
        # The lockfile only exists while the client runs
        for d in self.dirs:
            try:
                _, _, port, password, proto = (d / "lockfile").read_text().strip().split(":")
            except (OSError, ValueError):
                continue
            return http_json(f"{proto}://127.0.0.1:{port}{path}", auth=f"riot:{password}", method=method, body=body)
        return None

    def account(self) -> dict | None:
        """The League account logged in to the client: {riotId, icon}, or None."""
        me = self.get("/lol-summoner/v1/current-summoner")
        if not isinstance(me, dict) or not me.get("gameName"):
            return None
        return {"riotId": f"{me['gameName']}#{me.get('tagLine', '')}", "icon": me.get("profileIconId")}

    def set_icon(self, icon: int) -> bool:
        """Sets the account's profile icon, as the client's own icon picker does."""
        return self.get("/lol-summoner/v1/current-summoner/icon", method="PUT", body={"profileIconId": icon}) is not None

    def phase(self) -> str | None:
        return self.get("/lol-gameflow/v1/gameflow-phase")


def game_info(client: Client) -> dict:
    """Which game this is, from the client: {matchId, queue}, either None when it doesn't say."""
    gd = (client.get("/lol-gameflow/v1/session") or {}).get("gameData") or {}
    platform = (client.get("/lol-chat/v1/me") or {}).get("platformId")
    if not platform:
        region = (client.get("/riotclient/region-locale") or {}).get("region", "")
        platform = REGION_PLATFORM.get(region.upper())
    game_id = gd.get("gameId")
    queue = (gd.get("queue") or {}).get("id")
    # Every game is sent, Practice Tool and customs too (the server decides what can be bet on);
    # those have queue 0 or -1, which isn't a queue: null
    return {"matchId": f"{platform}_{game_id}" if game_id and platform else None,
            "queue": queue if isinstance(queue, int) and queue > 0 else None}


def all_game_data() -> dict | None:
    data = http_json(f"{LIVE}/allgamedata", timeout=2)
    return data if isinstance(data, dict) and "gameData" in data else None


def champ_select(client: Client) -> dict | None:
    """Champ select as the client shows it to you: your team's picks (hovered or locked), the other
    team's locked picks, the bans and the timer. Teammates' names are left out (ranked hides them)."""
    s = client.get("/lol-champ-select/v1/session")
    if not isinstance(s, dict) or "myTeam" not in s:
        return None
    me = s.get("localPlayerCellId")
    locked = {a.get("actorCellId") for turn in s.get("actions") or [] for a in turn
              if isinstance(a, dict) and a.get("type") == "pick" and a.get("completed")}
    bans = {a.get("actorCellId"): a.get("championId") for turn in s.get("actions") or [] for a in turn
            if isinstance(a, dict) and a.get("type") == "ban" and a.get("completed") and a.get("championId")}

    def side(team: list) -> list[dict]:
        return [{"champId": p.get("championId") or p.get("championPickIntent") or 0,
                 "locked": p.get("cellId") in locked or (bool(p.get("championId")) and not s.get("actions")),
                 "pos": (p.get("assignedPosition") or "").upper(), "me": p.get("cellId") == me,
                 "team": 200 if p.get("team") == 2 else 100, "cell": p.get("cellId")}
                for p in team if isinstance(p, dict)]
    mine, theirs = side(s.get("myTeam") or []), side(s.get("theirTeam") or [])
    my_cells = {p["cell"] for p in mine}
    timer = s.get("timer") or {}
    who = client.get("/lol-summoner/v1/current-summoner") or {}
    # Who's who in the chat: ranked hides teammates' names, but each line carries the same obfuscated
    # puuid as their slot here, and so their lane and champion
    seats = {}
    for p in s.get("myTeam") or []:
        if isinstance(p, dict):
            seat = {"pos": (p.get("assignedPosition") or "").upper(), "champId": p.get("championId") or p.get("championPickIntent") or 0}
            for k in ("obfuscatedPuuid", "puuid", "obfuscatedSummonerId", "summonerId"):
                if p.get(k):
                    seats[str(p[k])] = seat
    return {
        "chat": champ_select_chat(client, who.get("puuid") or who.get("summonerId"), seats),
        "myTeam": mine, "theirTeam": [p for p in theirs if p["champId"]],
        "bans": {"mine": [c for cell, c in bans.items() if cell in my_cells],
                 "theirs": [c for cell, c in bans.items() if cell not in my_cells]},
        "phase": str(timer.get("phase") or ""),
        "left": round((timer.get("adjustedTimeLeftInPhase") or 0) / 1000),
    }


def champ_select_chat(client: Client, me_id=None, seats: dict | None = None) -> list[dict]:
    """The champ select's team chat, oldest first: [{me, text, at, pos, champId}]. No names (ranked
    hides teammates'), but the writer's lane and champion when their slot is known (seats: champ
    select's ids -> {pos, champId}); the client's own notices are left out."""
    convs = client.get("/lol-chat/v1/conversations")
    conv = next((c for c in convs or [] if isinstance(c, dict) and c.get("type") == "championSelect"), None)
    if not conv:
        return []
    msgs = client.get(f"/lol-chat/v1/conversations/{conv.get('id')}/messages")
    out = []
    for m in (msgs if isinstance(msgs, list) else [])[-40:]:
        # Champ select's lines are "groupchat" (a room); "system" ones are the client's joined_room notices
        if not isinstance(m, dict) or m.get("type") not in ("chat", "groupchat") or not str(m.get("body") or "").strip():
            continue
        # Your own lines carry your puuid (fromPuuid, and fromId as "<puuid>@<server>"); teammates' are hidden
        me = bool(me_id) and me_id in (m.get("fromPuuid"), m.get("fromSummonerId"), str(m.get("fromId") or "").split("@")[0])
        seat = next((seats[str(m[k])] for k in ("fromObfuscatedPuuid", "fromPuuid", "fromObfuscatedSummonerId", "fromSummonerId")
                     if m.get(k) and str(m[k]) in (seats or {})), {})
        out.append({"me": me, "text": str(m["body"])[:200], "at": str(m.get("timestamp") or "")[:30],
                    "pos": seat.get("pos") or "", "champId": seat.get("champId") or 0})
    return out
