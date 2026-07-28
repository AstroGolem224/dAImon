#!/usr/bin/env python3
"""T-1.1 — schneidet einen Take in einzelne Aussprachen und beschriftet sie.

    python3 segment.py take-20260728-203000.wav
    python3 segment.py --list

Zwei unabhaengige Quellen bestimmen die Schnitte:

  * Energie. RMS je 100-ms-Block gegen dieselbe absolute Schwelle, die auch
    mictest.py und record.check() benutzen. Daraus entstehen Sprechabschnitte.
  * vosk. Ein deutsches Modell transkribiert den ganzen Take mit
    Wort-Zeitstempeln. Ein Abschnitt ohne erkanntes Wort ist verdaechtig --
    dort war Geraeusch, kein Sprechen.

Warum vosk und nicht das Zipformer-Modell aus diesem Spike: das Zipformer IST
der Prueflig. Wuerde es die Schnitte bestimmen, entschiede der Prueflig mit,
was als Aussprache zaehlt, und die spaetere FRR waere zirkulaer. vosk gehoert
einer anderen Modellfamilie an und weiss vom Wake-Word nichts.

Die Bedingung ("normal", "leise", ...) kommt aus dem Regieprotokoll von
take.py, ueber den Zeitpunkt des Abschnitts. Sie wird NICHT aus dem Ton
geraten.
"""

import argparse
import array
import json
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAKES = HERE / "samples" / "takes"
OUT = HERE / "samples" / "positive"
MODEL = HERE / "models" / "vosk-model-de-0.21"

BLOCK_MS = 100
RMS_FLOOR = 0.02     # identisch zu mictest.py und record.check()
MIN_SPEECH_S = 0.30  # kuerzer ist keine Silbe
MAX_SPEECH_S = 3.00  # laenger ist keine einzelne Aussprache mehr
MERGE_GAP_S = 0.25   # kurze Luecken innerhalb eines Wortes ueberbruecken
PAD_S = 0.15         # Rand, damit der Anlaut nicht abgeschnitten wird


def load_wav(path):
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit("erwartet mono s16")
        return w.getframerate(), array.array("h", w.readframes(w.getnframes()))


def spans_from_energy(samples, rate):
    n = rate * BLOCK_MS // 1000
    rms = [(sum(v * v for v in samples[i:i + n]) / n) ** 0.5 / 32768
           for i in range(0, len(samples) - n, n)]
    loud = [r > RMS_FLOOR for r in rms]

    spans, start = [], None
    for i, f in enumerate(loud):
        if f and start is None:
            start = i
        elif not f and start is not None:
            spans.append([start, i])
            start = None
    if start is not None:
        spans.append([start, len(loud)])

    merged = []
    for s in spans:
        if merged and (s[0] - merged[-1][1]) * BLOCK_MS / 1000 <= MERGE_GAP_S:
            merged[-1][1] = s[1]
        else:
            merged.append(s)

    out = []
    for a, b in merged:
        # Die Dauerpruefung laeuft auf der UNGEPOLSTERTEN Laenge. Wuerde sie den
        # Rand mitzaehlen, waere jeder Huster von 0,1 s nach dem Polstern 0,4 s
        # lang und kaeme als Aussprache durch -- genau das ist im Rauchtest
        # passiert. Gepolstert wird nur, was ausgeschnitten wird, damit der
        # Anlaut nicht abreisst.
        roh_t0 = a * BLOCK_MS / 1000
        roh_t1 = b * BLOCK_MS / 1000
        roh_dur = roh_t1 - roh_t0
        t0 = max(0.0, roh_t0 - PAD_S)
        t1 = min(len(samples) / rate, roh_t1 + PAD_S)
        block_rms = rms[a:b] or [0.0]
        out.append({
            "t0": round(t0, 3), "t1": round(t1, 3),
            "dauer_s": round(roh_dur, 3), "dauer_mit_rand_s": round(t1 - t0, 3),
            "rms": round(sum(block_rms) / len(block_rms), 5),
            "zu_kurz": roh_dur < MIN_SPEECH_S, "zu_lang": roh_dur > MAX_SPEECH_S,
        })
    return out, rms


def transcribe(path, rate):
    """Wort-Zeitstempel ueber den ganzen Take. Leere Liste, wenn vosk fehlt."""
    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except ImportError:
        print("  ! vosk nicht installiert — Abschnitte bleiben unbeschriftet")
        return []
    if not MODEL.is_dir():
        print(f"  ! Modell fehlt unter {MODEL} — Abschnitte bleiben unbeschriftet")
        return []

    SetLogLevel(-1)
    rec = KaldiRecognizer(Model(str(MODEL)), rate)
    rec.SetWords(True)
    words = []
    with wave.open(str(path), "rb") as w:
        while chunk := w.readframes(4000):
            if rec.AcceptWaveform(chunk):
                words += json.loads(rec.Result()).get("result", [])
    words += json.loads(rec.FinalResult()).get("result", [])
    return words


def condition_at(cues, t):
    for c in cues:
        if c["t_start"] <= t <= c.get("t_end", float("inf")):
            return c["condition"]
    return "unzugeordnet"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("take", nargs="?")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    takes = sorted(TAKES.glob("take-*.wav"))
    if args.list or not args.take:
        if not takes:
            print(f"keine Takes unter {TAKES}")
            return 1
        for t in takes:
            print(f"  {t.name}  {t.stat().st_size / 32000:.1f} s")
        if not args.take:
            print("\nBitte einen Take angeben.")
            return 1
        return 0

    wav = TAKES / args.take if not Path(args.take).exists() else Path(args.take)
    if not wav.exists():
        raise SystemExit(f"{wav} nicht gefunden")
    cues_path = wav.with_suffix("").with_suffix(".cues.json")
    if not cues_path.exists():
        cues_path = wav.parent / (wav.stem + ".cues.json")
    cues = json.loads(cues_path.read_text())["cues"] if cues_path.exists() else []
    if not cues:
        print("  ! kein Regieprotokoll — alle Abschnitte werden 'unzugeordnet'")

    rate, samples = load_wav(wav)
    print(f"Take {wav.name}: {len(samples) / rate:.1f} s")

    spans, _ = spans_from_energy(samples, rate)
    print(f"  {len(spans)} Sprechabschnitte nach Energie")

    words = transcribe(wav, rate)
    if words:
        print(f"  {len(words)} Woerter von vosk transkribiert")

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.wav"):
        old.unlink()

    manifest, counts, kept = [], {}, 0
    for span in spans:
        span["condition"] = condition_at(cues, span["t0"])
        span["transkript"] = " ".join(
            w["word"] for w in words if span["t0"] <= w["start"] < span["t1"]
        )
        span["hat_wort"] = bool(span["transkript"])
        span["verworfen"] = (
            "zu kurz" if span["zu_kurz"] else
            "zu lang" if span["zu_lang"] else
            "kein Wort erkannt" if words and not span["hat_wort"] else None
        )
        if span["verworfen"] is None:
            cond = span["condition"]
            counts[cond] = counts.get(cond, 0) + 1
            name = f"{cond}_{counts[cond]:03d}_{int(span['t0'] * 1000):07d}.wav"
            a, b = int(span["t0"] * rate), int(span["t1"] * rate)
            with wave.open(str(OUT / name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(samples[a:b].tobytes())
            span["datei"] = name
            kept += 1
        manifest.append(span)

    (OUT / "manifest.json").write_text(json.dumps({
        "take": wav.name, "rate": rate, "schwellen": {
            "rms_floor": RMS_FLOOR, "min_s": MIN_SPEECH_S, "max_s": MAX_SPEECH_S,
        },
        "transkribiert_mit": MODEL.name if words else None,
        "behalten": kept, "gefunden": len(spans),
        "abschnitte": manifest,
    }, indent=1, ensure_ascii=False))

    print(f"\n  behalten {kept} von {len(spans)}")
    erwartet = {c["condition"]: c["expected"] for c in cues}
    for cond in sorted(set(erwartet) | set(counts)):
        soll, ist = erwartet.get(cond, 0), counts.get(cond, 0)
        flag = "" if ist == soll else f"   <== erwartet {soll}"
        print(f"    {cond:14s} {ist:3d}{flag}")
    verworfen = [s for s in manifest if s["verworfen"]]
    if verworfen:
        print("  verworfen:")
        grund = {}
        for s in verworfen:
            grund[s["verworfen"]] = grund.get(s["verworfen"], 0) + 1
        for g, n in grund.items():
            print(f"    {n:3d}x {g}")
    print(f"\n  {OUT / 'manifest.json'}")
    print(f"  Naechster Schritt:\n    {HERE / 'venv/bin/python'} {HERE / 'evaluate.py'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
