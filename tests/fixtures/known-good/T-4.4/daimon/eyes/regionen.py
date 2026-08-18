"""T-5.5 -- Textregionen finden, ohne OpenCV.

Eine Portierung der OpenCV-Sequenz aus Design 4.4, mit `numpy` allein:

    BT.601-Graustufen -> 3x3-Morphologiegradient -> Otsu
    -> 9x1-Schliessung -> Zusammenhangskomponenten -> Formfilter

Der Grund, das nachzubauen statt `opencv-python` zu nehmen: die Abhaengigkeit
waegt gut 60 MB und zieht eine zweite BLAS mit. Was hier gebraucht wird, sind
sechs Operationen auf einem Graustufenbild.

**Die Zusammenhangskomponenten laufen ueber ZEILENLAEUFE, nicht ueber Pixel.**
Ein Durchlauf Pixel fuer Pixel in Python kostet auf einem 1440p-Bild Sekunden
-- die Akzeptanzliste nennt 10 bis 19 ms. Nach der 9x1-Schliessung besteht
eine Zeile aber aus wenigen zusammenhaengenden Laeufen, und die lassen sich
mit `numpy` in einem Rutsch finden. Verbunden werden sie mit Union-Find; das
ist der Teil, der in Python laeuft, und er sieht nur die Laeufe.
"""
from __future__ import annotations

import numpy as np

# Formfilter aus Design 4.4. Kleiner als das ist Rauschen, groesser als die
# halbe Flaeche ist kein Text, sondern ein Hintergrund.
MIN_BOX_W = 8
MIN_BOX_H = 6
SEITENVERHAELTNIS = (1.0, 40.0)
MAX_AREA_FRACTION = 0.5


def graustufen(rgb: np.ndarray) -> np.ndarray:
    """BT.601. Die Gewichte sind Norm, keine Vorliebe.

    Ein ungewichteter Mittelwert macht rotes Text auf schwarzem Grund
    dunkler, als das Auge ihn sieht -- und genau dieser Text faellt dann
    unter die Otsu-Schwelle.
    """
    r = rgb[:, :, 0].astype(np.uint16)
    g = rgb[:, :, 1].astype(np.uint16)
    b = rgb[:, :, 2].astype(np.uint16)
    return ((r * 77 + g * 150 + b * 29) >> 8).astype(np.uint8)


def _fenster3(bild: np.ndarray) -> np.ndarray:
    """Die neun 3x3-Nachbarn als Stapel. Raender werden gespiegelt.

    Gespiegelt und nicht mit Null gefuellt: eine Nullfuellung erzeugt am Rand
    einen kuenstlichen Kontrast, und der Gradient zoege dort eine Kante, wo
    keine ist.
    """
    p = np.pad(bild, 1, mode="edge")
    return np.stack([p[y:y + bild.shape[0], x:x + bild.shape[1]]
                     for y in range(3) for x in range(3)])


def morphologiegradient(grau: np.ndarray) -> np.ndarray:
    """Dilatation minus Erosion, 3x3. Betont Kanten, nicht Flaechen."""
    stapel = _fenster3(grau)
    return stapel.max(axis=0) - stapel.min(axis=0)


def otsu(bild: np.ndarray) -> int:
    """Die Schwelle, die die Varianz zwischen den Klassen maximiert.

    Heuristikfrei: jede feste Schwelle waere auf einem dunklen Thema eine
    andere als auf einem hellen, und der Fehler waere still.
    """
    hist = np.bincount(bild.ravel(), minlength=256).astype(np.float64)
    gesamt = hist.sum()
    if gesamt == 0:
        return 0
    stufen = np.arange(256, dtype=np.float64)
    gewicht_a = np.cumsum(hist)
    gewicht_b = gesamt - gewicht_a
    summe_a = np.cumsum(hist * stufen)
    summe_gesamt = summe_a[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        mittel_a = summe_a / gewicht_a
        mittel_b = (summe_gesamt - summe_a) / gewicht_b
        zwischen = gewicht_a * gewicht_b * (mittel_a - mittel_b) ** 2
    zwischen[~np.isfinite(zwischen)] = -1.0
    return int(np.argmax(zwischen))


def schliessen_waagerecht(binaer: np.ndarray, breite: int = 9) -> np.ndarray:
    """Dilatation dann Erosion mit einem 9x1-Kern.

    Waagerecht, weil Text waagerecht laeuft: die Luecken zwischen Buchstaben
    sollen zugehen, die Zeilen darueber und darunter aber getrennt bleiben.
    Ein quadratischer Kern verschmilzt einen Absatz zu einem Block.
    """
    rand = breite // 2
    p = np.pad(binaer, ((0, 0), (rand, rand)), mode="constant")
    spalten = [p[:, i:i + binaer.shape[1]] for i in range(breite)]
    dilatiert = np.maximum.reduce(spalten)
    p2 = np.pad(dilatiert, ((0, 0), (rand, rand)), mode="constant",
                constant_values=True)
    spalten2 = [p2[:, i:i + binaer.shape[1]] for i in range(breite)]
    return np.minimum.reduce(spalten2)


def _laeufe(zeile: np.ndarray) -> list[tuple[int, int]]:
    """Die zusammenhaengenden `True`-Abschnitte einer Zeile als (start, ende)."""
    if not zeile.any():
        return []
    d = np.diff(np.concatenate(([0], zeile.view(np.int8), [0])))
    starts = np.flatnonzero(d == 1)
    enden = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), enden.tolist()))


def komponenten(binaer: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Zusammenhangskomponenten als (x, y, breite, hoehe).

    Ueber Zeilenlaeufe und Union-Find. Zwei Laeufe benachbarter Zeilen
    gehoeren zusammen, wenn sie sich waagerecht ueberlappen -- 8er-Nachbar-
    schaft waere hier falsch: sie verbindet zwei Textzeilen ueber eine einzige
    diagonal beruehrende Ecke.
    """
    eltern: list[int] = []

    def finden(i: int) -> int:
        while eltern[i] != i:
            eltern[i] = eltern[eltern[i]]
            i = eltern[i]
        return i

    def vereinen(a: int, b: int) -> None:
        wa, wb = finden(a), finden(b)
        if wa != wb:
            eltern[max(wa, wb)] = min(wa, wb)

    vorherige: list[tuple[int, int, int]] = []      # (start, ende, marke)
    alle: list[tuple[int, int, int, int]] = []      # (start, ende, y, marke)

    for y in range(binaer.shape[0]):
        aktuelle: list[tuple[int, int, int]] = []
        for start, ende in _laeufe(binaer[y]):
            marke = len(eltern)
            eltern.append(marke)
            for vstart, vende, vmarke in vorherige:
                if vstart < ende and start < vende:      # Ueberlappung
                    vereinen(marke, vmarke)
            aktuelle.append((start, ende, marke))
            alle.append((start, ende, y, marke))
        vorherige = aktuelle

    kaesten: dict[int, list[int]] = {}
    for start, ende, y, marke in alle:
        w = finden(marke)
        k = kaesten.get(w)
        if k is None:
            kaesten[w] = [start, y, ende, y + 1]
        else:
            k[0] = min(k[0], start)
            k[1] = min(k[1], y)
            k[2] = max(k[2], ende)
            k[3] = max(k[3], y + 1)
    return [(x0, y0, x1 - x0, y1 - y0) for x0, y0, x1, y1 in kaesten.values()]


def formfilter(kaesten, bildflaeche: int) -> list[tuple[int, int, int, int]]:
    """Was zu klein, zu duenn oder zu gross ist, ist kein Text."""
    behalten = []
    for x, y, w, h in kaesten:
        if w < MIN_BOX_W or h < MIN_BOX_H:
            continue
        verhaeltnis = w / h
        if not (SEITENVERHAELTNIS[0] <= verhaeltnis <= SEITENVERHAELTNIS[1]):
            continue
        if bildflaeche and (w * h) / bildflaeche > MAX_AREA_FRACTION:
            continue
        behalten.append((x, y, w, h))
    return behalten


def textregionen(grau: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Die ganze Sequenz aus Design 4.4 auf einem Graustufenbild."""
    gradient = morphologiegradient(grau)
    schwelle = otsu(gradient)
    binaer = gradient > schwelle
    geschlossen = schliessen_waagerecht(binaer)
    return formfilter(komponenten(geschlossen), grau.size)


def vereinigung(kaesten) -> tuple[int, int, int, int] | None:
    """Der kleinste Kasten, der alle enthaelt."""
    if not kaesten:
        return None
    x0 = min(x for x, _, _, _ in kaesten)
    y0 = min(y for _, y, _, _ in kaesten)
    x1 = max(x + w for x, _, w, _ in kaesten)
    y1 = max(y + h for _, y, _, h in kaesten)
    return (x0, y0, x1 - x0, y1 - y0)
