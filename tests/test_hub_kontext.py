"""T-5.9b -- das verdrahtete Gate, am Hub gemessen.

Der Kanarienvogel liegt im Kontextspeicher und wird viermal befragt: ohne
Rundenmarke, ohne Bildschirmbezug, mit unlesbarer Anfrage -- er darf nie
heraus. Und einmal mit Marke und Bezug: dann MUSS er. Ohne die letzte Probe
pruefte der Rest nur, dass die Verdrahtung fehlt.
"""
from __future__ import annotations

import pytest

from daimon.eyes import context
from daimon.hub.declassify import Deklassifizierung
from daimon.hub.marks import MarkenBuch
from daimon.recorder.store import ART_OCR, Archiv
from daimon.recorder.suche import Archivsuche

KANARIE = "Kontonummer DE00 1234"
FRAGE = "was steht auf dem Bildschirm"


class HubStub:
    """Nur die Teile, die `kontext_anfrage` anfasst."""

    def __init__(self, gate, marken, log, diag) -> None:
        self._gate, self.marken, self.log, self.diag = gate, marken, log, diag

    _gate_teile = lambda self: self._gate      # noqa: E731
    kontext_anfrage = None                     # wird unten gebunden


class Log:
    def warn(self, *a, **k): pass
    def info(self, *a, **k): pass


class Diag:
    def __init__(self): self.verworfen_gruende = []
    def verworfen(self, grund): self.verworfen_gruende.append(grund)


@pytest.fixture
def hub(tmp_path):
    from daimon.hub.daemon import Hub

    speicher = context.Kontextspeicher(verzeichnis=tmp_path / "context")
    speicher.hinzufuegen(context.ART_OCR, "org.kde.kate", KANARIE)

    archiv = Archiv(tmp_path / "archiv.db")
    archiv.migrieren()
    archiv.schreiben(ART_OCR, "vorgestern stand hier Weizenbaum",
                     fenster="Chromium")
    archiv.schliessen()

    marken = MarkenBuch(log=Log())
    gate = Deklassifizierung(marken=marken, speicher=speicher,
                             archiv=Archivsuche(tmp_path / "archiv.db"))
    stub = HubStub(gate, marken, Log(), Diag())
    stub.kontext_anfrage = Hub.kontext_anfrage.__get__(stub, HubStub)
    return stub


# -- Was nicht herauskommt -------------------------------------------------

def test_ohne_rundenmarke_kein_kontext(hub):
    antwort = hub.kontext_anfrage({"art": "deklassifizieren", "text": FRAGE})
    assert antwort["ok"] is False
    assert antwort["grund"] == "keine_marke"


def test_ohne_bildschirmbezug_kein_kontext(hub):
    hub.marken.ausgeben(quelle="auth", turn_id="t-1")
    antwort = hub.kontext_anfrage({"art": "deklassifizieren",
                                   "text": "wie spaet ist es"})
    assert antwort["ok"] is False
    assert antwort["grund"] == "kein_bildschirmbezug"
    # Die Marke ist NICHT verbrannt -- der Nutzer kann in derselben Runde
    # nachfassen.
    assert hub.marken.aktuelle() == "t-1"


def test_unlesbares_und_fremde_art(hub):
    assert hub.kontext_anfrage("kein dict")["grund"] == "unlesbar"
    assert hub.kontext_anfrage({"art": "lesen"})["grund"] == "unbekannte_art"
    assert hub.kontext_anfrage({"art": "deklassifizieren",
                                "text": "  "})["grund"] == "kein_text"


def test_die_turn_id_aus_der_anfrage_wird_ignoriert(hub):
    """Ein Absender, der die Runde nennt, bekommt trotzdem nichts."""
    antwort = hub.kontext_anfrage({"art": "deklassifizieren", "text": FRAGE,
                                   "turn_id": "geraten"})
    assert antwort["ok"] is False and antwort["grund"] == "keine_marke"


# -- Was herauskommt -------------------------------------------------------

def test_mit_marke_und_bezug_kommt_der_kontext(hub):
    hub.marken.ausgeben(quelle="auth", turn_id="t-1")
    antwort = hub.kontext_anfrage({"art": "deklassifizieren", "text": FRAGE})
    assert antwort["ok"] is True
    assert any(KANARIE in e for e in antwort["eintraege"])
    assert antwort["senke"] == "durchgang2"
    # Einmal eingeloest: die zweite Frage derselben Runde bekommt nichts.
    assert hub.marken.aktuelle() is None


def test_archivtreffer_nur_bei_zeitbezug(hub):
    hub.marken.ausgeben(quelle="auth", turn_id="t-1")
    ohne = hub.kontext_anfrage({"art": "deklassifizieren", "text": FRAGE})
    assert ohne["archiv"] == []

    hub.marken.ausgeben(quelle="auth", turn_id="t-2")
    mit = hub.kontext_anfrage({
        "art": "deklassifizieren",
        "text": "was stand vorgestern auf dem Bildschirm von Weizenbaum"})
    assert any("Weizenbaum" in e for e in mit["archiv"])


def test_ein_klemmendes_gate_gibt_eine_absage_statt_eines_absturzes(hub):
    class Kaputt:
        def freigeben(self, **_):
            raise RuntimeError("Speicher weg")

    hub._gate = Kaputt()
    antwort = hub.kontext_anfrage({"art": "deklassifizieren", "text": FRAGE})
    assert antwort["ok"] is False and antwort["grund"] == "gate_weg"
