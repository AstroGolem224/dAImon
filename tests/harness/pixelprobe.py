#!/usr/bin/env python3
"""Pixelprobe fuer T-1.1: ist das Overlay ueber einem Vollbildfenster sichtbar?

Aufbau, uebernommen aus dem Spike T-1.8 (`spikes/layershell/run_fullscreen_test.py`)
und auf den echten Pruefling umgebaut. Der Spike zeichnete einen Vollton-Marker;
`daimon-face` zeichnet ein Sprite, dessen Farben wir nicht vorhersagen wollen.
Also wird nicht auf eine bekannte Overlay-Farbe geprueft, sondern darauf, dass
die Vollbildfarbe an der Sprite-Stelle **verschwindet** und danach
**wiederkommt**.

Drei Aufnahmen, und jede einzelne traegt eine Aussage:

  A  nur das Vollbildfenster      Sondenpunkt == Vollbildfarbe   (Vorher-Beleg)
  B  Vollbildfenster + Overlay    Sondenpunkt != Vollbildfarbe   (der Test)
                                  Kontrollpunkt == Vollbildfarbe (Rest bleibt durchsichtig)
  C  nach dem Beenden             Sondenpunkt == Vollbildfarbe   (es war wirklich das Overlay)

Die Vollbildfarbe ist **je Lauf zufaellig**. Damit ist eine weggelassene
Vorher-Aufnahme nicht zu kaschieren: wer B allein prueft, kann nicht wissen,
ob dort vorher schon etwas anderes stand.

Der Kontrollpunkt ist der Grund, warum "Marker ausserhalb der kontrollierten
Region gesucht" auffliegt: er liegt sicher im Vollbildfenster und sicher
neben dem Sprite. Bliebe er in B nicht die Vollbildfarbe, waere das Overlay
nicht durchsichtig, sondern verdeckend -- und die Zusage aus 8.1 gebrochen.

Ausgabe: eine Zeile `name=ja|nein` je Pruefung, plus Messwerte als `#`-Zeilen.
"""

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

TMP = Path(sys.argv[1])
BIN = Path(sys.argv[2])
# Das Manifest kommt immer aus dem echten Repo: die Mutanten unterscheiden
# sich im Quelltext, nicht in den Assets, und 550 KB Sprite-Sheet je Mutant
# in die Historie zu legen waere Verschwendung.
MANIFEST = Path(sys.argv[3])
# Wo das Sprite steht. Der Pruefling bekommt genau diese Position, damit die
# Sonde nicht raten muss.
SPRITE_X, SPRITE_Y = 900, 500
# Zellmitte: 192x208 laut pet.json.
SONDE = (SPRITE_X + 96, SPRITE_Y + 104)
# Kontrollpunkt: weit genug weg vom Sprite, aber sicher im Vollbildfenster.
KONTROLLE = (SPRITE_X + 96, SPRITE_Y - 200)

zeilen = []


def sag(name, wert):
    zeilen.append(f"{name}={'ja' if wert else 'nein'}")


def notiz(text):
    zeilen.append(f"# {text}")


def farbe_wuerfeln():
    """Kraeftig und eindeutig, damit ein Toleranzvergleich nicht zufaellig
    passt."""
    while True:
        rgb = tuple(random.randrange(0, 256) for _ in range(3))
        if max(rgb) - min(rgb) >= 70 and 250 <= sum(rgb) <= 560:
            return rgb


def schuss(name):
    p = TMP / f"{name}.png"
    if p.exists():
        p.unlink()
    subprocess.run(["spectacle", "-b", "-n", "-f", "-o", str(p)], timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        if p.exists() and p.stat().st_size > 0:
            break
        time.sleep(0.25)
    time.sleep(0.3)
    return p


def pixel(pfad, punkt):
    im = Image.open(pfad).convert("RGB")
    return im.getpixel(punkt)


def nah(a, b, toleranz=8):
    return all(abs(int(x) - int(y)) <= toleranz for x, y in zip(a, b))


def main():
    rgb = farbe_wuerfeln()
    hex_farbe = "#%02x%02x%02x" % rgb
    notiz(f"Vollbildfarbe {hex_farbe}, Sonde {SONDE}, Kontrolle {KONTROLLE}")

    fenster = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "vollbildfenster.py"),
         hex_farbe, str(TMP / "klicks.log"), "120"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    face = None
    try:
        time.sleep(3)
        a = schuss("A")
        p_a, k_a = pixel(a, SONDE), pixel(a, KONTROLLE)
        notiz(f"A Sonde={p_a} Kontrolle={k_a}")
        # Positivkontrolle des ganzen Aufbaus: das Vollbildfenster ist da und
        # deckt beide Punkte. Ohne das waere jede Aussage unten wertlos --
        # dann haette man den Desktop fotografiert.
        sag("A_vollbildfenster_deckt_die_sonde", nah(p_a, rgb))
        sag("A_vollbildfenster_deckt_die_kontrolle", nah(k_a, rgb))

        face = subprocess.Popen(
            [str(BIN), "--sprite-position", f"{SPRITE_X},{SPRITE_Y}",
             "--pet-manifest", str(MANIFEST),
             "--diag-socket", str(TMP / "d.sock")],
            env=dict(os.environ, DAIMON_MAX_SECS="60"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        bereit = False
        ende = time.monotonic() + 20
        while time.monotonic() < ende:
            if (TMP / "d.sock").exists():
                bereit = True
                break
            time.sleep(0.1)
        sag("overlay_startet", bereit)
        time.sleep(2)

        b = schuss("B")
        p_b, k_b = pixel(b, SONDE), pixel(b, KONTROLLE)
        notiz(f"B Sonde={p_b} Kontrolle={k_b}")
        # DER Test: an der Sprite-Stelle ist die Vollbildfarbe verschwunden.
        sag("B_overlay_liegt_ueber_dem_vollbildfenster", not nah(p_b, rgb))
        # Und daneben bleibt alles durchsichtig -- ein Overlay, das den ganzen
        # Schirm verdeckt, waere kein Overlay.
        sag("B_daneben_bleibt_durchsichtig", nah(k_b, rgb))

        face.terminate()
        face.wait(timeout=10)
        face = None
        time.sleep(2)
        c = schuss("C")
        p_c = pixel(c, SONDE)
        notiz(f"C Sonde={p_c}")
        # Es war wirklich das Overlay und nicht irgendetwas anderes auf dem
        # Schirm: nach dem Beenden ist die Vollbildfarbe zurueck.
        sag("C_nach_dem_beenden_ist_die_farbe_zurueck", nah(p_c, rgb))
    finally:
        for prozess in (face, fenster):
            if prozess and prozess.poll() is None:
                prozess.terminate()
    print("\n".join(zeilen))


if __name__ == "__main__":
    main()
