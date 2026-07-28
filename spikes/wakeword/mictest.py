#!/usr/bin/env python3
"""T-1.1 — Mikrofontest und Eichaufnahme, bevor irgendetwas aufgenommen wird.

    python3 mictest.py                 # 3 s Schnelltest
    python3 mictest.py --seconds 30    # Eichaufnahme vor dem langen Take

Warum es das gibt: am 2026-07-27 wurden 50 Proben aufgenommen und alle 50 waren
unbrauchbar. Die Aufschluesselung nach Bedingung zeigt, dass es kein Werkzeug-
und kein Sprechfehler war -- alle acht Bedingungen sehen gleich aus:

    laut     RMS 0,0060   normal   RMS 0,0056
    leise    RMS 0,0031   Raum     RMS 0,00097

"laut" kommt auf -19,6 dBFS Spitze. Wer in ein Streaming-Mikro bei 100 Prozent
laut hineinspricht, muss nahe an die Aussteuerungsgrenze. Es kommt also nur ein
Bruchteil an. Ein Aufbau, der laut und leise nur um Faktor 2 trennt, kann kein
Wake-Word bewerten -- egal wie sauber der Rekorder startet.

Gemessen wird ueber genau dieselbe Kette wie in record.py und take.py:
pw-record auf die Standardquelle, 16 kHz, mono, s16. Ein Test ueber einen
anderen Weg wuerde nichts beweisen. Die Schwellen sind dieselben Konstanten
wie in record.check(), damit ein bestandener Test und eine angenommene
Aufnahme dasselbe bedeuten.
"""

import argparse
import array
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

BLOCK_MS = 100
RMS_FLOOR = 0.02       # Untergrenze fuer "das ist Sprache"
MIN_LOUD_S = 0.30      # so lange muss zusammenhaengend Signal anliegen
GOOD_RMS = 0.05        # ab hier ist der Pegel komfortabel, nicht nur ausreichend


def blocks_rms(samples, rate):
    n = rate * BLOCK_MS // 1000
    return [(sum(v * v for v in samples[i:i + n]) / n) ** 0.5 / 32768
            for i in range(0, len(samples) - n, n)]


def runs(flags):
    """Alle Laengen zusammenhaengender True-Laeufe."""
    out, run = [], 0
    for f in flags:
        if f:
            run += 1
        elif run:
            out.append(run)
            run = 0
    if run:
        out.append(run)
    return out


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
    time.sleep(seconds - int(seconds))
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3.0)
    args = ap.parse_args()
    long_run = args.seconds >= 15

    src = subprocess.run(["pactl", "get-default-source"],
                         capture_output=True, text=True).stdout.strip()
    print(f"Standardquelle: {src or '<unbekannt>'}\n")
    print("=" * 64)
    if long_run:
        print(f"  EICHAUFNAHME, {args.seconds:.0f} Sekunden.")
        print("  Sprich normal weiter, mit Pausen dazwischen -- zum Beispiel")
        print('  immer wieder "Ember Shard", so wie du es spaeter sagen wuerdest.')
        print("  Die Pausen sind wichtig: daran erkenne ich spaeter die Schnitte.")
    else:
        print(f"  SCHNELLTEST, {args.seconds:.0f} Sekunden.")
        print('  Bitte normal sprechen, z.B. "Ember Shard, Ember Shard".')
    print("=" * 64)
    for i in (3, 2, 1):
        print(f"  {i} …", end="\r", flush=True)
        time.sleep(0.7)

    tmp = Path(tempfile.mkdtemp(prefix="mictest-")) / "probe.wav"
    capture(args.seconds, tmp)

    with wave.open(str(tmp), "rb") as w:
        rate = w.getframerate()
        samples = array.array("h", w.readframes(w.getnframes()))
    if not samples:
        print("\n  FEHLER: Aufnahme ist leer.")
        return 2

    peak = max(abs(v) for v in samples) / 32768
    total = (sum(v * v for v in samples) / len(samples)) ** 0.5 / 32768
    rms = blocks_rms(samples, rate)
    loud = [r > RMS_FLOOR for r in rms]
    spans = runs(loud)
    best_s = max(spans, default=0) * BLOCK_MS / 1000
    speech_s = sum(spans) * BLOCK_MS / 1000
    loud_rms = [r for r in rms if r > RMS_FLOOR]
    quiet_rms = [r for r in rms if r <= RMS_FLOOR]

    print()
    print(f"  Dauer               {len(samples) / rate:.2f} s")
    print(f"  Spitze              {peak:.3f}")
    print(f"  RMS gesamt          {total:.4f}")
    if loud_rms:
        print(f"  RMS beim Sprechen   {sum(loud_rms)/len(loud_rms):.4f}   "
              f"(komfortabel ab {GOOD_RMS})")
    if quiet_rms:
        print(f"  RMS in den Pausen   {sum(quiet_rms)/len(quiet_rms):.4f}")
    print(f"  Sprache gesamt      {speech_s:.1f} s in {len(spans)} Abschnitten")
    print(f"  laengster Abschnitt {best_s:.1f} s   (gefordert >= {MIN_LOUD_S:.1f} s)")
    print()
    print("  Pegelverlauf, ein Zeichen je 100 ms   (# laut, - leise, . Pause):")
    bar = "".join("#" if r > RMS_FLOOR else ("-" if r > RMS_FLOOR / 4 else ".")
                  for r in rms)
    for i in range(0, len(bar), 64):
        print(f"    {bar[i:i + 64]}")
    print()

    if peak < 0.02:
        print("  DURCHGEFALLEN — praktisch stumm. Falsche Quelle, stummgeschaltet")
        print("  oder das Mikrofon liegt nicht an. Erst das loesen.")
        return 1
    if best_s < MIN_LOUD_S:
        print("  DURCHGEFALLEN — Signal da, aber zu leise oder zu kurz.")
        print("  Genau dieses Muster hatten die 50 unbrauchbaren Proben.")
        print("  Eingangspegel anheben oder naeher ans Mikrofon.")
        return 1

    print("  BESTANDEN — der Aufnahmepfad traegt.")
    if loud_rms and sum(loud_rms) / len(loud_rms) < GOOD_RMS:
        print(f"  Aber knapp: Sprech-RMS {sum(loud_rms)/len(loud_rms):.4f} liegt unter {GOOD_RMS}.")
        print("  Es wuerde gehen, mit mehr Pegel waere es robuster.")
    if long_run:
        print(f"  {len(spans)} Sprechabschnitte erkannt — die Segmentierung findet Schnitte.")
        print("  Naechster Schritt: take.py fuer den langen Take.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
