#!/usr/bin/env python3
"""T-1.1 — Positivkontrolle: funktioniert der Aufbau ueberhaupt?

    venv/bin/python control.py

Diese Kontrolle haette am Anfang stehen muessen. Ohne sie ist "0 Treffer" nicht
interpretierbar: es kann heissen, dass das Wort schwierig ist, dass die
Aufnahme taugt nichts, oder dass der Spotter falsch aufgesetzt ist. Wir haben
zwei Aufnahmedurchlaeufe und drei Werkzeugreparaturen lang geraten, welches
davon es ist.

Drei Stufen, jede beweist etwas anderes:

  1. Modell-eigene Testdateien mit Modell-eigenen Keywords.
     Beweist: sherpa-onnx laedt, das Format der Keyword-Datei stimmt, die
     Erkennungsschleife funktioniert.
  2. Selbst tokenisierte Woerter gegen dieselben Testdateien.
     Beweist: unser Weg von einem Wort zu einer Keyword-Zeile ist richtig --
     das ist die Stufe, an der ein handgeschriebenes keywords.txt scheitern
     wuerde.
  3. Woerter, die im Audio nicht vorkommen.
     Beweist: es feuert nicht einfach auf alles.

Schlaegt eine Stufe fehl, ist jedes Urteil ueber Matthias' Stimme wertlos.
"""

import sys
import wave
from pathlib import Path

import numpy as np
import sentencepiece as spm
import sherpa_onnx

HERE = Path(__file__).resolve().parent
MODEL = HERE / "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
WAVS = MODEL / "test_wavs"
BOOST = 1.5
THRESHOLD = 0.25


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        return w.getframerate(), np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def spotter(keywords_file):
    return sherpa_onnx.KeywordSpotter(
        tokens=str(MODEL / "tokens.txt"),
        encoder=str(MODEL / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=str(MODEL / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=str(MODEL / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        keywords_file=str(keywords_file), num_threads=1, provider="cpu",
    )


def detect(sp, samples, rate, chunk=3200):
    st = sp.create_stream()
    out = []
    for i in range(0, len(samples), chunk):
        st.accept_waveform(rate, samples[i:i + chunk])
        while sp.is_ready(st):
            sp.decode_stream(st)
            if r := sp.get_result(st):
                out.append(r)
                sp.reset_stream(st)
    st.accept_waveform(rate, np.zeros(8000, dtype=np.float32))
    while sp.is_ready(st):
        sp.decode_stream(st)
        if r := sp.get_result(st):
            out.append(r)
            sp.reset_stream(st)
    return out


def tokenize(words, path):
    bpe = spm.SentencePieceProcessor(model_file=str(MODEL / "bpe.model"))
    path.write_text("\n".join(
        f"{' '.join(bpe.encode(w, out_type=str))} :{BOOST} #{THRESHOLD}" for w in words
    ) + "\n")
    return path


def main():
    tmp = HERE / ".kw_control.txt"
    ok = True

    print("STUFE 1 — Modell-eigene Testdateien, Modell-eigene Keywords")
    erwartet = {"0.wav": {"LIGHT UP"}, "1.wav": {"LOVELY CHILD", "FOREVER"}}
    sp = spotter(WAVS / "test_keywords.txt")
    for name, want in erwartet.items():
        rate, samples = read_wav(WAVS / name)
        got = set(detect(sp, samples, rate))
        good = got == want
        ok &= good
        print(f"  {name}: {sorted(got)}  {'ok' if good else f'ERWARTET {sorted(want)}'}")

    print("\nSTUFE 2 — selbst tokenisierte Woerter, dieselben Testdateien")
    rate, samples = read_wav(WAVS / "0.wav")
    words = ["YELLOW LAMPS", "NIGHTFALL", "BROTHELS"]
    got = set(detect(spotter(tokenize(words, tmp)), samples, rate))
    good = got == set(words)
    ok &= good
    print(f"  0.wav: {sorted(got)}  {'ok' if good else f'ERWARTET {sorted(words)}'}")

    print("\nSTUFE 3 — Woerter, die dort nicht vorkommen")
    absent = ["EMBER SHARD", "PURPLE ELEPHANT"]
    got = detect(spotter(tokenize(absent, tmp)), samples, rate)
    good = not got
    ok &= good
    print(f"  0.wav: {got}  {'ok' if good else 'FALSCHTREFFER'}")

    tmp.unlink(missing_ok=True)
    print()
    if ok:
        print("  AUFBAU IN ORDNUNG. Bleibt ein Ergebnis aus, liegt es an der")
        print("  Aufnahme oder am Wort -- nicht am Werkzeug.")
        return 0
    print("  AUFBAU KAPUTT. Jedes Urteil ueber eine Stimme waere wertlos,")
    print("  bevor das hier steht.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
