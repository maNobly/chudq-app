"""OBS, the friend's own install, driven through obs-websocket 5 (built into OBS 28+; streaming to
chudq.lol needs WHIP, so OBS 30+). The app keeps a profile and scene collection of its own
("ChudQ") so other OBS setups are left alone: the League window and its sound, the mic if it is on,
and a stream encoder picked for the GPU with no B-frames (WebRTC can't play them).

What OBS only reads when it starts or switches profile - the websocket server and the profile's
output settings - is written to its files before that; the rest is set through the websocket.
"""

from __future__ import annotations

import base64
import configparser
import hashlib
import io
import json
import os
import socket
import struct
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from .paths import log

OBS_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "obs-studio"
WS_CONFIG = OBS_DIR / "plugin_config" / "obs-websocket" / "config.json"
NAME = "ChudQ"  # OBS profile and scene collection
PROFILE = OBS_DIR / "basic" / "profiles" / NAME
LEAGUE_WINDOW = "League of Legends (TM) Client:RiotWindowClass:League of Legends.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---- a WebSocket client, enough for obs-websocket ---------------------------------------------

class WebSocket:
    def __init__(self, host: str, port: int, timeout: float = 10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           "Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed during the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("OBS closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, opcode: int, payload: bytes) -> None:
        n, mask = len(payload), os.urandom(4)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 \
            else bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text: str) -> None:
        self._send(0x1, text.encode())

    def recv(self) -> str:
        message = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)  # the server never masks
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise ConnectionError("OBS closed the connection")
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if b0 & 0x80:
                    return message.decode()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class OBSError(Exception):
    pass


class OBS:
    """obs-websocket 5: requests only, no events."""

    def __init__(self, port: int, password: str):
        self.ws = WebSocket("127.0.0.1", port)
        hello = json.loads(self.ws.recv())["d"]
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        if "authentication" in hello:
            a = hello["authentication"]
            secret = base64.b64encode(hashlib.sha256((password + a["salt"]).encode()).digest())
            ident["authentication"] = base64.b64encode(hashlib.sha256(secret + a["challenge"].encode()).digest()).decode()
        self.ws.send(json.dumps({"op": 1, "d": ident}))
        if json.loads(self.ws.recv())["op"] != 2:
            raise OBSError("not identified")

    def call(self, request: str, **data) -> dict:
        rid = uuid.uuid4().hex
        self.ws.send(json.dumps({"op": 6, "d": {"requestType": request, "requestId": rid, "requestData": data}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg["op"] == 7 and msg["d"]["requestId"] == rid:
                st = msg["d"]["requestStatus"]
                if not st["result"]:
                    raise OBSError(f'{request}: {st.get("code")} {st.get("comment", "")}')
                return msg["d"].get("responseData") or {}

    def busy(self) -> str | None:
        """'recording' or 'streaming' when an output is on, else None."""
        if self.call("GetRecordStatus").get("outputActive"):
            return "recording"
        if self.call("GetStreamStatus").get("outputActive"):
            return "streaming"
        return None

    def profile(self) -> str:
        return self.call("GetProfileList")["currentProfileName"]

    def close(self) -> None:
        self.ws.close()


# ---- what is installed ----------------------------------------------------------------------------

def powershell(command: str, timeout: float = 20) -> str:
    try:
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def find_obs(configured: str) -> Path | None:
    """obs64.exe: the setting, else where OBS's installer says it put it."""
    if Path(configured).is_file():
        return Path(configured)
    try:
        import winreg
    except ImportError:
        return None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\OBS Studio") as k:
                exe = Path(winreg.QueryValueEx(k, "")[0]) / "bin" / "64bit" / "obs64.exe"
        except OSError:
            continue
        if exe.is_file():
            return exe
    return None


_versions: dict[str, tuple] = {}


def obs_version(exe: Path) -> tuple:
    """OBS's version from its exe, as (30, 2, 3); () when it can't be read."""
    key = f"{exe}|{exe.stat().st_mtime if exe.exists() else 0}"
    if key not in _versions:
        text = powershell(f"(Get-Item -LiteralPath '{exe}').VersionInfo.ProductVersion")
        try:
            _versions[key] = tuple(int(x) for x in text.split("-")[0].split(".")[:3])
        except ValueError:
            _versions[key] = ()
    return _versions[key]


_gpus: list[str] | None = None


def gpus() -> list[str]:
    global _gpus
    if _gpus is None:
        out = powershell("Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }")
        _gpus = [line.strip() for line in out.splitlines() if line.strip()]
        log(f"graphics: {', '.join(_gpus) or 'unknown'}")
    return _gpus


def pick_encoder(names: list[str], version: tuple) -> str:
    """The GPU's H.264 encoder (NVIDIA before AMD before Intel, for laptops with two), else x264."""
    text = " ".join(names).lower()
    if "nvidia" in text:
        return "obs_nvenc_h264_tex" if version >= (30, 2) else "jim_nvenc"  # renamed in OBS 30.2
    if "radeon" in text or "amd" in text:
        return "h264_texture_amf"
    if "intel" in text:
        return "obs_qsv11_v2"
    return "obs_x264"


def encoder_settings(encoder: str, kbps: int) -> dict:
    """CBR at the preset bitrate, a keyframe every 2 s, no B-frames (each encoder names them its own way)."""
    s = {"rate_control": "CBR", "bitrate": kbps, "keyint_sec": 2, "bf": 0, "bframes": 0}
    if "nvenc" in encoder:
        s.update(preset2="p5", tune="ll", multipass="disabled", profile="high")
    elif "amf" in encoder:
        s.update(preset="quality", profile="high")
    elif "qsv" in encoder:
        s.update(target_usage="TU4", profile="high", latency="low")
    else:
        s.update(preset="veryfast", tune="zerolatency", profile="main", x264opts="bframes=0")
    return s


# ---- OBS's files ----------------------------------------------------------------------------------

def obs_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return "obs64.exe" in out


def set_ini(path: Path, values: dict[str, dict[str, object]], encoding="utf-8") -> None:
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str  # OBS keys are case-sensitive
    if path.exists():
        ini.read(path, encoding="utf-8-sig")
    for section, kv in values.items():
        if not ini.has_section(section):
            ini.add_section(section)
        for k, v in kv.items():
            ini.set(section, k, str(v).lower() if isinstance(v, bool) else str(v))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding) as f:
        ini.write(f, space_around_delimiters=False)


def websocket_login(default_port: int) -> tuple[int, str]:
    """The websocket's port and password, as OBS's own file has them (the League Recorder, if it is
    installed too, uses the same file and so the same password)."""
    try:
        ws = json.loads(WS_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ws = {}
    return int(ws.get("server_port") or default_port), ws.get("server_password") or ""


def write_websocket_config(default_port: int) -> None:
    """The websocket server on, with a password: the one already there, or a new one."""
    try:
        current = json.loads(WS_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    current.update({"server_enabled": True, "auth_required": True, "first_load": False, "alerts_enabled": False,
                    "server_port": current.get("server_port") or default_port,
                    "server_password": current.get("server_password") or base64.urlsafe_b64encode(os.urandom(18)).decode()})
    WS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    WS_CONFIG.write_text(json.dumps(current, indent=2), encoding="utf-8")


def write_profile(preset: tuple, encoder: str) -> None:
    """Our profile's output settings, read by OBS when it starts or switches to the profile - so
    only written while OBS isn't running in it (else it would save its own over them on exit)."""
    w, h, fps, kbps = preset
    # A fresh install would open the auto-configuration wizard
    for name in ("user.ini", "global.ini"):
        set_ini(OBS_DIR / name, {"General": {"FirstRun": True}})
    set_ini(PROFILE / "basic.ini", {
        "General": {"Name": NAME},
        "Output": {"Mode": "Advanced"},
        # WHIP carries Opus audio; track 1 holds the game (and the mic when it is on)
        "AdvOut": {"Encoder": encoder, "AudioEncoder": "ffmpeg_opus", "TrackIndex": 1, "Track1Bitrate": 160,
                   "Track1Name": "Game"},
        "Video": {"BaseCX": w, "BaseCY": h, "OutputCX": w, "OutputCY": h, "FPSType": 1, "FPSInt": fps,
                  "ScaleType": "bicubic"},
        "Audio": {"SampleRate": 48000, "ChannelSetup": "Stereo"},
    })
    (PROFILE / "streamEncoder.json").write_text(json.dumps(encoder_settings(encoder, kbps)), encoding="utf-8")


def profile_matches(obs: OBS, encoder: str, kbps: int) -> bool:
    """Whether OBS, running in our profile, streams with this encoder and bitrate."""
    try:
        have = obs.call("GetProfileParameter", parameterCategory="AdvOut", parameterName="Encoder").get("parameterValue")
        bitrate = json.loads((PROFILE / "streamEncoder.json").read_text(encoding="utf-8")).get("bitrate")
    except (OSError, ValueError, OBSError):
        return False
    return have == encoder and bitrate == kbps


# ---- streaming from an OBS that is already open -----------------------------------------------------
# OBS records and streams at once, each with an encoder of its own. So an OBS someone already has open
# (their own recorder, say) can carry the chudq stream beside whatever it does, as long as its profile
# doesn't stream anywhere else: WebRTC needs Opus audio and no B-frames, which would break a Twitch
# setup. Only the profile's stream settings change; its scenes and recording settings are left alone.

STREAM_TYPES = {"rtmp_common": ("key",), "rtmp_custom": ("server", "key"), "whip_custom": ("server",)}


def streams_to_chudq(obs: OBS) -> bool:
    """The profile's stream goes to chudq (the WHIP address chudQ puts in)."""
    svc = obs.call("GetStreamServiceSettings")
    return svc.get("streamServiceType") == "whip_custom" and "/api/app/whip" in str((svc.get("streamServiceSettings") or {}).get("server", ""))


def other_destination(obs: OBS) -> str | None:
    """Where the profile streams to, if anywhere but chudq: then it isn't ours to change."""
    svc = obs.call("GetStreamServiceSettings")
    kind, settings = svc.get("streamServiceType", ""), svc.get("streamServiceSettings") or {}
    if kind == "whip_custom" and "/api/app/whip" in str(settings.get("server", "")):
        return None
    if any(settings.get(k) for k in STREAM_TYPES.get(kind, ())):
        return settings.get("service") or settings.get("server") or kind
    return None


def profile_dir(name: str) -> Path | None:
    """A profile's folder: named after the profile, but OBS may have changed the name to make it a folder."""
    for d in (OBS_DIR / "basic" / "profiles").iterdir() if (OBS_DIR / "basic" / "profiles").exists() else []:
        ini = configparser.ConfigParser(interpolation=None)
        ini.optionxform = str
        try:
            ini.read(d / "basic.ini", encoding="utf-8-sig")
        except (OSError, configparser.Error):
            continue
        if ini.get("General", "Name", fallback=None) == name:
            return d
    return None


def stream_settings(preset: tuple, encoder: str, base_width: int) -> dict[str, str]:
    w, h, _, _ = preset
    return {"Encoder": encoder, "AudioEncoder": "ffmpeg_opus",
            "Rescale": "true" if base_width > w else "false", "RescaleRes": f"{w}x{h}"}


def _param(obs: OBS, category: str, name: str) -> str:
    return str(obs.call("GetProfileParameter", parameterCategory=category, parameterName=name).get("parameterValue") or "")


def can_stream_here(obs: OBS, folder: Path, preset: tuple, encoder: str) -> bool:
    """Whether the open OBS, as it runs now, streams with these settings. Output settings are read when
    OBS starts, so they must be in the profile and OBS must have started since they were written."""
    try:
        if _param(obs, "Output", "Mode") != "Advanced":
            return False
        width = obs.call("GetVideoSettings")["baseWidth"]
        if any(_param(obs, "AdvOut", k).lower() != v for k, v in stream_settings(preset, encoder, width).items()
               if k in ("Encoder", "AudioEncoder")):
            return False
        path = folder / "streamEncoder.json"
        return json.loads(path.read_text(encoding="utf-8")) == encoder_settings(encoder, preset[3]) and _started_after(path)
    except (OSError, ValueError, KeyError, OBSError):
        return False


def _started_after(path: Path) -> bool:
    """Whether the running OBS started after the file was written (else it hasn't read it)."""
    out = powershell("(Get-Process obs64 -ErrorAction SilentlyContinue | Select -First 1).StartTime.ToUniversalTime().ToString('o')")
    try:
        started = datetime.fromisoformat(out.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return started > path.stat().st_mtime


def add_stream_settings(obs: OBS, folder: Path, preset: tuple, encoder: str) -> None:
    """The stream settings into the open profile: through the websocket, so OBS's own save on exit keeps
    them, and its stream encoder file. They apply from OBS's next start."""
    width = obs.call("GetVideoSettings")["baseWidth"]
    for k, v in stream_settings(preset, encoder, width).items():
        obs.call("SetProfileParameter", parameterCategory="AdvOut", parameterName=k, parameterValue=v)
    (folder / "streamEncoder.json").write_text(json.dumps(encoder_settings(encoder, preset[3])), encoding="utf-8")


def start_obs(exe: Path, profile: str = NAME, collection: str = NAME) -> None:
    log(f"starting OBS ({profile})")
    subprocess.Popen([str(exe), "--profile", profile, "--collection", collection, "--minimize-to-tray",
                      "--disable-missing-files-check", "--disable-shutdown-check"], cwd=exe.parent,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def stop_obs() -> None:
    """Close OBS (it saves its settings and exits) and wait for it to be gone."""
    subprocess.run(["taskkill", "/IM", "obs64.exe"], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(40):
        if not obs_running():
            return
        time.sleep(0.25)
    subprocess.run(["taskkill", "/IM", "obs64.exe", "/F"], capture_output=True, creationflags=NO_WINDOW)


def connect(default_port: int, tries: int = 1) -> OBS:
    last = None
    for i in range(tries):
        port, password = websocket_login(default_port)
        try:
            obs = OBS(port, password)
            # The websocket answers a few seconds before OBS has loaded ("207 not ready"): wait for that too
            obs.call("GetProfileList")
            return obs
        except (OSError, ConnectionError, OBSError, ValueError, KeyError) as e:
            last = e
            if i + 1 < tries:
                time.sleep(1)
    raise OBSError(f"can't reach OBS's websocket: {last}")


# ---- our scene ------------------------------------------------------------------------------------

def setup(obs: OBS, preset: tuple, mic: bool, other_audio: bool = False, capture: str = "game") -> None:
    """Switch OBS to our profile and scene collection, and make the scene match the settings: the
    League window scaled to the canvas, the game's sound (or all of the PC's, which has the game's in
    it), and the default mic if it is on. capture "window" takes the game with Windows' own capture
    instead of OBS's game capture, which records black where OBS and League run on different GPUs."""
    w, h, fps, _ = preset
    if obs.profile() != NAME:
        obs.call("SetCurrentProfile", profileName=NAME)  # loads the profile's output settings
    cols = obs.call("GetSceneCollectionList")
    if cols["currentSceneCollectionName"] != NAME:
        if NAME in cols["sceneCollections"]:
            obs.call("SetCurrentSceneCollection", sceneCollectionName=NAME)
        else:
            obs.call("CreateSceneCollection", sceneCollectionName=NAME)
        time.sleep(2)
    v = obs.call("GetVideoSettings")
    if (v.get("outputWidth"), v.get("outputHeight"), v.get("fpsNumerator"), v.get("fpsDenominator")) != (w, h, fps, 1):
        obs.call("SetVideoSettings", baseWidth=w, baseHeight=h, outputWidth=w, outputHeight=h,
                 fpsNumerator=fps, fpsDenominator=1)
    scene = "League"
    if scene not in [s["sceneName"] for s in obs.call("GetSceneList")["scenes"]]:
        obs.call("CreateScene", sceneName=scene)
    obs.call("SetCurrentProgramScene", sceneName=scene)

    want = {"Game": ("window_capture", {"window": LEAGUE_WINDOW, "priority": 2, "method": 2, "cursor": True, "client_area": True})
            if capture == "window" else
            ("game_capture", {"capture_mode": "window", "window": LEAGUE_WINDOW, "priority": 2,
                              "capture_cursor": True, "capture_audio": False})}
    # One sound source, never both: the desktop mix already has League in it, so capturing League on
    # its own as well would put the game's sound in the stream twice (an echo)
    if other_audio:
        want["Desktop audio"] = ("wasapi_output_capture", {"device_id": "default"})
    else:
        want["Game audio"] = ("wasapi_process_output_capture", {"window": LEAGUE_WINDOW, "priority": 2})
    if mic:
        want["Mic"] = ("wasapi_input_capture", {"device_id": "default"})
    have = {i["inputName"]: i["inputKind"] for i in obs.call("GetInputList")["inputs"]}
    for name, kind in have.items():
        if name not in want or want[name][0] != kind:
            obs.call("RemoveInput", inputName=name)  # the default Desktop Audio and Mic/Aux among them
    for name, (kind, settings) in want.items():
        if have.get(name) == kind:
            obs.call("SetInputSettings", inputName=name, inputSettings=settings, overlay=True)
        else:
            obs.call("CreateInput", sceneName=scene, inputName=name, inputKind=kind, inputSettings=settings)
        obs.call("SetInputMute", inputName=name, inputMuted=False)
    item = obs.call("GetSceneItemId", sceneName=scene, sourceName="Game")["sceneItemId"]
    obs.call("SetSceneItemTransform", sceneName=scene, sceneItemId=item, sceneItemTransform={
        "positionX": 0, "positionY": 0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_INNER",
        "boundsAlignment": 0, "boundsWidth": w, "boundsHeight": h})


def is_black(obs: OBS, source: str = "Game") -> bool:
    """Whether a source shows nothing but black right now (a tiny screenshot of it, brightest pixel)."""
    from PIL import Image
    shot = obs.call("GetSourceScreenshot", sourceName=source, imageFormat="png", imageWidth=64, imageHeight=36)
    data = base64.b64decode(shot["imageData"].partition(",")[2])
    return Image.open(io.BytesIO(data)).convert("L").getextrema()[1] < 10
