"""The window's page and the little API it uses, on 127.0.0.1 only. A second copy of the app finds
the first through /api/show and brings its window up instead of starting again.

    GET  /                  the page (page/index.html)
    GET  /icon.png          the app's icon
    GET  /api/status        login, League, live data, stream, settings
    POST /api/login {name, password};  /api/logout
    POST /api/signup {name, password}: for the League account in the client (account.py proves it)
    POST /api/join          the sign-up's League account isn't the squad's: ask the admin to let it in
    POST /api/add-account   the League account in the client, onto the logged-in chudq account
    POST /api/settings {send_live, stream, preset, mic, other_audio, obs_path, start_at_logon}
    POST /api/open {what}   open chudq.lol or OBS's download page in the browser
    POST /api/update        download the newer version and install it (update.py)
    POST /api/update/check  ask chudq.lol for a newer version now
    POST /api/show          bring the window up

Every POST must be JSON from the page itself: a web page elsewhere can't send that to 127.0.0.1
without asking first (CORS), and the Origin check turns it away even if it tries.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, account, api, config, icon
from .paths import PAGE, log


class App:
    """What the API reaches into; app.py fills it in."""
    watcher = None
    streamer = None
    updater = None
    on_show = None
    on_settings = None  # (before, after)
    proof = account.Proof()


def status() -> dict:
    cfg = config.load()
    w = App.watcher.status if App.watcher else {}
    s = App.streamer.status if App.streamer else {}
    return {"version": __version__, "server": cfg["server"], "loggedIn": bool(cfg["token"]),
            "name": cfg["name"], "player": cfg["player"], "request": cfg["request"], "accounts": cfg["accounts"],
            "leagueAccount": w.get("account"), "notConnected": w.get("notConnected"),
            "proof": App.proof.status, "proving": App.proof.running(),
            "league": {k: w.get(k) for k in ("client", "phase", "inGame", "champSelect", "loaded", "champion", "matchId", "posts", "error")},
            "lastPost": w.get("lastPost"), "now": time.time(),
            "stream": s,
            "update": App.updater.status if App.updater else None,
            "settings": {k: cfg[k] for k in ("send_live", "stream", "preset", "mic", "other_audio", "obs_path", "start_at_logon")},
            "presets": list(config.PRESETS)}


class Handler(BaseHTTPRequestHandler):
    server_version = "chudQ"

    def log_message(self, fmt, *args):
        pass

    def send(self, body: bytes, kind: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code=200) -> None:
        self.send(json.dumps(data).encode(), "application/json", code)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self.send((PAGE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/icon.png":
            out = io.BytesIO()
            icon.image().save(out, format="PNG")
            return self.send(out.getvalue(), "image/png")
        if path == "/api/status":
            return self.send_json(status())
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json" or \
                (origin and origin != f"http://127.0.0.1:{self.server.server_port}"):
            return self.send_json({"error": "not from the app's page"}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n < 65536 else {}
        except ValueError:
            body = {}
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/show":
                App.on_show and App.on_show()
                return self.send_json({"shown": True, "app": "chudQ"})
            if path == "/api/login":
                name, password = str(body.get("name", "")).strip(), str(body.get("password", ""))
                if not name or not password:
                    raise ValueError("Your chudq name and password")
                api.login(name, password)
                return self.send_json(status())
            if path == "/api/signup":
                name, password = str(body.get("name", "")).strip(), str(body.get("password", ""))
                if not name or not password:
                    raise ValueError("Pick a chudq name and a password")
                App.proof.sign_up(name, password)
                return self.send_json(status())
            if path == "/api/join":
                App.proof.join()
                return self.send_json(status())
            if path == "/api/add-account":
                App.proof.add()
                return self.send_json(status())
            if path == "/api/logout":
                api.logout()
                return self.send_json(status())
            if path == "/api/settings":
                before = config.load()
                after = config.check(body)
                App.on_settings and App.on_settings(before, after)
                return self.send_json(status())
            if path == "/api/update/check":
                App.updater.check()
                return self.send_json(status())
            if path == "/api/update":
                App.updater.install()
                return self.send_json(status())
            if path == "/api/open":
                url = {"site": config.load()["server"], "obs": "https://obsproject.com/download"}.get(body.get("what"))
                if url:
                    os.startfile(url)
                return self.send_json({"ok": bool(url)})
        except (ValueError, api.ServerError) as e:
            return self.send_json({"error": str(e)}, 400)
        self.send_json({"error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second program take a port that is in use; the port has to be
    # ours alone, so a second copy of the app finds the first instead of serving beside it
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(port: int) -> ThreadingHTTPServer:
    httpd = Server(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="server").start()
    log(f"page on http://127.0.0.1:{port}/")
    return httpd
