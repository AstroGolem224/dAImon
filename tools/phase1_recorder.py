#!/usr/bin/env python3
"""T-1.10: kleine, passive Langzeitmessung fuer den Phase-1-Alltagstest.

Der Recorder liest nur systemd-Zustand, ``/proc/<pid>/stat`` und den
Face-Diagnose-Socket. Er startet oder steuert keinen dAImon-Dienst.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = REPO / "tests" / "evidence" / "phase1-usage.json"
UNITS = (
    "daimon-hub.service",
    "daimon-hookbridge.service",
    "daimon-focus.service",
    "daimon-face.service",
)
CLK_TCK = os.sysconf("SC_CLK_TCK")


@dataclass(frozen=True)
class ProcessSample:
    pid: int
    start_ticks: int
    cpu_ticks: int


@dataclass(frozen=True)
class Observation:
    datum: str
    uptime_s: float
    services: dict[str, ProcessSample]
    restarts: dict[str, int]
    face_pid: int | None = None
    face_tones: int | None = None


def _leer() -> dict[str, Any]:
    return {
        "v": 1,
        "days": 0,
        "needs_input_events": 0,
        "idle_cpu_p95": 0.0,
        "crashes": 0,
        "verdict": "pending",
        "fehlalarme": None,
        "ablenkungen": None,
        "tage": [],
    }


def _laden(pfad: Path) -> dict[str, Any]:
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _leer()
    if not isinstance(daten, dict) or daten.get("v") != 1:
        raise ValueError(f"{pfad}: unbekanntes oder beschaedigtes Format")
    return daten


def _p95(werte: list[float]) -> float:
    """Nearest-rank-p95; fuer jede Stichprobenzahl eindeutig definiert."""
    if not werte:
        return 0.0
    sortiert = sorted(werte)
    return float(sortiert[math.ceil(0.95 * len(sortiert)) - 1])


def _tag(daten: dict[str, Any], datum: str) -> dict[str, Any]:
    for tag in daten["tage"]:
        if tag.get("datum") == datum:
            return tag
    tag = {
        "datum": datum,
        "laufzeit_stichproben": 0,
        "needs_input_events": 0,
        "idle_cpu_samples": [],
        "crashes": 0,
        "dienste": [],
    }
    daten["tage"].append(tag)
    daten["tage"].sort(key=lambda eintrag: str(eintrag.get("datum", "")))
    return tag


def aktualisieren(alt: dict[str, Any], beobachtung: Observation) -> dict[str, Any]:
    """Eine Beobachtung einarbeiten, ohne menschliche Felder zu ueberschreiben."""
    daten = copy.deepcopy(alt)
    vorgabe = _leer()
    for name, wert in vorgabe.items():
        daten.setdefault(name, copy.deepcopy(wert))

    intern = daten.setdefault("_recorder", {})
    cpu_basis = intern.setdefault("cpu_baselines", {})
    restart_basis = intern.setdefault("restart_baselines", {})
    ton_basis = intern.get("face_tones")

    crash_delta = 0
    for unit, aktuell in beobachtung.restarts.items():
        vorher = restart_basis.get(unit)
        if isinstance(vorher, int) and aktuell >= vorher:
            crash_delta += aktuell - vorher
        restart_basis[unit] = aktuell
    daten["crashes"] = int(daten.get("crashes", 0)) + crash_delta

    ton_delta = 0
    if beobachtung.face_pid is not None and beobachtung.face_tones is not None:
        if isinstance(ton_basis, dict):
            vorher_pid = ton_basis.get("pid")
            vorher_wert = ton_basis.get("value")
            if vorher_pid == beobachtung.face_pid and isinstance(vorher_wert, int):
                ton_delta = max(0, beobachtung.face_tones - vorher_wert)
            elif isinstance(vorher_wert, int):
                # Nach einem Face-Neustart beginnt der Prozesszaehler bei null.
                ton_delta = beobachtung.face_tones
        intern["face_tones"] = {
            "pid": beobachtung.face_pid,
            "value": beobachtung.face_tones,
        }
    daten["needs_input_events"] = (
        int(daten.get("needs_input_events", 0)) + ton_delta
    )

    cpu_gesamt = 0.0
    cpu_gemessen = False
    for unit, aktuell in beobachtung.services.items():
        vorher = cpu_basis.get(unit)
        if (
            isinstance(vorher, dict)
            and vorher.get("pid") == aktuell.pid
            and vorher.get("start_ticks") == aktuell.start_ticks
            and beobachtung.uptime_s > float(vorher.get("uptime_s", 0.0))
            and aktuell.cpu_ticks >= int(vorher.get("cpu_ticks", 0))
        ):
            delta_s = beobachtung.uptime_s - float(vorher["uptime_s"])
            delta_ticks = aktuell.cpu_ticks - int(vorher["cpu_ticks"])
            cpu_gesamt += delta_ticks / CLK_TCK / delta_s * 100.0
            cpu_gemessen = True
        cpu_basis[unit] = {
            "pid": aktuell.pid,
            "start_ticks": aktuell.start_ticks,
            "cpu_ticks": aktuell.cpu_ticks,
            "uptime_s": beobachtung.uptime_s,
        }

    # Ohne einen nachweislich laufenden Prozess entsteht kein Arbeitstag.
    if beobachtung.services:
        tag = _tag(daten, beobachtung.datum)
        tag["laufzeit_stichproben"] = int(tag["laufzeit_stichproben"]) + 1
        tag["needs_input_events"] = int(tag["needs_input_events"]) + ton_delta
        tag["crashes"] = int(tag["crashes"]) + crash_delta
        tag["dienste"] = sorted(
            set(tag.get("dienste", ())) | set(beobachtung.services)
        )
        if cpu_gemessen:
            tag["idle_cpu_samples"].append(round(cpu_gesamt, 6))

    alle_cpu = [
        float(wert)
        for tag in daten["tage"]
        for wert in tag.get("idle_cpu_samples", ())
    ]
    daten["days"] = len(daten["tage"])
    daten["idle_cpu_p95"] = round(_p95(alle_cpu), 6)
    # verdict, fehlalarme und ablenkungen bleiben absichtlich unangetastet.
    return daten


def atomar_schreiben(pfad: Path, daten: dict[str, Any]) -> None:
    """Temp-Datei im Zielverzeichnis, flush, fsync, replace, Verzeichnis-fsync."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    nutzlast = (json.dumps(daten, indent=2, sort_keys=True) + "\n").encode()
    fd, tmp_name = tempfile.mkstemp(
        dir=pfad.parent, prefix=pfad.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(nutzlast)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, pfad)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    dir_fd = os.open(pfad.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def aufzeichnen(pfad: Path, beobachtung: Observation) -> dict[str, Any]:
    daten = aktualisieren(_laden(pfad), beobachtung)
    atomar_schreiben(pfad, daten)
    return daten


def _unit_status(unit: str) -> dict[str, str]:
    ergebnis = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "-p",
            "ActiveState",
            "-p",
            "MainPID",
            "-p",
            "NRestarts",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if ergebnis.returncode != 0:
        return {}
    return dict(
        zeile.split("=", 1)
        for zeile in ergebnis.stdout.splitlines()
        if "=" in zeile
    )


def _proc_stat(pid: int) -> ProcessSample:
    # comm (Feld 2) darf Leerzeichen und Klammern enthalten: am letzten ") "
    # trennen, danach beginnt Feld 3.
    roh = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    felder = roh[roh.rfind(") ") + 2 :].split()
    return ProcessSample(
        pid=pid,
        cpu_ticks=int(felder[11]) + int(felder[12]),
        start_ticks=int(felder[19]),
    )


def _face_diag(pfad: Path) -> int | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(pfad))
            teile = bytearray()
            while len(teile) < 65_536 and not teile.endswith(b"\n"):
                block = client.recv(4096)
                if not block:
                    break
                teile.extend(block)
        wert = json.loads(teile.decode("utf-8")).get("toene_gespielt")
        return int(wert) if isinstance(wert, int) and wert >= 0 else None
    except (OSError, UnicodeError, ValueError):
        return None


def beobachten(diag_socket: Path, units: tuple[str, ...] = UNITS) -> Observation:
    services: dict[str, ProcessSample] = {}
    restarts: dict[str, int] = {}
    face_pid: int | None = None
    for unit in units:
        status = _unit_status(unit)
        try:
            restarts[unit] = int(status["NRestarts"])
        except (KeyError, ValueError):
            pass
        try:
            pid = int(status["MainPID"])
            if status.get("ActiveState") != "active" or pid <= 0:
                continue
            services[unit] = _proc_stat(pid)
            if unit == "daimon-face.service":
                face_pid = pid
        except (KeyError, ValueError, OSError):
            continue
    uptime_s = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    return Observation(
        datum=datetime.now().astimezone().date().isoformat(),
        uptime_s=uptime_s,
        services=services,
        restarts=restarts,
        face_pid=face_pid,
        face_tones=_face_diag(diag_socket) if face_pid is not None else None,
    )


def main() -> int:
    runtime = Path(
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument(
        "--diag-socket", type=Path, default=runtime / "daimon" / "face-diag.sock"
    )
    args = parser.parse_args()

    beobachtung = beobachten(args.diag_socket)
    daten = aufzeichnen(args.evidence, beobachtung)
    print(
        json.dumps(
            {
                "evidence": str(args.evidence),
                "running": sorted(beobachtung.services),
                "days": daten["days"],
                "needs_input_events": daten["needs_input_events"],
                "idle_cpu_p95": daten["idle_cpu_p95"],
                "crashes": daten["crashes"],
                "verdict": daten["verdict"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
