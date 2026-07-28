#!/usr/bin/env python3
"""T-1.1 — ein Take am Stueck, statt 50 Einzelaufnahmen.

    python3 take.py                # voller Take, alle acht Bedingungen
    python3 take.py --only normal  # nur eine Bedingung
    python3 take.py --dry          # nur die Regie zeigen, nichts aufnehmen

Warum am Stueck: der Durchlauf vom 2026-07-27 hatte 50 Einzelaufnahmen, und
der Rekorder startete jeweils erst nach der Aufforderung zum Sprechen. Diese
ganze Fehlerklasse verschwindet, wenn durchgaengig aufgenommen und erst
hinterher geschnitten wird -- es gibt dann keinen Moment, in dem gesprochen
wird und der Rekorder nicht laeuft.

Aufgenommen wird eine einzige WAV-Datei. Parallel entsteht ein Regieprotokoll
mit den Zeitpunkten, an denen eine neue Bedingung angesagt wurde. segment.py
schneidet danach und ordnet jedes Stueck ueber diese Zeitpunkte der richtigen
Bedingung zu.

Waehrend der Aufnahme laeuft eine Pegelanzeige mit. Ein Take dauert mehrere
Minuten -- ohne Anzeige wuerde ein Aufbaufehler erst am Ende auffallen, und
genau das ist beim letzten Mal passiert.
"""

import argparse
import array
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAKES = HERE / "samples" / "takes"

RATE = 16000
RMS_FLOOR = 0.02          # dieselbe Schwelle wie mictest.py und record.check()
PAUSE_S = 1.6             # Pause zwischen zwei Aussprachen -- die Schnittkante
SPEAK_S = 1.4             # so lange darf eine Aussprache dauern

# Acht Bedingungen, zusammen 50 Aussprachen. "normal" bekommt mehr, weil daraus
# die eigentliche FRR-Grundzahl entsteht; die uebrigen pruefen die Raender.
CONDITIONS = [
    ("normal",      8, "normal, so wie du es im Alltag sagen wuerdest"),
    ("laut",        6, "deutlich lauter als normal"),
    ("leise",       6, "leise, fast gemurmelt"),
    ("schnell",     6, "schnell hintereinander weg"),
    ("fern",        6, "aus etwa zwei Metern Abstand zum Mikrofon"),
    ("abgewandt",   6, "vom Mikrofon weggedreht"),
    ("hintergrund", 6, "mit Musik oder Video im Hintergrund"),
    ("nebenbei",    6, "nebenbei, waehrend du etwas anderes tust"),
]
WORD = "Ember Shard"


def rms_of(path: Path, seconds: float) -> float:
    """RMS der letzten `seconds` der wachsenden Datei. Fuer die Pegelanzeige."""
    try:
        size = path.stat().st_size
        want = int(RATE * seconds) * 2
        with path.open("rb") as fh:
            fh.seek(max(44, size - want))
            raw = fh.read()
        if len(raw) < 2:
            return 0.0
        a = array.array("h", raw[: len(raw) // 2 * 2])
        return (sum(v * v for v in a) / len(a)) ** 0.5 / 32768
    except (OSError, ValueError):
        return 0.0


def meter(level: float) -> str:
    filled = min(28, int(level / 0.12 * 28))
    mark = "#" if level > RMS_FLOOR else "."
    return f"[{mark * filled}{' ' * (28 - filled)}] {level:.4f}"


def wait(path: Path, seconds: float, label: str) -> None:
    """Wartet und zeigt dabei den Pegel. Ersetzt ein blindes sleep()."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        left = end - time.monotonic()
        print(f"  {label:22s} {meter(rms_of(path, 0.4))} {left:4.1f}s ",
              end="\r", flush=True)
        time.sleep(0.12)
    print(" " * 78, end="\r")


def plan(only):
    conds = [c for c in CONDITIONS if only is None or c[0] == only]
    if not conds:
        raise SystemExit(f"unbekannte Bedingung {only!r}. "
                         f"Bekannt: {', '.join(c[0] for c in CONDITIONS)}")
    return conds


def show_plan(conds):
    total = sum(n for _, n, _ in conds)
    secs = sum(n * (SPEAK_S + PAUSE_S) for _, n, _ in conds) + len(conds) * 6
    print(f"Regie: {total} Aussprachen von \"{WORD}\" in {len(conds)} Bedingungen, "
          f"rund {secs / 60:.1f} min")
    for name, n, hint in conds:
        print(f"  {name:12s} {n:2d}x  — {hint}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    conds = plan(args.only)
    show_plan(conds)
    if args.dry:
        return 0

    TAKES.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    wav = TAKES / f"take-{stamp}.wav"
    cues_path = TAKES / f"take-{stamp}.cues.json"

    print()
    print("=" * 66)
    print("  Die Aufnahme laeuft gleich DURCHGEHEND. Sprich nur, wenn die")
    print("  Zeile 'SPRICH' zeigt, und schweig in den Pausen -- an den Pausen")
    print("  wird spaeter geschnitten. Falscher Einsatz ist nicht schlimm,")
    print("  sprich das Wort dann einfach nochmal in der naechsten Runde.")
    print("  Abbruch mit Strg-C, der Take bleibt bis dahin erhalten.")
    print("=" * 66)
    input("  Enter zum Starten … ")

    proc = subprocess.Popen(
        ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", str(wav)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    t0 = time.time()
    while time.time() - t0 < 2.0:
        if wav.exists() and wav.stat().st_size > 0:
            break
        time.sleep(0.05)
    else:
        proc.kill()
        err = proc.stderr.read().decode()[:300] if proc.stderr else ""
        raise SystemExit(f"FEHLER: pw-record liefert keine Daten. {err}")

    t_start = time.time()
    cues = []
    try:
        # Vorlauf: eine ruhige Sekunde als Referenz fuer den Grundpegel.
        wait(wav, 2.0, "Grundpegel, schweig")
        for name, n, hint in conds:
            print(f"\n── {name.upper()} — {hint}")
            cues.append({
                "condition": name, "expected": n,
                "t_start": round(time.time() - t_start, 3), "hint": hint,
            })
            wait(wav, 4.0, "gleich geht es los")
            for i in range(1, n + 1):
                print(f"  ► SPRICH  {name} {i}/{n}: \"{WORD}\"" + " " * 20)
                wait(wav, SPEAK_S, f"SPRICH {i}/{n}")
                wait(wav, PAUSE_S, "… Pause, schweig")
            cues[-1]["t_end"] = round(time.time() - t_start, 3)
    except KeyboardInterrupt:
        print("\n  abgebrochen — der bisherige Take bleibt erhalten.")
        if cues and "t_end" not in cues[-1]:
            cues[-1]["t_end"] = round(time.time() - t_start, 3)
    finally:
        time.sleep(0.6)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    with wave.open(str(wav), "rb") as w:
        dur = w.getnframes() / w.getframerate()
    cues_path.write_text(json.dumps(
        {"wav": wav.name, "word": WORD, "rate": RATE, "dauer_s": round(dur, 2),
         "cues": cues}, indent=1, ensure_ascii=False))

    print(f"\n  Take: {wav}  ({dur:.1f} s)")
    print(f"  Regie: {cues_path}")
    print(f"\n  Naechster Schritt:\n    python3 {HERE / 'segment.py'} {wav.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
