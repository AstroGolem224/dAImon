"""Kleine Gut-Implementierung fuer den Laufzeit-Verifizierer T-0.11."""
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 65536
EVENTS = {
    "SessionStart", "UserPromptSubmit", "PreToolUse", "Notification", "Stop",
    "StopFailure", "SubagentStop", "PreCompact", "SessionEnd",
}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, runtime: Path, audit: Path, mutation: str):
        super().__init__(address, handler)
        self.runtime = runtime
        self.audit = audit
        self.mutation = mutation
        self.token = (runtime / "hook-token").read_text(encoding="ascii").strip()
        self.gate = threading.BoundedSemaphore(8)

    def forward(self, payload: dict) -> None:
        wire = json.dumps({"v": 1, "type": "hook", "payload": payload}).encode() + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(str(self.runtime / "hookbridge.sock"))
            client.sendall(wire)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *_args) -> None:
        pass

    def answer(self, status: int) -> None:
        if self.server.mutation == "bad-status" and status >= 400:
            status = 200
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        prefix = self.server.mutation == "prefix-route"
        route_ok = self.path.startswith("/hoo") if prefix else self.path == "/hook"
        if not route_ok:
            self.answer(404)
            return
        supplied = self.headers.get("Authorization", "")
        if self.server.mutation != "no-token" and supplied != f"Bearer {self.server.token}":
            with self.server.audit.open("a", encoding="utf-8") as out:
                out.write("unauthorized hook request\n")
            self.answer(401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.answer(400)
            return
        if self.server.mutation != "unlimited-body" and length > MAX_BODY:
            self.answer(413)
            return
        if not self.server.gate.acquire(blocking=False):
            self.answer(503)
            return
        try:
            self.connection.settimeout(0.5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                self.answer(400)
                return
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.answer(400)
                return
            if payload.get("hook_event_name") not in EVENTS:
                self.answer(422)
                return
            self.server.forward(payload)
            self.answer(200)
        except (OSError, TimeoutError):
            self.answer(502)
        finally:
            self.server.gate.release()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--audit-log", type=Path, required=True)
    args = parser.parse_args()
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    token = args.runtime_dir / "hook-token"
    if not token.exists():
        token.write_text(os.urandom(24).hex() + "\n", encoding="ascii")
    os.chmod(token, 0o600)
    marker = Path(__file__).parents[2] / "mutation.txt"
    mutation = os.environ.get("DAIMON_T011_MUTATION", "")
    if not mutation and marker.is_file():
        mutation = marker.read_text(encoding="ascii").strip()
    server = Server(("127.0.0.1", args.port), Handler, args.runtime_dir,
                    args.audit_log, mutation)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
