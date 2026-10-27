"""allgamedata -> the contract's live data (squad/STREAMING.md). Run from squad/app:

    python -m unittest discover tests
"""

import copy
import json
import unittest
from pathlib import Path

from chudq import gamedata

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "allgamedata.json").read_text(encoding="utf-8"))


class Trim(unittest.TestCase):
    def setUp(self):
        self.data = copy.deepcopy(SAMPLE)
        self.live = gamedata.trim(self.data, "EUW1_7123456789", 420)

    def test_top_level(self):
        live = self.live
        self.assertEqual(set(live), {"version", "matchId", "queue", "gameTime", "mode", "me", "players", "events"})
        self.assertEqual((live["version"], live["matchId"], live["queue"]), (1, "EUW1_7123456789", 420))
        self.assertEqual(live["gameTime"], 412.3)
        self.assertEqual(live["mode"], "CLASSIC")
        self.assertEqual(live["me"], {"riotId": "streamer#EUW", "gold": 1235, "level": 9})

    def test_player(self):
        ahri = self.live["players"][0]
        self.assertEqual(ahri, {
            "riotId": "streamer#EUW", "champ": "Ahri", "champName": "Ahri", "team": 100, "pos": "MIDDLE", "level": 9,
            "k": 3, "d": 1, "a": 2, "cs": 80, "ward": 7.5,
            "items": [6655, 3020, 1052, 3340],  # slot order, not the order the game lists them
            "dead": False, "respawn": 0, "spells": ["Flash", "Ignite"], "keystone": 8112, "bot": False})

    def test_players_in_order_with_teams(self):
        players = self.live["players"]
        self.assertEqual(len(players), 10)
        self.assertEqual([p["team"] for p in players], [100] * 5 + [200] * 5)
        self.assertEqual(players[5]["riotId"], "enemy top#KR1")

    def test_champion_ids(self):
        by_name = {p["champName"]: p["champ"] for p in self.live["players"]}
        self.assertEqual(by_name["Wukong"], "MonkeyKing")
        self.assertEqual(by_name["Kai'Sa"], "Kaisa")
        self.assertEqual(by_name["Dr. Mundo"], "DrMundo")
        self.data["allPlayers"][0]["rawChampionName"] = ""
        self.assertEqual(gamedata.trim(self.data)["players"][0]["champ"], "Ahri")  # no raw name: the display name

    def test_dead_and_bot(self):
        wukong, garen = self.live["players"][1], self.live["players"][2]
        self.assertEqual((wukong["dead"], wukong["respawn"]), (True, 12))
        self.assertTrue(garen["bot"])
        self.assertEqual(garen["riotId"], "Bot Garen")
        self.assertIsNone(garen["pos"])  # the game sends "" outside ranked roles

    def test_events_whole(self):
        self.assertEqual(self.live["events"], SAMPLE["events"]["Events"])

    def test_small(self):
        self.assertLess(len(json.dumps(self.live)), len(json.dumps(SAMPLE)) / 3)
        self.assertLess(len(json.dumps(self.live)), 256 * 1024)

    def test_unknown_match(self):
        live = gamedata.trim(self.data)
        self.assertIsNone(live["matchId"])
        self.assertIsNone(live["queue"])

    def test_old_riot_id_fields(self):
        p = self.data["allPlayers"][3]
        del p["riotId"]
        self.assertEqual(gamedata.trim(self.data)["players"][3]["riotId"], "ally adc#EUW")

    def test_spectating_or_replay(self):
        self.data["activePlayer"] = {"errorCode": "RESOURCE_NOT_FOUND", "httpStatus": 404}
        self.assertIsNone(gamedata.trim(self.data))


class Clock(unittest.TestCase):
    def test_only_the_clock_moved(self):
        a = gamedata.trim(SAMPLE)
        later = copy.deepcopy(SAMPLE)
        later["gameData"]["gameTime"] += 3
        later["allPlayers"][1]["respawnTimer"] -= 3
        self.assertEqual(gamedata.without_clock(a), gamedata.without_clock(gamedata.trim(later)))

    def test_your_gold_is_a_change(self):
        a = gamedata.trim(SAMPLE)
        later = copy.deepcopy(SAMPLE)
        later["activePlayer"]["currentGold"] += 6
        self.assertNotEqual(gamedata.without_clock(a), gamedata.without_clock(gamedata.trim(later)))

    def test_a_real_change(self):
        a = gamedata.trim(SAMPLE)
        later = copy.deepcopy(SAMPLE)
        later["allPlayers"][0]["scores"]["creepScore"] += 1
        self.assertNotEqual(gamedata.without_clock(a), gamedata.without_clock(gamedata.trim(later)))
        later = copy.deepcopy(SAMPLE)
        later["events"]["Events"].append({"EventID": 7, "EventName": "ChampionKill", "EventTime": 415.0})
        self.assertNotEqual(gamedata.without_clock(a), gamedata.without_clock(gamedata.trim(later)))


if __name__ == "__main__":
    unittest.main()
