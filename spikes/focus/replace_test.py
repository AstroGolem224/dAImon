#!/usr/bin/env python3
"""T-1.9, offener Restpunkt: ueberlebt das KWin-Script einen Compositor-Neustart?

Zwei Stufen, weil die ehrliche Antwort zwei verschiedene Fragen sind:

  Stufe 1 (dieses Skript, gefahrlos): ein *verschachtelter* kwin_wayland mit
  eigenem XDG_CONFIG_HOME/XDG_DATA_HOME und `--virtual` (rendert in einen
  Framebuffer, kein Fenster, kein Eingabe-Grab). Er laedt dasselbe Script.
  Wir toeten ihn und starten ihn neu und pruefen, ob das Script von allein
  wiederkommt und weiter meldet. Das beantwortet den Kern: kommt die
  Gatterkette nach einem Compositor-Neustart ohne Handgriff zurueck?

  Stufe 2 (NICHT hier): `kwin_wayland --replace` in der echten Sitzung.
  Laut `kwin_wayland --help` beendet das die Instanz, damit
  `kwin_wayland_wrapper` sie neu startet -- die Sitzung ueberlebt also
  grundsaetzlich. Trotzdem ist das ein Eingriff in die laufende Sitzung von
  Matthias und braucht seine ausdrueckliche Zustimmung. Dieses Skript
  fuehrt ihn nicht aus.

Der Empfaenger (`probe.py`) muss laufen; die verschachtelte Instanz meldet an
denselben Sitzungsbus und damit in dieselbe `events.jsonl`. Auseinanderhalten
laesst sich das ueber den Byte-Offset der Datei vor dem jeweiligen Schritt.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVENTS = HERE / "events.jsonl"
PKG = HERE / "daimon-focusprobe"

SOCKET = "wayland-daimon-replacetest"
BOOT_TIMEOUT = 25.0  # bis das Script sein "loaded" meldet
HARD_TIMEOUT = 90.0  # Notbremse fuer eine haengende Instanz


def new_events(offset):
    """Alle Ereignisse ab Byte-Offset, plus der neue Offset."""
    if not EVENTS.exists():
        return [], 0
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


def make_env(root: Path):
    """Eigene Konfiguration, damit die Sitzung von Matthias unberuehrt bleibt."""
    cfg = root / "config"
    data = root / "data"
    (cfg / "kwinrc").parent.mkdir(parents=True, exist_ok=True)
    (cfg / "kwinrc").write_text(
        "[Plugins]\ndaimon-focusprobeEnabled=true\n"
        "[Wayland]\nInputMethod=\n"
    )
    scripts = data / "kwin" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PKG, scripts / "daimon-focusprobe", dirs_exist_ok=True)

    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(cfg)
    env["XDG_DATA_HOME"] = str(data)
    # XDG_DATA_DIRS unveraendert lassen, sonst findet KWin seine eigenen
    # Effekte und Uebersetzungen nicht.
    env.pop("WAYLAND_DISPLAY", None)
    env["WAYLAND_DISPLAY"] = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    return env


def start(env, log: Path):
    fh = log.open("ab")
    proc = subprocess.Popen(
        [
            "kwin_wayland",
            "--virtual",
            "--width", "800",
            "--height", "600",
            "--socket", SOCKET,
        ],
        env=env,
        stdout=fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc


def stop(proc):
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def wait_for_loaded(offset, deadline):
    """Wartet auf ein 'loaded'-Ereignis. Gibt (gefunden, offset, wartezeit) zurueck."""
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        rows, offset = new_events(offset)
        for r in rows:
            if r.get("kind") == "loaded":
                return True, offset, time.monotonic() - t0
        time.sleep(0.2)
    return False, offset, time.monotonic() - t0


def run_cycle(env, log, offset, label):
    proc = start(env, log)
    ok, offset, dt = wait_for_loaded(offset, time.monotonic() + BOOT_TIMEOUT)
    print(f"{label}: loaded={ok} nach {dt:.1f} s (pid {proc.pid}, exit {proc.poll()})",
          flush=True)
    return proc, offset, ok, round(dt * 1000, 1)


def main():
    if subprocess.run(["pgrep", "-f", "spikes/focus/probe.py"],
                      capture_output=True).returncode != 0:
        raise SystemExit("probe.py laeuft nicht -- ohne Empfaenger ist die Messung wertlos")

    root = Path(tempfile.mkdtemp(prefix="daimon-replacetest-"))
    log = root / "kwin.log"
    env = make_env(root)
    _, offset = new_events(0)  # ans Ende der bestehenden Datei

    proc = None
    t_start = time.monotonic()
    try:
        proc, offset, ok1, ms1 = run_cycle(env, log, offset, "1. Start")
        if not ok1:
            raise SystemExit(
                "Die verschachtelte Instanz hat das Script nicht geladen. "
                f"Protokoll: {log}"
            )

        stop(proc)
        proc = None
        time.sleep(2.0)

        if time.monotonic() - t_start > HARD_TIMEOUT:
            raise SystemExit("Notbremse: Gesamtlaufzeit ueberschritten")

        proc, offset, ok2, ms2 = run_cycle(env, log, offset, "2. Start nach Abschuss")

        result = {
            "stufe": "verschachtelter kwin_wayland --virtual, eigene Konfiguration",
            "erster_start_loaded": ok1,
            "erster_start_ms": ms1,
            "neustart_loaded": ok2,
            "neustart_ms": ms2,
            "survives_restart": bool(ok1 and ok2),
            "hinweis": (
                "Gemessen wurde: kommt das KWin-Script nach einem Compositor-"
                "Neustart von allein zurueck und meldet weiter an denselben "
                "DBus-Empfaenger. Nicht gemessen: 'kwin_wayland --replace' in "
                "der echten Sitzung -- das braucht Matthias' Zustimmung."
            ),
            "kwin_log": str(log),
        }
        print(json.dumps(result, indent=1, ensure_ascii=False), flush=True)
        (HERE / "replace_test.json").write_text(
            json.dumps(result, indent=1, ensure_ascii=False)
        )
    finally:
        if proc is not None:
            stop(proc)
        # Konfigurationsbaum stehen lassen waere Muell; das Protokoll retten wir.
        keep = HERE / "replace_test_kwin.log"
        if log.exists():
            shutil.copy(log, keep)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
