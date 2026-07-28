#!/usr/bin/env python3
"""T-1.9, letzter Akzeptanzpunkt: `kwin_wayland --replace` in der ECHTEN Sitzung.

Die Trockenuebung (replace_test.py) hat einen verschachtelten Compositor
abgeschossen und neu gestartet. Das beantwortet die Frage im Prinzip, aber
nicht fuer den Wrapper-Pfad: laut `kwin_wayland --help` beendet `--replace`
die laufende Instanz, damit `kwin_wayland_wrapper` sie neu startet. Ob das
KWin-Script dabei mitkommt und ob der DBus-Empfaenger seine Verbindung
behaelt, entscheidet sich nur hier.

Gemessen wird gegen `events.jsonl`, nicht gegen eine Selbstauskunft:

  * Byte-Offset der Datei vor dem Eingriff merken
  * `kwin_wayland --replace` ausloesen
  * warten, bis ein `loaded`-Ereignis mit NEUER Compositor-Instanz erscheint
  * danach pruefen, ob `activated` weiterhin faellt -- ein einmaliges `loaded`
    beweist nur das Laden, nicht dass die Kette wieder traegt

Watchdog: harte Obergrenze, danach Abbruch mit Befund statt Haengen.
"""

import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVENTS = HERE / "events.jsonl"

LOADED_TIMEOUT = 60.0   # bis das Script sich nach dem Neustart meldet
ACTIVITY_WINDOW = 45.0  # danach auf echten Fensterverkehr warten


def kwin_pid():
    out = subprocess.run(["pgrep", "-x", "kwin_wayland"], capture_output=True, text=True)
    return sorted(out.stdout.split()) if out.returncode == 0 else []


def tail(offset):
    if not EVENTS.exists():
        return [], offset
    with EVENTS.open("rb") as fh:
        fh.seek(offset)
        blob = fh.read()
        end = fh.tell()
    rows = []
    for line in blob.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows, end


def wait_for(offset, kinds, timeout):
    """Wartet auf ein Ereignis aus `kinds`. Gibt (Ereignis|None, offset, Sekunden)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rows, offset = tail(offset)
        for r in rows:
            if r.get("kind") in kinds:
                return r, offset, time.monotonic() - t0
        time.sleep(0.2)
    return None, offset, time.monotonic() - t0


def main():
    if subprocess.run(["pgrep", "-f", "spikes/focus/probe.py"],
                      capture_output=True).returncode != 0:
        raise SystemExit("probe.py laeuft nicht -- ohne Empfaenger ist die Messung wertlos")

    before_pids = kwin_pid()
    _, offset = tail(0)
    print(f"kwin_wayland vorher: {before_pids}", flush=True)

    t0 = time.monotonic()
    proc = subprocess.run(
        ["kwin_wayland", "--replace"],
        capture_output=True, text=True, timeout=30,
        stdin=subprocess.DEVNULL,
    )
    print(f"--replace abgesetzt, rc={proc.returncode} nach {time.monotonic() - t0:.1f} s",
          flush=True)
    if proc.stderr.strip():
        print("stderr:", proc.stderr.strip()[:500], flush=True)

    loaded, offset, dt_loaded = wait_for(offset, {"loaded"}, LOADED_TIMEOUT)
    after_pids = kwin_pid()
    print(f"kwin_wayland nachher: {after_pids}", flush=True)
    print(f"loaded nach {dt_loaded:.1f} s: {bool(loaded)}", flush=True)

    activity, offset, dt_act = (None, offset, 0.0)
    if loaded:
        print("Bitte einmal das Fenster wechseln, damit die Kette belegt werden kann "
              f"(warte bis zu {ACTIVITY_WINDOW:.0f} s) ...", flush=True)
        activity, offset, dt_act = wait_for(offset, {"activated"}, ACTIVITY_WINDOW)
        print(f"activated nach {dt_act:.1f} s: {bool(activity)}", flush=True)

    result = {
        "stufe": "kwin_wayland --replace in der echten Sitzung",
        "kwin_pids_vorher": before_pids,
        "kwin_pids_nachher": after_pids,
        "neue_instanz": before_pids != after_pids,
        "replace_returncode": proc.returncode,
        "loaded_nach_s": round(dt_loaded, 2) if loaded else None,
        "loaded": bool(loaded),
        "activated_danach": bool(activity),
        "activated_nach_s": round(dt_act, 2) if activity else None,
        "erstes_ereignis_nach_replace": loaded,
        "survives_replace": bool(loaded),
        "hinweis": (
            "loaded belegt, dass das Script nach dem Compositor-Neustart von allein "
            "wieder geladen wurde und den DBus-Empfaenger erreicht. activated_danach "
            "belegt zusaetzlich, dass die Kette wieder echten Fensterverkehr traegt. "
            "Ist activated_danach false, heisst das nicht zwingend Fehlschlag -- es "
            "kann auch bedeuten, dass in der Wartezeit kein Fensterwechsel stattfand."
        ),
    }
    print(json.dumps(result, indent=1, ensure_ascii=False), flush=True)
    (HERE / "replace_live.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
