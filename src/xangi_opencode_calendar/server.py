from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .opencode_cli import OpenCodeCli


def load_ui() -> str:
    return (Path(__file__).with_name("ui.html")).read_text(encoding="utf-8")


class CalendarServer(ThreadingHTTPServer):
    def __init__(self, address, auth_token, *, workspace, source: OpenCodeCli):
        super().__init__(address, CalendarHandler)
        self.auth_token = auth_token
        self.workspace = Path(workspace).resolve()
        self.source = source


class CalendarHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
            )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, value):
        self._send(status, json.dumps(value, ensure_ascii=False).encode())

    def _authorized(self):
        expected = f"Bearer {self.server.auth_token}"
        supplied = self.headers.get("Authorization", "")
        if hmac.compare_digest(supplied, expected):
            return True
        self._json(401, {"error": "unauthorized"})
        return False

    def _sessions(self):
        return self.server.source.sessions()

    def do_GET(self):
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path in {"/", "/ui"}:
            self._send(200, load_ui().encode(), "text/html; charset=utf-8")
            return
        if path == "/health":
            self._json(200, {"status": "ok", "service": "xangi-opencode-calendar", "version": __version__, "workspace": str(self.server.workspace), "capabilities": ["workspace.opencode-calendar"]})
            return
        if path == "/api/sessions":
            try:
                self._json(200, self._sessions())
            except RuntimeError as error:
                self._json(502, {"error": str(error)})
            return
        if path.startswith("/api/sessions/"):
            session_id = path.rsplit("/", 1)[-1]
            try:
                self._json(200, self.server.source.details(session_id))
            except RuntimeError as error:
                self._json(502, {"error": str(error)})
            return
        if path == "/agent":
            self._json(200, {"capability": "workspace.opencode-calendar", "ok": True})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        if urlparse(self.path).path == "/agent":
            self._json(200, {"ok": True, "text": "OpenCode Calendarは読み取り専用です。/uiでセッションを確認してください。"})
            return
        self._json(404, {"error": "not found"})
