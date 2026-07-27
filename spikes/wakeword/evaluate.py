#!/usr/bin/env python3
"""T−1.1 — wertet die Aufnahmen aus und rechnet FRR und FAR.

    venv/bin/python evaluate.py

Fährt den sherpa-onnx-KWS über alle Positivproben (Falsch-Negativ-Rate) und
über das Hintergrundmaterial (Falsch-Positiv-Rate je Stunde), und zwar über
ein Raster von Schwellenwerten. Schreibt results.json.

Zielwerte aus dem Plan: FRR < 10 %, FAR < 1/h.
"""

import json
import sys
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

HERE = Path(__file__).parent
MODEL = next(HERE.glob("sherpa-onnx-kws-zipformer-*"), None)
POS = HERE / "samples" / "positive"
BG = HERE / "samples" / "background"

# Schwellenraster. sherpa-onnx erwartet die Schwelle je Keyword in der
# keywords-Datei; wir erzeugen sie je Durchlauf neu.
THRESHOLDS = [0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
BOOST = 1.5


def read_wav(p: Path):
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == 16000, f"{p}: {w.getframerate()} Hz statt 16000"
        assert w.getnchannels() == 1, f"{p}: {w.getnchannels()} Kanäle statt 1"
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def build_spotter(threshold: float):
    kwfile = HERE / f".kw_{threshold}.txt"
    lines = []
    for tok in (HERE / "keywords.txt").read_text().splitlines():
        tok = tok.strip()
        if tok:
            lines.append(f"{tok} :{BOOST} #{threshold}")
    kwfile.write_text("\n".join(lines) + "\n")
    return sherpa_onnx.KeywordSpotter(
        tokens=str(MODEL / "tokens.txt"),
        encoder=str(MODEL / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=str(MODEL / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=str(MODEL / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        keywords_file=str(kwfile),
        num_threads=1,
        provider="cpu",
    )


def detections(spotter, samples: np.ndarray, chunk: int = 3200):
    """Zahl der Treffer in einem Signal."""
    st = spotter.create_stream()
    hits = 0
    for i in range(0, len(samples), chunk):
        st.accept_waveform(16000, samples[i:i + chunk])
        while spotter.is_ready(st):
            spotter.decode_stream(st)
            r = spotter.get_result(st)
            if r:
                hits += 1
                spotter.reset_stream(st)
    tail = np.zeros(int(0.5 * 16000), dtype=np.float32)
    st.accept_waveform(16000, tail)
    while spotter.is_ready(st):
        spotter.decode_stream(st)
        if spotter.get_result(st):
            hits += 1
            spotter.reset_stream(st)
    return hits


def main():
    if MODEL is None:
        sys.exit("KWS-Modell fehlt. Erst herunterladen (siehe NOTES.md).")
    pos = sorted(POS.glob("*.wav")) if POS.exists() else []
    bg = sorted(BG.glob("*.wav")) if BG.exists() else []
    if not pos:
        sys.exit("Keine Positivproben. Erst: python3 record.py positive")

    bg_seconds = sum(f.stat().st_size / (16000 * 2) for f in bg)
    print(f"{len(pos)} Positivproben, {bg_seconds/3600:.2f} h Hintergrund\n")

    rows = []
    for th in THRESHOLDS:
        sp = build_spotter(th)
        tp = sum(1 for f in pos if detections(sp, read_wav(f)) > 0)
        frr = 1 - tp / len(pos)

        fa = 0
        for f in bg:
            fa += detections(sp, read_wav(f))
        far = fa / (bg_seconds / 3600) if bg_seconds > 0 else None

        # FRR je Bedingung -- zeigt, welche Situation das Wort killt
        by_cond = {}
        for f in pos:
            cond = f.name.split("_")[0]
            d = by_cond.setdefault(cond, [0, 0])
            d[1] += 1
            if detections(sp, read_wav(f)) > 0:
                d[0] += 1
        cond_frr = {c: round(1 - hit / n, 3) for c, (hit, n) in by_cond.items()}

        row = {"threshold": th, "trials": len(pos), "true_positives": tp,
               "false_rejects": len(pos) - tp, "frr": round(frr, 3),
               "background_hours": round(bg_seconds / 3600, 3),
               "false_accepts": fa,
               "far_per_hour": round(far, 3) if far is not None else None,
               "frr_by_condition": cond_frr}
        rows.append(row)
        far_s = f"{far:.2f}/h" if far is not None else "n/a"
        print(f"  Schwelle {th:.2f}  FRR {frr*100:5.1f} %   FAR {far_s}")

    ok = [r for r in rows
          if r["frr"] < 0.10 and r["far_per_hour"] is not None
          and r["far_per_hour"] < 1.0]
    best = min(ok, key=lambda r: r["frr"]) if ok else None

    res = {
        "spike": "T-1.1",
        "word": "Embershard (tokenisiert als EMBER SHARD -- als ein Wort schlaegt es nie an)",
        "engine": "sherpa-onnx-kws-zipformer-gigaspeech-3.3M int8",
        "tokenization": (HERE / "keywords.txt").read_text().strip().splitlines(),
        "rows": rows,
        "verdict": "pass" if best else "fail",
        "chosen_threshold": best["threshold"] if best else None,
        "decision": (
            f"Tauglich bei Schwelle {best['threshold']}: FRR {best['frr']*100:.1f} %, "
            f"FAR {best['far_per_hour']:.2f}/h."
            if best else
            "Kein Schwellenwert erreicht FRR < 10 % UND FAR < 1/h. "
            "Plan B: livekit-wakeword trainiert ein deutsches Modell. "
            "Plan C: nur Push-to-Talk."
        ),
        "blocking": True,
    }
    (HERE / "results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    for f in HERE.glob(".kw_*.txt"):
        f.unlink()
    print("\n" + res["decision"])
    print(f"\n→ {HERE/'results.json'}")


if __name__ == "__main__":
    main()
