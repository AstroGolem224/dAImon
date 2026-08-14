"""T-7.1 -- was das Archiv zusagt, hier gemessen.

Jede Pruefung steht neben einer Positivkontrolle, wo eine Abwesenheit
behauptet wird: "nach 48 h kein Frame mehr" ist auch dann wahr, wenn nie
einer da war.
"""
from __future__ import annotations

import os
import stat

import pytest

from daimon.common.protocol import Mark, Marked
from daimon.recorder.store import (
    ART_FRAME, ART_OCR, ART_TITEL, ART_TRANSKRIPT, AUFBEWAHRUNG,
    MODUS, SCHEMA_VERSION, STUFE_FULL, STUFE_METADATA, STUFE_TRANSIENT,
    Archiv, ArchivFehler, VERZEICHNIS_MODUS)


class Uhr:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def archiv(tmp_path):
    uhr = Uhr()
    a = Archiv(tmp_path / "archiv" / "archiv.db", uhr=uhr,
               grenze_bytes=10 * 1024)
    a.migrieren()
    a.uhr = uhr                      # fuer die Tests erreichbar
    yield a
    a.schliessen()


# -- Schema und Rechte ------------------------------------------------------

def test_migration_vor_und_zurueck(archiv):
    assert archiv.version() == SCHEMA_VERSION
    assert archiv.migrieren(0) == 0
    assert archiv.migrieren() == SCHEMA_VERSION


def test_datei_0600_verzeichnis_0700(archiv):
    archiv.schreiben(ART_TITEL, "Posteingang")
    assert stat.S_IMODE(archiv.pfad.stat().st_mode) == MODUS
    assert (stat.S_IMODE(archiv.pfad.parent.stat().st_mode)
            == VERZEICHNIS_MODUS)
    for endung in ("-wal", "-shm"):
        neben = archiv.pfad.parent / (archiv.pfad.name + endung)
        if neben.exists():
            assert stat.S_IMODE(neben.stat().st_mode) == MODUS


# -- Die Markierung ist der Typ, nicht die Spalte ---------------------------

def test_alles_kommt_tainted_zurueck(archiv):
    archiv.schreiben(ART_OCR, "Kontonummer DE00", stufe=STUFE_FULL)
    (eintrag,) = archiv.lesen(ART_OCR)
    assert isinstance(eintrag["wert"], Marked)
    assert eintrag["wert"].mark is Mark.TAINTED
    assert eintrag["wert"].value == "Kontonummer DE00"
    # Es gibt keine Spalte, die man vergessen koennte -- also auch keine,
    # die jemand auf `trusted` setzen kann.
    spalten = {z[1] for z in archiv.oeffnen().execute(
        "PRAGMA table_info(archiv)")}
    assert "mark" not in spalten and "__mark__" not in spalten


# -- Was gar nicht hineindarf ----------------------------------------------

def test_rohaudio_wird_abgewiesen(archiv):
    for art in ("audio", "rohaudio", "pcm", "wav"):
        with pytest.raises(ArchivFehler, match="Rohaudio"):
            archiv.schreiben(art, "...")
    # Positivkontrolle: das Transkript derselben Aeusserung darf.
    assert archiv.schreiben(ART_TRANSKRIPT, "guten Morgen") > 0


def test_art_ohne_frist_wird_abgewiesen(archiv):
    with pytest.raises(ArchivFehler, match="Aufbewahrungsfrist"):
        archiv.schreiben("clipboard", "geheim")


def test_transient_schreibt_nicht_und_metadata_ohne_inhalt(archiv):
    archiv.schreiben(ART_OCR, "steht nur im Speicher",
                     stufe=STUFE_TRANSIENT)
    assert archiv.lesen(ART_OCR) == []
    archiv.schreiben(ART_OCR, "Inhalt weg", fenster="Mail",
                     stufe=STUFE_METADATA)
    (eintrag,) = archiv.lesen(ART_OCR)
    assert eintrag["wert"].value == ""
    assert eintrag["fenster"] == "Mail"      # Herkunft bleibt


# -- Verfall: der Text ueberlebt das Bild ----------------------------------

def test_frames_nach_48h_weg_text_nach_30_tagen(archiv):
    uhr = archiv.uhr
    archiv.schreiben(ART_FRAME, daten=b"\xff\xd8jpeg")
    archiv.schreiben(ART_OCR, "eine halbe Mail")
    assert len(archiv.lesen()) == 2                     # Positivkontrolle

    uhr.t += AUFBEWAHRUNG[ART_FRAME] + 1.0
    bericht = archiv.aufraeumen()
    assert bericht["verfallen"] == {ART_FRAME: 1}
    assert archiv.lesen(ART_FRAME) == []
    assert len(archiv.lesen(ART_OCR)) == 1, "der Text hat das Bild nicht ueberlebt"

    uhr.t += AUFBEWAHRUNG[ART_OCR]
    archiv.aufraeumen()
    assert archiv.lesen(ART_OCR) == []


# -- Die harte Obergrenze verdraengt, sie meldet keinen Fehler -------------

def test_obergrenze_verdraengt_die_aeltesten(archiv):
    uhr = archiv.uhr
    for i in range(400):
        uhr.t += 1.0
        archiv.schreiben(ART_OCR, f"{i:04d}" + "x" * 100)
    assert archiv.belegung() > archiv.grenze_bytes      # Positivkontrolle

    bericht = archiv.aufraeumen()
    assert bericht["verdraengt"] > 0
    assert archiv.belegung() <= archiv.grenze_bytes
    # Verdraengt wurden die AELTESTEN: der letzte Eintrag steht noch.
    juengste = archiv.lesen(ART_OCR, hoechstens=1)[0]
    assert juengste["wert"].value.startswith("0399")


# -- Volltextsuche ---------------------------------------------------------

def test_suche_findet_und_verliert_geloeschtes(archiv):
    uhr = archiv.uhr
    archiv.schreiben(ART_OCR, "Kanarienvogel im Posteingang", fenster="Mail")
    (treffer,) = archiv.suchen("Kanarienvogel")
    assert treffer["wert"].mark is Mark.TAINTED
    assert "Kanarienvogel" in treffer["wert"].value
    assert archiv.suchen("Mail") != []                 # auch der Fenstertitel

    # Nach dem Verfall ist der Treffer auch aus dem Index weg -- sonst
    # ueberlebte der Mitschnitt seinen eigenen Verfall im Volltextindex.
    uhr.t += AUFBEWAHRUNG[ART_OCR] + 1.0
    archiv.aufraeumen()
    assert archiv.suchen("Kanarienvogel") == []
    # Positivkontrolle NACH dem Aufraeumen: der Index findet weiterhin, was
    # da ist -- sonst waere "nicht gefunden" auch dann gruen, wenn die Suche
    # das Aufraeumen nicht ueberlebt haette.
    archiv.schreiben(ART_TITEL, "Wetterbericht")
    assert archiv.suchen("Wetterbericht") != []


def test_alles_loeschen_laesst_keine_datei(archiv):
    archiv.schreiben(ART_OCR, "weg damit")
    assert archiv.alles_loeschen() == 1
    for endung in ("", "-wal", "-shm"):
        assert not os.path.exists(str(archiv.pfad) + endung)
