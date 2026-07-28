#!/usr/bin/env python3
"""Isolierter STT-Worker fuer die T-1.2-Messung."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import wave
from pathlib import Path

import numpy as np
import onnx_asr


def read_pcm16(path: Path, seconds: float | None = None) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError(f"Erwartet wird Mono-PCM16: {path}")
        sample_rate = wav.getframerate()
        frames = wav.getnframes()
        if seconds is not None:
            frames = min(frames, round(seconds * sample_rate))
        samples = np.frombuffer(wav.readframes(frames), dtype="<i2").astype(np.float32)
    return samples / 32768.0, sample_rate


def normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, ref_word in enumerate(reference, start=1):
        current = [row]
        for column, hyp_word in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (ref_word != hyp_word),
                )
            )
        previous = current
    return previous[-1]


def timed_recognize(model: object, waveform: np.ndarray, sample_rate: int, language: str) -> tuple[float, str]:
    started = time.perf_counter_ns()
    text = model.recognize(waveform, sample_rate=sample_rate, language=language)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return elapsed_ms, text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--english-0", type=Path, required=True)
    parser.add_argument("--english-1", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--german", type=Path, required=True)
    parser.add_argument("--steady-n", type=int, default=12)
    args = parser.parse_args()

    providers = [
        ("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}),
        "CPUExecutionProvider",
    ]
    model = onnx_asr.load_model(
        "onnx-community/whisper-base",
        path=args.model_dir,
        quantization="fp16",
        providers=providers,
    )
    active_providers = {
        "encoder": model.asr._encoder.get_providers(),
        "decoder": model.asr._decoder.get_providers(),
    }
    if active_providers["encoder"][0] != "CUDAExecutionProvider":
        raise RuntimeError(f"Encoder nicht auf CUDA: {active_providers}")
    if active_providers["decoder"][0] != "CUDAExecutionProvider":
        raise RuntimeError(f"Decoder nicht auf CUDA: {active_providers}")

    five_seconds, sample_rate = read_pcm16(args.english_0, seconds=5.0)
    if five_seconds.size != sample_rate * 5:
        raise RuntimeError("Die englische Messdatei ist kuerzer als 5,0 Sekunden")

    print("BENCH_START", flush=True)
    cold_ms, five_second_text = timed_recognize(model, five_seconds, sample_rate, "en")
    steady_ms = [
        timed_recognize(model, five_seconds, sample_rate, "en")[0]
        for _ in range(args.steady_n)
    ]

    english_hypotheses = [
        model.recognize(args.english_0, language="en"),
        model.recognize(args.english_1, language="en"),
    ]
    german_hypothesis = model.recognize(args.german, language="de")

    references: list[str] = []
    for line in args.transcript.read_text(encoding="utf-8").splitlines():
        _, reference = line.split(" ", maxsplit=1)
        references.append(reference)
    ref_words = [word for text in references for word in normalize_words(text)]
    hyp_words = [word for text in english_hypotheses for word in normalize_words(text)]
    errors = edit_distance(ref_words, hyp_words)

    result = {
        "engine": "onnx-asr 0.12.0",
        "model": "onnx-community/whisper-base, split ONNX, fp16",
        "provider": "CUDAExecutionProvider (onnxruntime-gpu 1.27.0)",
        "active_providers": active_providers,
        "first_infer_cold_ms": round(cold_ms, 3),
        "steady_ms_p50": round(float(np.percentile(steady_ms, 50)), 3),
        "steady_ms_p95": round(float(np.percentile(steady_ms, 95)), 3),
        "steady_samples_ms": [round(value, 3) for value in steady_ms],
        "n": args.steady_n,
        "utterance_s": five_seconds.size / sample_rate,
        "five_second_text": five_second_text,
        "english_references": references,
        "english_hypotheses": english_hypotheses,
        "wer_en": round(errors / len(ref_words), 6),
        "wer_en_errors": errors,
        "wer_en_reference_words": len(ref_words),
        "german_hypothesis": german_hypothesis,
        "steady_mean_ms": round(statistics.fmean(steady_ms), 3),
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
