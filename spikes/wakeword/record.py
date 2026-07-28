#!/usr/bin/env python3
"""T−1.1 — geführte Aufnahme für die Wake-Word-Messung.

Nimmt Positivproben ("Embershard") unter wechselnden Bedingungen auf und
optional Hintergrundmaterial für die Falsch-Positiv-Rate.

    python3 record.py positive          # geführte Aufnahme, 50 Proben
    python3 record.py positive --n 10   # kürzer
    python3 record.py background --min 60

Aufnahme über pw-record (PipeWire), 16 kHz mono S16LE — genau das Format,
das der KWS-Erkenner erwartet. Keine Python-Audio-Abhängigkeit nötig.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
POS = HERE / "samples" / "positive"

# Untergrenzen fuer "das ist wirklich Sprache". Absolut, nicht relativ zur
# Spitze -- siehe check(). Dieselben Werte wie in mictest.py, damit ein
# bestandener Mikrofontest und eine angenommene Aufnahme dasselbe bedeuten.
RMS_FLOOR = 0.02
MIN_LOUD_S = 0.30
BG = HERE / "samples" / "background"

# Bedingungen, unter denen aufgenommen wird. Die Streuung ist der Punkt:
# ein Wake-Word, das nur im Idealfall anschlägt, ist im Alltag nutzlos.
CONDITIONS = [
    ("normal",     "Normale Lautstärke, ~50 cm vom Mikrofon, ruhiger Raum"),
    ("leise",      "Leise, fast gemurmelt"),
    ("laut",       "Deutlich lauter als normal"),
    ("fern",       "Aus 2–3 Metern Entfernung"),
    ("schnell",    "Schnell hingesagt, verschliffen"),
    ("nebenbei",   "Beiläufig, während du auf den Bildschirm schaust"),
    ("hintergrund","Mit Musik oder Video im Hintergrund"),
    ("abgewandt",  "Vom Mikrofon weggedreht"),
]


def start_recorder(path: Path):
    """Startet pw-record und wartet, bis wirklich Daten fliessen.

    WICHTIG: pw-record braucht Anlaufzeit (PipeWire-Stream oeffnen). Wer erst
    "sprich jetzt" sagt und danach den Rekorder startet, nimmt Stille auf --
    genau das ist am 2026-07-27 passiert und hat 50 Proben unbrauchbar gemacht.
    Deshalb: erst starten, auf echten Datenfluss warten, DANN zum Sprechen
    auffordern.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(
        ["pw-record", "--rate", "16000", "--channels", "1",
         "--format", "s16", str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    # Warten bis die Datei waechst -- erst dann laeuft der Stream wirklich.
    t0 = time.time()
    last = -1
    while time.time() - t0 < 2.0:
        time.sleep(0.05)
        sz = path.stat().st_size if path.exists() else 0
        if sz > 0 and sz != last:
            time.sleep(0.15)   # kleiner Vorlauf
            return p
        last = sz
    return p


def record(path: Path, seconds: float) -> bool:
    """Nur noch fuer Hintergrundaufnahmen: startet und laeuft N Sekunden."""
    p = start_recorder(path)
    time.sleep(seconds)
    p.terminate()
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()
    if not path.exists() or path.stat().st_size < 2000:
        err = p.stderr.read().decode()[:200] if p.stderr else ""
        print(f"  ! Aufnahme leer oder zu kurz. {err}")
        return False
    return True


def check(path: Path) -> tuple[bool, str]:
    """Prueft sofort, ob wirklich Sprache drin ist. Eine stille Aufnahme
    faellt so beim Aufnehmen auf und nicht erst bei der Auswertung."""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            n = w.getnframes()
            raw = w.readframes(n)
    except Exception as e:
        return False, f"unlesbar ({e})"
    if n < 8000:
        return False, "zu kurz"
    import array
    a = array.array("h", raw)
    peak = max(abs(v) for v in a) / 32768

    # Absolute Schwelle, nicht relativ zur Spitze. Die alte Fassung verglich
    # gegen `peak * 0.3` -- bei einer fast stummen Aufnahme ist der Bezugspunkt
    # selbst schon Rauschen, und die Pruefung geht durch. Nachgemessen an den
    # 50 archivierten Proben vom 2026-07-27: die relative Fassung liess 39 von
    # 50 durch, die absolute laesst 1 durch (fern_003 mit genau 0,3 s).
    #
    # RMS je 100-ms-Block, nicht Spitzenwert: ein einzelner Knacks erzeugt eine
    # hohe Spitze ohne jede Sprache dahinter.
    n = 1600  # 100 ms bei 16 kHz
    rms = [(sum(v * v for v in a[i:i+n]) / n) ** 0.5 / 32768
           for i in range(0, len(a) - n, n)]
    run = best = 0
    for r in rms:
        run = run + 1 if r > RMS_FLOOR else 0
        best = max(best, run)
    loud_s = best * 0.1
    total = (sum(v * v for v in a) / len(a)) ** 0.5 / 32768

    if peak < 0.02:
        return False, f"zu leise (Spitze {peak:.3f}) — Mikrofonpegel prüfen"
    if loud_s < MIN_LOUD_S:
        return False, (
            f"kein zusammenhängendes Sprachsignal: nur {loud_s:.1f} s über "
            f"RMS {RMS_FLOOR} (gefordert {MIN_LOUD_S:.1f} s), RMS gesamt {total:.4f}. "
            f"Genau dieses Muster hatten die 50 unbrauchbaren Proben — "
            f"erst mictest.py bestehen."
        )
    return True, ""


def countdown(msg: str, n: int = 3) -> None:
    print(f"\n{msg}")
    for i in range(n, 0, -1):
        print(f"  {i} …", end="\r", flush=True)
        time.sleep(0.7)
    print("  ► JETZT sprechen ", end="", flush=True)


def do_positive(n: int, word: str) -> None:
    POS.mkdir(parents=True, exist_ok=True)
    done = len(list(POS.glob("*.wav")))
    print(f"""
╭─ T−1.1 Positivproben ─────────────────────────────────────────
│ Wort: „{word}"
│ Ziel: {n} Aufnahmen, bereits vorhanden: {done}
│
│ Es wird in Runden durch {len(CONDITIONS)} Bedingungen gegangen.
│ Sprich das Wort EINMAL pro Aufnahme, normal, ohne Kunstpause.
│ Strg-C bricht ab; bereits Aufgenommenes bleibt erhalten.
╰───────────────────────────────────────────────────────────────""")
    input("\nEnter zum Start …")

    made = 0
    try:
        while made < n:
            cond, desc = CONDITIONS[made % len(CONDITIONS)]
            print(f"\n── {made+1}/{n} · Bedingung: {cond}")
            print(f"   {desc}")
            ts = datetime.now().strftime("%H%M%S")
            path = POS / f"{cond}_{done+made:03d}_{ts}.wav"

            print("\n  Bereit? ", end="", flush=True)
            for i in (3, 2, 1):
                print(f"{i} … ", end="", flush=True)
                time.sleep(0.7)

            proc = start_recorder(path)          # laeuft BEVOR aufgefordert wird
            print("\n  ► JETZT: „Embershard\"", flush=True)
            time.sleep(2.2)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

            ok, why = check(path)
            print(f"  {'✓ ok' if ok else '✗ ' + why}")
            if ok:
                made += 1
            else:
                path.unlink(missing_ok=True)
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\n\nAbgebrochen.")

    total = len(list(POS.glob("*.wav")))
    print(f"\n{total} Positivproben unter {POS}")
    if total < 50:
        print(f"Für ein belastbares Urteil fehlen noch {50-total}.")


def do_background(minutes: float) -> None:
    BG.mkdir(parents=True, exist_ok=True)
    print(f"""
╭─ T−1.1 Hintergrundmaterial ───────────────────────────────────
│ {minutes:.0f} Minuten Ton OHNE das Wake-Word.
│ Podcast, Video, Gespräch, Musik — je vielfältiger, desto besser.
│ Daraus wird die Falsch-Positiv-Rate je Stunde gerechnet.
│
│ Sag „Embershard" in dieser Zeit NICHT.
╰───────────────────────────────────────────────────────────────""")
    input("\nEnter zum Start …")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BG / f"bg_{ts}_{int(minutes)}min.wav"
    print(f"Nimmt auf … Strg-C beendet vorzeitig.\n  → {path}")
    t0 = time.time()
    try:
        record(path, minutes * 60)
    except KeyboardInterrupt:
        pass
    dur = time.time() - t0
    print(f"\n{dur/60:.1f} Minuten aufgenommen.")
    total = sum(f.stat().st_size for f in BG.glob("*.wav")) / (16000 * 2)
    print(f"Hintergrund gesamt: {total/3600:.2f} h (Ziel: 3 h)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["positive", "background", "status"])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--min", type=float, default=30)
    ap.add_argument("--word", default="Embershard")  # tokenisiert als EMBER SHARD
    a = ap.parse_args()

    if a.mode == "positive":
        do_positive(a.n, a.word)
    elif a.mode == "background":
        do_background(a.min)
    else:
        p = len(list(POS.glob("*.wav"))) if POS.exists() else 0
        b = sum(f.stat().st_size for f in BG.glob("*.wav")) / (16000*2) if BG.exists() else 0
        print(f"Positivproben: {p}/50")
        print(f"Hintergrund:   {b/3600:.2f}/3.0 h")
        print("bereit für evaluate.py" if p >= 50 and b >= 3*3600
              else "noch nicht genug für ein belastbares Urteil")
