"""Die Denylist-Entscheidung faellt an DREI Stellen -- und muss ueberall
dieselbe sein.

BEFUND T-7.2 K8, gemessen von der Reviewer-Sitzung am 18.08.
(`tests/verify`, Ledger-Ausgang produktdefekt-rot):

    Die Redaktion haelt, was sie verspricht: ins Archiv gelangt nichts von
    einer gelisteten Anwendung. Die Sperre sitzt aber zu spaet.

`config/redaktion.yaml` sagt in seinem Kopf ausdruecklich, die Eintraege
seien **`.desktop`-Kennungen**, und "der Recorder loest KWins
`resource_class` gegen diese Kennungen auf". Genau eine der drei Stellen tat
das:

| Stelle | wann | vor dem 18.08. |
|---|---|---|
| `eyes.change.Kette` | vor dem Diff | rohe Klasse |
| `eyes.context.Kontextspeicher` | vor dem Live-Kontext | rohe Klasse |
| `recorder.redaktion.Redaktion` | vor dem Schreiben | aufgeloest |

Folge: ein Fenster eines Passwortmanagers wurde erfasst, durch den Diff
geschickt und geOCRt. Weg fiel nur der Archiveintrag -- der erkannte Text
stand im Quarantaene-Kontextspeicher und damit im Live-Kontext des Modells.

Diese Datei prueft alle drei gegen DIESELBEN Eingaben. Ein Ausgang, der
leiser ist als die anderen, faellt hier auf -- so wie es bei den zwei
Quarantaene-Ausgaengen am 17.08. schon einmal noetig war.
"""
from __future__ import annotations

import numpy as np
import pytest

from daimon.eyes import context
from daimon.eyes.change import Fenster, Kette
from daimon.eyes.change import GRUND_DENYLIST
from daimon.recorder.redaktion import GRUND_DENYLIST as R_DENYLIST
from daimon.recorder.redaktion import Redaktion, gesperrt

# Der Fall aus der Konfiguration: die Liste fuehrt die `.desktop`-Kennung,
# KWin meldet die Fensterklasse. Beide muessen zusammenfinden.
DENYLIST = ("org.keepassxc.KeePassXC",)
KENNUNGEN = {"keepassxc": "org.keepassxc.KeePassXC"}
GELISTET = "keepassxc"            # so meldet KWin
HARMLOS = "org.kde.kate"


# -- Die eine Entscheidung -------------------------------------------------

def test_die_kennung_wird_aufgeloest():
    """DER BEFUND in einer Zeile: die Klasse steht NICHT in der Liste, ihre
    `.desktop`-Kennung schon."""
    assert GELISTET not in {e.lower() for e in DENYLIST}
    assert gesperrt(GELISTET, DENYLIST, KENNUNGEN) is True


def test_ohne_zuordnung_greift_der_rueckfall():
    """Eine Anwendung ohne `.desktop`-Datei soll sich trotzdem sperren
    lassen -- dann traegt die rohe Klasse."""
    assert gesperrt("bitwarden", ("bitwarden",), {}) is True


def test_harmloses_bleibt_harmlos():
    """Ohne diese Zeile bestuende alles auch eine Fassung, die immer sperrt --
    und die haette den Bildschirmweg still abgeschaltet."""
    assert gesperrt(HARMLOS, DENYLIST, KENNUNGEN) is False
    assert gesperrt("", DENYLIST, KENNUNGEN) is False
    assert gesperrt(GELISTET, (), KENNUNGEN) is False


# -- Alle drei Stellen, dieselben Eingaben ---------------------------------

def _kette_sperrt(klasse: str, kennungen: dict) -> bool:
    kette = Kette(tor=lambda: True, denylist=DENYLIST, kennungen=kennungen)
    befund = kette.verarbeiten(
        np.zeros((4, 4, 3), np.uint8),
        Fenster(x=0, y=0, breite=4, hoehe=4, klasse=klasse))
    return befund.grund == GRUND_DENYLIST


def _speicher_sperrt(tmp_path, klasse: str, kennungen: dict) -> bool:
    s = context.Kontextspeicher(verzeichnis=tmp_path / klasse,
                                denylist=DENYLIST, kennungen=kennungen)
    return s.hinzufuegen(context.ART_OCR, klasse, "Masterpasswort") is False


def _redaktion_sperrt(tmp_path, klasse: str, kennungen: dict) -> bool:
    r = Redaktion(denylist=DENYLIST, runtime_dir=tmp_path,
                  kennungen=kennungen)
    return r.urteil(klasse).grund == R_DENYLIST


def test_alle_drei_sperren_das_gelistete_fenster(tmp_path):
    """Vor dem 18.08. sagte nur die dritte `True` -- und die kommt zuletzt."""
    assert _kette_sperrt(GELISTET, KENNUNGEN) is True
    assert _speicher_sperrt(tmp_path, GELISTET, KENNUNGEN) is True
    assert _redaktion_sperrt(tmp_path, GELISTET, KENNUNGEN) is True


def test_alle_drei_lassen_das_harmlose_durch(tmp_path):
    """Die Gegenrichtung, sonst waere oben auch eine Totalsperre gruen."""
    assert _kette_sperrt(HARMLOS, KENNUNGEN) is False
    assert _speicher_sperrt(tmp_path, HARMLOS, KENNUNGEN) is False
    assert _redaktion_sperrt(tmp_path, HARMLOS, KENNUNGEN) is False


def test_die_drei_urteilen_GLEICH(tmp_path):
    """Der eigentliche Befund: nicht dass eine Stelle zu leise war, sondern
    dass sie verschieden waren. Ein Angreifer sucht sich die leiseste."""
    for klasse in (GELISTET, HARMLOS, "org.keepassxc.KeePassXC", "",
                   "Bitwarden", "unbekannt-xyz"):
        urteile = {
            "kette": _kette_sperrt(klasse, KENNUNGEN),
            "speicher": _speicher_sperrt(tmp_path, klasse, KENNUNGEN),
            "redaktion": _redaktion_sperrt(tmp_path, klasse, KENNUNGEN),
        }
        # Ein leeres Fenster ist der eine erlaubte Unterschied: die Redaktion
        # sperrt es als `kennung_fehlt` (eigener Grund), die Kette schneidet
        # es aufs Vollbild zu. Beides ist FAIL-CLOSED, nur nicht dieselbe
        # Begruendung -- deshalb steht es hier ausdruecklich.
        if klasse == "":
            continue
        assert len(set(urteile.values())) == 1, (klasse, urteile)


# -- Der Zulauf: liefert der Dienst die Zuordnung ueberhaupt? --------------

def test_der_augendienst_laedt_die_zuordnung(tmp_path, monkeypatch):
    """Die Aufloesung nuetzt nichts, wenn niemand die Kennungen liefert --
    genau das Muster aus CLAUDE.md. Hier wird der ZULAUF geprueft, nicht die
    Funktion."""
    from daimon.eyes import daemon as D

    gerufen = []
    monkeypatch.setattr(D, "_kennungen_laden",
                        lambda: gerufen.append(1) or KENNUNGEN)
    augen = D.Augen(verzeichnis=tmp_path, denylist=DENYLIST)
    assert gerufen, "der Dienst laedt die .desktop-Zuordnung nicht"
    assert augen._kette._kennungen == KENNUNGEN
    assert augen._speicher._kennungen == KENNUNGEN


def test_eine_kaputte_zuordnung_nimmt_den_dienst_nicht_mit(monkeypatch):
    """Sie faellt auf den Rueckfall zurueck -- den Stand vor dem 18.08.:
    schwaecher, aber nicht kaputt. Ein Augendienst, der wegen fehlender
    `.desktop`-Dateien nicht startet, waere die schlechtere Antwort."""
    from daimon.eyes import daemon as D

    def kaputt():
        raise OSError("kein Zugriff")

    monkeypatch.setattr("daimon.recorder.redaktion.desktop_kennungen", kaputt)
    assert D._kennungen_laden() == {}
