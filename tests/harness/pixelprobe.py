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
import json
import random
import socket
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


def warte_auf_farbe(punkte, rgb, grenze_s, name):
    """Schiesst wiederholt, bis ALLE `punkte` die Farbe zeigen -- oder bis
    `grenze_s` um ist. Rueckgabe: (geschafft, letzter_schuss, versuche).

    T-1.1.v2. Vorher stand hier `time.sleep(3)`, und das ist genau so lange
    richtig, wie die Maschine nichts zu tun hat. Am 02.08.2026 lief parallel
    ein cargo-Build; das Vollbildfenster war nach 3 s noch nicht gemappt, die
    Sonde fotografierte den Desktop, und VIER Pruefungen wurden rot -- der
    naechste Lauf war gruen. Ein Verifizierer, der von der Systemlast abhaengt,
    wird irgendwann weggeklickt statt geglaubt.

    Wichtig, damit das kein Selbstbetrug wird: gewartet wird nur auf die
    VORAUSSETZUNG (das Testfenster steht), nie auf das Ergebnis der eigentlichen
    Messung. Wo auf ein Ergebnis gewartet wird -- Phase C -- ist die Zeitgrenze
    Teil der Zusage und steht in der Meldung.
    """
    ende = time.monotonic() + grenze_s
    versuche = 0
    while True:
        versuche += 1
        bild = schuss(name)
        werte = [pixel(bild, punkt) for punkt in punkte]
        if all(nah(w, rgb) for w in werte):
            return True, bild, versuche
        if time.monotonic() >= ende:
            return False, bild, versuche
        time.sleep(0.5)


def warte_auf_frames(diag_pfad, grenze_s):
    """Wartet, bis das Overlay MINDESTENS EINEN Puffer committet hat.

    Der Socket zu existieren heisst nur, dass der Prozess lebt -- nicht, dass
    er gezeichnet hat. Genau diese Verwechslung ist Fall 2 der Liste im
    Handover ("Latenz statt Zustellung").
    """
    ende = time.monotonic() + grenze_s
    while time.monotonic() < ende:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(3)
                c.connect(str(diag_pfad))
                zeile = c.makefile("rb").readline().decode()
            if json.loads(zeile).get("frames_rendered", 0) >= 1:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    return False


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
        # Voraussetzung, nicht Ergebnis: warten, bis das Vollbildfenster
        # wirklich steht. 30 s sind grosszuegig -- unter Last hat es am
        # 02.08. mehr als 3 s gebraucht, im Leerlauf steht es nach unter 1 s.
        steht, a, versuche = warte_auf_farbe([SONDE, KONTROLLE], rgb, 30, "A")
        p_a, k_a = pixel(a, SONDE), pixel(a, KONTROLLE)
        notiz(f"A Sonde={p_a} Kontrolle={k_a} (Aufbau nach {versuche} Aufnahmen)")
        if not steht:
            notiz("A: das Vollbildfenster stand nach 30 s nicht -- alles "
                  "Folgende waere eine Aussage ueber den Desktop")
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
        # Nicht schlafen, sondern auf einen tatsaechlich committeten Puffer
        # warten. Danach ist die Messung unten eine Aussage ueber das, was das
        # Overlay gezeichnet hat -- und nicht darueber, ob es schnell genug war.
        gezeichnet = warte_auf_frames(TMP / "d.sock", 20)
        sag("overlay_hat_gezeichnet", gezeichnet)

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
        # Hier wird auf ein ERGEBNIS gewartet, also ist die Grenze Teil der
        # Zusage: nach dem Beenden ist die Vollbildfarbe binnen 10 s zurueck.
        # Das ist strenger als das alte `sleep(2)` plus Einzelschuss -- dort
        # entschied die Tagesform, ob der Compositor schon neu gezeichnet
        # hatte.
        zurueck, c, versuche_c = warte_auf_farbe([SONDE], rgb, 10, "C")
        p_c = pixel(c, SONDE)
        notiz(f"C Sonde={p_c} (zurueck nach {versuche_c} Aufnahmen, Grenze 10 s)")
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
