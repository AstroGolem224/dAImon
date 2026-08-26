"""T-8.4 -- ein faelliger Termin wird zur Blase, nicht zu einer Handlung.

Die Naht, um die es geht: `plan.sock` (Produzent) -> `_bediene_produzent`
-> `state.warnblase` -> Schnappschuss. Kein Glied wird ersetzt. Geprueft
wird auch die andere Richtung: der Produzent `plan` kann NUR die beiden
Meldetypen -- wer `hook` oder `freigabe` sendet, fliegt raus.
"""
from __future__ import annotations

import io
import json
import socket
import time
from pathlib import Path

import pytest

from daimon.common import ipc
from daimon.common.logging import get_logger
from daimon.hub.daemon import STATE_SOCKET, Hub
from conftest import eigene_unit


@pytest.fixture(autouse=True)
def _produzenten_duerfen_melden(monkeypatch, tmp_path):
    """Dieselbe Vorkehrung wie in test_hub.py: die Allowlist wird mit der
    Unit DES TESTPROZESSES gefuellt, echt gemessen. Die Sperre selbst prueft
    `test_hub_socket_allowlisten.py`."""
    from daimon.hub import daemon as _D
    eigene = eigene_unit(tmp_path)
    monkeypatch.setattr(_D, "PRODUZENT_UNITS",
                        {p: (eigene,) for p in _D.PRODUZENT_UNITS})


def stiller_logger():
    return get_logger("test", socket_path="/nicht/da", stream=io.StringIO())


@pytest.fixture
def hub(tmp_path):
    h = Hub(runtime_dir=tmp_path / "rt", log=stiller_logger())
    h.start()
    time.sleep(0.3)
    yield h
    h.stop()


def sende(rt: Path, payload: dict, typ: str, produzent: str = "plan") -> None:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / f"{produzent}.sock"))
    c.sendall(json.dumps({"v": 1, "type": typ, "payload": payload}).encode() + b"\n")
    time.sleep(0.25)
    c.close()


def hole_state(rt: Path) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / STATE_SOCKET))
    try:
        return json.loads(c.makefile("rb").readline())
    finally:
        c.close()


# -- Die Kette ---------------------------------------------------------------

def test_termin_faellig_wird_zur_blase(hub):
    sende(hub.runtime_dir, {"titel": "Erinnerung", "text": "Tee"}, "termin_faellig")
    s = hole_state(hub.runtime_dir)
    assert s["bubble"] is not None
    assert s["bubble"]["title"] == "Erinnerung"
    assert s["bubble"]["body"] == "Tee"
    # NICHT dringend: `urgent` ist der Auditwarnung vorbehalten.
    assert s["bubble"]["urgent"] is False


def test_eine_erinnerung_ueberschreibt_die_auditwarnung_nicht(hub):
    """Der Befund vom 26.08.: beide schrieben in denselben einen Blasenplatz.
    Ein faelliger Termin loeschte die Meldung „Audit-Kette gerissen" binnen
    einer Abtastrunde (15 s) -- die Auditpruefung laeuft erst stuendlich
    wieder."""
    hub.state.warnblase("Audit-Kette gerissen", "1 Abweichung.")
    sende(hub.runtime_dir, {"titel": "Erinnerung", "text": "Tee"}, "termin_faellig")
    s = hole_state(hub.runtime_dir)
    assert s["bubble"]["title"] == "Audit-Kette gerissen"
    assert s["bubble"]["urgent"] is True

    # Positivkontrolle: weggeklickt, und die naechste Erinnerung kommt an --
    # ein Hub, der die zweite Blase generell verschluckt, bestuende den Test
    # oben ebenfalls.
    hub.state.clear_bubble()
    sende(hub.runtime_dir, {"titel": "Erinnerung", "text": "Tee"}, "termin_faellig")
    assert hole_state(hub.runtime_dir)["bubble"]["title"] == "Erinnerung"


def test_die_zurueckgestellte_erinnerung_kommt_nach_dem_wegklicken(hub):
    """Der zweite Teil desselben Befunds: zurueckgestellt hiess bisher weg.

    Der Plan-Dienst schickt fire-and-forget und markiert den Eintrag trotzdem
    `gemeldet` -- solange die Auditwarnung stand, verschluckte der Hub also
    JEDEN faelligen Termin lautlos. Die Naht laeuft ueber den echten
    Produzentensocket und den echten Schnappschuss.
    """
    hub.state.warnblase("Audit-Kette gerissen", "1 Abweichung.")
    sende(hub.runtime_dir, {"titel": "Erinnerung", "text": "Zahnarzt"},
          "termin_faellig")
    assert hole_state(hub.runtime_dir)["bubble"]["urgent"] is True

    hub.state.clear_bubble()                 # der Nutzer klickt die Warnung weg
    s = hole_state(hub.runtime_dir)
    assert s["bubble"] is not None, "die Erinnerung war fuer immer weg"
    assert s["bubble"]["title"] == "Erinnerung"
    assert s["bubble"]["body"] == "Zahnarzt"
    assert s["bubble"]["urgent"] is False

    # Und danach ist das Schliessfach leer -- ein zweites Wegklicken darf
    # dieselbe Erinnerung nicht noch einmal hervorholen.
    hub.state.clear_bubble()
    assert hole_state(hub.runtime_dir)["bubble"] is None


def test_fokus_ende_wird_zur_blase(hub):
    sende(hub.runtime_dir, {"titel": "Fokus vorbei", "text": "Kurz durchatmen."},
          "fokus_ende")
    s = hole_state(hub.runtime_dir)
    assert s["bubble"]["title"] == "Fokus vorbei"


def test_der_plan_socket_existiert_vor_dem_dienst(hub):
    """Der Hub oeffnet den Socket, der Planer verbindet sich -- ein Socket,
    der erst mit dem Dienst kaeme, liesse die ersten Erinnerungen ins Leere
    laufen."""
    assert (hub.runtime_dir / "plan.sock").exists()


def test_blasentext_aus_dem_plan_wird_gesaeubert(hub):
    """Dieselbe Saeuberung wie Hook-Text: der Titel kommt aus einer
    Datenbank, in die auch Transkripte geschrieben werden."""
    sende(hub.runtime_dir, {"titel": "Erinnerung", "text": "böse‮txt​"},
          "termin_faellig")
    body = hole_state(hub.runtime_dir)["bubble"]["body"]
    assert body.isascii()


# -- Die Grenze ---------------------------------------------------------------

def test_plan_darf_keinen_hook_typ_senden(hub):
    """`pruefe_typ` weist ab und trennt die Verbindung -- ein Produzent in
    fremder Rolle ist der Fall, um den es in T-0.7 geht."""
    sende(hub.runtime_dir, {"hook_event_name": "Notification",
                            "session_id": "s1"}, "hook")
    s = hole_state(hub.runtime_dir)
    assert s["mood"] == "sleeping"        # der "Hook" hat NICHT gewirkt


def test_plan_darf_keine_freigabe_senden(hub):
    sende(hub.runtime_dir, {}, "freigabe")
    assert hole_state(hub.runtime_dir)["bubble"] is None


def test_der_typ_steht_in_der_geschlossenen_menge():
    """Die beiden Typen -- und keine weiteren. Wer einen dritten will,
    aendert diese Zeile und denkt dabei ueber die Wirkung nach."""
    assert ipc.PRODUZENTEN["plan"] == frozenset({"termin_faellig", "fokus_ende"})


# -- Positivkontrolle -----------------------------------------------------------

def test_positivkontrolle_die_blase_kann_ueberhaupt_erscheinen(hub):
    """Ein Hub, der nie eine Blase zeigt, bestuende die Sperr-Tests oben
    alle."""
    assert hole_state(hub.runtime_dir)["bubble"] is None
    sende(hub.runtime_dir, {"titel": "Beweis", "text": "da"}, "termin_faellig")
    assert hole_state(hub.runtime_dir)["bubble"] is not None
