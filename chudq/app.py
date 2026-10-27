"""chudQ, the desktop app: a small window (WebView2, through pywebview) on the app's page, an icon in
the tray, and in the background the watcher (live data) and the streamer (OBS).

    chudQ.exe            open the window (or bring up the one already running)
    chudQ.exe --hidden   start in the tray only - how it starts at logon
    chudQ.exe --updated  the installer, after an update from inside the app: say so

Closing the window keeps the app in the tray, still sending; Quit in the tray's menu ends it. The
two things it does, "Send live game data" and "Stream my games", are switches in the tray's menu and
at the top of the window; either takes effect at once, mid-game too.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__, api, config, icon as app_icon, server
from .paths import APP, FROZEN, log
from .stream import Streamer
from .update import Updater
from .watcher import Watcher

NAME = "chudQ"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ChudQ"


def already_running(port: int) -> bool:
    """A copy of the app answering on its port: ask it to show its window."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/show", data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            answer = json.loads(r.read())
            return answer.get("shown", False) and answer.get("app") == "chudQ"
    except (OSError, ValueError):
        return False


def command() -> str:
    """How Windows should start the app at logon."""
    if FROZEN:
        return f'"{sys.executable}" --hidden'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else sys.executable}" -m chudq --hidden'


def set_autostart(on: bool) -> None:
    try:
        import winreg
    except ImportError:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, RUN_VALUE)
            except FileNotFoundError:
                pass


def check_login() -> None:
    """At start: is the saved login still good? (A 401 logs out; a server that's down changes nothing.)"""
    try:
        me = api.authed("GET", "/api/app/me")
        if me:
            log(f"logged in as {me.get('name')} (player {me.get('player')})")
    except api.ServerError as e:
        log(f"couldn't check the login: {e}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    hidden = "--hidden" in argv
    cfg = config.load()
    port = cfg["port"]
    if already_running(port):
        return 0
    try:
        httpd = server.serve(port)
    except OSError:
        log(f"port {port} is taken by something else; set another in {config.CONFIG}")
        return 1
    log(f"chudQ {__version__}, server {cfg['server']}")
    set_autostart(cfg.get("start_at_logon", True))
    threading.Thread(target=check_login, daemon=True, name="login").start()

    import pystray
    import webview

    window = None
    icon = None
    watcher = Watcher()
    streamer = Streamer(watcher)
    server.App.watcher, server.App.streamer = watcher, streamer

    def show(*_):
        if window:
            window.show()
            window.restore()
        if server.App.updater:
            server.App.updater.kick()          # a new version shows as soon as the window is opened

    def on_settings(before: dict, after: dict) -> None:
        if before.get("start_at_logon") != after.get("start_at_logon"):
            set_autostart(after.get("start_at_logon", True))
        watcher.kick()
        streamer.kick()
        if icon:
            icon.update_menu()

    def toggle(key: str):
        """A tray switch: flip the setting, then act on it like the window's switch does."""
        def flip(*_):
            before = config.load()
            on_settings(before, config.save(**{key: not before[key]}))
            log(f"{key} turned {'on' if not before[key] else 'off'} from the tray")
        return flip

    server.App.on_show, server.App.on_settings = show, on_settings

    quitting = threading.Event()

    def quit_app(*_):
        log("quitting")
        quitting.set()
        # Whatever happens below, the process ends: Quit means gone, tray icon and all
        threading.Timer(8, lambda: os._exit(0)).start()
        for step in (watcher.stop, streamer.stop, httpd.shutdown, icon.stop):
            try:
                step()
            except Exception as e:
                log(f"quitting: {type(e).__name__}: {e}")
        if window:
            window.destroy()

    updater = Updater(watcher, quit_app, just_updated="--updated" in argv)
    server.App.updater = updater

    def update(*_):
        try:
            updater.install()
        except ValueError as e:
            log(f"update from the tray: {e}")

    icon = pystray.Icon("ChudQ", app_icon.image(), NAME, menu=pystray.Menu(
        pystray.MenuItem("Open chudQ", show, default=True),
        pystray.MenuItem(lambda item: f"Update to {updater.status['latest']}", update,
                         visible=lambda item: updater.status["available"]),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Send live game data", toggle("send_live"), checked=lambda item: config.load()["send_live"]),
        pystray.MenuItem("Stream my games", toggle("stream"), checked=lambda item: config.load()["stream"]),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    ))

    def keep_icon_current():
        """The tray's picture and tooltip follow what the app is doing."""
        last = None
        while True:
            w, s, cfg = watcher.status, streamer.status, config.load()
            sending = w.get("loaded") and cfg["send_live"] and w.get("lastPost") and time.time() - w["lastPost"] < 30
            if not cfg["token"]:
                dot, tip = None, f"{NAME}: logged out"
            elif not cfg["send_live"] and not cfg["stream"]:
                dot, tip = None, f"{NAME}: off"
            elif s.get("state") == "live":
                dot, tip = "live", f"{NAME}: streaming{' + sending live data' if sending else ''}"
            elif sending:
                dot, tip = "sending", f"{NAME}: sending live data"
            elif s.get("state") in ("skipped", "error", "no-obs", "old-obs") and w.get("inGame"):
                dot, tip = None, f"{NAME}: not streaming - {s.get('message')}"[:127]
            elif w.get("client"):
                dot, tip = None, f"{NAME}: waiting for a game"
            else:
                dot, tip = None, f"{NAME}: League isn't open"
            if (dot, tip) != last:
                icon.icon = app_icon.image(dot)
                icon.title = tip
                icon.update_menu()  # the check marks follow switches flipped in the window
                last = (dot, tip)
            time.sleep(2)

    icon.run_detached()
    threading.Thread(target=keep_icon_current, daemon=True, name="tray").start()
    watcher.start()
    streamer.start()
    updater.start()

    window = webview.create_window(NAME, f"http://127.0.0.1:{port}/", width=460, height=720, min_size=(400, 560),
                                   hidden=hidden, background_color="#0b0f1a")

    def on_closing():
        if quitting.is_set():
            return True  # Quit: let the window go, and the app with it
        window.hide()    # the window's X: into the tray; Quit ends the app
        return False

    window.events.closing += on_closing
    webview.start(private_mode=False, storage_path=str(APP / "webview"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
