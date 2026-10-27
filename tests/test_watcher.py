"""The watcher's posting rules, with League and the server faked: a post every change, none for
a clock-only change until 15 s have passed, and /live/end when the game ends or sending is turned off."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()  # the app's folder, before chudq is imported

from chudq import config, watcher  # noqa: E402

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "allgamedata.json").read_text(encoding="utf-8"))


class Rules(unittest.TestCase):
    def setUp(self):
        config.save(token="t", send_live=True, stream=False, accounts=["streamer#EUW"])
        self.phase, self.data, self.calls, self.now = "InProgress", copy.deepcopy(SAMPLE), [], 1000.0
        client = mock.Mock(phase=lambda: self.phase, account=lambda: {"riotId": "streamer#EUW", "icon": 1})
        patches = [mock.patch.object(watcher.league, "Client", lambda cfg: client),
                   mock.patch.object(watcher.league, "all_game_data", lambda: self.data),
                   mock.patch.object(watcher.league, "game_info", lambda c: {"matchId": "EUW1_1", "queue": 420}),
                   mock.patch.object(watcher.api, "authed", lambda m, path, body=None: self.calls.append((path, body)) or {}),
                   mock.patch.object(watcher.api, "refresh_me", lambda: None),
                   mock.patch.object(watcher.time, "monotonic", lambda: self.now)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.w = watcher.Watcher()
        self.game = None

    def round(self, after=3):
        self.now += after
        self.game = self.w._round(self.game)
        return [c[0] for c in self.calls]

    def test_posts_and_skips(self):
        self.assertEqual(self.round(), ["/api/app/live"])
        self.assertEqual(self.calls[0][1]["matchId"], "EUW1_1")
        self.data["gameData"]["gameTime"] += 3
        self.assertEqual(len(self.round()), 1)                  # only the clock moved
        self.data["allPlayers"][0]["scores"]["kills"] += 1
        self.assertEqual(len(self.round()), 2)                  # a kill
        self.data["activePlayer"]["currentGold"] = (self.data["activePlayer"].get("currentGold") or 0) + 40
        self.assertEqual(len(self.round()), 3)                  # your gold went up
        self.assertEqual(len(self.round(after=16)), 4)          # 15 s without a post

    def test_game_end(self):
        self.round()
        self.phase, self.data = "EndOfGame", None
        self.assertEqual(self.round(), ["/api/app/live", "/api/app/live/end"])
        self.assertEqual(self.calls[-1][1], {"matchId": "EUW1_1"})

    def test_turned_off_mid_game(self):
        self.round()
        config.save(send_live=False)
        self.assertEqual(self.round(), ["/api/app/live", "/api/app/live/end"])
        self.data["allPlayers"][0]["scores"]["kills"] += 1
        self.round()
        self.assertEqual(len(self.calls), 2)                    # nothing more while off
        self.phase, self.data = "EndOfGame", None
        self.round()
        self.assertEqual(len(self.calls), 2)                    # and no second end
        config.save(send_live=True)

    def test_turned_on_mid_game(self):
        config.save(send_live=False)
        self.round()
        self.assertEqual(self.calls, [])
        config.save(send_live=True)
        self.assertEqual(self.round(), ["/api/app/live"])

    def test_other_account_sends_nothing(self):
        config.save(accounts=["someone else#EUW"])
        self.round()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.w.status["notConnected"], "streamer#EUW")
        config.save(accounts=["STREAMER#euw"])                     # Riot IDs match whatever the case
        self.assertEqual(self.round(), ["/api/app/live"])

    def test_streaming_with_live_data_off_posts_privately(self):
        config.save(send_live=False, stream=True)
        self.assertEqual(self.round(), ["/api/app/live"])
        self.assertTrue(self.calls[0][1]["private"])
        config.save(stream=False)

    def test_logged_out_sends_nothing(self):
        config.save(token=None)
        self.round()
        self.assertEqual(self.calls, [])


class ChampSelect(Rules):
    """Champ select is sent too (stage champSelect), kept through the loading screen (loading), and
    a dodge tells the site it's over."""

    def setUp(self):
        super().setUp()
        self.draft = {"myTeam": [{"champId": 103, "locked": False, "me": True, "pos": "MIDDLE", "team": 100}],
                      "theirTeam": [], "bans": {"mine": [], "theirs": []}, "phase": "BAN_PICK", "left": 30}
        p = mock.patch.object(watcher.league, "champ_select", lambda c: self.draft if self.phase == "ChampSelect" else None)
        p.start()
        self.addCleanup(p.stop)
        self.phase, self.data = "ChampSelect", None

    def test_champ_select_then_game(self):
        self.assertEqual(self.round(), ["/api/app/live"])
        self.assertEqual((self.calls[0][1]["stage"], self.calls[0][1]["queue"]), ("champSelect", 420))
        self.draft = {**self.draft, "left": 27}
        self.assertEqual(len(self.round()), 1)                  # only the timer moved
        self.draft = {**self.draft, "myTeam": [{**self.draft["myTeam"][0], "locked": True}]}
        self.assertEqual(len(self.round()), 2)                  # locked in
        self.draft = {**self.draft, "left": 30}
        self.assertEqual(len(self.round()), 3)                  # the next turn: the timer started again
        self.phase = "GameStart"                                # loading screen: no game data yet
        self.round()
        self.assertEqual(self.calls[-1][1]["stage"], "loading")
        self.phase, self.data = "InProgress", copy.deepcopy(SAMPLE)
        self.round()
        self.assertNotIn("stage", self.calls[-1][1])            # the game's own data takes over
        self.assertNotIn("/api/app/live/end", [c[0] for c in self.calls])

    def test_dodge(self):
        self.round()
        self.phase = "Lobby"
        self.assertEqual(self.round(), ["/api/app/live", "/api/app/live/end"])
        self.round()
        self.assertEqual(len(self.calls), 2)

    def test_live_data_off_sends_no_champ_select(self):
        config.save(send_live=False, stream=True)
        self.round()
        self.assertEqual(self.calls, [])
        config.save(stream=False)

    # the game-only rules don't apply here
    test_posts_and_skips = test_game_end = test_turned_off_mid_game = test_turned_on_mid_game = None
    test_other_account_sends_nothing = test_streaming_with_live_data_off_posts_privately = None


class ChampSelectReading(unittest.TestCase):
    def test_session(self):
        session = {"localPlayerCellId": 1, "timer": {"phase": "BAN_PICK", "adjustedTimeLeftInPhase": 25400},
                   "myTeam": [{"cellId": 0, "championId": 266, "assignedPosition": "top", "team": 1},
                              {"cellId": 1, "championId": 0, "championPickIntent": 103, "assignedPosition": "middle", "team": 1}],
                   "theirTeam": [{"cellId": 5, "championId": 122, "team": 2}, {"cellId": 6, "championId": 0, "team": 2}],
                   "actions": [[{"actorCellId": 0, "type": "ban", "completed": True, "championId": 157},
                                {"actorCellId": 5, "type": "ban", "completed": True, "championId": 86}],
                               [{"actorCellId": 0, "type": "pick", "completed": True, "championId": 266},
                                {"actorCellId": 5, "type": "pick", "completed": True, "championId": 122}]]}
        d = watcher.league.champ_select(mock.Mock(get=lambda path: session))
        self.assertEqual([(p["champId"], p["locked"], p["me"], p["pos"]) for p in d["myTeam"]],
                         [(266, True, False, "TOP"), (103, False, True, "MIDDLE")])
        self.assertEqual([(p["champId"], p["team"]) for p in d["theirTeam"]], [(122, 200)])
        self.assertEqual(d["bans"], {"mine": [157], "theirs": [86]})
        self.assertEqual((d["phase"], d["left"]), ("BAN_PICK", 25))
        self.assertIsNone(watcher.league.champ_select(mock.Mock(get=lambda path: None)))

    def test_chat(self):
        answers = {"/lol-chat/v1/conversations": [{"id": "lobby@x", "type": "customGame"}, {"id": "cs@x", "type": "championSelect"}],
                   "/lol-chat/v1/conversations/cs@x/messages": [
                       {"type": "system", "body": "joined_room"},
                       {"type": "chat", "body": "mid or feed", "fromSummonerId": 42, "timestamp": "2026-10-24T18:00:00Z"},
                       {"type": "chat", "body": "ok", "fromSummonerId": 7}]}
        chat = watcher.league.champ_select_chat(mock.Mock(get=lambda path: answers.get(path)), 42)
        self.assertEqual([(m["me"], m["text"]) for m in chat], [(True, "mid or feed"), (False, "ok")])
        self.assertEqual(watcher.league.champ_select_chat(mock.Mock(get=lambda path: None)), [])

    def test_chat_groupchat(self):
        # What the client really sends in champ select: a room, with your puuid on your own lines
        answers = {"/lol-chat/v1/conversations": [{"id": "cs@x", "type": "championSelect"}],
                   "/lol-chat/v1/conversations/cs@x/messages": [
                       {"type": "system", "body": "joined_room", "fromId": "0"},
                       {"type": "groupchat", "body": ":)", "fromId": "abc@eu1.pvp.net", "fromPuuid": "abc", "fromSummonerId": 0},
                       {"type": "groupchat", "body": "???", "fromId": "0", "fromPuuid": "", "fromSummonerId": 0}]}
        chat = watcher.league.champ_select_chat(mock.Mock(get=lambda path: answers.get(path)), "abc")
        self.assertEqual([(m["me"], m["text"]) for m in chat], [(True, ":)"), (False, "???")])

    def test_chat_seats(self):
        # A teammate's line carries the obfuscated puuid of their champ select slot: their lane shows
        answers = {"/lol-champ-select/v1/session": {
                       "localPlayerCellId": 0, "actions": [], "timer": {},
                       "myTeam": [{"cellId": 0, "puuid": "abc", "assignedPosition": "middle", "championId": 0, "team": 1},
                                  {"cellId": 1, "puuid": "", "obfuscatedPuuid": "obf-1", "assignedPosition": "bottom",
                                   "championPickIntent": 222, "team": 1}],
                       "theirTeam": []},
                   "/lol-summoner/v1/current-summoner": {"puuid": "abc", "summonerId": 1},
                   "/lol-chat/v1/conversations": [{"id": "cs@x", "type": "championSelect"}],
                   "/lol-chat/v1/conversations/cs@x/messages": [
                       {"type": "groupchat", "body": "mid", "fromPuuid": "abc", "fromId": "abc@eu1.pvp.net"},
                       {"type": "groupchat", "body": "jinx?", "fromId": "0", "fromPuuid": "", "fromObfuscatedPuuid": "obf-1"},
                       {"type": "groupchat", "body": "who", "fromId": "0", "fromObfuscatedPuuid": "nobody"}]}
        chat = watcher.league.champ_select(mock.Mock(get=lambda path: answers.get(path)))["chat"]
        self.assertEqual([(m["me"], m["pos"], m["champId"]) for m in chat], [(True, "MIDDLE", 0), (False, "BOTTOM", 222), (False, "", 0)])


class PracticeTool(unittest.TestCase):
    """Practice Tool and custom games are sent like any other; their non-queue ids become null."""

    def client(self, queue):
        session = {"gameData": {"gameId": 7123456789, "queue": {} if queue is None else {"id": queue}}}
        answers = {"/lol-gameflow/v1/session": session, "/lol-chat/v1/me": {"platformId": "EUW1"}}
        return mock.Mock(get=lambda path: answers.get(path))

    def test_queue_ids(self):
        for queue, want in ((-1, None), (0, None), (None, None), (420, 420)):
            info = watcher.league.game_info(self.client(queue))
            self.assertEqual(info, {"matchId": "EUW1_7123456789", "queue": want})

    def test_mode_passes_through(self):
        data = copy.deepcopy(SAMPLE)
        data["gameData"]["gameMode"] = "PRACTICETOOL"
        live = watcher.gamedata.trim(data, "EUW1_7123456789", None)
        self.assertEqual((live["mode"], live["queue"], live["matchId"]), ("PRACTICETOOL", None, "EUW1_7123456789"))


if __name__ == "__main__":
    unittest.main()


class LeftoverStream(unittest.TestCase):
    """OBS still streaming to chudq after chudQ restarted (an update mid-game) is taken back, so it
    stops after the game; a stream somewhere else is left alone."""

    def streamer(self, server):
        from chudq import stream
        s = stream.Streamer(mock.Mock(status={}))
        answers = {"GetStreamStatus": {"outputActive": True},
                   "GetStreamServiceSettings": {"streamServiceType": "whip_custom", "streamServiceSettings": {"server": server}}}
        s.obs = mock.Mock(call=lambda req, *a, **k: answers.get(req, {}))
        return s, stream

    def test_ours(self):
        s, _ = self.streamer("https://chudq.lol/api/app/whip/mathias")
        s._join(None, "LeagueRecorder", (), "x")
        self.assertTrue(s.live)

    def test_someone_elses(self):
        s, stream = self.streamer("https://example.com/whip")
        with self.assertRaises(stream.Skip):
            s._join(None, "LeagueRecorder", (), "x")
        self.assertFalse(s.live)
