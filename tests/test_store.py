"""T-6.1 -- die Datenbank.

Der Punkt, an dem alles haengt, ist der Rundgang der MARKIERUNG. Der Plan sagt
es ausdruecklich: sie soll den Weg durch die Datenbank als Typ ueberleben und
nicht als Spalte, die man vergessen kann. Hier wird deshalb nicht geprueft,
dass eine Spalte da ist, sondern was zurueckkommt, wenn sie es NICHT ist.
"""
from __future__ import annotations

import json
import sqlite3
import stat

import pytest

from daimon.common.protocol import Mark, Marked
from daimon.mind import store as st


def db(tmp_path, **kw):
    s = st.Store(tmp_path / "memory.db", **kw)
    s.migrieren()
    return s


# -- Datei und Rechte ------------------------------------------------------

def test_die_datenbank_hat_modus_0600(tmp_path):
    s = db(tmp_path)
    assert stat.S_IMODE(s.pfad.stat().st_mode) == 0o600


def test_auch_die_wal_datei_hat_0600(tmp_path):
    """Im WAL stehen dieselben Zeilen. Eine Datenbank mit 0600 neben einem WAL
    mit 0644 ist eine Datenbank mit 0644."""
    s = db(tmp_path)
    s.schreiben("notiz", Marked("etwas", Mark.USER_PTT))
    wal = tmp_path / "memory.db-wal"
    if wal.exists():                       # WAL erscheint erst beim Schreiben
        assert stat.S_IMODE(wal.stat().st_mode) == 0o600


# -- Migrationen, vor und zurueck -----------------------------------------

def test_migration_hinauf_setzt_die_version(tmp_path):
    s = st.Store(tmp_path / "memory.db")
    assert s.version() == 0
    assert s.migrieren() == st.SCHEMA_VERSION


def test_migration_hinunter_und_wieder_hinauf(tmp_path):
    """Eine Migration ohne Rueckweg ist keine Migration, sondern eine
    Einbahnstrasse mit Schemaversion."""
    s = db(tmp_path)
    s.schreiben("notiz", Marked("etwas", Mark.USER_PTT))
    assert s.migrieren(0) == 0
    with pytest.raises(sqlite3.OperationalError):
        s.oeffnen().execute("SELECT 1 FROM eintraege")
    assert s.migrieren() == st.SCHEMA_VERSION
    s.schreiben("notiz", Marked("wieder da", Mark.USER_PTT))
    assert len(s.lesen("notiz")) == 1


def test_ein_zwischenschritt_wird_erreicht(tmp_path):
    s = db(tmp_path)
    assert s.migrieren(1) == 1
    # Version 2 fuegt `turn_id` hinzu -- auf 1 gibt es die Spalte nicht.
    spalten = [r[1] for r in s.oeffnen().execute(
        "PRAGMA table_info(eintraege)").fetchall()]
    assert "turn_id" not in spalten


def test_eine_unmoegliche_zielversion_wird_abgelehnt(tmp_path):
    s = db(tmp_path)
    with pytest.raises(st.StoreFehler):
        s.migrieren(99)
    with pytest.raises(st.StoreFehler):
        s.migrieren(-1)


# -- Die Markierung ueberlebt ---------------------------------------------

@pytest.mark.parametrize("marke", list(Mark))
def test_jede_markierung_kommt_unveraendert_zurueck(tmp_path, marke):
    s = db(tmp_path)
    s.schreiben("notiz", Marked("Inhalt", marke))
    zurueck = s.lesen("notiz")[0]["wert"]
    assert isinstance(zurueck, Marked)
    assert zurueck.mark is marke
    assert zurueck.value == "Inhalt"


def test_ein_nackter_wert_wird_tainted(tmp_path):
    """Nicht weil das haeufig richtig ist, sondern weil das die harmlose
    Richtung ist, wenn jemand es vergisst."""
    s = db(tmp_path)
    s.schreiben("notiz", "einfach so")
    assert s.lesen("notiz")[0]["wert"].mark is Mark.TAINTED


def test_eine_verlorene_markierung_kommt_als_TAINTED_zurueck(tmp_path):
    """Der Mutationstest, den der Plan an dieser Grenze verlangt.

    Hier wird die Markierung in der Datenbank absichtlich zerstoert -- so wie
    es ein Fehler taete, der die Spalte vergisst. Zurueckkommen darf dann
    NICHT `trusted`.
    """
    s = db(tmp_path)
    s.schreiben("notiz", Marked("geheim", Mark.TRUSTED))
    s.oeffnen().execute("UPDATE eintraege SET wert = ?",
                        (json.dumps("geheim"),))
    zurueck = s.lesen("notiz")[0]["wert"]
    assert zurueck.mark is Mark.TAINTED
    assert zurueck.value == "geheim"


def test_ein_unlesbarer_eintrag_kommt_als_TAINTED_zurueck(tmp_path):
    """Ein kaputter Eintrag, der als vertrauenswuerdig zurueckkommt, waere der
    schlimmste Ausgang dieses Moduls."""
    s = db(tmp_path)
    s.schreiben("notiz", Marked("x", Mark.TRUSTED))
    s.oeffnen().execute("UPDATE eintraege SET wert = 'kein json'")
    assert s.lesen("notiz")[0]["wert"].mark is Mark.TAINTED


# -- Kein Sitzungszustand --------------------------------------------------

@pytest.mark.parametrize("art", ["mood", "session_mood", "listening",
                                 "tts_active", "MOOD", " zustand "])
def test_sitzungszustand_wird_aktiv_verweigert(tmp_path, art):
    """Wer ihn speichert, hat nach einem Absturz ein Pet, das sich fuer wach
    haelt, waehrend niemand da ist. Eine Ausnahme ist besser als eine
    Konvention -- eine Konvention haelt sich, bis jemand es eilig hat.
    """
    s = db(tmp_path)
    with pytest.raises(st.StoreFehler) as f:
        s.schreiben(art, Marked("awake", Mark.TRUSTED))
    assert "sleeping" in str(f.value)


def test_eine_erinnerung_mit_aehnlichem_namen_geht_durch(tmp_path):
    """Positiv-Kanarienvogel: die Sperre darf nicht alles verbieten."""
    s = db(tmp_path)
    assert s.schreiben("moodboard", Marked("x", Mark.USER_PTT)) > 0


def test_eine_leere_art_wird_abgelehnt(tmp_path):
    s = db(tmp_path)
    with pytest.raises(st.StoreFehler):
        s.schreiben("   ", Marked("x", Mark.USER_PTT))


# -- Lesen -----------------------------------------------------------------

def test_gelesen_wird_nach_art_und_zeit(tmp_path):
    s = db(tmp_path)
    s.schreiben("a", Marked("eins", Mark.USER_PTT), ts=100.0)
    s.schreiben("a", Marked("zwei", Mark.USER_PTT), ts=200.0)
    s.schreiben("b", Marked("drei", Mark.USER_PTT), ts=300.0)
    assert [e["wert"].value for e in s.lesen("a")] == ["zwei", "eins"]
    assert [e["wert"].value for e in s.lesen(seit=250.0)] == ["drei"]


def test_die_turn_id_kommt_zurueck(tmp_path):
    """T-6.7b prueft Rundengrenzen -- ohne sie liesse sich nicht sagen, aus
    welcher Runde ein Eintrag stammt."""
    s = db(tmp_path)
    s.schreiben("notiz", Marked("x", Mark.USER_PTT), turn_id="t-42")
    assert s.lesen("notiz")[0]["turn_id"] == "t-42"


# -- Ein Befehl loescht alles ---------------------------------------------

def test_loeschen_entfernt_zeilen_UND_datei(tmp_path):
    """`DELETE FROM` allein laesst die Seiten in der Datei stehen -- wer sie
    danach mit einem Hex-Editor oeffnet, findet seine Erinnerungen wieder."""
    s = db(tmp_path)
    s.schreiben("notiz", Marked("streng geheim", Mark.USER_PTT))
    s.schreiben("notiz", Marked("noch eins", Mark.USER_PTT))
    assert s.alles_loeschen() == 2
    assert not s.pfad.exists()
    assert not (tmp_path / "memory.db-wal").exists()


def test_nach_dem_loeschen_laesst_sich_weiterarbeiten(tmp_path):
    s = db(tmp_path)
    s.schreiben("notiz", Marked("x", Mark.USER_PTT))
    s.alles_loeschen()
    s.migrieren()
    assert s.schreiben("notiz", Marked("neu", Mark.USER_PTT)) > 0
    assert len(s.lesen("notiz")) == 1
