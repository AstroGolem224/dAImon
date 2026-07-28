#!/usr/bin/env python3
"""T-1.1 — Vorabtest: erkennt das KWS-Modell das Wort in einer frischen Aufnahme?

    venv/bin/python mictest.py                 # 15 s
    venv/bin/python mictest.py --seconds 30
    venv/bin/python mictest.py --keep          # Aufnahme behalten

WAS SICH GEAENDERT HAT UND WARUM
--------------------------------
Die erste Fassung urteilte gegen erfundene Akustikwerte: RMS-Untergrenze 0,02,
Mindestabstand 18 dB, Hoechstlaenge eines Abschnitts 3 s. Alle drei stammten
aus den 50 kaputten Aufnahmen vom 2026-07-27 -- also ausschliesslich aus
Negativbeispielen. Was gute Sprache auszeichnet, war nirgends belegt.

Die Folge: Matthias fiel dreimal durch, obwohl dasselbe Mikrofon im Diktat der
Desktop-App tadellos funktioniert. Zwei der drei Urteile waren nachweislich
falsch. "Uebersteuert" wurde aus der Spitze allein behauptet, ohne je zu
zaehlen, wie viele Samples wirklich anstehen -- bei Sprech-RMS 0,0935 und
Spitze 1,000 ist das ein Scheitelfaktor von 20,6 dB, fuer Sprache voellig
normal. Und "Abschnitte verschmelzen" bewertete, wie jemand spricht, nicht ob
das Mikrofon taugt.

Deshalb prueft dieses Skript jetzt gegen den einzigen Massstab, der zaehlt:
dasselbe sherpa-onnx-KWS-Modell, das spaeter auch evaluate.py benutzt. Erkennt
es das Wort, traegt der Aufbau. Erkennt es nichts, ist das ein Ergebnis ueber
das Wort oder das Modell -- und genau danach fragt T-1.1.

Die Pegelwerte werden weiter ausgegeben, aber sie urteilen nicht mehr.
Abgelehnt wird nur noch, was jede Messung wertlos macht: praktisch stumm, oder
grob uebersteuert -- und uebersteuert heisst jetzt "mehr als 1 Prozent der
Samples stehen an der Grenze", gemessen statt behauptet.
"""

import argparse
import math
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from evaluate import THRESHOLDS, build_spotter, detections, read_wav  # noqa: E402

BLOCK_MS = 100
RMS_FLOOR = 0.02        # nur noch fuer die Anzeige des Pegelverlaufs
DEAD_PEAK = 0.01        # darunter ist nichts angekommen
CLIP_LEVEL = 0.99
MAX_CLIP_FRACTION = 0.01


def capture(seconds, path):
    proc = subprocess.Popen(
        ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    t0 = time.time()
    while time.time() - t0 < 2.0:
        if path.exists() and path.stat().st_size > 0:
            break
        time.sleep(0.05)
    else:
        proc.kill()
        err = proc.stderr.read().decode()[:300] if proc.stderr else ""
        raise SystemExit(f"\n  FEHLER: pw-record liefert keine Daten. {err}")

    print("  ► JETZT sprechen", flush=True)
    for left in range(int(seconds), 0, -1):
        print(f"    noch {left:3d} s ", end="\r", flush=True)
        time.sleep(1)
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    src = subprocess.run(["pactl", "get-default-source"],
                         capture_output=True, text=True).stdout.strip()
    print(f"Standardquelle: {src or '<unbekannt>'}\n")
    print("=" * 66)
    print(f"  {args.seconds:.0f} Sekunden. Sag mehrfach \"Ember Shard\",")
    print("  ganz normal, mit kurzen Pausen dazwischen.")
    print("  Sprich einfach so, wie du auch diktierst.")
    print("=" * 66)
    for i in (3, 2, 1):
        print(f"  {i} …", end="\r", flush=True)
        time.sleep(0.7)

    tmp = Path(tempfile.mkdtemp(prefix="mictest-")) / "probe.wav"
    capture(args.seconds, tmp)

    samples = read_wav(tmp)
    if not len(samples):
        print("\n  FEHLER: Aufnahme ist leer.")
        return 2

    peak = float(np.max(np.abs(samples)))
    total = float(np.sqrt(np.mean(samples ** 2)))
    n = 16000 * BLOCK_MS // 1000
    rms = [float(np.sqrt(np.mean(samples[i:i + n] ** 2)))
           for i in range(0, len(samples) - n, n)]
    clip_frac = float(np.mean(np.abs(samples) >= CLIP_LEVEL))
    loud = [r for r in rms if r > RMS_FLOOR]
    quiet = [r for r in rms if r <= RMS_FLOOR]
    snr = (20 * math.log10((sum(loud) / len(loud)) / (sum(quiet) / len(quiet)))
           if loud and quiet else float("nan"))

    print()
    print(f"  Dauer            {len(samples) / 16000:.2f} s")
    print(f"  Spitze           {peak:.3f}")
    print(f"  RMS gesamt       {total:.4f}")
    print(f"  an der Grenze    {clip_frac * 100:.3f} % der Samples")
    if not math.isnan(snr):
        print(f"  Sprache/Pause    {snr:.1f} dB")
    print()
    print("  Pegelverlauf, ein Zeichen je 100 ms:")
    bar = "".join("#" if r > RMS_FLOOR else ("-" if r > RMS_FLOOR / 4 else ".")
                  for r in rms)
    for i in range(0, len(bar), 64):
        print(f"    {bar[i:i + 64]}")
    print()

    if peak < DEAD_PEAK:
        print("  ABGELEHNT — praktisch stumm. Falsche Quelle oder stummgeschaltet.")
        return 1
    if clip_frac > MAX_CLIP_FRACTION:
        print(f"  ABGELEHNT — {clip_frac * 100:.1f} % der Samples stehen an der")
        print("  Aussteuerungsgrenze. Das ist echte Verzerrung, nicht nur eine")
        print("  hohe Spitze. Etwas leiser, dann nochmal.")
        return 1

    # Der eigentliche Test: was sagt das Modell, um das es geht?
    print("  Das KWS-Modell hoert sich die Aufnahme an …")
    hits = {}
    for th in THRESHOLDS:
        try:
            hits[th] = detections(build_spotter(th), samples)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! Modell laeuft nicht: {type(exc).__name__}: {exc}")
            return 2

    print()
    print("  Schwelle   Treffer")
    for th, h in hits.items():
        mark = "  <==" if h else ""
        print(f"    {th:<6.2f}   {h}{mark}")
    print()

    best = max(hits.values())
    if args.keep or best == 0:
        dest = HERE / "samples" / "mictest-letzte.wav"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(tmp.read_bytes())
        print(f"  Aufnahme behalten: {dest}")

    if best > 0:
        th = min(t for t, h in hits.items() if h > 0)
        print(f"  BESTANDEN — das Modell erkennt das Wort, ab Schwelle {th}.")
        print("  Der Aufbau traegt. Naechster Schritt: take.py")
        return 0

    print("  KEIN TREFFER bei keiner Schwelle.")
    print("  Das ist noch kein Urteil ueber den Aufbau: dasselbe Ergebnis hatte")
    print("  EMBERSHARD als ein Wort, waehrend EMBER SHARD als zwei Woerter")
    print("  zuverlaessig feuerte -- der Unterschied liegt allein in der")
    print("  Tokenisierung (siehe NOTES.md). Die Aufnahme wurde behalten, damit")
    print("  sich das nachrechnen laesst, statt nochmal zu raten.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
