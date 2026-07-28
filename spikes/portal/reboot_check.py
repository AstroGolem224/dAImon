#!/usr/bin/env python3
"""One-command post-reboot check with watchdogs and DBus-derived result."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from analyze_trace import analyze


HERE = Path(__file__).resolve().parent
PROBE = HERE / "portal_probe.py"
TRACE = HERE / "runs" / "reboot.dbus.log"
ANALYSIS = HERE / "runs" / "reboot.analysis.json"
RESULTS = HERE / "results.json"


def stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def main() -> int:
    if not RESULTS.exists():
        print(f"Fehlt: {RESULTS}", file=sys.stderr)
        return 2
    proc = subprocess.Popen([sys.executable, str(PROBE), "--case", "reboot"])
    try:
        returncode = proc.wait(timeout=125)
    except subprocess.TimeoutExpired:
        stop(proc)
        print("Reboot-Probe: äußerer 125-s-Watchdog ausgelöst", file=sys.stderr)
        return 124
    if returncode != 0:
        print(f"Reboot-Probe fehlgeschlagen (Exit {returncode})", file=sys.stderr)
        return returncode

    evidence = analyze(TRACE)
    ANALYSIS.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    prompted = evidence["prompted_from_dbus"]
    if not isinstance(prompted, bool):
        print("DBus-Mitschnitt erlaubt keine Prompt-Ableitung", file=sys.stderr)
        return 3

    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    results["reboot_prompted"] = prompted
    results["notes"] = (
        str(results["notes"])
        + f" Reboot-Nachtest: reboot_prompted={str(prompted).lower()}, "
        + "abgeleitet aus runs/reboot.dbus.log."
    )
    RESULTS.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"reboot_prompted={str(prompted).lower()} (aus {TRACE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

