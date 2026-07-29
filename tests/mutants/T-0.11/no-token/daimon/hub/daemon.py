"""Beobachtbarer Unix-Socket-Hub fuer das Gut-Muster T-0.11."""
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from pathlib import Path


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rev = 0
        self.sessions: dict[str, dict] = {}

    def apply(self, p: dict) -> None:
        sid = str(p.get("session_id", "?"))
        name = p["hook_event_name"]
        with self.lock:
            old = self.sessions.get(sid, {"agents": 0, "mood": "observing"})
            agents = old["agents"]
            mood = old["mood"]
            if name == "PreToolUse":
                mood = "working"
                if p.get("tool_name") == "Agent":
                    agents += 1
            elif name == "SubagentStop":
                agents = max(0, agents - 1)
                mood = "working"
            elif name == "Stop":
                mood = "done" if agents == 0 else "working"
            elif name == "StopFailure":
                mood = "failed"
            elif name == "Notification":
                mood = "needs_input"
            elif name == "UserPromptSubmit":
                mood = "thinking"
            elif name == "SessionStart":
                mood = "observing"
            elif name == "SessionEnd":
                self.sessions.pop(sid, None)
                self.rev += 1
                return
            self.sessions[sid] = {
                "agents": agents, "mood": mood, "pid": p.get("pid"),
                "updated": time.monotonic(),
            }
            self.rev += 1

    def snapshot(self) -> dict:
        with self.lock:
            dead = []
            now = time.monotonic()
            for sid, item in self.sessions.items():
                pid = item.get("pid")
                # Kurz fuer schnelle Mutationstests; der echte Hub verwendet
                # seine produktive Lease-Schonfrist.
                if pid and now - item["updated"] > 0.4:
                    try:
                        os.kill(int(pid), 0)
                    except (OSError, ValueError):
                        dead.append(sid)
            for sid in dead:
                self.sessions.pop(sid, None)
                self.rev += 1
            mood = "sleeping"
            if self.sessions:
                mood = list(self.sessions.values())[-1]["mood"]
            return {"v": 2, "rev": self.rev, "mood": mood,
                    "sessions": len(self.sessions)}


def listener(path: Path) -> socket.socket:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    os.chmod(path, 0o600)
    sock.listen(16)
    sock.settimeout(0.5)
    return sock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args()
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    state = State()
    hook = listener(args.runtime_dir / "hookbridge.sock")
    query = listener(args.runtime_dir / "state.sock")

    def hooks() -> None:
        while True:
            try:
                conn, _ = hook.accept()
            except socket.timeout:
                continue
            with conn, conn.makefile("rb") as inp:
                try:
                    event = json.loads(inp.readline())
                    state.apply(event["payload"])
                except (KeyError, json.JSONDecodeError):
                    pass

    threading.Thread(target=hooks, daemon=True).start()
    try:
        while True:
            try:
                conn, _ = query.accept()
            except socket.timeout:
                continue
            with conn:
                conn.sendall(json.dumps(state.snapshot()).encode() + b"\n")
    finally:
        hook.close()
        query.close()


if __name__ == "__main__":
    raise SystemExit(main())
