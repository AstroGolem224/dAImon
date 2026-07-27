#!/usr/bin/env python3
"""Test 3: KDE-Bug 503121 — 20 Hide/Show-Zyklen, drei Abhilfen.

configure-Zaehler allein reicht nicht: er beweist nur, dass ein Event kam.
Deshalb haelt der Client nach dem letzten Zyklus offen und wir pruefen per
Screenshot, ob die Surface wirklich wieder sichtbar ist.
"""

import json
import random
import re
import subprocess
import time
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
EV = HERE / "evidence"
EV.mkdir(exist_ok=True)
SPIKE = HERE / "target/release/spike"

MARKER = (2400, 500, 200, 200)
PROBE = (MARKER[0] + 100, MARKER[1] + 100)


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


def probe(path):
    return list(Image.open(path).convert("RGB").getpixel(PROBE))


def close(a, b, tol=6):
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


def roll():
    while True:
        c = random.randrange(0, 0x1000000)
        rgb = ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
        if max(rgb) - min(rgb) >= 70 and 250 <= sum(rgb) <= 560:
            return f"{c:06X}", list(rgb)


def run(mode):
    hexc, rgb = roll()
    print(f"--- {mode}  Farbe {hexc} {rgb}")
    proc = subprocess.Popen(
        [str(SPIKE), "cycles", "--cycle-mode", mode, "--cycles", "20",
         "--marker", ",".join(map(str, MARKER)), "--color", hexc, "--hold", "25"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = []
    res = {"mode": mode, "color": hexc, "color_rgb": rgb}
    t0 = time.time()
    held = False
    while time.time() - t0 < 120:
        line = proc.stdout.readline()
        if not line:
            break
        out.append(line.rstrip())
        if line.startswith("RESULT cycle_mode"):
            m = re.search(r"cycles=(\d+) configures=(\d+) closed=(\d+) error=(.*)", line)
            res["cycles"] = int(m.group(1))
            res["configures"] = int(m.group(2))
            res["closed"] = int(m.group(3))
            res["error"] = m.group(4).strip()
        if line.startswith("RESULT times_ms"):
            res["times_ms"] = json.loads(line.split("=", 1)[1].strip())
        if line.startswith("HOLD"):
            held = True
            break
    if held:
        time.sleep(2)
        res["probe_after_cycles"] = probe(shot(f"cycle_{mode}.png"))
        res["visible_after_cycles"] = close(res["probe_after_cycles"], rgb)
        print("   sichtbar nach 20 Zyklen:", res["visible_after_cycles"],
              res["probe_after_cycles"])
    else:
        res["visible_after_cycles"] = None
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(2)
    res["log_tail"] = out[-6:]
    print("   configures:", res.get("configures"), "/", res.get("cycles"),
          "error:", res.get("error"))
    return res


def main():
    all_res = {}
    for mode in ("null", "reset", "recreate"):
        all_res[mode] = run(mode)
        time.sleep(2)
    (EV / "cycle_test.json").write_text(json.dumps(all_res, indent=2))
    print(json.dumps(all_res, indent=2))


if __name__ == "__main__":
    main()
