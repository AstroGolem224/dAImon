#!/usr/bin/env python3
"""T-1.1 — Mikrofontest vor dem Aufnehmen. Drei Sekunden, dann ein Urteil.

Warum es das gibt: am 2026-07-27 wurden 50 Proben aufgenommen und alle 50 waren
unbrauchbar. Der Energieverlauf zeigte kein einziges 100-ms-Fenster ueber
RMS 0,02 -- laute Dauer im Median 0,0 s. Das ist kein schwieriges Wort und kein
zu frueh gesprochenes, das ist ein stummer Eingang. Bevor jemand nochmal 50 mal
spricht, muss der Pfad einmal bewiesen sein.

Gemessen wird ueber genau dieselbe Kette wie in record.py: pw-record auf die
Standardquelle, 16 kHz, mono, s16. Ein Test ueber einen anderen Weg wuerde
nichts beweisen.
"""

import array
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

SECONDS = 3.0
BLOCK_MS = 100
RMS_FLOOR = 0.02       # Untergrenze fuer "das ist Sprache"
MIN_LOUD_S = 0.30      # so lange muss zusammenhaengend Signal anliegen


def blocks_rms(samples, rate):
    n = rate * BLOCK_MS // 1000
    out = []
    for i in range(0, len(samples) - n, n):
        chunk = samples[i:i + n]
        out.append((sum(v * v for v in chunk) / len(chunk)) ** 0.5 / 32768)
    return out


def longest_run(flags):
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def main():
    src = subprocess.run(["pactl", "get-default-source"],
                         capture_output=True, text=True).stdout.strip()
    print(f"Standardquelle: {src or '<unbekannt>'}")
    print()
    print("=" * 60)
    print(f"  Bitte {SECONDS:.0f} Sekunden lang normal sprechen.")
    print('  Zum Beispiel: "Ember Shard, Ember Shard, Ember Shard"')
    print("=" * 60)
    for i in (3, 2, 1):
        print(f"  {i} …", end="\r", flush=True)
        time.sleep(0.7)

    tmp = Path(tempfile.mkdtemp(prefix="mictest-")) / "probe.wav"
    proc = subprocess.Popen(
        ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", str(tmp)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    # Erst wenn die Datei waechst, laeuft der Stream wirklich.
    t0 = time.time()
    while time.time() - t0 < 2.0:
        if tmp.exists() and tmp.stat().st_size > 0:
            break
        time.sleep(0.05)
    else:
        proc.kill()
        err = proc.stderr.read().decode()[:300] if proc.stderr else ""
        print(f"\n  FEHLER: pw-record liefert keine Daten. {err}")
        return 2

    print("  ► JETZT sprechen", flush=True)
    time.sleep(SECONDS)
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()

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
    run_s = longest_run(loud) * BLOCK_MS / 1000

    print()
    print(f"  Dauer            {len(samples) / rate:.2f} s")
    print(f"  Spitze           {peak:.3f}")
    print(f"  RMS gesamt       {total:.4f}")
    print(f"  laut ({RMS_FLOOR}) am Stueck {run_s:.1f} s   (gefordert >= {MIN_LOUD_S:.1f} s)")
    print()
    print("  Pegelverlauf, ein Zeichen je 100 ms:")
    bar = "".join("#" if r > RMS_FLOOR else ("-" if r > RMS_FLOOR / 4 else ".")
                  for r in rms)
    for i in range(0, len(bar), 60):
        print(f"    {bar[i:i + 60]}")
    print()

    if run_s >= MIN_LOUD_S and total > RMS_FLOOR / 2:
        print("  BESTANDEN — der Aufnahmepfad traegt. Jetzt sind die 50 Proben sinnvoll.")
        rc = 0
    elif peak < 0.02:
        print("  DURCHGEFALLEN — praktisch stumm. Falsche Quelle, stummgeschaltet")
        print("  oder das Mikrofon liegt nicht an. Erst das loesen.")
        rc = 1
    else:
        print("  DURCHGEFALLEN — Signal da, aber zu leise oder zu kurz.")
        print("  Genau dieses Muster hatten die 50 unbrauchbaren Proben.")
        print("  Eingangspegel anheben oder naeher ans Mikrofon.")
        rc = 1

    tmp.unlink(missing_ok=True)
    tmp.parent.rmdir()
    return rc


if __name__ == "__main__":
    sys.exit(main())
