"""T-5.5 -- die Gatterkette und die Regionenerkennung.

Zwei Tests tragen die Aufgabe, und beide stehen ausdruecklich in der
Akzeptanzliste: zwei Frames, die sich nur an EINER Stelle unterscheiden,
muessen als veraendert erkannt werden -- daran ist das gekachelte dHash
gescheitert. Und ein kuenstlich verzoegertes Ergebnis einer aelteren
Generation muss nachweislich verworfen werden.
"""
from __future__ import annotations

import numpy as np
import pytest

from daimon.eyes import change as ch
from daimon.eyes import regionen as rg


# -- Hilfsmittel -----------------------------------------------------------

def leer(breite=320, hoehe=200, wert=20):
    return np.full((hoehe, breite, 3), wert, dtype=np.uint8)


def text_hinein(bild, x, y, laenge=60, hoehe=8, wert=230):
    """Ein heller waagerechter Balken -- so sieht eine Textzeile fuer den
    Gradienten aus."""
    bild[y:y + hoehe, x:x + laenge] = wert
    return bild


VOLLES_FENSTER = ch.Fenster(x=0, y=0, breite=320, hoehe=200, klasse="editor")


def kette(**kw):
    return ch.Kette(tor=lambda: True, **kw)


# -- Die Regionenerkennung -------------------------------------------------

def test_graustufen_folgen_bt601():
    """Ein ungewichteter Mittelwert macht roten Text dunkler, als das Auge
    ihn sieht -- und genau der faellt dann unter die Otsu-Schwelle."""
    rgb = np.zeros((1, 3, 3), dtype=np.uint8)
    rgb[0, 0] = (255, 0, 0)
    rgb[0, 1] = (0, 255, 0)
    rgb[0, 2] = (0, 0, 255)
    g = rg.graustufen(rgb)[0]
    assert g[1] > g[0] > g[2]           # Gruen > Rot > Blau


def test_zwei_textzeilen_bleiben_zwei_regionen():
    """Ein quadratischer Kern verschmilzt einen Absatz zu einem Block.
    Waagerecht geschlossen bleiben die Zeilen getrennt."""
    b = leer()
    text_hinein(b, 40, 40)
    text_hinein(b, 40, 90)
    kaesten = rg.textregionen(rg.graustufen(b))
    assert len(kaesten) >= 2


def test_der_formfilter_wirft_zu_kleines_weg():
    klein = [(0, 0, 3, 3)]
    assert rg.formfilter(klein, 100000) == []


def test_der_formfilter_wirft_zu_grosses_weg():
    """Was mehr als die halbe Flaeche fuellt, ist Hintergrund, nicht Text."""
    gross = [(0, 0, 300, 200)]
    assert rg.formfilter(gross, 320 * 200) == []


def test_otsu_findet_die_luecke():
    """Die Schwelle ist EINSCHLIESSLICH: `t` heisst Klasse A ist `[0..t]`.

    Deshalb wird nicht auf einen Wert geprueft, sondern darauf, dass die
    Trennung stimmt -- ein Test auf die Zahl selbst haette hier eine richtige
    Schwelle als Fehler gemeldet.
    """
    bild = np.concatenate([np.full(500, 10, np.uint8),
                           np.full(500, 200, np.uint8)])
    t = rg.otsu(bild)
    assert (bild > t).sum() == 500
    assert 10 <= t < 200


# -- Der Test, an dem das gekachelte dHash gescheitert ist ------------------

def test_zwei_frames_die_sich_nur_an_einer_stelle_unterscheiden():
    """Genau der Fall, den screenpipe zweimal still verpasst hat.

    Ein 4x4-Raster ueber 160x90 haette hier kein einziges Bit gekippt. Die
    Signatur ueber den ganzen Zuschnitt kippt.
    """
    k = kette()
    a = text_hinein(leer(), 40, 40)
    erste = k.verarbeiten(a, VOLLES_FENSTER)
    assert erste.veraendert is True

    # Derselbe Bildschirm noch einmal: nichts Neues.
    assert k.verarbeiten(a.copy(), VOLLES_FENSTER).grund == ch.GRUND_UNVERAENDERT

    # EINE Zeile aendert sich -- ein Balken wird laenger.
    b = a.copy()
    b[40:48, 100:118] = 230
    zweite = k.verarbeiten(b, VOLLES_FENSTER)
    assert zweite.veraendert is True
    assert zweite.signatur != erste.signatur


# -- Die Reihenfolge der Gatter --------------------------------------------

def test_die_denylist_steht_vor_allem_anderen():
    """Ein Passwortmanager wird nicht erfasst und danach verworfen -- er wird
    gar nicht erst erfasst."""
    k = kette(denylist=["org.keepassxc.KeePassXC"])
    f = ch.Fenster(x=0, y=0, breite=320, hoehe=200,
                   klasse="org.keepassxc.KeePassXC")
    b = k.verarbeiten(text_hinein(leer(), 40, 40), f)
    assert b.grund == ch.GRUND_DENYLIST
    assert b.ausschnitt is None
    # Keine spaetere Stufe wurde ueberhaupt gemessen.
    assert "textregionen" not in b.kosten
    assert "signatur" not in b.kosten


def test_die_denylist_vergleicht_ohne_ruecksicht_auf_gross_und_klein():
    """KWin meldet `org.kde.konsole`, der Nutzer schreibt `Konsole`."""
    k = kette(denylist=["Konsole"])
    f = ch.Fenster(x=0, y=0, breite=320, hoehe=200, klasse="konsole")
    assert k.verarbeiten(leer(), f).grund == ch.GRUND_DENYLIST


def test_drm_wird_uebersprungen():
    k = kette()
    f = ch.Fenster(x=0, y=0, breite=320, hoehe=200, klasse="player", drm=True)
    b = k.verarbeiten(text_hinein(leer(), 40, 40), f)
    assert b.grund == ch.GRUND_DRM
    assert "textregionen" not in b.kosten


def test_ein_geschlossenes_tor_haelt_die_kette_an():
    k = ch.Kette(tor=lambda: False)
    b = k.verarbeiten(text_hinein(leer(), 40, 40), VOLLES_FENSTER)
    assert b.grund == ch.GRUND_TOR
    assert "textregionen" not in b.kosten


def test_jede_abweisung_liefert_trotzdem_einen_befund():
    """`None` waere von „nicht gelaufen" nicht zu unterscheiden."""
    k = ch.Kette(tor=lambda: False)
    b = k.verarbeiten(leer(), VOLLES_FENSTER)
    assert isinstance(b, ch.Befund) and b.generation == 1


# -- Der Zuschnitt aufs Fenster --------------------------------------------

def test_nur_das_fokussierte_fenster_wird_angesehen():
    """Der Zuschnitt aufs Fenster ist der Durchsatzgewinn, nicht die
    Kachelung: 277--350 ms statt 3300--4200 ms."""
    b = leer(640, 400)
    text_hinein(b, 400, 300)                    # ausserhalb des Fensters
    k = kette()
    f = ch.Fenster(x=0, y=0, breite=200, hoehe=150, klasse="editor")
    assert k.verarbeiten(b, f).grund == ch.GRUND_KEIN_TEXT


def test_die_region_ist_in_bildschirmkoordinaten():
    """Wer den Bezug verliert, beschreibt die richtige Stelle des falschen
    Bildschirms."""
    b = leer(640, 400)
    text_hinein(b, 320 + 40, 200 + 40)
    k = kette()
    f = ch.Fenster(x=320, y=200, breite=300, hoehe=180, klasse="editor")
    befund = k.verarbeiten(b, f)
    assert befund.veraendert is True
    x, y, w, h = befund.region
    assert x >= 320 and y >= 200


# -- Kopie statt Referenz --------------------------------------------------

def test_der_ausschnitt_ist_eine_kopie():
    """Ein Blick zeigte auf den Frame-Puffer, den PipeWire wieder einzieht --
    und das faellt erst auf, wenn ein VLM etwas beschreibt, das nie auf dem
    Bildschirm stand."""
    b = text_hinein(leer(), 40, 40)
    befund = kette().verarbeiten(b, VOLLES_FENSTER)
    vorher = befund.ausschnitt.copy()
    b[:, :] = 7                                  # der Puffer wird eingezogen
    assert np.array_equal(befund.ausschnitt, vorher)


# -- Generationen ----------------------------------------------------------

def test_jeder_frame_traegt_eine_generationsnummer():
    k = kette()
    a = k.verarbeiten(text_hinein(leer(), 40, 40), VOLLES_FENSTER)
    b = k.verarbeiten(text_hinein(leer(), 40, 90), VOLLES_FENSTER)
    assert (a.generation, b.generation) == (1, 2)


def test_ein_verzoegertes_ergebnis_einer_aelteren_generation_wird_verworfen():
    """Der Nebenlaeufigkeitsfall aus der Akzeptanzliste.

    OCR und VLM brauchen Hunderte von Millisekunden. Trifft das Ergebnis von
    Frame 3 nach dem von Frame 7 ein, drehte es den Kontextspeicher zurueck --
    ein Bildschirm von vorhin ist keine Beobachtung mehr.
    """
    o = ch.Ordner()
    assert o.annehmen(3) is True
    assert o.annehmen(7) is True
    assert o.annehmen(4) is False               # der Nachzuegler
    assert o.annehmen(7) is False               # und ein Doppel
    assert o.verworfen == 2
    assert o.annehmen(8) is True


# -- Kosten je Stufe -------------------------------------------------------

def test_jede_durchlaufene_stufe_wird_gemessen():
    b = kette().verarbeiten(text_hinein(leer(), 40, 40), VOLLES_FENSTER)
    for stufe in ("denylist", "drm", "tor", "fensterzuschnitt",
                  "textregionen", "vereinigungszuschnitt", "signatur"):
        assert stufe in b.kosten, stufe
        assert b.kosten[stufe] >= 0.0


def test_die_abdeckung_der_vereinigung_wird_mitgeliefert():
    """T--1.10 misst 97--99 % -- der Zuschnitt ist ein No-Op, und die Zahl
    sagt es, statt dass man es erbt."""
    b = kette().verarbeiten(text_hinein(leer(), 40, 40), VOLLES_FENSTER)
    assert 0.0 < b.abdeckung <= 1.0
