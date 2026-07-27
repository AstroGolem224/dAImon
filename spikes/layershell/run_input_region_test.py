#!/usr/bin/env python3
"""Test 6: input_region / Click-Through.  Zweiter Anlauf.

WARUM EIN ZWEITER ANLAUF
------------------------
Der erste Anlauf meldete input_region_works=false. Das war ein Fehler des
Pruefstands, nicht von KWin: `ydotool mousemove -a` setzt auf dieser Maschine
den Zeiger fuer JEDES Ziel auf (0,0) -- nachgemessen mit einem
bildschirmfuellenden XWayland-Fenster und xdotool. Der Zeiger ist also nie in
die Input-Region gefahren; entsprechend gab es kein pointer_enter, und die
Klicks landeten irgendwo im Fenster darunter.

Dieser Aufbau kommt ohne absolute Positionierung aus:
  1. Der Zeiger wird per RELATIVER Bewegung im Regelkreis grob in die
     Bildschirmmitte gefahren (Rueckmeldung ueber xdotool, das dank des
     bildschirmfuellenden XWayland-Fensters darunter live ist).
  2. Die Ist-Position P wird gelesen.
  3. Die Input-Region wird UM P HERUM gelegt (P +- 100 px). Damit ist die
     Genauigkeit der Bewegung voellig egal.
  4. Gemessen wird: Klick an P (innen) und Klick bei P + 900 px in x (aussen).

Aufbau:
  * unten  : einfarbiges Vollbildfenster (tkinter/XWayland), protokolliert jeden
             Klick mit Wurzelkoordinaten
  * darueber: das Layer-Overlay mit set_input_region auf ein kleines Rechteck

SICHERHEIT: Das Overlay laeuft immer mit SPIKE_MAX_SECS=15. Die Input-Region ist
nie groesser als 200x200 px. Nach dem Lauf wird geprueft, dass kein spike-Prozess
uebrig ist.
"""

import json
import os
import random
import signal
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / "evidence"
EV.mkdir(exist_ok=True)
SPIKE = HERE / "target/release/spike"
LOG = EV / "below_clicks.log"

ENV = dict(os.environ, YDOTOOL_SOCKET="/run/user/1000/.ydotool_socket")
HALF = 100                      # halbe Kantenlaenge der Input-Region
AWAY = 900                      # relative Bewegung fuer "ausserhalb"
MAX_SECS = "15"                 # Watchdog im Overlay-Prozess


def ydo(*args):
    return subprocess.run(["ydotool", *args], env=ENV,
                          capture_output=True, text=True, timeout=15)


def where():
    o = subprocess.run(["xdotool", "getmouselocation", "--shell"],
                       capture_output=True, text=True).stdout
    d = dict(l.split("=") for l in o.strip().splitlines())
    return int(d["X"]), int(d["Y"])


def home(tx, ty, tol=25, maxit=60):
    """Relativer Regelkreis. Die Zeigerbeschleunigung macht exakte Spruenge
    unmoeglich, deshalb kleine Schritte mit Rueckmeldung."""
    for _ in range(maxit):
        cx, cy = where()
        dx, dy = tx - cx, ty - cy
        if abs(dx) <= tol and abs(dy) <= tol:
            return True
        ydo("mousemove", "-x", str(max(-30, min(30, dx))),
            "-y", str(max(-30, min(30, dy))))
        time.sleep(0.03)
    return False


def clicks_in_log():
    return [l for l in LOG.read_text().splitlines() if l.startswith("click ")]


def click():
    ydo("click", "0xC0")
    time.sleep(1.0)


def main():
    res = {}
    if LOG.exists():
        LOG.unlink()
    LOG.write_text("")

    if subprocess.run(["pgrep", "-x", "ydotoold"], capture_output=True).returncode != 0:
        subprocess.Popen(["ydotoold"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    res["ydotoold"] = subprocess.run(["pgrep", "-x", "ydotoold"],
                                     capture_output=True).returncode == 0

    below = ov = None
    try:
        c = random.randrange(0, 0x1000000)
        below = subprocess.Popen(
            ["python3", str(HERE / "below_window.py"), f"#{c:06x}", str(LOG), "45"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(4)
        res["below_ready"] = "ready" in LOG.read_text()

        # --- Zeiger grob in die Mitte, dann Ist-Position lesen
        res["homed"] = home(1500, 700)
        P = where()
        res["pointer_before_overlay"] = list(P)
        region = (P[0] - HALF, P[1] - HALF, 2 * HALF, 2 * HALF)
        res["region"] = list(region)
        res["inside"] = list(P)

        ov = subprocess.Popen(
            [str(SPIKE), "map", "--color", "F0F0F0",
             "--marker", ",".join(map(str, region)),
             "--input-region", ",".join(map(str, region))],
            env=dict(os.environ, SPIKE_MAX_SECS=MAX_SECS),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        os.set_blocking(ov.stdout.fileno(), False)
        time.sleep(2.0)

        def drain():
            buf = ""
            for _ in range(3):
                try:
                    chunk = ov.stdout.read()
                except (BlockingIOError, TypeError):
                    chunk = None
                if chunk:
                    buf += chunk
                time.sleep(0.12)
            return buf

        res["log_startup"] = drain().strip().splitlines()

        # --- 1. Zeiger wackeln, damit ein enter/motion garantiert faellt
        ydo("mousemove", "-x", "3", "-y", "3")
        time.sleep(0.3)
        ydo("mousemove", "-x", "-3", "-y", "-3")
        time.sleep(0.5)
        s = drain()
        res["log_move_inside"] = s.strip().splitlines()
        # Das pointer_enter faellt schon beim Mappen, weil der Zeiger dann bereits
        # in der Region steht. Deshalb zaehlt auch die Startphase.
        res["enter_inside"] = ("pointer_enter" in s
                               or any("pointer_enter" in l for l in res["log_startup"]))
        res["motion_inside"] = "pointer_motion" in s

        # --- 2. Klick innen
        before = clicks_in_log()
        click()
        s = drain()
        res["log_click_inside"] = s.strip().splitlines()
        res["press_inside"] = "pointer_press" in s
        res["below_clicks_inside"] = clicks_in_log()[len(before):]
        res["below_got_click_inside"] = bool(res["below_clicks_inside"])

        # --- 3. Zeiger weit nach rechts, raus aus der Region
        ydo("mousemove", "-x", str(AWAY), "-y", "0")
        time.sleep(0.8)
        s = drain()
        res["log_move_outside"] = s.strip().splitlines()
        res["leave_on_exit"] = "pointer_leave" in s
        res["enter_outside"] = "pointer_enter" in s
        res["pointer_outside"] = list(where())

        # --- 4. Klick aussen
        before = clicks_in_log()
        click()
        s = drain()
        res["log_click_outside"] = s.strip().splitlines()
        res["press_outside"] = "pointer_press" in s
        res["below_clicks_outside"] = clicks_in_log()[len(before):]
        res["below_got_click_outside"] = bool(res["below_clicks_outside"])
        res["below_log"] = LOG.read_text().strip().splitlines()[-40:]

        res["criteria"] = {
            "overlay_bekommt_enter_innen": res["enter_inside"],
            "overlay_bekommt_press_innen": res["press_inside"],
            "fenster_darunter_bekommt_klick_innen_NICHT":
                not res["below_got_click_inside"],
            "overlay_bekommt_leave_beim_verlassen": res["leave_on_exit"],
            "overlay_bekommt_press_aussen_NICHT": not res["press_outside"],
            "fenster_darunter_bekommt_klick_aussen": res["below_got_click_outside"],
        }
        res["input_region_works"] = all(res["criteria"].values())

        # --- 5. Negativkontrolle: dasselbe Overlay OHNE --input-region.
        # Die Vorgabe ist die leere Region; die Flaeche muss dann ueberall
        # klickdurchlaessig sein. Ohne diese Kontrolle koennte oben auch schlicht
        # "Layer-Surfaces bekommen nie Eingaben" gemessen worden sein.
        ov.send_signal(signal.SIGTERM)
        ov.wait(timeout=5)
        ov = None
        home(1500, 700)
        Q = where()
        res["ctrl_pointer"] = list(Q)
        ctrl = subprocess.Popen(
            [str(SPIKE), "map", "--color", "F0F0F0",
             "--marker", ",".join(map(str, (Q[0] - HALF, Q[1] - HALF, 2 * HALF, 2 * HALF)))],
            env=dict(os.environ, SPIKE_MAX_SECS=MAX_SECS),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        os.set_blocking(ctrl.stdout.fileno(), False)
        time.sleep(2.0)
        ov = ctrl
        start = drain()
        res["ctrl_input_region_line"] = [l for l in start.splitlines()
                                         if "input_region" in l]
        before = clicks_in_log()
        click()
        cs = drain()
        res["ctrl_overlay_log"] = cs.strip().splitlines()
        res["ctrl_overlay_got_press"] = "pointer_press" in cs
        res["ctrl_below_clicks"] = clicks_in_log()[len(before):]
        res["ctrl_click_through"] = (bool(res["ctrl_below_clicks"])
                                     and not res["ctrl_overlay_got_press"])

    finally:
        if ov:
            ov.send_signal(signal.SIGTERM)
            try:
                ov.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ov.kill()
        if below:
            below.terminate()
            try:
                below.wait(timeout=5)
            except subprocess.TimeoutExpired:
                below.kill()
        subprocess.run(["pkill", "-x", "spike"], capture_output=True)
        time.sleep(1)
        res["spike_left_over"] = subprocess.run(
            ["pgrep", "-x", "spike"], capture_output=True).returncode == 0

    (EV / "input_region_test.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
