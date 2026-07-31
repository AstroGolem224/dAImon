#!/usr/bin/env python3
"""Mood-Probe fuer T-2.1: sind alle acht Moods wirklich unterscheidbar?

Der Plan ist hier ungewoehnlich deutlich, und das aus gutem Grund:

    Ein unterschiedlicher `sprite`-Bezeichner im Diagnose-Socket beweist
    nichts -- identische Sprites bestuenden ihn.

Verglichen werden deshalb die **Pixel**. Je Mood wird die Pet-Region
aufgenommen und gegen alle anderen gehalten. Acht Moods ergeben 28 Paare, und
jedes einzelne muss sich unterscheiden.

Aufgenommen wird ueber einem Vollbildfenster mit fester Farbe -- sonst
schwankt der Hintergrund zwischen zwei Aufnahmen und der Unterschied kaeme
vom Desktop statt vom Pet.

Die Pet-Region kann hier ueber Koordinaten zugeschnitten werden, anders als
beim Auth-Dialog in T-1.7: das Sprite steht, wo `--sprite-position` es
hinstellt, und Bildschirmkoordinaten sind gleich Screenshot-Koordinaten (in
T-1.1 nachgemessen). Ein Wayland-CLIENT kennt seine Position nicht -- wir
geben sie ihm ja vor.

Ausgabe: `name=ja|nein` je Pruefung, Messwerte als `#`-Zeilen.
"""

import itertools
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

TMP = Path(sys.argv[1])
BIN = Path(sys.argv[2])
MANIFEST = Path(sys.argv[3])

SPX, SPY = 900, 500
ZW, ZH = 192, 208
MOODS = ["sleeping", "idle", "observing", "thinking", "working", "done",
         "failed", "needs_input"]
# Ab dieser mittleren Kanalabweichung gelten zwei Aufnahmen als verschieden.
# Der Wert wird nicht geraten, sondern gegen das gemessene Rauschen gehalten:
# zwei Aufnahmen DESSELBEN Moods liefern die Untergrenze.
SCHWELLE = 2.0

zeilen = []


def sag(name, wert):
    zeilen.append(f"{name}={'ja' if wert else 'nein'}")


def notiz(text):
    zeilen.append(f"# {text}")


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
    time.sleep(0.2)
    return Image.open(p).convert("RGB").crop((SPX, SPY, SPX + ZW, SPY + ZH))


def abstand(a, b):
    """Mittlere Kanalabweichung. Kein Hash-Trick: eine Zahl, die man
    hinschreiben und nachrechnen kann, ist hier mehr wert als ein
    perceptual hash, dessen Schwelle niemand begruendet."""
    diff = ImageChops.difference(a, b)
    return sum(ImageStat.Stat(diff).mean) / 3.0


def ctl(pfad, befehl):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(10)
    c.connect(str(pfad))
    c.sendall(befehl.encode() + b"\n")
    antwort = c.makefile("rb").readline().decode().strip()
    c.close()
    return antwort


def diag(pfad):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(10)
    c.connect(str(pfad))
    d = json.loads(c.makefile("rb").readline())
    c.close()
    return d


def main():
    fenster = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "vollbildfenster.py"),
         "#204080", str(TMP / "klicks.log"), "240"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    face = None
    try:
        time.sleep(3)
        face = subprocess.Popen(
            [str(BIN), "--sprite-position", f"{SPX},{SPY}",
             "--pet-manifest", str(MANIFEST),
             "--diag-socket", str(TMP / "d.sock"),
             "--control-socket", str(TMP / "c.sock")],
            env=dict(os.environ, DAIMON_MAX_SECS="240"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ende = time.monotonic() + 25
        bereit = False
        while time.monotonic() < ende:
            if (TMP / "c.sock").exists() and (TMP / "d.sock").exists():
                bereit = True
                break
            time.sleep(0.1)
        sag("overlay_startet", bereit)
        if not bereit:
            print("\n".join(zeilen))
            return
        time.sleep(2)

        bilder = {}
        bezeichner = {}
        for mood in MOODS:
            if ctl(TMP / "c.sock", f"mood {mood}") != "ok":
                sag(f"mood_{mood}_angenommen", False)
                continue
            # Der Uebergang braucht Zeit -- gemessen wird der Endzustand,
            # nicht die Mitte einer Interpolation.
            time.sleep(2.5)
            bilder[mood] = schuss(mood)
            bezeichner[mood] = diag(TMP / "d.sock").get("sprite", "")
        sag("alle_acht_moods_angenommen", len(bilder) == len(MOODS))

        # Positivkontrolle des Vergleichs: derselbe Mood zweimal aufgenommen
        # ergibt (nahezu) dasselbe Bild. Ohne sie waere jeder Unterschied
        # unten auch blosses Rauschen -- und "acht verschiedene Bilder" waere
        # bei einem flackernden Hintergrund trivial erfuellt.
        ctl(TMP / "c.sock", "mood idle")
        time.sleep(2.5)
        a = schuss("kontrolle_a")
        time.sleep(1.0)
        b = schuss("kontrolle_b")
        rauschen = abstand(a, b)
        notiz(f"Rauschen zwischen zwei Aufnahmen desselben Moods: {rauschen:.3f}")
        sag("rauschen_ist_klein", rauschen < SCHWELLE / 2)

        if len(bilder) == len(MOODS):
            paare = list(itertools.combinations(MOODS, 2))
            schlimmste = min(
                ((abstand(bilder[x], bilder[y]), x, y) for x, y in paare),
                key=lambda t: t[0])
            notiz(f"{len(paare)} Paare geprueft, geringster Abstand "
                  f"{schlimmste[0]:.3f} zwischen {schlimmste[1]} und {schlimmste[2]}")
            sag("alle_paare_unterscheidbar", schlimmste[0] >= SCHWELLE)
            # Und der Abstand muss deutlich ueber dem Rauschen liegen, nicht
            # nur ueber einer geratenen Schwelle.
            sag("abstand_deutlich_ueber_dem_rauschen",
                schlimmste[0] > rauschen * 3 + 0.5)

            # Der Bezeichner darf NICHT das sein, was sie unterscheidet.
            # Diese Zeile ist kein Kriterium, sondern eine Notiz -- sie zeigt,
            # dass ein Verifizierer, der auf Bezeichner sieht, hier zu wenig
            # Information haette.
            verschiedene = len(set(bezeichner.values()))
            notiz(f"verschiedene sprite-Bezeichner ueber acht Moods: "
                  f"{verschiedene} ({sorted(set(bezeichner.values()))})")

        # Fehlende Pose faellt auf idle zurueck, ohne Fehler: der Prozess
        # lebt noch, nachdem alle acht durch sind.
        sag("prozess_lebt_nach_allen_moods", face.poll() is None)
    finally:
        for prozess in (face, fenster):
            if prozess and prozess.poll() is None:
                prozess.terminate()
    print("\n".join(zeilen))


if __name__ == "__main__":
    main()
