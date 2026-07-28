#!/usr/bin/env python3
"""Erzeugt das deutsche Testmaterial fuer Arm A und misst dabei die
sherpa-VITS-Grundlinie fuer Arm B mit. Ein Lauf, zwei Zwecke.

WICHTIG fuer die Auswertung: das Audio ist synthetisch. Die WER-Zahlen aus
bench_asr.py sind damit **relativ** zwischen den Engines vergleichbar, aber
keine absoluten WER-Werte -- TTS-Audio ist sauberer als jedes Mikrofon.
Wer absolute Zahlen braucht, spricht die Saetze aus SENTENCES selbst ein und
legt sie als NNN.wav + NNN.txt nach samples/testset/.
"""
import argparse
import pathlib
import sys
import time
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import VramWatch, append_result, percentile  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "samples" / "testset"

# 20 Saetze, ~25 Woerter im Schnitt. Absichtlich mit Fachbegriffen, Zahlen und
# Umlauten -- das ist der Wortschatz, den dAImon tatsaechlich hoeren wird.
SENTENCES = [
    "Zeig mir bitte was gerade auf dem rechten Bildschirm steht",
    "Die Sitzung wartet seit vier Minuten auf eine Freigabe",
    "Oeffne das Protokoll vom siebenundzwanzigsten Juli",
    "Wie viele Tasks stehen noch im Rueckstand des Projekts",
    "Der Compiler meldet einen Fehler in Zeile zweihundertdreizehn",
    "Bitte fasse die letzten fuenf Nachrichten kurz zusammen",
    "Starte den Dienst neu und pruefe danach den Status",
    "Ich brauche die Zusammenfassung der Aenderungen von gestern Abend",
    "Welche Prozesse belegen gerade den meisten Arbeitsspeicher",
    "Schalte die Aufnahme aus bis ich es wieder erlaube",
    "Der Grafikkartentreiber ist auf Version sechshundertzehn",
    "Uebersetze diesen Absatz ins Englische und behalte den Ton bei",
    "Suche im Archiv nach dem Begriff Herkunftsmarkierung",
    "Sag mir Bescheid sobald der Durchlauf abgeschlossen ist",
    "Das Fenster hat den Fokus verloren waehrend ich getippt habe",
    "Erstelle eine Notiz mit dem Titel offene Fragen zur Architektur",
    "Wie hoch ist die Auslastung der Grafikkarte im Leerlauf",
    "Beende alle Hintergrundschleifen und melde dich danach",
    "Der Schwellenwert liegt bei null Komma acht fuenf",
    "Lies mir vor was in der obersten Zeile des Terminals steht",
]


def load_tts(model_dir: pathlib.Path):
    import sherpa_onnx

    return sherpa_onnx.OfflineTts(
        sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(model_dir / "de_DE-thorsten-high.onnx"),
                    tokens=str(model_dir / "tokens.txt"),
                    data_dir=str(model_dir / "espeak-ng-data"),
                ),
                num_threads=2,
                provider="cpu",
            ),
            max_num_sentences=1,
        )
    )


def write_wav(path: pathlib.Path, samples, rate: int):
    import numpy as np

    pcm = (np.clip(np.asarray(samples), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=pathlib.Path,
                    default=HERE / "models" / "vits-piper-de_DE-thorsten-high")
    ap.add_argument("--runs", type=int, default=20,
                    help="Messlaeufe fuer die Grundlinie, nach dem Aufwaermen")
    args = ap.parse_args()

    if not args.model_dir.exists():
        sys.exit(f"Modell fehlt: {args.model_dir}\nErst setup.sh laufen lassen.")

    OUT.mkdir(parents=True, exist_ok=True)
    idle = VramWatch().idle

    t0 = time.perf_counter()
    tts = load_tts(args.model_dir)
    cold_ms = (time.perf_counter() - t0) * 1000

    # Testmenge schreiben
    audio_secs = 0.0
    for i, text in enumerate(SENTENCES):
        a = tts.generate(text, sid=0, speed=1.0)
        write_wav(OUT / f"{i:03d}.wav", a.samples, a.sample_rate)
        (OUT / f"{i:03d}.txt").write_text(text + "\n")
        audio_secs += len(a.samples) / a.sample_rate
    print(f"{len(SENTENCES)} Dateien nach {OUT} ({audio_secs:.1f} s Audio)")

    # Grundlinie messen: derselbe Satz, wiederholt, nach dem Aufwaermen
    probe = SENTENCES[0]
    for _ in range(3):
        tts.generate(probe, sid=0, speed=1.0)

    lat, rtfs = [], []
    with VramWatch() as w:
        for _ in range(args.runs):
            t = time.perf_counter()
            a = tts.generate(probe, sid=0, speed=1.0)
            ms = (time.perf_counter() - t) * 1000
            lat.append(ms)
            rtfs.append((ms / 1000) / (len(a.samples) / a.sample_rate))

    append_result({
        "arm": "B-tts", "model": "sherpa-vits-de_DE-thorsten-high",
        "license": "Apache-2.0 (Lib) / CC0 (Stimme)", "gated": False,
        "loaded": True, "backend": "sherpa-onnx CPU, 2 Threads",
        "cold_start_ms": round(cold_ms, 1),
        "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95),
        # Grundlinie synthetisiert am Stueck, genau wie Magpie -> kein echtes TTFA.
        "ttfa_ms": None,
        "ttfa_reason": "Batch-Synthese ohne Streaming-Schleife; Wert waere aus p50 geraten",
        "rtf": round(sum(rtfs) / len(rtfs), 4),
        "vram_idle_mb": idle, "vram_peak_mb": w.peak, "vram_after_exit_mb": None,
        "wer": None, "audio_source": "n/a (Erzeuger)", "n": args.runs,
        "verdict": "Grundlinie",
    })


if __name__ == "__main__":
    main()
