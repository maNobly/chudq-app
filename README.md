# chudQ app

The Windows tray app for [chudq.lol](https://chudq.lol), a friend group's League of Legends stats
and betting site. While League runs it sends the game's live data to chudq.lol, and if you turn on
"Stream my games" it streams the game there through OBS (WHIP).

This is the app's full source, so anyone can see what it does before running it:

- it reads League's own local APIs on your PC (the client's, for champ select and your account,
  and the game's Live Client Data API on port 2999);
- over the internet it talks to chudq.lol only, with the login you give it (its window also loads
  its fonts from Google Fonts);
- it starts and controls OBS (through obs-websocket) only if you turn streaming on;
- it updates itself by downloading the newest installer from chudq.lol and running it.

It doesn't touch the game process, read its memory or inject anything.

The installer at https://chudq.lol/download is built from this code with `installer/build.py`;
the download page lists each installer's SHA-256.

Two switches, in the tray's menu (with check marks) and at the top of the window, persisted in
config.json: **Send live game data** (on by default) and **Stream my games** (off by default).
Either takes effect at once: streaming off mid-game stops the OBS stream, on mid-game starts it;
live data off mid-game stops the posts and sends `/api/app/live/end`. The tray's tooltip says what
it is doing ("chudQ: streaming", "chudQ: sending live data", "chudQ: off", ...).

Accounts: log in with a chudq account, or sign up in the window with the League account the client
is logged in to. Only games on your own League accounts are sent or streamed; for any other, the
window offers "Add it to my account". Signing up and adding both prove the League account the way
chudq.lol does, by a starter profile icon Riot must show, but the app sets it through the League
client and puts the old icon back (account.py), so there's nothing to do by hand.

It needs no Riot API key: everything it reads comes from League itself, on your PC.

```
chudq/
  app.py        window (pywebview) + tray (pystray), autostart (HKCU Run, --hidden), single instance
  server.py     the window's page and its API on 127.0.0.1:47180
  page/         the page
  watcher.py    every 3 s in a game: Live Client API -> POST /api/app/live; /live/end after
  gamedata.py   allgamedata -> the contract's JSON (pure; tested)
  league.py     the League client's API (lockfile) and the Live Client API (:2999)
  stream.py     OBS around each game: start in the tray, StartStream when the game loads, stop after
  obs.py        obs-websocket client, the ChudQ profile/scene, GPU encoder, OBS version
  api.py        chudq.lol's /api/app calls with the token; 401 -> logged out
  account.py    signing up / adding the client's League account, proven by a profile icon
  config.py     %LOCALAPPDATA%\ChudQ\config.json
  paths.py      where things are; app.log
installer/      build.py (PyInstaller onedir + Inno Setup) and ChudQ.iss
tests/          test_gamedata.py (realistic allgamedata fixture), test_watcher.py (posting rules)
```

## Run from a checkout (Windows)

    python -m chudq            # window;  --hidden for the tray only

Needs pywebview, pystray and Pillow. To test against a local squad server, set
`"server": "http://localhost:8780"` in `%LOCALAPPDATA%\ChudQ\config.json` and restart the app.

## Tests

    python -m unittest discover tests

## Build the installer (Windows)

With a Python venv that has pywebview, pythonnet, pystray, Pillow and PyInstaller, and Inno Setup 6:

    <venv>\Scripts\python.exe installer\build.py

It builds in `%LOCALAPPDATA%\ChudQ-build` and leaves `ChudQ-Setup-<version>.exe` there (version
from `chudq/__init__.py`). The installer is per-user (`%LOCALAPPDATA%\Programs\chudQ`, no admin),
adds a Start menu entry and starts the app with Windows in the tray. It is unsigned, so SmartScreen
asks once ("More info" -> "Run anyway"). 
### Why the app starts with its own bootloader

PyInstaller puts a small prebuilt program (the bootloader) at the front of every app it builds.
That same file is in countless other programs, malware too, so antivirus guesses such as Windows
Defender's `Trojan:Win32/Ulthar.A!ml` can flag apps built with it. `installer/bootloader.sh`
compiles the bootloader from PyInstaller's own source (with Zig, on Linux or WSL), and
`build.py` uses it when its version matches the venv's PyInstaller.

## OBS

The friend's own OBS 30+ (for WHIP). The app writes, before OBS starts:
- `%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json`: server on, port 4455, the
  password already there (the League Recorder shares this file) or a new one;
- profile `ChudQ`: Advanced output, stream encoder from the GPU (NVENC `obs_nvenc_h264_tex`, or
  `jim_nvenc` before OBS 30.2; AMF `h264_texture_amf`; QSV `obs_qsv11_v2`; else `obs_x264`), Opus
  audio, `streamEncoder.json` with CBR at the preset bitrate, keyframe every 2 s, no B-frames;

then starts OBS minimised to the tray with that profile and scene collection, sets the scene up
(game capture of the League window, its sound or with "All PC sound" the whole desktop mix instead, the default mic only if on) and the stream service
(`whip_custom`, `<server>/api/app/whip`, bearer token = the app's token). A quality change restarts
OBS between games. OBS the app started is closed when League closes.

An OBS already open in another profile (a recorder's, say) is streamed from as it is, beside whatever
it does, if that profile streams nowhere else: the app adds the stream settings to that profile once
and restarts OBS between games, same profile and scenes. A profile with a Twitch or other destination,
simple output mode, or an OBS already streaming is left alone, and the window says why.

## License

MIT, see [LICENSE](LICENSE).
