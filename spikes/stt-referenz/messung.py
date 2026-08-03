#!/usr/bin/env python3
"""Traegt sherpa-onnx mit parakeet-tdt-0.6b-v3 den STT-Pfad? T-3.8, Vormessung.

Zwei Fragen, bevor `daimon/gpu/stt.py` entsteht:

  1. **Reicht die CPU?** Wenn ja, entfallen `onnxruntime-gpu` (rund 2 GB), die
     drei Pinning-Kriterien des Plans UND das GPU-Gate aus T-3.7 -- der STT haette
     dann 0 VRAM und koennte laufen, waehrend ein Spiel die Karte haelt. Das ist
     kein Sparen an der Abnahme, sondern ein besserer Erfuellungsweg desselben
     Ziels ("Audio rein, Text raus, VRAM danach frei" ist trivial erfuellt, wenn
     nie VRAM belegt wird).
  2. **Wie gut versteht es Deutsch?** Hier nur gegen SYNTHETISCHE Sprache, denn
     Matthias' Aufnahmen sind noch nicht da. Das ist ausdruecklich NICHT die
     Grundlinie: eine WER gegen den eigenen TTS misst, wie gut das STT den TTS
     versteht. Sie ist eine Vorentscheidung ueber den Pfad und spaeter der
     Regressionstest (gleiche Eingabe, gleiche Ausgabe).

Aufruf:

    python3 spikes/stt-referenz/messung.py aufnahmen [threads]   # die Grundlinie
    python3 spikes/stt-referenz/messung.py synthetisch [threads] # Regressionslauf

Der synthetische Lauf ist NICHT die Grundlinie, und das ist gemessen, nicht
vermutet: thorsten ist eine deutsche Stimme und spricht die englischen
Lehnwoerter deutsch aus -- "Hub" wird "Hoop", "Overlay" wird "Oberley", "Build"
wird "wohlt". Das STT hoert dabei korrekt, WAS gesagt wurde; die Fehler sind die
des Sprechers. Und genau diese Woerter sind das Vokabular dieses Pets. Der
synthetische Lauf taugt deshalb als Regressionstest (gleiche Eingabe, gleiche
Ausgabe) und fuer nichts anderes.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

HIER = Path(__file__).resolve().parent
REPO = HIER.parents[1]
MODELL = HIER / "models" / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
STIMME = REPO / "spikes/nvidia-voice/models/vits-piper-de_DE-thorsten-high"


# Zahlwoerter auf Ziffern. Das Modell schreibt "19", die Referenz sagt
# "neunzehn" -- ohne diese Abbildung misst die WER Rechtschreibung statt
# Erkennung. Gemessen am 03.08.: sie druckt die deutsche WER von 16,0 auf
# 14,3 Prozent, und kein einziger dieser "Fehler" war ein missverstandenes Wort.
# Die Richtung ist Wort -> Ziffer, weil Ziffern eindeutig sind: "18.30" und
# "achtzehn uhr dreissig" treffen sich bei "18 30 uhr", "einundzwanzig" bleibt
# als Wort stehen und wird beidseitig gleich behandelt.
ZAHLWORT = {
    "null": "0", "eins": "1", "ein": "1", "eine": "1", "zwei": "2", "drei": "3",
    "vier": "4", "fünf": "5", "sechs": "6", "sieben": "7", "acht": "8",
    "neun": "9", "zehn": "10", "elf": "11", "zwölf": "12", "dreizehn": "13",
    "vierzehn": "14", "fünfzehn": "15", "sechzehn": "16", "siebzehn": "17",
    "achtzehn": "18", "neunzehn": "19", "zwanzig": "20", "dreißig": "30",
    "vierzig": "40", "fünfzig": "50", "sechzig": "60", "hundert": "100",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


def normalisieren(text: str) -> list[str]:
    """Kleinschreibung, Satzzeichen weg, Umlaute BLEIBEN.

    Umlaute nicht zu ae/oe/ue zu falten ist Absicht: ein Modell, das "Aenderung"
    statt "Änderung" schreibt, hat das Wort verstanden, aber nicht die deutsche
    Rechtschreibung -- und genau dieser Unterschied soll sichtbar bleiben, statt
    in der Normalisierung zu verschwinden.
    """
    text = unicodedata.normalize("NFC", text.lower())
    # Bindestriche werden GESCHLOSSEN, nicht zu Leerraum: deutsche Komposita
    # sind ein Wort, und ob man "Rueckkopplungssperren-Nachlauf" oder
    # "Rueckkopplungssperrennachlauf" schreibt, ist Stil und keine Erkennung.
    # Wer den Bindestrich zu Leerraum macht, zaehlt aus einem Wort zwei und
    # bestraft das Modell fuer die eigene Schreibweise -- gemessen am 03.08.:
    # zwei von 27 Fehlern in Satz 07 und 13 waren genau das.
    text = text.replace("-", "").replace("\u2011", "")
    behalten = [z if (z.isalnum() or z.isspace()) else " " for z in text]
    return [ZAHLWORT.get(w, w) for w in "".join(behalten).split()]


def wer(referenz: str, erkannt: str) -> tuple[float, int, int]:
    """Wortfehlerrate per Levenshtein. `(rate, fehler, woerter)`."""
    r, h = normalisieren(referenz), normalisieren(erkannt)
    if not r:
        # Stille: jedes Wort ist ein Fehler, und die Rate ist nicht definiert.
        return (float(len(h)), len(h), 0)
    vorher = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        jetzt = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            jetzt[j] = min(vorher[j] + 1, jetzt[j - 1] + 1,
                           vorher[j - 1] + (rw != hw))
        vorher = jetzt
    return (vorher[-1] / len(r), vorher[-1], len(r))


def aufnahme_samples(datei: Path) -> tuple["object", int]:
    """WAV lesen, ohne Vorbehandlung. s16 -> float32 in [-1, 1]."""
    import wave

    import numpy as np
    with wave.open(str(datei), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise SystemExit(f"{datei.name}: erwartet 16 bit mono, "
                             f"ist {w.getsampwidth()*8} bit / "
                             f"{w.getnchannels()} Kanal")
        rate = w.getframerate()
        rohdaten = w.readframes(w.getnframes())
    return (np.frombuffer(rohdaten, dtype="<i2").astype("float32") / 32768.0,
            rate)


def main(argv: list[str]) -> int:
    quelle = argv[1] if len(argv) > 1 else "aufnahmen"
    if quelle not in ("aufnahmen", "synthetisch"):
        raise SystemExit(__doc__)
    threads = int(argv[2]) if len(argv) > 2 else 8
    import numpy as np
    import sherpa_onnx
    from daimon.face import tts as T

    if not MODELL.is_dir():
        raise SystemExit(f"Modell fehlt: {MODELL}")

    t0 = time.monotonic()
    erkenner = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(MODELL / "encoder.int8.onnx"),
        decoder=str(MODELL / "decoder.int8.onnx"),
        joiner=str(MODELL / "joiner.int8.onnx"),
        tokens=str(MODELL / "tokens.txt"),
        num_threads=threads,
        provider="cpu",
        model_type="nemo_transducer",
        # Der Plan verlangt "Deutsch und Englisch". v3 deckt 25 europaeische
        # Sprachen ab und braucht dafuer KEINEN Sprachschalter -- geprueft wird
        # das unten an den beiden englischen Saetzen.
    )
    ladezeit_ms = (time.monotonic() - t0) * 1000

    sprecher = None
    manifest = {}
    if quelle == "synthetisch":
        sprecher = T.Sprecher(hub_socket="/nicht/da", modell_dir=str(STIMME),
                              threads=8)
        sprecher.laden()
    else:
        pfad = HIER / "aufnahmen" / "manifest.json"
        if not pfad.exists():
            raise SystemExit(f"Keine Aufnahmen ({pfad}). Erst einsprechen: "
                             f"aufnehmen.py")
        manifest = json.loads(pfad.read_text())["aufnahmen"]

    saetze = json.loads((HIER / "saetze.json").read_text())["saetze"]
    ergebnisse = []
    for satz in saetze:
        if quelle == "synthetisch":
            rate = sprecher.samplerate
            if satz["sprache"] == "stille":
                # 3 s Nullen: die Halluzinationskontrolle, hier synthetisch.
                samples = np.zeros(3 * rate, dtype="float32")
            else:
                audio = sprecher._tts.generate(satz["text"], sid=0, speed=1.0)
                samples = np.asarray(audio.samples, dtype="float32")
        else:
            eintrag = manifest.get(satz["id"])
            if eintrag is None:
                print(f"  {satz['id']}  FEHLT in den Aufnahmen")
                continue
            samples, rate = aufnahme_samples(
                HIER / "aufnahmen" / eintrag["datei"])
        dauer_s = len(samples) / rate

        t1 = time.monotonic()
        strom = erkenner.create_stream()
        strom.accept_waveform(rate, samples)
        erkenner.decode_stream(strom)
        latenz_ms = (time.monotonic() - t1) * 1000
        text = strom.result.text.strip()

        rate, fehler, woerter = wer(satz["text"], text)
        ergebnisse.append({
            "id": satz["id"], "sprache": satz["sprache"],
            "audio_s": round(dauer_s, 2), "latenz_ms": round(latenz_ms, 1),
            "rtf": round(latenz_ms / 1000 / dauer_s, 3) if dauer_s else None,
            "referenz": satz["text"], "erkannt": text,
            "wer": round(rate, 4), "fehler": fehler, "woerter": woerter,
        })
        marke = "STILLE" if satz["sprache"] == "stille" else f"WER {rate:6.1%}"
        print(f"  {satz['id']}  {dauer_s:5.2f}s  {latenz_ms:7.1f}ms  "
              f"rtf {latenz_ms/1000/dauer_s:5.3f}  {marke}")
        if satz["sprache"] == "stille":
            print(f"      auf Stille erkannt: {text!r}")
        elif rate > 0:
            print(f"      ref: {satz['text']}")
            print(f"      ist: {text}")

    sprachig = [e for e in ergebnisse if e["sprache"] != "stille"]
    de = [e for e in sprachig if e["sprache"] == "de"]
    en = [e for e in sprachig if e["sprache"] == "en"]
    lat = sorted(e["latenz_ms"] for e in sprachig)
    fuenf = [e for e in sprachig if 3.5 <= e["audio_s"] <= 7.0]
    stille = next(e for e in ergebnisse if e["sprache"] == "stille")

    def gesamt_wer(gruppe):
        f = sum(e["fehler"] for e in gruppe)
        w = sum(e["woerter"] for e in gruppe)
        return round(f / w, 4) if w else None

    zusammen = {
        "modell": MODELL.name, "provider": "cpu", "threads": threads,
        "ladezeit_ms": round(ladezeit_ms, 1),
        "quelle": ("AUFNAHMEN (Matthias, eigenes Mikrofon) -- die Grundlinie"
                   if quelle == "aufnahmen"
                   else "SYNTHETISCH (piper/thorsten) -- NICHT die Grundlinie"),
        "n": len(sprachig),
        "wer_de": gesamt_wer(de), "wer_en": gesamt_wer(en),
        "wer_gesamt": gesamt_wer(sprachig),
        "latenz_median_ms": statistics.median(lat),
        "latenz_p95_ms": lat[int(0.95 * (len(lat) - 1))],
        "rtf_median": round(statistics.median(e["rtf"] for e in sprachig), 3),
        "latenz_5s_median_ms": (round(statistics.median(
            e["latenz_ms"] for e in fuenf), 1) if fuenf else None),
        "auf_stille_erkannt": stille["erkannt"],
        "halluziniert_auf_stille": bool(stille["erkannt"]),
        "ergebnisse": ergebnisse,
    }
    (HIER / "runs").mkdir(exist_ok=True)
    ziel = HIER / "runs" / f"{quelle}-cpu-{threads}threads.json"
    ziel.write_text(json.dumps(zusammen, indent=2, ensure_ascii=False) + "\n")

    print(f"\nModell geladen in {ladezeit_ms:.0f} ms, {threads} Threads, CPU")
    print(f"WER deutsch {zusammen['wer_de']:.1%}, englisch "
          f"{zusammen['wer_en']:.1%}   [{zusammen['quelle']}]")
    print(f"Latenz Median {zusammen['latenz_median_ms']:.0f} ms, p95 "
          f"{zusammen['latenz_p95_ms']:.0f} ms, RTF-Median "
          f"{zusammen['rtf_median']}")
    print(f"5-Sekunden-Aeusserung: {zusammen['latenz_5s_median_ms']} ms")
    print(f"Auf Stille: {zusammen['auf_stille_erkannt']!r}")
    print(f"Geschrieben: {ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
