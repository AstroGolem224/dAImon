"""T-4.7 — eine feste Operation je genehmigter Aktion, und keine sonst.

Der Kern ist NICHT "ein verbotener Aufruf faellt durch" -- das tut auch ein
Broker, der gar nichts kann. Der Kern ist: ein Kurzbefehl DERSELBEN
Komponente, der nicht freigegeben ist, faellt durch, waehrend der
freigegebene daneben ausgefuehrt wird.
"""
from __future__ import annotations

import pytest

from daimon.brokers.dbus.broker import (AUDIENCE, VERBOTEN, BrokerFehler,
                                        DBusBroker)
from daimon.common import order as fmt
from daimon.hub.order import Auftragsbuch
from daimon.hub.policy import Policy

KATALOG = {
    "media.playpause": {
        "status": "approved", "rationale": "selbstinvers",
        "kglobalaccel": ["mediacontrol", "playpausemedia"],
    },
    # Derselbe Ursprung, NICHT freigegeben. Das ist der Kern des Tasks.
    "media.evil": {
        "status": "candidate",
        "kglobalaccel": ["mediacontrol", "stopmedia"],
    },
    # Freigegeben, aber ohne kglobalaccel-Ursprung: gehoert einem anderen
    # Broker und bekommt hier keine Operation.
    "audio.volume.set": {
        "status": "approved", "rationale": "wertbeschraenkt",
    },
}


class LaufStub:
    def __init__(self, rc: int = 0) -> None:
        self.aufrufe: list[list[str]] = []
        self.rc = rc

    def __call__(self, argv, **_kw):
        self.aufrufe.append(list(argv))

        class E:
            returncode = self.rc
            stdout = ""
            stderr = "" if self.rc == 0 else "dbus sagt nein"
        return E()


def broker(rc: int = 0) -> tuple[DBusBroker, LaufStub]:
    stub = LaufStub(rc)
    return DBusBroker.aus_katalog(KATALOG, lauf=stub), stub


def auftrag_bytes(action_id: str, buch: Auftragsbuch, **kw) -> bytes:
    a = buch.ausstellen(audience=kw.pop("audience", AUDIENCE),
                        action_id=action_id, params=kw.pop("params", {}),
                        turn_id="r1", jetzt=0.0, **kw)
    return fmt.kanonisch(a)


# --------------------------------------------------------------------------
# Die Operationstabelle
# --------------------------------------------------------------------------

def test_nur_approved_bekommt_eine_operation():
    b, _ = broker()
    assert set(b.operationen) == {"media.playpause"}


def test_der_mitgelieferte_katalog_ergibt_lauter_feste_operationen():
    """Gegen die echte Datei, nicht gegen ein Muster."""
    pol = Policy.laden()
    b = DBusBroker.aus_katalog(pol.katalog)
    assert b.operationen, "keine einzige Operation gebaut"
    for kennung, op in b.operationen.items():
        assert op.dienst == "org.kde.kglobalaccel", kennung
        assert op.methode == "invokeShortcut", kennung
        # Der Aufruf liegt am Komponentenobjekt, und der Pfad ist ein
        # gueltiger DBus-Objektpfad -- Punkte sind darin nicht erlaubt.
        assert op.pfad.startswith("/component/"), kennung
        assert "." not in op.pfad, kennung
        # Nichts daran ist zur Laufzeit waehlbar: die Argumente stehen fest.
        assert op.argumente and all(isinstance(a, str) for a in op.argumente)
    # Die beiden Katalogeintraege ohne kglobalaccel-Ursprung bekommen hier
    # KEINE Operation -- sie gehoeren anderen Brokern.
    assert "audio.volume.set" not in b.operationen
    assert "kde.window.raise" not in b.operationen


def test_ein_name_der_die_argumentzeile_aufbrechen_koennte_wird_abgelehnt():
    with pytest.raises(BrokerFehler):
        DBusBroker.aus_katalog({"b.oese": {
            "status": "approved",
            "kglobalaccel": ["mediacontrol", "play', 'x', '', ''] ; rm -rf"]}})


# --------------------------------------------------------------------------
# Ausfuehren
# --------------------------------------------------------------------------

def test_positivkontrolle_ein_genehmigter_auftrag_wird_ausgefuehrt():
    b, stub = broker()
    buch = Auftragsbuch()
    e = b.ausfuehren(auftrag_bytes("media.playpause", buch), jetzt=1.0,
                     ticket_einloesen=lambda t: buch.einloesen(t, jetzt=1.0))
    assert e["ok"], e
    assert len(stub.aufrufe) == 1
    argv = stub.aufrufe[0]
    assert argv[0] == "gdbus"
    assert "org.kde.kglobalaccel" in argv
    assert "playpausemedia" in argv
    assert "/component/mediacontrol" in argv


def test_der_kern_nicht_genehmigt_derselben_komponente_wird_abgewiesen():
    b, stub = broker()
    buch = Auftragsbuch()
    e = b.ausfuehren(auftrag_bytes("media.evil", buch), jetzt=1.0,
                     ticket_einloesen=lambda t: buch.einloesen(t, jetzt=1.0))
    assert not e["ok"]
    assert e["grund"] == "keine_operation"
    # Und nichts ist passiert -- kein Aufruf, kein halber Aufruf.
    assert stub.aufrufe == []


def test_ein_auftrag_fuer_einen_anderen_broker_wird_abgewiesen():
    b, stub = broker()
    buch = Auftragsbuch()
    roh = auftrag_bytes("media.playpause", buch, audience="fs")
    e = b.ausfuehren(roh, jetzt=1.0, ticket_einloesen=lambda t: None)
    assert not e["ok"] and e["grund"] == "auftrag"
    assert stub.aufrufe == []


def test_ein_abgelaufener_auftrag_wird_abgewiesen():
    b, stub = broker()
    buch = Auftragsbuch()
    roh = auftrag_bytes("media.playpause", buch)
    e = b.ausfuehren(roh, jetzt=10_000.0, ticket_einloesen=lambda t: None)
    assert not e["ok"] and e["grund"] == "auftrag"
    assert stub.aufrufe == []


def test_das_ticket_wird_unmittelbar_vor_dem_aufruf_eingeloest():
    """Und genau einmal: der zweite Versuch fuehrt nichts aus."""
    b, stub = broker()
    buch = Auftragsbuch()
    roh = auftrag_bytes("media.playpause", buch)
    erst = b.ausfuehren(roh, jetzt=1.0,
                        ticket_einloesen=lambda t: buch.einloesen(t, jetzt=1.0))
    zweit = b.ausfuehren(roh, jetzt=1.0,
                         ticket_einloesen=lambda t: buch.einloesen(t, jetzt=1.0))
    assert erst["ok"]
    assert not zweit["ok"] and zweit["grund"] == "ticket"
    assert len(stub.aufrufe) == 1


def test_ein_fehlgeschlagener_dbus_aufruf_meldet_misserfolg():
    b, _ = broker(rc=2)
    buch = Auftragsbuch()
    e = b.ausfuehren(auftrag_bytes("media.playpause", buch), jetzt=1.0,
                     ticket_einloesen=lambda t: buch.einloesen(t, jetzt=1.0))
    assert not e["ok"] and e["grund"] == "dbus" and e["rc"] == 2


# --------------------------------------------------------------------------
# loadScript ist an beiden Schichten aus
# --------------------------------------------------------------------------

def test_kein_katalogeintrag_erzeugt_eine_scripting_operation():
    pol = Policy.laden()
    b = DBusBroker.aus_katalog(pol.katalog)
    for op in b.operationen.values():
        assert not any(v in op.schnittstelle for v in VERBOTEN)
        assert op.dienst != "org.kde.KWin"


def test_der_proxy_filter_schliesst_loadscript_aus():
    """Die zweite Schicht, gelesen als Datei statt behauptet."""
    from pathlib import Path
    text = Path("config/dbus-filter.conf").read_text(encoding="utf-8")
    zeilen = [z.strip() for z in text.splitlines()
              if z.strip() and not z.strip().startswith("#")]
    assert "--filter" in zeilen
    assert "--log" in zeilen
    assert any(z == "--call=org.kde.kwin.Scripting=none" for z in zeilen)
    assert any(z == "--call=org.kde.KWin=none" for z in zeilen)
    # Positivkontrolle: der eine erlaubte Aufruf steht auch wirklich drin.
    assert any("invokeShortcut" in z for z in zeilen)
    # Und nichts erlaubt Scripting.
    assert not any("Scripting" in z and z.endswith(("@/", "Scripting"))
                   for z in zeilen if "=none" not in z)
