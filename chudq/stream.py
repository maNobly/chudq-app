"""Streaming a game to chudq.lol through OBS, when "Stream my games" is on.

While the League client is open, OBS runs in the tray in our profile (obs.py); when the game has
loaded the stream starts - WHIP to <server>/api/app/whip with the app's token - and when the game is
over it stops. When the client closes, an OBS the app started itself is closed again.

An OBS that is already open in another profile is streamed from as it is, beside whatever it does
(recording, say), if that profile doesn't stream anywhere else (obs.other_destination): the first
time its stream settings are added and it is restarted between games, in the same profile and scenes.
A profile set up for Twitch or the like is left alone, and status says why. The live data goes either way.
Turning streaming on or off mid-game starts or stops the stream right away (kick()).
"""

from __future__ import annotations

import threading
import time

from . import config, obs as obs_mod
from .obs import NAME, OBS, OBSError
from .paths import log

MIN_VERSION = (30, 0)  # WHIP output


class Skip(Exception):
    """OBS can't be ours right now; the message says why."""


class Streamer:
    def __init__(self, watcher):
        self.watcher = watcher
        self.status = {"state": "off", "message": "Streaming is off", "obs": None, "encoder": None, "since": None}
        self.obs: OBS | None = None
        self.launched = False     # OBS was started by the app, so the app may close it
        self.live = False         # a stream the app started is on
        self.skipped = False      # this game isn't streamed (OBS was busy)
        self.setup_done = None    # the (preset, mic, other_audio) the scene was last set up for, on this connection
        self.joined = None        # (profile, preset, encoder) of someone else's OBS checked to stream from
        self.restarted = None     # the same, for the one restart that adds the stream settings
        self.retry_at = 0.0
        self.loaded_at = None     # when this game's picture could first be checked
        self.black = 0            # checks in a row that found the game capture black
        self._stop = threading.Event()
        self._wake = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="streamer").start()

    def kick(self) -> None:
        """A setting changed: act on it now (start or stop the stream mid-game)."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            self._release()
        except Exception as e:
            log(f"closing OBS: {type(e).__name__}: {e}")

    def _set(self, state: str, message: str, **kw) -> None:
        if (state, message) != (self.status["state"], self.status["message"]) and state not in ("off",):
            log(f"stream: {state} - {message}")
        self.status = {**self.status, "state": state, "message": message, **kw}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._round()
            except Skip as e:
                self._set("skipped" if self.watcher.status["inGame"] else "busy", str(e))
                if self.watcher.status["loaded"] and not self.skipped:
                    self.skipped = True
                    log(f"not streaming this game: {e}")
                self._drop()
                self._wake.wait(8)  # OBS stays busy a while; no need to knock every two seconds
            except Exception as e:  # OBS closed, crashed or said no: reconnect next round
                self._set("error", f"OBS: {e}")
                self._drop()
                self._stop.wait(5)
            self._wake.wait(2)
            self._wake.clear()

    def _drop(self) -> None:
        if self.obs:
            self.obs.close()
        self.obs, self.setup_done, self.joined = None, None, None

    def _round(self) -> None:
        cfg = config.load()
        w = self.watcher.status
        exe = obs_mod.find_obs(cfg["obs_path"])
        version = obs_mod.obs_version(exe) if exe else ()
        self.status["obs"] = {"path": str(exe) if exe else None, "version": ".".join(map(str, version)) or None}
        if not cfg["token"] or not cfg["stream"]:
            self._release()  # turned off mid-game: the stream stops now
            self.skipped, self.retry_at = False, 0.0
            return self._set("off", "Streaming is off" if cfg["token"] else "Log in to stream")
        if not exe:
            self._release()
            return self._set("no-obs", "OBS isn't installed")
        if version and version < MIN_VERSION:
            self._release()
            return self._set("old-obs", f"OBS {'.'.join(map(str, version))} is too old: streaming needs OBS 30 or newer")
        if w.get("notConnected"):
            self._release()
            return self._set("busy", f"{w['notConnected']} isn't on your chudq account: add it to stream its games")
        if not w["inGame"]:
            self.skipped, self.black, self.loaded_at = False, 0, None
            if self.live:
                self._stop_stream()
            if not w["client"]:
                self._release()
                return self._set("waiting", "Waiting for League")
        elif self.skipped:
            return
        preset = config.PRESETS[cfg["preset"]]
        encoder = obs_mod.pick_encoder(obs_mod.gpus(), version)
        self._ensure(exe, cfg, preset, encoder)
        if self.live:
            self._check_picture(cfg, w)
            if not self.obs.call("GetStreamStatus").get("outputActive"):
                log("the stream stopped on its own")
                self.live = False
                self.retry_at = time.monotonic() + 15
            return
        # Once the game's data has reached chudq.lol: the server lets the stream out only beside it
        posted = w.get("lastPost") and time.time() - w["lastPost"] < 20
        if w["loaded"] and posted and time.monotonic() >= self.retry_at:
            self._start_stream(cfg)
        elif not w["inGame"]:
            self._set("ready", "Ready: the stream starts when your game loads", encoder=encoder)

    def _check_picture(self, cfg: dict, w: dict) -> None:
        """OBS's game capture records black on some PCs (a laptop's two GPUs: OBS on one, League on the
        other). Seen black for half a minute of a loaded game, chudQ takes the game with Windows' own
        capture instead, from then on."""
        if not w["loaded"] or self.joined is not None or cfg["capture"] != "game":
            self.loaded_at = None if not w["loaded"] else self.loaded_at
            return
        now = time.monotonic()
        self.loaded_at = self.loaded_at or now
        if now - self.loaded_at < 15 or self.black < 0:
            return                      # past the loading screen first; -1: seen a picture this game
        try:
            black = obs_mod.is_black(self.obs)
        except Exception as e:          # an OBS without screenshots, say: leave it as it is
            log(f"couldn't check the picture: {e}")
            self.black = -1
            return
        self.black = self.black + 1 if black else -1
        if self.black >= 15:            # about 30 s of black (a check every 2 s)
            log("game capture shows only black: switching to window capture")
            config.save(capture="window")
            self.setup_done, self.black = None, 0

    def _ensure(self, exe, cfg: dict, preset: tuple, encoder: str) -> None:
        """OBS running in our profile and set up, or Skip."""
        if self.obs is None:
            if not obs_mod.obs_running():
                self._launch(exe, cfg, preset, encoder)
            else:
                try:
                    self.obs = obs_mod.connect(cfg["obs_port"], tries=3)
                except OBSError:
                    raise Skip("OBS is open but chudQ can't reach it; close OBS once and chudQ will start it") from None
        profile = self.obs.profile()
        if profile != NAME:
            return self._join(exe, profile, preset, encoder)
        self.joined = None
        if self.obs.call("GetStreamStatus").get("outputActive"):
            self.live = True  # a stream of ours from before a reconnect
            return
        if self.obs.call("GetRecordStatus").get("outputActive"):
            raise Skip("OBS is recording")
        if not obs_mod.profile_matches(self.obs, encoder, preset[3]):
            # Output settings are read at OBS's start: restart it with the new ones
            log("stream quality changed: restarting OBS")
            self._drop()
            obs_mod.stop_obs()
            self._launch(exe, cfg, preset, encoder)
        want = (preset, cfg["mic"], cfg["other_audio"], cfg["capture"])
        if self.setup_done != want:
            obs_mod.setup(self.obs, *want)
            self.setup_done = want

    def _join(self, exe, profile: str, preset: tuple, encoder: str) -> None:
        """An OBS open in someone else's profile: stream from it beside what it's doing, scenes and all."""
        if self.joined == (profile, preset, encoder):
            return                           # checked on this connection already
        if self.obs.call("GetStreamStatus").get("outputActive"):
            if self.live:
                return
            if obs_mod.streams_to_chudq(self.obs):
                # Ours from before chudQ restarted (an update mid-game): take it back, so it stops after the game
                self.live = True
                log(f"OBS was still streaming to chudq (profile {profile}): carrying on with it")
                return
            raise Skip(f"OBS is already streaming (profile {profile})")
        if where := obs_mod.other_destination(self.obs):
            raise Skip(f"OBS's profile {profile} streams to {where}: chudQ won't change it. Close OBS and chudQ uses its own")
        folder = obs_mod.profile_dir(profile)
        if folder is None:
            raise Skip(f"Can't find OBS's profile {profile}")
        if obs_mod.can_stream_here(self.obs, folder, preset, encoder):
            self.joined = (profile, preset, encoder)
            return self._set("ready", f"Ready: the stream starts when your game loads (from OBS's {profile} profile)")
        if obs_mod._param(self.obs, "Output", "Mode") != "Advanced":
            raise Skip(f"OBS's profile {profile} uses simple output mode: close OBS and chudQ uses its own")
        if self.restarted == (profile, preset, encoder):
            # Set up and restarted once already and still not right: never restart it again and again
            raise Skip(f"OBS's profile {profile} didn't take the stream settings: close OBS and chudQ uses its own")
        if self.watcher.status["inGame"] or self.obs.busy():
            raise Skip("OBS needs one restart to stream too: chudQ does it after this game")
        collection = self.obs.call("GetSceneCollectionList")["currentSceneCollectionName"]
        log(f"adding the stream settings to OBS's profile {profile} and restarting it")
        self._set("starting", "Restarting OBS once so it can stream too")
        obs_mod.add_stream_settings(self.obs, folder, preset, encoder)
        self.restarted = (profile, preset, encoder)
        self._drop()
        obs_mod.stop_obs()
        obs_mod.start_obs(exe, profile, collection)   # as it was: same profile and scenes, not ours to close
        self.launched = False
        self.obs = obs_mod.connect(config.load()["obs_port"], tries=60)

    def _launch(self, exe, cfg: dict, preset: tuple, encoder: str) -> None:
        self._set("starting", "Starting OBS")
        obs_mod.write_websocket_config(cfg["obs_port"])
        obs_mod.write_profile(preset, encoder)
        obs_mod.start_obs(exe)
        self.launched = True
        self.obs = obs_mod.connect(cfg["obs_port"], tries=60)
        self.setup_done = None

    def _start_stream(self, cfg: dict) -> None:
        self.obs.call("SetStreamServiceSettings", streamServiceType="whip_custom",
                      streamServiceSettings={"server": f"{cfg['server']}/api/app/whip", "bearer_token": cfg["token"]})
        self.obs.call("StartStream")
        for _ in range(20):
            time.sleep(0.5)
            if self.obs.call("GetStreamStatus").get("outputActive"):
                self.live = True
                return self._set("live", "Streaming your game", since=time.time())
        self.retry_at = time.monotonic() + 30
        raise OBSError("the stream didn't start (is chudq.lol reachable? see OBS's log)")

    def _stop_stream(self) -> None:
        self.live = False
        self.status["since"] = None
        try:
            if self.obs and self.obs.call("GetStreamStatus").get("outputActive"):
                self.obs.call("StopStream")
                log("stream stopped: the game is over")
        except (OSError, ConnectionError, OBSError) as e:
            log(f"stopping the stream: {e}")

    def _release(self) -> None:
        """Stop our stream, and close OBS if the app started it and nothing else is using it."""
        if self.live:
            self._stop_stream()
        if not self.launched:
            return self._drop()
        try:
            if self.obs is None and obs_mod.obs_running():
                self.obs = obs_mod.connect(config.load()["obs_port"])
            mine = self.obs is not None and self.obs.profile() == NAME and not self.obs.busy()
        except (OSError, ConnectionError, OBSError):
            mine = False
        self._drop()
        if mine:
            log("closing OBS")
            obs_mod.stop_obs()
        self.launched = False
