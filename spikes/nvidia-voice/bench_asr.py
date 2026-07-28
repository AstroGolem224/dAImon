#!/usr/bin/env python3
"""Arm A -- NVIDIA-ASR gegen die sherpa-Grundlinie, auf demselben Audiomaterial.

Zwei Engines hinter einer Schnittstelle:
  onnx-asr : Parakeet / Canary auf onnxruntime-gpu 1.27.0 (der in T-1.2 belegte Pfad)
  sherpa   : Grundlinie, damit der Vergleich im selben results.json steht

Beispiel:
  ./venv/bin/python bench_asr.py --engine onnx-asr --model nemo-parakeet-tdt-0.6b-v3
  ./venv/bin/python bench_asr.py --engine sherpa   --model whisper-small
"""
import argparse
import pathlib
import sys
import time
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import VramWatch, append_result, percentile, wer  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
TESTSET = HERE / "samples" / "testset"

LICENSES = {
    "nemo-parakeet-tdt-0.6b-v3": "CC-BY-4.0",
    "nemo-parakeet-tdt-0.6b-v2": "CC-BY-4.0",
    "nemo-canary-1b-v2": "CC-BY-4.0",
}

# Gemessen an onnx-asr 0.12.0: canary-180m-flash ist dort NICHT dabei, obwohl es
# auf HF liegt. Wer es will, braucht den NeMo-Weg aus Arm B -- also Torch, also
# eine zweite schwere Abhaengigkeit fuer ein Modell, das nur schneller waere.


def load_testset():
    wavs = sorted(TESTSET.glob("*.wav"))
    if not wavs:
        sys.exit(f"Kein Testmaterial in {TESTSET}. Erst make_testset.py laufen lassen.")
    items = []
    for w in wavs:
        ref = w.with_suffix(".txt")
        if not ref.exists():
            sys.exit(f"Referenztext fehlt zu {w.name}")
        with wave.open(str(w)) as f:
            secs = f.getnframes() / f.getframerate()
        items.append((w, ref.read_text().strip(), secs))
    return items


def make_onnx_asr(model: str):
    import onnx_asr
    from onnx_asr.utils import ModelNotSupportedError

    try:
        m = onnx_asr.load_model(model, providers=["CUDAExecutionProvider"])
    except ModelNotSupportedError:
        sys.exit(f"onnx-asr kennt '{model}' nicht. Bekannt sind u.a.: "
                 "nemo-parakeet-tdt-0.6b-v3, nemo-canary-1b-v2. "
                 "Alles andere braucht den NeMo-Weg.")
    return lambda path: m.recognize(str(path)), "onnxruntime-gpu CUDAExecutionProvider"


def make_sherpa(model_dir: pathlib.Path):
    import sherpa_onnx

    # fp32 ausdruecklich, nicht int8 -- die Grundlinie soll nicht zufaellig
    # quantisiert sein, je nachdem wie glob sortiert.
    enc = next(p for p in sorted(model_dir.glob("*encoder*.onnx")) if ".int8." not in p.name)
    dec = next(p for p in sorted(model_dir.glob("*decoder*.onnx")) if ".int8." not in p.name)
    rec = sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=str(enc), decoder=str(dec),
        tokens=str(model_dir / (enc.name.split("-encoder")[0] + "-tokens.txt")),
        language="de", num_threads=4, provider="cpu",
    )

    def run(path):
        import numpy as np
        with wave.open(str(path)) as f:
            rate = f.getframerate()
            pcm = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
        s = rec.create_stream()
        s.accept_waveform(rate, pcm.astype("float32") / 32768.0)
        rec.decode_stream(s)
        return s.result.text

    return run, "sherpa-onnx Whisper CPU, 4 Threads"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["onnx-asr", "sherpa"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-dir", type=pathlib.Path,
                    help="nur fuer --engine sherpa: Verzeichnis des Whisper-Modells")
    ap.add_argument("--runs", type=int, default=20)
    args = ap.parse_args()

    items = load_testset()
    idle = VramWatch().idle

    t0 = time.perf_counter()
    if args.engine == "onnx-asr":
        recognize, backend = make_onnx_asr(args.model)
    else:
        recognize, backend = make_sherpa(args.model_dir or (HERE / "models" / args.model))
    recognize(items[0][0])                       # Aufwaermen zaehlt zum Kaltstart
    cold_ms = (time.perf_counter() - t0) * 1000

    # Genauigkeit ueber die ganze Testmenge
    errs = [wer(ref, recognize(w)) for w, ref, _ in items]
    mean_wer = sum(errs) / len(errs)

    # Latenz an einer Aeusserung, wiederholt -- nicht ueber die Testmenge gemittelt,
    # sonst mischt sich die Laengenstreuung in die p95-Zahl.
    probe = min(items, key=lambda it: abs(it[2] - 5.0))
    print(f"Latenzprobe: {probe[0].name} ({probe[2]:.1f} s)")
    lat = []
    with VramWatch() as w:
        for _ in range(args.runs):
            t = time.perf_counter()
            recognize(probe[0])
            lat.append((time.perf_counter() - t) * 1000)

    p95 = percentile(lat, 95)
    append_result({
        "arm": "A-asr", "model": args.model,
        "license": LICENSES.get(args.model, "siehe Modellkarte"),
        "gated": False, "loaded": True, "backend": backend,
        "cold_start_ms": round(cold_ms, 1),
        "p50_ms": percentile(lat, 50), "p95_ms": p95,
        "ttfa_ms": None, "ttfa_reason": "Offline-Erkennung, kein Streaming in diesem Lauf",
        "rtf": round((sum(lat) / len(lat) / 1000) / probe[2], 4),
        "vram_idle_mb": idle, "vram_peak_mb": w.peak, "vram_after_exit_mb": None,
        "wer": round(mean_wer, 4),
        "audio_source": "synthetisch (sherpa-VITS thorsten) -- WER nur relativ vergleichbar",
        "n": args.runs,
        "verdict": "gemessen, Bewertung in NOTES.md",
    })
    print(f"WER {mean_wer:.3f} ueber {len(items)} Saetze, p95 {p95} ms")


if __name__ == "__main__":
    main()
