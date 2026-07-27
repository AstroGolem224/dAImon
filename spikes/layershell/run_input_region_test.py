#!/usr/bin/env python3
"""Test 6: input_region / Click-Through.

Aufbau:
  * unten  : ein einfarbiges Vollbildfenster (tkinter/XWayland), das jeden
             Klick mit Wurzelkoordinaten protokolliert
  * darueber: das Layer-Overlay mit set_input_region auf ein kleines Rechteck

Vier Messungen:
  1. Zeiger INNERHALB des Rechtecks  -> Overlay meldet pointer_enter
  2. Klick   INNERHALB               -> Overlay meldet pointer_press,
                                        Fenster darunter meldet NICHTS
  3. Zeiger AUSSERHALB               -> Overlay meldet pointer_leave, kein enter
  4. Klick   AUSSERHALB              -> Fenster darunter meldet den Klick,
                                        Overlay meldet NICHTS

Eingaben ueber ydotool (uinput). Es wird ausschliesslich in das eigene
Testfenster geklickt.
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

REGION = (2400, 500, 200, 200)          # x, y, w, h — Eingabe- und Marker-Rechteck
INSIDE = (REGION[0] + 100, REGION[1] + 100)
OUTSIDE = (REGION[0] + 700, REGION[1] + 100)   # gleiche Hoehe, weit rechts


def ydo(*args):
    return subprocess.run(["ydotool", *args], capture_output=True, text=True, timeout=15)


def move(x, y):
    ydo("mousemove", "-a", "-x", str(x), "-y", str(y))
    time.sleep(1.0)


def click():
    ydo("click", "0xC0")
    time.sleep(1.2)


def read_new(fh):
    return fh.read()


def main():
    res = {"region": REGION, "inside": INSIDE, "outside": OUTSIDE}
    if LOG.exists():
        LOG.unlink()
    LOG.write_text("")

    # ydotoold muss laufen
    if subprocess.run(["pgrep", "-x", "ydotoold"], capture_output=True).returncode != 0:
        subprocess.Popen(["ydotoold"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    res["ydotoold"] = subprocess.run(["pgrep", "-x", "ydotoold"],
                                     capture_output=True).returncode == 0

    below = ov = None
    try:
        c = random.randrange(0, 0x1000000)
        below = subprocess.Popen(
            ["python3", str(HERE / "below_window.py"), f"#{c:06x}", str(LOG), "90"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)
        res["below_ready"] = "ready" in LOG.read_text()

        ov = subprocess.Popen(
            [str(SPIKE), "map", "--color", "F0F0F0",
             "--marker", ",".join(map(str, REGION)),
             "--input-region", ",".join(map(str, REGION))],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        os.set_blocking(ov.stdout.fileno(), False)
        time.sleep(3)

        def drain():
            buf = ""
            for _ in range(3):
                try:
                    chunk = ov.stdout.read()
                except (BlockingIOError, TypeError):
                    chunk = None
                if chunk:
                    buf += chunk
                time.sleep(0.15)
            return buf

        drain()
        below_before = LOG.read_text()

        # --- 1. Zeiger hinein
        move(*INSIDE)
        s = drain()
        res["log_move_inside"] = s.strip().splitlines()
        res["enter_inside"] = "pointer_enter" in s

        # --- 2. Klick drinnen
        below_before = LOG.read_text()
        click()
        s = drain()
        res["log_click_inside"] = s.strip().splitlines()
        res["press_inside"] = "pointer_press" in s
        res["below_got_click_inside"] = LOG.read_text() != below_before

        # --- 3. Zeiger hinaus
        move(*OUTSIDE)
        s = drain()
        res["log_move_outside"] = s.strip().splitlines()
        res["leave_on_exit"] = "pointer_leave" in s
        res["enter_outside"] = "pointer_enter" in s

        # --- 4. Klick draussen
        below_before = LOG.read_text()
        click()
        s = drain()
        res["log_click_outside"] = s.strip().splitlines()
        res["press_outside"] = "pointer_press" in s
        after = LOG.read_text()
        res["below_got_click_outside"] = after != below_before
        res["below_log"] = after.strip().splitlines()

        res["input_region_works"] = bool(
            res["enter_inside"] and res["press_inside"]
            and not res["below_got_click_inside"]
            and not res["enter_outside"] and not res["press_outside"]
            and res["below_got_click_outside"])

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
        time.sleep(1)

    (EV / "input_region_test.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
