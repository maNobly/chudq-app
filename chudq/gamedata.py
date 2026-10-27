"""The game's live data, cut down to what chudq.lol shows (squad/STREAMING.md, "Live data").

The Live Client API's allgamedata is 30-100 KB, most of it ability and rune descriptions; the
site needs everyone's champion, scores, items and spells, your gold and level, and the event
feed. Nothing here talks to anything, so it can be tested anywhere.
"""

from __future__ import annotations

TEAMS = {"ORDER": 100, "CHAOS": 200}
RAW_CHAMP = "game_character_displayname_"


def riot_id(p: dict) -> str | None:
    """Name#TAG; older game versions only had riotIdGameName/TagLine, or the summoner name."""
    if p.get("riotId"):
        return p["riotId"]
    if p.get("riotIdGameName"):
        return f'{p["riotIdGameName"]}#{p.get("riotIdTagLine", "")}'
    return p.get("summonerName") or None


def champ_id(p: dict) -> str | None:
    """The champion's Data Dragon id (Wukong is MonkeyKing): rawChampionName without its prefix."""
    raw = p.get("rawChampionName") or ""
    return raw[len(RAW_CHAMP):] if raw.startswith(RAW_CHAMP) else p.get("championName")


def items(raw) -> list:
    """A player's items by slot. Not always a list: a Practice Tool dummy's are {"error": "Unable to find player"}."""
    if not isinstance(raw, list):
        return []
    return [i.get("itemID") for i in sorted((i for i in raw if isinstance(i, dict)), key=lambda i: i.get("slot", 0))]


def player(p: dict) -> dict:
    scores = p.get("scores") or {}
    spells = p.get("summonerSpells") or {}
    return {
        "riotId": riot_id(p),
        "champ": champ_id(p),
        "champName": p.get("championName"),
        "team": TEAMS.get(p.get("team"), p.get("team")),
        "pos": p.get("position") or None,
        "level": p.get("level"),
        "k": scores.get("kills", 0), "d": scores.get("deaths", 0), "a": scores.get("assists", 0),
        "cs": scores.get("creepScore", 0), "ward": round(scores.get("wardScore") or 0, 1),
        "items": items(p.get("items")),
        "dead": bool(p.get("isDead")),
        "respawn": round(p.get("respawnTimer") or 0),
        "spells": [(spells.get(k) or {}).get("displayName") for k in ("summonerSpellOne", "summonerSpellTwo")
                   if spells.get(k)],
        "keystone": ((p.get("runes") or {}).get("keystone") or {}).get("id"),
        "bot": bool(p.get("isBot")),
    }


def trim(data: dict, match_id: str | None = None, queue: int | None = None) -> dict | None:
    """allgamedata -> the body of POST /api/app/live. None when it isn't a game of yours to send:
    a replay or a spectated game has no active player."""
    me = data.get("activePlayer") or {}
    if not isinstance(me, dict) or not riot_id(me):
        return None
    gd = data.get("gameData") or {}
    return {
        "version": 1,
        "matchId": match_id,
        "queue": queue,
        "gameTime": round(gd.get("gameTime") or 0, 1),
        "mode": gd.get("gameMode"),
        "me": {"riotId": riot_id(me), "gold": round(me.get("currentGold") or 0), "level": me.get("level")},
        # Practice Tool's target dummies have no champion, and errors where their stats would be
        "players": [player(p) for p in data.get("allPlayers") or [] if isinstance(p, dict) and p.get("championName")],
        "events": list((data.get("events") or {}).get("Events") or []),
    }


def without_clock(live: dict) -> dict:
    """What has to change for a post to be worth sending: everything but what moves with the clock
    alone (the game time, respawn countdowns). Your gold counts: the site shows it, and left to the
    15 s floor it lagged and jumped."""
    return {**live, "gameTime": None, "players": [{**p, "respawn": None} for p in live["players"]]}
