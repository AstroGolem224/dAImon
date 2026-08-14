"""T-7.5 -- die Suche, und dass sie am Gate haengt.

Der Kanarienvogel liegt im Archiv und wird viermal befragt: ohne Schein,
ohne Marke, proaktiv -- er darf nie herauskommen. Und einmal unter Marke mit
Bezug: dann MUSS er. Ohne die letzte Probe pruefte der Rest nur, dass die
Suche kaputt ist.
"""
from __future__ import annotations

import pytest

from daimon.common.protocol import Mark
from daimon.hub.declassify import (
    Deklassifizierung, Freigabeschein, GateFehler, GRUND_PROAKTIV, zeitbezug)
from daimon.recorder.store import ART_OCR, ART_TITEL, ART_TRANSKRIPT, Archiv
from daimon.recorder.suche import Archivsuche, QuarantaeneFehler, suchbegriff

KANARIE = "Weizenbaum"
FRAGE = "was stand vorhin auf dem Bildschirm"


class Marken:
    """Ein Markenbuch, das genau eine Marke kennt und sie EINMAL hergibt."""

    def __init__(self, gueltig: str = "t-1") -> None:
        self.gueltig = gueltig
        self.eingeloest: list[str] = []

    def einloesen(self, turn_id: str) -> None:
        from daimon.hub.marks import MarkenFehler
        if turn_id != self.gueltig or turn_id in self.eingeloest:
            raise MarkenFehler("keine gueltige Marke")
        self.eingeloest.append(turn_id)


class LeererSpeicher:
    def freigeben(self, schein):
        assert type(schein).__name__ == "Freigabeschein"
        return {}


@pytest.fixture
def archiv_pfad(tmp_path):
    a = Archiv(tmp_path / "archiv.db")
    a.migrieren()
    a.schreiben(ART_OCR, f"Joseph {KANARIE} schrieb ueber ELIZA",
                fenster="Chromium")
    a.schreiben(ART_TITEL, "Notizen - Kate")
    a.schreiben(ART_TRANSKRIPT, "erinnere mich an den Vortrag")
    a.schliessen()
    return a.pfad


@pytest.fixture
def gate(archiv_pfad):
    return Deklassifizierung(marken=Marken(), speicher=LeererSpeicher(),
                             archiv=Archivsuche(archiv_pfad))


# -- Ohne Schein kommt nichts heraus ---------------------------------------

def test_suche_ohne_schein_scheitert(archiv_pfad):
    s = Archivsuche(archiv_pfad)
    with pytest.raises(QuarantaeneFehler, match="Freigabeschein"):
        s.freigeben(True, KANARIE)          # ein Wahrheitswert ist kein Schein
    with pytest.raises(QuarantaeneFehler):
        s.freigeben(None, KANARIE)
    # Positivkontrolle: MIT Schein ist der Kanarienvogel da.
    treffer = s.freigeben(Freigabeschein(turn_id="t-1"), KANARIE)
    assert any(KANARIE in t.value for t in treffer)


def test_treffer_sind_tainted(archiv_pfad):
    treffer = Archivsuche(archiv_pfad).freigeben(
        Freigabeschein(turn_id="t-1"), KANARIE)
    assert treffer and all(t.mark is Mark.TAINTED for t in treffer)


def test_nur_der_treffer_nicht_die_umgebung(archiv_pfad):
    """Drei Eintraege liegen im Archiv, einer passt -- einer kommt."""
    treffer = Archivsuche(archiv_pfad).freigeben(
        Freigabeschein(turn_id="t-1"), KANARIE)
    assert len(treffer) == 1
    assert "Notizen" not in treffer[0].value


def test_die_datenbank_wird_nur_lesend_geoeffnet(archiv_pfad):
    import sqlite3
    s = Archivsuche(archiv_pfad)
    db = s._lesen_nur()
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT INTO archiv (art, ts, text) VALUES ('ocr',1,'x')")
    finally:
        db.close()


def test_kaputter_suchbegriff_gibt_keine_treffer_statt_absturz(archiv_pfad):
    s = Archivsuche(archiv_pfad)
    assert s.freigeben(Freigabeschein(turn_id="t-1"), 'NEAR("') == []


def test_suchbegriff_entschaerft_operatoren():
    begriff = suchbegriff('NEAR(bildschirm passwort) OR archiv:geheim')
    assert begriff.startswith('"')
    assert "NEAR(" not in begriff.replace('"NEAR(bildschirm', "")
    # Kurze Fuellwoerter fallen weg, damit die Suche nicht alles trifft.
    assert '"was"' not in suchbegriff("was stand da vorhin")


# -- Das Gate entscheidet, nicht die Suche ---------------------------------

def test_zeitbezug_ist_eng():
    assert zeitbezug("was stand vorhin da")
    assert zeitbezug("was war gestern auf dem Bildschirm")
    assert not zeitbezug("mach das nochmal")
    assert not zeitbezug("zeig mir wieder das Fenster")
    assert not zeitbezug("")


def test_ohne_marke_kein_archivtreffer(gate):
    with pytest.raises(GateFehler):
        gate.freigeben(aeusserung=FRAGE, turn_id=None)


def test_proaktiv_sieht_das_archiv_nicht(gate):
    with pytest.raises(GateFehler) as exc:
        gate.freigeben(aeusserung=FRAGE, turn_id="t-1", proaktiv=True)
    assert exc.value.grund == GRUND_PROAKTIV
    # Und die Marke ist NICHT verbraucht: der proaktive Versuch darf dem
    # Nutzer seine Runde nicht wegnehmen. Dieselbe Marke traegt danach eine
    # echte Frage -- samt Treffer, weil der Kanarienvogel diesmal drinsteht.
    freigabe = gate.freigeben(aeusserung=f"{FRAGE} von {KANARIE}",
                              turn_id="t-1")
    assert any(KANARIE in t.value for t in freigabe.archiv)


def test_mit_marke_und_bezug_kommt_der_kanarienvogel(gate):
    freigabe = gate.freigeben(aeusserung=f"{FRAGE} von {KANARIE}",
                              turn_id="t-1")
    assert any(KANARIE in t.value for t in freigabe.archiv)
    assert freigabe.umfang["archiv"] == len(freigabe.archiv)


def test_ohne_zeitbezug_wird_nicht_gesucht(gate):
    """Die Frage nach dem JETZT ist keine Frage nach dreissig Tagen."""
    freigabe = gate.freigeben(aeusserung="was steht auf dem Bildschirm",
                              turn_id="t-1")
    assert freigabe.archiv == []
    assert "archiv" not in freigabe.umfang


def test_ein_klemmendes_archiv_reisst_die_freigabe_nicht_mit(archiv_pfad):
    class Kaputt:
        def freigeben(self, schein, anfrage):
            raise RuntimeError("Datenbank weg")

    g = Deklassifizierung(marken=Marken(), speicher=LeererSpeicher(),
                          archiv=Kaputt())
    freigabe = g.freigeben(aeusserung=FRAGE, turn_id="t-1")
    assert freigabe.archiv == []
    assert freigabe.turn_id == "t-1"
