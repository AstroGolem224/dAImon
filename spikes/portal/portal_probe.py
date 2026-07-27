#!/usr/bin/env python3
"""Minimal ScreenCast portal probe.

The probe negotiates a stream but never opens the PipeWire remote and therefore
never reads or stores screen pixels.  A separate dbus-monitor process records
the portal traffic.  Monitor processes are rotated before their 30 s watchdog.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / "token.json"
RUNS_DIR = HERE / "runs"
CLIENT_LIMIT_SECONDS = 120
SHORT_REQUEST_SECONDS = 30
MONITOR_SEGMENT_SECONDS = 25


def plain(value: Any) -> Any:
    if isinstance(value, dbus.Dictionary):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (dbus.Array, list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (dbus.String, dbus.ObjectPath)):
        return str(value)
    if isinstance(value, (dbus.Boolean, bool)):
        return bool(value)
    if isinstance(
        value,
        (
            dbus.Byte,
            dbus.Int16,
            dbus.Int32,
            dbus.Int64,
            dbus.UInt16,
            dbus.UInt32,
            dbus.UInt64,
            int,
        ),
    ):
        return int(value)
    return str(value)


class Monitor:
    """Continuously record DBus using <=25 s dbus-monitor child processes."""

    MATCHES = [
        "interface='org.freedesktop.portal.ScreenCast'",
        "interface='org.freedesktop.portal.Request'",
        "interface='org.freedesktop.portal.Session'",
        "interface='org.freedesktop.impl.portal.ScreenCast'",
        "path_namespace='/org/freedesktop/portal/desktop'",
    ]

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="dbus-monitor", daemon=True)
        self.error: str | None = None

    @staticmethod
    def _stop_process(proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    def _run(self) -> None:
        segment = 0
        try:
            with self.path.open("wb", buffering=0) as output:
                while not self.stop_event.is_set():
                    segment += 1
                    output.write(
                        (
                            f"# dbus-monitor segment={segment} "
                            f"monotonic={time.monotonic():.6f}\n"
                        ).encode()
                    )
                    proc = subprocess.Popen(
                        ["dbus-monitor", "--session", *self.MATCHES],
                        stdout=output,
                        stderr=subprocess.STDOUT,
                    )
                    self.ready_event.set()
                    self.stop_event.wait(MONITOR_SEGMENT_SECONDS)
                    self._stop_process(proc)
        except Exception as exc:  # pragma: no cover - diagnostic path
            self.error = repr(exc)
            self.ready_event.set()

    def start(self) -> None:
        self.thread.start()
        if not self.ready_event.wait(timeout=3):
            raise RuntimeError("dbus-monitor wurde nicht innerhalb von 3 s bereit")
        if self.error:
            raise RuntimeError(f"dbus-monitor fehlgeschlagen: {self.error}")
        time.sleep(0.25)

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("dbus-monitor-Thread ließ sich nicht beenden")
        if self.error:
            raise RuntimeError(f"dbus-monitor fehlgeschlagen: {self.error}")


class PortalClient:
    def __init__(self) -> None:
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        obj = self.bus.get_object(PORTAL_NAME, PORTAL_PATH)
        self.portal = dbus.Interface(obj, SCREENCAST_IFACE)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.deadline = time.monotonic() + CLIENT_LIMIT_SECONDS
        self.session_handle: str | None = None
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def token(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    def request(
        self,
        method_name: str,
        args: list[Any],
        options: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        handle_token = self.token(method_name.lower())
        request_path = (
            f"/org/freedesktop/portal/desktop/request/{self.sender}/{handle_token}"
        )
        options = dict(options)
        options["handle_token"] = dbus.String(handle_token)
        loop = GLib.MainLoop()
        answer: dict[str, Any] = {}
        started = time.monotonic()

        def on_response(response: dbus.UInt32, results: dbus.Dictionary) -> None:
            answer["response"] = int(response)
            answer["results"] = results
            loop.quit()

        self.bus.add_signal_receiver(
            on_response,
            signal_name="Response",
            dbus_interface=REQUEST_IFACE,
            path=request_path,
        )
        try:
            returned_path = str(getattr(self.portal, method_name)(*args, options))
            if returned_path != request_path:
                raise RuntimeError(
                    f"unerwarteter Request-Pfad: {returned_path}, erwartet {request_path}"
                )
            remaining = max(0.0, self.deadline - time.monotonic())
            allowed = min(float(timeout_seconds), remaining)
            if allowed <= 0:
                raise TimeoutError("globaler 120-s-Client-Watchdog abgelaufen")

            def on_timeout() -> bool:
                answer["timeout"] = True
                loop.quit()
                return GLib.SOURCE_REMOVE

            timer_id = GLib.timeout_add(int(allowed * 1000), on_timeout)
            loop.run()
            GLib.source_remove(timer_id)
        finally:
            self.bus.remove_signal_receiver(
                on_response,
                signal_name="Response",
                dbus_interface=REQUEST_IFACE,
                path=request_path,
            )

        elapsed = time.monotonic() - started
        event = {
            "method": method_name,
            "request_path": request_path,
            "elapsed_seconds": round(elapsed, 6),
            "response": answer.get("response"),
        }
        self.events.append(event)
        if answer.get("timeout"):
            raise TimeoutError(
                f"{method_name}: Watchdog nach {allowed:.1f} s abgelaufen"
            )
        if answer.get("response") != 0:
            raise RuntimeError(
                f"{method_name}: Portal-Antwort {answer.get('response')} "
                f"(0 wäre Erfolg)"
            )
        return answer["results"]

    def run(self, restore_token: str | None) -> str:
        create = self.request(
            "CreateSession",
            [],
            {
                "session_handle_token": dbus.String(self.token("session")),
            },
            SHORT_REQUEST_SECONDS,
        )
        self.session_handle = str(create["session_handle"])

        select_options: dict[str, Any] = {
            "types": dbus.UInt32(1),  # MONITOR
            "multiple": dbus.Boolean(False),
            "persist_mode": dbus.UInt32(2),  # EXPLICITLY_REVOKED
        }
        if restore_token is not None:
            select_options["restore_token"] = dbus.String(restore_token)
        self.request(
            "SelectSources",
            [dbus.ObjectPath(self.session_handle)],
            select_options,
            SHORT_REQUEST_SECONDS,
        )

        print(
            "\n"
            "====================================================================\n"
            "BITTE DEN BILDSCHIRMFREIGABE-DIALOG BESTÄTIGEN —\n"
            "Bildschirm auswählen, dann „Teilen“.\n"
            "Falls kein Dialog erscheint, bitte nichts tun; der Lauf geht weiter.\n"
            "Maximale Wartezeit: 120 s ab Start dieses Clients.\n"
            "====================================================================\n",
            flush=True,
        )
        start = self.request(
            "Start",
            [dbus.ObjectPath(self.session_handle), dbus.String("")],
            {},
            CLIENT_LIMIT_SECONDS,
        )
        new_token = str(start["restore_token"])
        if not new_token:
            raise RuntimeError("Start lieferte keinen restore_token")
        return new_token

    def close(self) -> None:
        if not self.session_handle:
            return
        try:
            obj = self.bus.get_object(PORTAL_NAME, self.session_handle)
            dbus.Interface(obj, SESSION_IFACE).Close()
        except dbus.DBusException as exc:
            print(f"Hinweis: Session.Close meldete: {exc}", file=sys.stderr)


def load_token_data() -> dict[str, Any]:
    if not TOKEN_FILE.exists():
        return {"current": None, "history": []}
    data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    if not isinstance(data.get("history"), list):
        raise ValueError("token.json: history muss eine Liste sein")
    return data


def mutate_token(token: str) -> str:
    if not token:
        raise ValueError("Kein Token zum Verfälschen vorhanden")
    replacement = "A" if token[-1] != "A" else "B"
    return token[:-1] + replacement


def choose_token(case: str, data: dict[str, Any]) -> tuple[str | None, str]:
    current = data.get("current")
    history = data.get("history", [])
    if case == "initial":
        return None, "kein Token (Erstlauf)"
    if case in {"restart", "reboot"}:
        if not current:
            raise ValueError(f"{case}: token.json enthält keinen aktuellen Token")
        return str(current), "aktueller gespeicherter Token"
    if case == "invalid-mutated":
        if not current:
            raise ValueError("Kein aktueller Token zum Verfälschen")
        return mutate_token(str(current)), "aktueller Token, letztes Zeichen geändert"
    if case == "invalid-missing":
        return None, "Token absichtlich weggelassen"
    if case == "invalid-other-session":
        if not history:
            raise ValueError("Kein Token einer vorigen Session in history vorhanden")
        return str(history[0]["token"]), "verbrauchter Token einer vorigen Session"
    raise ValueError(f"Unbekannter Fall: {case}")


def save_new_token(data: dict[str, Any], new_token: str, case: str) -> None:
    old = data.get("current")
    history = list(data.get("history", []))
    if old:
        history.insert(
            0,
            {
                "token": old,
                "replaced_after_case": case,
                "replaced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
        )
    updated = {
        "current": new_token,
        "updated_after_case": case,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "history": history[:10],
    }
    temporary = TOKEN_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, TOKEN_FILE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        required=True,
        choices=[
            "initial",
            "restart",
            "reboot",
            "invalid-mutated",
            "invalid-missing",
            "invalid-other-session",
        ],
    )
    parser.add_argument(
        "--trace",
        type=Path,
        help="Rohmitschnitt (Standard: spikes/portal/runs/<case>.dbus.log)",
    )
    args = parser.parse_args()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    trace = (args.trace or RUNS_DIR / f"{args.case}.dbus.log").resolve()
    if HERE not in trace.parents:
        parser.error("--trace muss innerhalb von spikes/portal/ liegen")

    data = load_token_data()
    restore_token, token_description = choose_token(args.case, data)
    print(f"Fall: {args.case}; Eingabe: {token_description}", flush=True)
    monitor = Monitor(trace)
    client: PortalClient | None = None
    outcome: dict[str, Any] = {
        "case": args.case,
        "trace": str(trace.relative_to(HERE)),
        "token_input": token_description,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    exit_code = 1
    try:
        monitor.start()
        client = PortalClient()
        new_token = client.run(restore_token)
        # Critical invariant: every successful Start immediately replaces current.
        save_new_token(data, new_token, args.case)
        outcome["status"] = "success"
        outcome["token_replaced_after_start"] = True
        exit_code = 0
    except (dbus.DBusException, KeyError, RuntimeError, TimeoutError, ValueError) as exc:
        outcome["status"] = "error"
        outcome["error"] = str(exc)
        outcome["token_replaced_after_start"] = False
        print(f"FEHLER: {exc}", file=sys.stderr, flush=True)
    finally:
        if client is not None:
            outcome["portal_requests"] = client.events
            client.close()
        try:
            monitor.stop()
        except RuntimeError as exc:
            outcome["monitor_error"] = str(exc)
            print(f"FEHLER: {exc}", file=sys.stderr, flush=True)
            exit_code = 1
        outcome["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        result_path = RUNS_DIR / f"{args.case}.client.json"
        result_path.write_text(
            json.dumps(outcome, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return exit_code


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(124))
    raise SystemExit(main())

