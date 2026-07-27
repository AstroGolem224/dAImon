#!/usr/bin/env python3
"""Test 2: Ueberlebt das Overlay ein Fullscreen-Fenster?

Beweisfuehrung bewusst ausserhalb des Clients: zufaellige Farbe, gezeichnet
an bekannter Position, danach per Screenshot ausgelesen. Was der Client ueber
sich selbst meldet, zaehlt hier nicht.

Vier Aufnahmen derselben kontrollierten Region (Mittelpunkt des Markers):
  A  nichts                            -> Marker-Farbe darf NICHT passen (Referenz)
  E  nur Fullscreen-Fenster            -> muss sich von A unterscheiden.
                                          Das ist der unabhaengige Nachweis,
                                          dass das Fullscreen-Fenster genau
                                          diese Stelle wirklich ueberdeckt.
  C  Fullscreen + Overlay              -> Marker-Farbe MUSS passen  (der Test)
  B  nur Overlay                       -> Marker-Farbe MUSS passen
  D  nach dem Aufraeumen               -> Marker-Farbe darf NICHT passen

Das Fullscreen-Fenster ist eine Konsole, deren Hintergrund per ANSI auf eine
zweite gewuerfelte Farbe gesetzt wird. Damit ist E nicht nur "irgendwie anders",
sondern nachweislich das Fenster.
"""

import json
import random
import signal
import subprocess
import time
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
EV = HERE / "evidence"
EV.mkdir(exist_ok=True)
SPIKE = HERE / "target/release/spike"

MARKER = (2000, 600, 240, 240)  # x, y, w, h  auf 5120x1440
PROBE = (MARKER[0] + MARKER[2] // 2, MARKER[1] + MARKER[3] // 2)
# Kontrollpunkt: ausserhalb des Markers, aber in derselben Bildschirmmitte,
# also sicher innerhalb eines Fullscreen-Fensters.
CTRL = (MARKER[0] + MARKER[2] // 2, MARKER[1] - 200)


def shot(name):
    p = EV / name
    if p.exists():
        p.unlink()
    subprocess.run(["spectacle", "-b", "-n", "-f", "-o", str(p)], timeout=40,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if p.exists() and p.stat().st_size > 0:
            break
        time.sleep(0.25)
    time.sleep(0.3)
    return p


def px(path, pt):
    im = Image.open(path).convert("RGB")
    x, y = pt
    vals = [im.getpixel((x + dx, y + dy)) for dx in (-2, 0, 2) for dy in (-2, 0, 2)]
    uniform = all(v == vals[0] for v in vals)
    return list(vals[4]), uniform


def close(a, b, tol=6):
    return all(abs(int(p) - int(q)) <= tol for p, q in zip(a, b))


def roll():
    while True:
        c = random.randrange(0, 0x1000000)
        rgb = ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
        if max(rgb) - min(rgb) >= 70 and 250 <= sum(rgb) <= 560:
            return c, rgb


def main():
    ocol, orgb = roll()          # Overlay-Marker
    fcol, frgb = roll()          # Fullscreen-Fensterhintergrund
    res = {
        "marker": MARKER, "probe": PROBE, "ctrl": CTRL,
        "overlay_color_hex": f"{ocol:06X}", "overlay_color_rgb": list(orgb),
        "fullscreen_color_hex": f"{fcol:06X}", "fullscreen_color_rgb": list(frgb),
    }
    print("Overlay-Farbe   ", res["overlay_color_hex"], orgb)
    print("Fullscreen-Farbe", res["fullscreen_color_hex"], frgb)

    ov = kon = None
    try:
        res["A_probe"], _ = px(shot("A_nothing.png"), PROBE)
        print("A nichts               probe=", res["A_probe"])

        # --- Fullscreen-Fenster allein
        ansi = f"\\033[48;2;{frgb[0]};{frgb[1]};{frgb[2]}m"
        kon = subprocess.Popen(
            ["konsole", "--fullscreen", "--separate", "--hide-menubar", "--hide-tabbar",
             "-e", "sh", "-c", f"printf '{ansi}'; clear; sleep 300"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(8)
        p = shot("E_fullscreen_only.png")
        res["E_probe"], _ = px(p, PROBE)
        res["E_ctrl"], _ = px(p, CTRL)
        res["fullscreen_covers_probe"] = not close(res["E_probe"], res["A_probe"], tol=8)
        res["fullscreen_bg_recognised"] = close(res["E_probe"], frgb, tol=20)
        print("E nur Fullscreen       probe=", res["E_probe"],
              " deckt Messpunkt:", res["fullscreen_covers_probe"],
              " Farbe erkannt:", res["fullscreen_bg_recognised"])

        # --- Overlay dazu
        ov = subprocess.Popen(
            [str(SPIKE), "map", "--color", res["overlay_color_hex"],
             "--marker", ",".join(map(str, MARKER))],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        t0 = time.time()
        ready = False
        while time.time() - t0 < 10:
            line = ov.stdout.readline()
            if not line:
                break
            print("  [spike]", line.rstrip())
            if line.startswith("READY"):
                ready = True
                break
        res["ready"] = ready
        res["pid"] = ov.pid
        (EV / "overlay_pid").write_text(str(ov.pid))
        time.sleep(3)

        p = shot("C_fullscreen_plus_overlay.png")
        res["C_probe"], res["C_uniform"] = px(p, PROBE)
        res["C_ctrl"], _ = px(p, CTRL)
        res["fullscreen_pixel_match"] = bool(close(res["C_probe"], orgb))
        print("C Fullscreen+Overlay   probe=", res["C_probe"],
              " ctrl=", res["C_ctrl"],
              " MATCH:", res["fullscreen_pixel_match"])

        # --- Fullscreen weg, Overlay bleibt
        kon.terminate()
        subprocess.run(["pkill", "-f", "sleep 300"], check=False)
        kon = None
        time.sleep(4)
        res["B_probe"], _ = px(shot("B_overlay_only.png"), PROBE)
        res["overlay_visible_alone"] = bool(close(res["B_probe"], orgb))
        print("B nur Overlay          probe=", res["B_probe"],
              " MATCH:", res["overlay_visible_alone"])

        res["client_alive_at_end"] = ov.poll() is None
        res["baseline_false_positive"] = bool(close(res["A_probe"], orgb))

    finally:
        if kon:
            kon.terminate()
        subprocess.run(["pkill", "-f", "sleep 300"], check=False)
        time.sleep(2)
        if ov:
            ov.send_signal(signal.SIGTERM)
            try:
                ov.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ov.kill()
        time.sleep(2)

    res["D_probe"], _ = px(shot("D_cleanup.png"), PROBE)
    res["cleanup_ok"] = not close(res["D_probe"], orgb)
    print("D aufgeraeumt          probe=", res["D_probe"], " weg:", res["cleanup_ok"])

    (EV / "fullscreen_test.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
