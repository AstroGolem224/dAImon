"""T-5.2 — die Portal-ScreenCast-Sitzung.

Der Kern der Zusage ist „Bildschirmzugriff mit genau EINEM Klick": beim ersten
Mal fragt das Portal, danach traegt der `restore_token`. Alles, was diesen
Token betrifft, ist deshalb hier gepruft -- und zwar an der Datei, nicht an
einer Selbstauskunft.

Der DBus-Teil ist EINSPEISBAR (`portal=`). Ein Test, der einen echten
Portal-Dialog braucht, wird nie gefahren; ein Test, der die Folge gegen eine
Attrappe fuehrt, prueft genau die Reihenfolge und die Optionen, auf die es
ankommt. Der Live-Teil bleibt dem Verifizierer und einem Menschen.
"""
from __future__ import annotations

import json
import stat

import pytest

from daimon.eyes import screencast as sc


class PortalAttrappe:
    """So viel Portal, wie `PortalSitzung` anfasst. Merkt sich jeden Aufruf."""

    def __init__(self, *, token_zurueck: str = "T-neu",
                 scheitert_bei_restore: bool = False) -> None:
        self.aufrufe: list[tuple[str, dict]] = []
        self.token_zurueck = token_zurueck
        self.scheitert_bei_restore = scheitert_bei_restore
        self.geschlossen = 0

    def anfrage(self, methode: str, optionen: dict) -> dict:
        self.aufrufe.append((methode, dict(optionen)))
        if methode == "CreateSession":
            return {"session_handle": "/org/x/session/1"}
        if methode == "SelectSources":
            if self.scheitert_bei_restore and "restore_token" in optionen:
                raise sc.PortalFehler("restore_token abgelehnt")
            return {}
        if methode == "Start":
            return {"restore_token": self.token_zurueck,
                    "streams": [(42, {})]}
        raise AssertionError(f"unerwartete Methode {methode}")

    def schliessen(self) -> None:
        self.geschlossen += 1

    def optionen(self, methode: str) -> dict:
        for m, o in self.aufrufe:
            if m == methode:
                return o
        raise AssertionError(f"{methode} wurde nie gerufen")


def sitzung(tmp_path, portal=None, **kw):
    return sc.PortalSitzung(token_datei=tmp_path / "screencast-token",
                            portal=portal or PortalAttrappe(), **kw)


# -- Die gepinnten Optionen ------------------------------------------------

def test_persist_mode_und_cursor_mode_stehen_fest(tmp_path):
    p = PortalAttrappe()
    sitzung(tmp_path, p).oeffnen()
    o = p.optionen("SelectSources")
    assert o["persist_mode"] == 2          # EXPLICITLY_REVOKED
    assert o["cursor_mode"] == 4           # METADATA
    assert o["types"] == 1                 # MONITOR
    assert o["multiple"] is False


def test_ohne_token_wird_keiner_mitgeschickt(tmp_path):
    """Der erste Lauf hat keinen -- ein leerer String waere ein ungueltiger."""
    p = PortalAttrappe()
    sitzung(tmp_path, p).oeffnen()
    assert "restore_token" not in p.optionen("SelectSources")


# -- Der Token: die eigentliche Zusage -------------------------------------

def test_token_wird_nach_jedem_start_ueberschrieben(tmp_path):
    """Ein Token, der nach dem Start stehen bleibt, ist der von gestern."""
    datei = tmp_path / "screencast-token"
    s = sc.PortalSitzung(token_datei=datei, portal=PortalAttrappe(token_zurueck="A"))
    s.oeffnen()
    assert json.loads(datei.read_text())["current"] == "A"

    s2 = sc.PortalSitzung(token_datei=datei, portal=PortalAttrappe(token_zurueck="B"))
    s2.oeffnen()
    assert json.loads(datei.read_text())["current"] == "B"


def test_die_tokendatei_hat_modus_0600(tmp_path):
    datei = tmp_path / "screencast-token"
    sc.PortalSitzung(token_datei=datei, portal=PortalAttrappe()).oeffnen()
    assert stat.S_IMODE(datei.stat().st_mode) == 0o600


def test_ein_vorhandener_token_wird_mitgeschickt(tmp_path):
    datei = tmp_path / "screencast-token"
    sc.PortalSitzung(token_datei=datei, portal=PortalAttrappe(token_zurueck="A")).oeffnen()
    p = PortalAttrappe(token_zurueck="B")
    sc.PortalSitzung(token_datei=datei, portal=p).oeffnen()
    assert p.optionen("SelectSources")["restore_token"] == "A"


# -- Der ungueltige Token: Rueckfall statt Haengen -------------------------

def test_ungueltiger_token_faellt_interaktiv_zurueck(tmp_path):
    """Kein stilles Haengen: die Sitzung wird OHNE Token neu versucht."""
    datei = tmp_path / "screencast-token"
    datei.write_text(json.dumps({"current": "kaputt", "history": []}))
    p = PortalAttrappe(token_zurueck="C", scheitert_bei_restore=True)
    s = sc.PortalSitzung(token_datei=datei, portal=p)
    ergebnis = s.oeffnen()
    assert ergebnis["interaktiver_rueckfall"] is True
    versuche = [o for m, o in p.aufrufe if m == "SelectSources"]
    assert len(versuche) == 2
    assert "restore_token" in versuche[0] and "restore_token" not in versuche[1]
    assert json.loads(datei.read_text())["current"] == "C"


# -- SHM statt DmaBuf ------------------------------------------------------

@pytest.mark.parametrize("typ, erlaubt", [
    ("MemPtr", True), ("MemFd", True), ("DmaBuf", False),
])
def test_dmabuf_wird_abgelehnt_statt_schwarz_zu_liefern(typ, erlaubt):
    """Ein schwarzes Bild ist schlimmer als eine Fehlermeldung."""
    if erlaubt:
        assert sc.datentyp_pruefen(typ) is True
    else:
        with pytest.raises(sc.PortalFehler):
            sc.datentyp_pruefen(typ)


# -- Widerruf --------------------------------------------------------------

def test_widerruf_loescht_den_token_und_schliesst_die_sitzung(tmp_path):
    datei = tmp_path / "screencast-token"
    p = PortalAttrappe()
    s = sc.PortalSitzung(token_datei=datei, portal=p)
    s.oeffnen()
    assert datei.exists()
    s.widerrufen()
    assert not datei.exists()
    assert p.geschlossen == 1


def test_widerruf_ohne_offene_sitzung_ist_harmlos(tmp_path):
    s = sitzung(tmp_path)
    s.widerrufen()          # darf nicht werfen


# -- Der Widerrufsweg des Face (T-5.2, Kontextmenue) -----------------------

def test_die_widerrufsmarke_des_face_wird_erkannt_und_verbraucht(tmp_path):
    """Das Face schreibt eine Marke, es loescht den Token nicht selbst.

    Es kann es auch nicht: der Token liegt unter `$XDG_CONFIG_HOME/daimon/`,
    wo das Face `ProtectHome=read-only` hat -- und dort liegt der
    `anthropic-token`. Wer dem Overlay das Verzeichnis oeffnete, gaebe ihm
    Zugriff auf beides.
    """
    marke = tmp_path / "screencast-widerruf"
    datei = tmp_path / "screencast-token"
    p = PortalAttrappe()
    s = sc.PortalSitzung(token_datei=datei, portal=p, widerruf_marke=marke)
    s.oeffnen()

    assert s.widerruf_angefordert() is False
    marke.touch()
    assert s.widerruf_angefordert() is True

    bericht = s.widerrufen()
    assert bericht["token_geloescht"] is True
    assert not datei.exists()
    # Die Marke ist verbraucht -- sonst widerriefe der naechste Blick erneut.
    assert not marke.exists()
    assert s.widerruf_angefordert() is False
