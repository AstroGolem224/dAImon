"""T-4.15 — zehn gleiche Auftraege, eine Wirkung."""
from __future__ import annotations

from daimon.hub.action_queue import Aktionsschlange


def test_derselbe_auftrag_zehnmal_ergibt_genau_eine_wirkung():
    q = Aktionsschlange()
    wirkungen = 0
    for _ in range(10):
        if q.einloesen("t-1")["ok"]:
            wirkungen += 1
    assert wirkungen == 1
    assert q.verworfen["ticket_verbraucht"] == 9


def test_es_gibt_keinen_automatischen_zweiten_versuch():
    """Auch nach einem Misserfolg nicht.

    Ein Retry macht aus einem unbekannten Ausgang zwei moegliche
    Ausfuehrungen -- bei "Datei loeschen" ist das der teurere Fehler.
    """
    q = Aktionsschlange()
    q.einloesen("t-1")
    q.bestaetigen("t-1", ok=False)
    assert not q.einloesen("t-1")["ok"]


def test_ein_abbruch_zwischen_einloesung_und_bestaetigung_ist_unknown():
    q = Aktionsschlange()
    q.einloesen("t-1")
    assert q.ausgang("t-1") == "unknown"
    assert q.offene_ausgaenge() == {"t-1": "unknown"}
    q.bestaetigen("t-1", ok=True)
    assert q.ausgang("t-1") == "ok"
    assert q.offene_ausgaenge() == {}


def test_ein_nie_eingeloestes_ticket_ist_kein_unknown():
    """`denied` und `unknown` sind verschiedene Aussagen."""
    q = Aktionsschlange()
    assert q.ausgang("gab-es-nie") == "denied"


def test_ueber_der_hoechstzahl_wird_abgelehnt_statt_aufgestaut():
    q = Aktionsschlange(max_offene=3)
    for i in range(3):
        assert q.rueckfrage_oeffnen(f"r{i}")["ok"]
    vierte = q.rueckfrage_oeffnen("r3")
    assert not vierte["ok"] and vierte["grund"] == "zu_viele_rueckfragen"
    # Nach dem Schliessen einer ist wieder Platz -- Positivkontrolle.
    q.rueckfrage_schliessen("r0")
    assert q.rueckfrage_oeffnen("r3")["ok"]


def test_die_rate_je_runde_ist_begrenzt():
    q = Aktionsschlange(max_je_runde=5)
    for _ in range(5):
        assert q.anfrage_zulassen("runde-1")["ok"]
    assert not q.anfrage_zulassen("runde-1")["ok"]
    # Eine andere Runde ist davon unberuehrt.
    assert q.anfrage_zulassen("runde-2")["ok"]


def test_jeder_verwurf_wird_gezaehlt_und_benannt():
    q = Aktionsschlange(max_offene=1, max_je_runde=1)
    q.einloesen("t"); q.einloesen("t")
    q.rueckfrage_oeffnen("a"); q.rueckfrage_oeffnen("b")
    q.anfrage_zulassen("r"); q.anfrage_zulassen("r")
    q.bestaetigen("fremd", ok=True)
    assert q.verworfen == {"ticket_verbraucht": 1, "zu_viele_rueckfragen": 1,
                           "rate_limit": 1, "unbekanntes_ticket": 1}
