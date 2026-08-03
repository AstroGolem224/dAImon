#!/usr/bin/env python3
"""Die 21 Referenzaufnahmen fuer T-3.8 einsprechen. 16 kHz mono, ein Satz je Datei.

Warum von Hand und nicht synthetisch
----------------------------------------------------------------------------
Eine WER gegen synthetische Sprache misst, wie gut das STT den TTS versteht --
nicht, wie gut es Matthias versteht. Entschieden am 03.08.: seine Stimme, sein
Mikrofon, sein Raum. Piper-Synthese kommt zusaetzlich als Regressionstest (immer
gleiche Eingabe, immer gleiche Ausgabe), aber nicht als Grundlinie.

Was hier bewusst NICHT passiert
----------------------------------------------------------------------------
Kein Trimmen, kein Normalisieren, kein Rauschfilter. Die Aufnahme ist die
Messgroesse; wer sie vorbehandelt, misst die Vorbehandlung mit. Aufgezeichnet
wird direkt in 16 kHz mono s16 -- dasselbe Format, in dem der Ringpuffer aus
T-3.3 mitschneidet. Wer in 48 kHz aufnimmt und spaeter resampelt, hat den
Resampler im Messpfad.

Aufruf:

    python3 spikes/stt-referenz/aufnehmen.py            # alle offenen Saetze
    python3 spikes/stt-referenz/aufnehmen.py 07 12      # nur diese wiederholen

Ablauf je Satz: Text lesen, ENTER, sprechen, ENTER. Danach `w` fuer wiederholen,
`a` fuer abspielen, ENTER fuer weiter. Fertige Aufnahmen werden uebersprungen,
der Lauf ist also jederzeit abbrechbar und fortsetzbar.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

HIER = Path(__file__).resolve().parent
SAETZE = HIER / "saetze.json"
AUFNAHMEN = HIER / "aufnahmen"
MANIFEST = AUFNAHMEN / "manifest.json"
RATE = 16000


def pruefe_werkzeuge() -> None:
    for werkzeug in ("pw-record", "pw-cat"):
        if subprocess.run(["sh", "-c", f"command -v {werkzeug}"],
                          capture_output=True).returncode != 0:
            raise SystemExit(f"{werkzeug} fehlt (Paket pipewire-audio).")


def aufnehmen(ziel: Path) -> None:
    """Bis ENTER aufnehmen. Die Dauer kommt aus der DATEI, nicht aus der Uhr --
    eine gemessene Wanduhrzeit enthielte die Reaktionszeit am Tastendruck.

    `pw-record` schreibt selbst einen WAV-Kopf; beendet wird es mit SIGINT, weil
    SIGKILL den Kopf mit der Laengenangabe nicht mehr nachtraegt und die Datei
    dann von manchen Lesern als leer gilt.
    """
    p = subprocess.Popen(
        ["pw-record", f"--rate={RATE}", "--channels=1", "--format=s16",
         str(ziel)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        input("    [aufnehmen ... ENTER beendet] ")
    finally:
        p.send_signal(2)          # SIGINT: Kopf wird noch geschrieben
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def abspielen(datei: Path) -> None:
    subprocess.run(["pw-cat", "--playback", str(datei)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pruefe_datei(datei: Path) -> dict:
    """Format und Inhalt nachsehen, statt der Aufnahme zu glauben.

    Eine WAV-Datei mit 44 Byte ist ein Kopf ohne Ton -- und genau die entsteht,
    wenn das Mikrofon stummgeschaltet ist. Ohne diese Pruefung faellt das erst
    beim Transkribieren auf, dann sind die zwanzig Minuten Sprechen schon vorbei.
    """
    with wave.open(str(datei), "rb") as w:
        rahmen, rate, kanaele, breite = (w.getnframes(), w.getframerate(),
                                         w.getnchannels(), w.getsampwidth())
        rohdaten = w.readframes(min(rahmen, rate * 30))
    # Spitzenamplitude ohne numpy: s16 little endian.
    spitze = 0
    for i in range(0, len(rohdaten) - 1, 2):
        wert = int.from_bytes(rohdaten[i:i + 2], "little", signed=True)
        spitze = max(spitze, abs(wert))
    return {
        "sekunden": round(rahmen / rate, 2) if rate else 0.0,
        "rate": rate, "kanaele": kanaele, "bytes_je_sample": breite,
        "spitze": spitze,
        "sha256": hashlib.sha256(datei.read_bytes()).hexdigest()[:16],
    }


def main(argv: list[str]) -> int:
    pruefe_werkzeuge()
    daten = json.loads(SAETZE.read_text(encoding="utf-8"))
    AUFNAHMEN.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {
        "v": 1, "rate": RATE, "aufnahmen": {}}

    nur = set(argv[1:])
    offen = [s for s in daten["saetze"]
             if (not nur or s["id"] in nur)
             and (s["id"] in nur or s["id"] not in manifest["aufnahmen"])]
    if not offen:
        print("Nichts offen. Alle Saetze sind aufgenommen.")
        print(f"Manifest: {MANIFEST}")
        return 0

    print(f"\n{len(offen)} Aufnahmen. 16 kHz mono, ein Satz je Datei.")
    print("Sprich normal -- nicht besonders deutlich. Gemessen werden soll der "
          "Alltag,\nnicht die Buehnenaussprache.\n")

    for i, satz in enumerate(offen, 1):
        ziel = AUFNAHMEN / f"satz-{satz['id']}.wav"
        print(f"--- {i}/{len(offen)}  (Satz {satz['id']}, {satz['sprache']}) ---")
        if satz["sprache"] == "stille":
            print("    KEINE Sprache. Etwa drei Sekunden Raumstille -- nicht")
            print("    sprechen, nicht huesteln. Das ist die Kontrolle gegen")
            print("    Halluzination.")
        else:
            print(f"    »{satz['text']}«")
        while True:
            input("    ENTER zum Starten ")
            aufnehmen(ziel)
            try:
                pruefung = pruefe_datei(ziel)
            except (OSError, wave.Error) as exc:
                print(f"    Datei unlesbar ({exc}) -- nochmal.")
                continue
            print(f"    {pruefung['sekunden']} s, Spitze {pruefung['spitze']} "
                  f"von 32768, {pruefung['rate']} Hz")
            if satz["sprache"] != "stille" and pruefung["spitze"] < 1000:
                print("    ZU LEISE (oder das Mikrofon ist stumm). Bitte "
                      "wiederholen.")
                continue
            if satz["sprache"] == "stille" and pruefung["spitze"] > 4000:
                print("    ZU LAUT fuer eine Stille-Aufnahme. Nochmal.")
                continue
            wahl = input("    ENTER = weiter, w = wiederholen, a = abspielen: ")
            if wahl.strip().lower() == "a":
                abspielen(ziel)
                wahl = input("    ENTER = weiter, w = wiederholen: ")
            if wahl.strip().lower() == "w":
                continue
            manifest["aufnahmen"][satz["id"]] = {
                "datei": ziel.name, "text": satz["text"],
                "sprache": satz["sprache"], "aufgenommen": time.strftime("%F %T"),
                **pruefung,
            }
            MANIFEST.write_text(json.dumps(manifest, indent=2,
                                           ensure_ascii=False) + "\n")
            break
        print()

    fertig = len(manifest["aufnahmen"])
    gesamt = len(daten["saetze"])
    gesamtdauer = sum(a["sekunden"] for a in manifest["aufnahmen"].values())
    print(f"{fertig} von {gesamt} aufgenommen, zusammen "
          f"{gesamtdauer:.1f} Sekunden.")
    print(f"Manifest: {MANIFEST}")
    if fertig < gesamt:
        print("Fehlt noch:", ", ".join(
            s["id"] for s in daten["saetze"]
            if s["id"] not in manifest["aufnahmen"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
