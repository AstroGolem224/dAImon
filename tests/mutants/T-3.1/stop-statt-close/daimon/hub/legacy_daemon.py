#!/usr/bin/env python3
"""
pet_daemon — Phase 3: Agent-Status.

Nimmt Claude-Code-Hook-Events auf 127.0.0.1:8787/hook entgegen,
haelt daraus einen Mood-Zustand und liefert ihn dem Godot-Client
auf /state aus.

Nur stdlib. Start:  python3 pet_daemon.py
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8787
SESSION_TTL = 60 * 60  # tote Sessions nach 1h vergessen

# Mood-Prioritaet: hoeherer Wert gewinnt, wenn mehrere Sessions laufen.
PRIORITY = {
    "sleeping": 0,
    "idle": 1,
    "observing": 2,
    "thinking": 3,
    "working": 4,
    "done": 5,
    "failed": 6,
    "needs_input": 7,
}

log = logging.getLogger("pet")


class State:
    """Zustand ueber alle Claude-Code-Sessions hinweg."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._bubble: dict | None = None
        self._rev = 0  # steigt bei jeder Aenderung -> Client kann diffen

    def apply(self, event: dict) -> None:
        name = event.get("hook_event_name", "")
        sid = event.get("session_id", "default")
        cwd = event.get("cwd", "")
        mood, bubble = self._map(name, event)
        if mood is None:
            return

        with self._lock:
            if name == "SessionEnd":
                self._sessions.pop(sid, None)
            else:
                self._sessions[sid] = {"mood": mood, "cwd": cwd, "ts": time.time()}
            if bubble is not None:
                bubble["id"] = f"b_{self._rev}"
                bubble["session_id"] = sid
                self._bubble = bubble
            self._rev += 1
        log.info("%-18s %-12s %s", name, mood, cwd.split("/")[-1])

    def _map(self, name: str, e: dict) -> tuple[str | None, dict | None]:
        """Hook-Event -> (mood, bubble|None). Hier lebt das ganze Verhalten."""
        if name == "SessionStart":
            return "observing", None

        if name == "UserPromptSubmit":
            return "thinking", None

        if name in ("PreToolUse", "PostToolUse", "PostToolBatch"):
            return "working", None

        if name == "Notification":
            kind = e.get("notification_type") or e.get("matcher") or ""
            if kind == "permission_prompt":
                return "needs_input", {
                    "title": "braucht dein OK",
                    "body": e.get("message", "Claude wartet auf eine Freigabe."),
                    "urgent": True,
                }
            if kind == "idle_prompt":
                return "idle", None
            return "observing", None

        if name == "Stop":
            msg = (e.get("last_assistant_message") or "").strip()
            return "done", {
                "title": "fertig",
                "body": _shorten(msg, 240) if msg else "Task durch.",
                "urgent": False,
            }

        if name in ("StopFailure", "PostToolUseFailure"):
            return "failed", {
                "title": "schiefgegangen",
                "body": _shorten(str(e.get("error", "unbekannter Fehler")), 240),
                "urgent": True,
            }

        if name == "SessionEnd":
            return "sleeping", None

        return None, None

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            for sid in [s for s, v in self._sessions.items() if now - v["ts"] > SESSION_TTL]:
                del self._sessions[sid]

            if not self._sessions:
                mood = "sleeping"
            else:
                mood = max(
                    (v["mood"] for v in self._sessions.values()),
                    key=lambda m: PRIORITY.get(m, 0),
                )
            return {
                "v": 1,
                "rev": self._rev,
                "mood": mood,
                "sessions": len(self._sessions),
                "bubble": self._bubble,
            }

    def clear_bubble(self) -> None:
        with self._lock:
            self._bubble = None
            self._rev += 1


def _shorten(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "\u2026"


STATE = State()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"

        if self.path.startswith("/hook"):
            try:
                STATE.apply(json.loads(raw or b"{}"))
            except Exception:
                log.exception("kaputtes hook-payload")
            # Immer 200 und immer schnell: der Hook darf Claude Code nie blockieren.
            self._send(200, {"ok": True})
            return

        if self.path.startswith("/bubble/dismiss"):
            STATE.clear_bubble()
            self._send(200, {"ok": True})
            return

        self._send(404, {"error": "unknown"})

    def do_GET(self) -> None:
        if self.path.startswith("/state"):
            self._send(200, STATE.snapshot())
            return
        self._send(404, {"error": "unknown"})

    def log_message(self, *args) -> None:
        pass  # eigenes Logging, nicht das von BaseHTTPRequestHandler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("pet-daemon laeuft auf http://%s:%d", HOST, PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("tschuess")


if __name__ == "__main__":
    main()
