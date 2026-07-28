#!/usr/bin/env python3
"""Arm B -- Magpie-TTS auf sm_120. Erste Frage ist nicht die Latenz, sondern ob
es ueberhaupt laedt: die Riva-Release-Notes nennen Magpie Multilingual als auf
Blackwell nicht unterstuetzt. Das betrifft den NIM-Container; ob es fuer NeMo
direkt gilt, ist offen und genau der Grund fuer diesen Lauf.

Zwei Wege, in dieser Reihenfolge:
  1. In-Process ueber die NeMo-Python-API -> Wiederholungslaeufe moeglich
  2. Ersatzweg ueber examples/tts/magpietts_inference.py als Unterprozess
     -> nur ein Kaltlauf, n=1, und das steht dann auch so im verdict

Fehlschlag ist ein Ergebnis. Das Skript schreibt in beiden Faellen eine Zeile.
"""
import argparse
import pathlib
import subprocess
import sys
import time
import traceback
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import VramWatch, append_result, percentile  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "samples" / "magpie"
CODEC = "nvidia/nemo-nano-codec-22khz-1.89kbps-21.5fps"

# ~25 Woerter, derselbe Satzbau wie die Grundlinien-Probe in make_testset.py
PROBE = ("Die Sitzung wartet seit vier Minuten auf eine Freigabe und der "
         "Durchlauf bricht ab wenn niemand bis achtzehn Uhr bestaetigt")

LICENSE = "NVIDIA Open Model License, gated -- Zustimmung nennt non-commercial"


def wav_secs(path: pathlib.Path):
    """Beweis, dass wirklich Audio entstanden ist. Ein Exitcode 0 reicht nicht."""
    if not path.exists() or path.stat().st_size < 1024:
        return 0.0
    with wave.open(str(path)) as f:
        return f.getnframes() / f.getframerate()


def try_inprocess(checkpoint: str, lang: str, speaker: int):
    """Gibt (synth_fn, klassenname) zurueck oder None. Die Klassennamen wandern
    zwischen NeMo-Versionen, deshalb wird geraten statt hart importiert."""
    import importlib

    for mod, cls in [
        ("nemo.collections.tts.models", "MagpieTTS_Model"),
        ("nemo.collections.tts.models", "MagpieTTSModel"),
        ("nemo.collections.tts.models.magpietts", "MagpieTTSModel"),
    ]:
        try:
            klass = getattr(importlib.import_module(mod), cls)
        except (ImportError, AttributeError):
            continue
        model = klass.from_pretrained(checkpoint).eval().cuda()

        def synth(text, out: pathlib.Path, _m=model):
            _m.synthesize_to_file(text, out, language=lang, speaker=speaker)

        return synth, f"NeMo in-process {mod}.{cls}"
    return None


def run_subprocess(nemo_repo: pathlib.Path, checkpoint: str, out: pathlib.Path,
                   text: str, lang: str, speaker: int):
    script = nemo_repo / "examples" / "tts" / "magpietts_inference.py"
    if not script.exists():
        sys.exit(f"NeMo-Beispielskript fehlt: {script}\nErst setup.sh mit --arm-b laufen lassen.")
    manifest = OUT / "manifest.json"
    manifest.write_text(
        '{"text": %s, "language": "%s", "speaker": %d, "audio_filepath": "%s"}\n'
        % (repr(text).replace("'", '"'), lang, speaker, out)
    )
    cmd = [sys.executable, str(script), "--nemo_files", checkpoint,
           "--codecmodel_path", CODEC, "--manifest_path", str(manifest),
           "--out_dir", str(OUT)]
    print(" ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout[-2000:], p.stderr[-4000:], sep="\n", file=sys.stderr)
    return p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", default="nvidia/magpie_tts_multilingual_357m")
    ap.add_argument("--nemo-repo", type=pathlib.Path, default=HERE / "NeMo")
    ap.add_argument("--lang", default="de")
    ap.add_argument("--speaker", type=int, default=0)
    ap.add_argument("--speakers", type=int, default=5,
                    help="so viele Sprecher-Identitaeten nach samples/ ausgeben")
    ap.add_argument("--runs", type=int, default=20)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    idle = VramWatch().idle
    row = {
        "arm": "B-tts", "model": args.checkpoint, "license": LICENSE, "gated": True,
        "ttfa_ms": None,
        "ttfa_reason": "NeMo synthetisiert am Stueck; ohne Streaming-Schleife kein echtes TTFA",
        "vram_idle_mb": idle, "vram_after_exit_mb": None, "wer": None,
        "audio_source": "n/a", "n": 0,
    }

    try:
        t0 = time.perf_counter()
        got = try_inprocess(args.checkpoint, args.lang, args.speaker)
    except Exception:
        traceback.print_exc()
        got = None

    if got is None:
        # Ersatzweg: ein Kaltlauf, ehrlich als solcher markiert.
        print("Keine nutzbare NeMo-API gefunden -- Ersatzweg ueber das Beispielskript.")
        out = OUT / "probe_subprocess.wav"
        t0 = time.perf_counter()
        with VramWatch() as w:
            rc = run_subprocess(args.nemo_repo, args.checkpoint, out, PROBE,
                                args.lang, args.speaker)
        ms = (time.perf_counter() - t0) * 1000
        secs = wav_secs(out)
        row.update({
            "loaded": secs > 0.5, "backend": "NeMo magpietts_inference.py (Unterprozess)",
            "cold_start_ms": round(ms, 1), "p50_ms": None, "p95_ms": None,
            "rtf": round((ms / 1000) / secs, 4) if secs else None,
            "vram_peak_mb": w.peak, "n": 1,
            "verdict": ("nur Kaltlauf inkl. Modellladen -- nicht mit der Grundlinie "
                        "vergleichbar" if secs > 0.5 else
                        f"FEHLGESCHLAGEN, rc={rc}, Audio {secs:.2f} s"),
        })
        append_result(row)
        return

    synth, backend = got
    probe_out = OUT / "warmup.wav"
    synth(PROBE, probe_out)
    cold_ms = (time.perf_counter() - t0) * 1000
    secs = wav_secs(probe_out)
    if secs < 0.5:
        row.update({"loaded": False, "backend": backend, "cold_start_ms": round(cold_ms, 1),
                    "p50_ms": None, "p95_ms": None, "rtf": None,
                    "vram_peak_mb": VramWatch().idle, "n": 0,
                    "verdict": f"laedt, synthetisiert aber nichts ({secs:.2f} s Audio)"})
        append_result(row)
        return

    for sid in range(args.speakers):                 # zum Anhoeren, nicht zum Messen
        try:
            synth(PROBE, OUT / f"speaker_{sid}_{args.lang}.wav")
        except Exception as e:
            print(f"Sprecher {sid} nicht verfuegbar: {e}")
            break

    for _ in range(2):
        synth(PROBE, probe_out)
    lat = []
    with VramWatch() as w:
        for _ in range(args.runs):
            t = time.perf_counter()
            synth(PROBE, probe_out)
            lat.append((time.perf_counter() - t) * 1000)

    row.update({
        "loaded": True, "backend": backend, "cold_start_ms": round(cold_ms, 1),
        "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95),
        "rtf": round((sum(lat) / len(lat) / 1000) / secs, 4),
        "vram_peak_mb": w.peak, "n": args.runs,
        "verdict": "gemessen, Klangurteil in NOTES.md",
    })
    append_result(row)
    print(f"Samples zum Anhoeren: {OUT}")


if __name__ == "__main__":
    main()
