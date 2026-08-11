"""T-0.8 — Tests fuer das Broker-Ticketbuch.

Persistenz und Atomaritaet stehen hier im Vordergrund: ein verbrauchtes
Ticket muss einen Neustart ueberleben, und ein Schreibvorgang darf nie ein
halb geschriebenes Buch hinterlassen. Auch hier gilt: Positivkontrolle zu
jeder Ablehnung, erwartete Werte als Literale.
"""

import json
import os
import threading
from pathlib import Path

import pytest

from daimon.hub.marks import MarkenFehler
from daimon.hub.tickets import Ticketbuch


class Uhr:
    def __init__(self, start: float = 50_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class Aufzeichner:
    def __init__(self) -> None:
        self.zeilen: list[dict] = []

    def info(self, message: str, **felder) -> None:
        self.zeilen.append({"message": message, **felder})

    def warn(self, message: str, **felder) -> None:
        self.zeilen.append({"message": message, **felder})

    def handlungen(self, typ: str) -> list[str]:
        return [z["DAIMON_HANDLUNG"] for z in self.zeilen
                if z.get("DAIMON_TYP") == typ]


def test_ticket_glueckspfad(tmp_path):
    log = Aufzeichner()
    buch = Ticketbuch(tmp_path / "tickets.json", jetzt=Uhr(), log=log)
    tid = buch.ausgeben(auftrag_hash="auftrag-1")
    assert isinstance(tid, str) and tid
    buch.einloesen(tid, auftrag_hash="auftrag-1")
    assert log.handlungen("broker_ticket") == ["ausgabe", "einloesung"]


def test_ticket_wiedereinloesung_abgelehnt(tmp_path):
    buch = Ticketbuch(tmp_path / "tickets.json", jetzt=Uhr(), log=Aufzeichner())
    tid = buch.ausgeben(auftrag_hash="auftrag-1")
    buch.einloesen(tid, auftrag_hash="auftrag-1")
    with pytest.raises(MarkenFehler):
        buch.einloesen(tid, auftrag_hash="auftrag-1")


def test_ticket_falscher_auftrag_hash_verbrennt_ticket(tmp_path):
    """Ein Fehlversuch ist kein Gratisversuch: das Ticket wird verbraucht,
    BEVOR die Ablehnung fliegt. Sonst verraet jede ticket_id ihren
    auftrag_hash ueber beliebig viele Versuche (Orakel) -- dieselbe
    Begruendung, mit der FreigabeBuch.bestaetigen die Nonce verbrennt."""
    buch = Ticketbuch(tmp_path / "tickets.json", jetzt=Uhr(), log=Aufzeichner())
    tid = buch.ausgeben(auftrag_hash="auftrag-1")
    with pytest.raises(MarkenFehler):
        buch.einloesen(tid, auftrag_hash="auftrag-2")
    # Der richtige Hash kommt zu spaet: das Ticket ist verbrannt.
    with pytest.raises(MarkenFehler):
        buch.einloesen(tid, auftrag_hash="auftrag-1")
    # Positivkontrolle: ein unangetastetes Ticket funktioniert weiterhin.
    tid2 = buch.ausgeben(auftrag_hash="auftrag-2")
    buch.einloesen(tid2, auftrag_hash="auftrag-2")


def test_ticket_verbrannung_ueberlebt_neustart(tmp_path):
    """Das Verbrennen geht sofort auf die Platte -- ein Neustart darf das
    Orakel nicht wieder oeffnen."""
    pfad = tmp_path / "tickets.json"
    uhr = Uhr()
    buch = Ticketbuch(pfad, jetzt=uhr)
    tid = buch.ausgeben(auftrag_hash="geheim")
    with pytest.raises(MarkenFehler):
        buch.einloesen(tid, auftrag_hash="geraten")
    # Auf der Platte steht das Ticket als verbraucht.
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    assert daten["tickets"][tid]["verbraucht"] is True
    del buch
    # "Neustart": der richtige Hash loest das verbrannte Ticket nicht ein.
    neu = Ticketbuch(pfad, jetzt=uhr)
    with pytest.raises(MarkenFehler):
        neu.einloesen(tid, auftrag_hash="geheim")


def test_ticket_unbekannte_id_abgelehnt(tmp_path):
    buch = Ticketbuch(tmp_path / "tickets.json", jetzt=Uhr())
    with pytest.raises(MarkenFehler):
        buch.einloesen("erfunden", auftrag_hash="auftrag-1")


def test_ticket_ablauf_abgelehnt(tmp_path):
    uhr = Uhr()
    buch = Ticketbuch(tmp_path / "tickets.json", frist_s=300.0, jetzt=uhr,
                      log=Aufzeichner())
    tid = buch.ausgeben(auftrag_hash="auftrag-1")
    uhr.t += 301.0
    with pytest.raises(MarkenFehler):
        buch.einloesen(tid, auftrag_hash="auftrag-1")
    # Positivkontrolle.
    tid2 = buch.ausgeben(auftrag_hash="auftrag-2")
    buch.einloesen(tid2, auftrag_hash="auftrag-2")


def test_ticketbuch_ueberlebt_neustart_mit_verbrauchten_tickets(tmp_path):
    pfad = tmp_path / "tickets.json"
    uhr = Uhr()

    erstes = Ticketbuch(pfad, jetzt=uhr, log=Aufzeichner())
    offen = erstes.ausgeben(auftrag_hash="auftrag-offen")
    verbraucht = erstes.ausgeben(auftrag_hash="auftrag-verbraucht")
    erstes.einloesen(verbraucht, auftrag_hash="auftrag-verbraucht")
    del erstes

    # "Neustart": neue Instanz auf denselben Pfad.
    zweites = Ticketbuch(pfad, jetzt=uhr, log=Aufzeichner())
    # Das verbrauchte Ticket bleibt verbraucht.
    with pytest.raises(MarkenFehler):
        zweites.einloesen(verbraucht, auftrag_hash="auftrag-verbraucht")
    # Das offene Ticket bleibt einloesbar.
    zweites.einloesen(offen, auftrag_hash="auftrag-offen")
    # Und jetzt ist auch das verbraucht -- persistent.
    drittes = Ticketbuch(pfad, jetzt=uhr)
    with pytest.raises(MarkenFehler):
        drittes.einloesen(offen, auftrag_hash="auftrag-offen")


def test_ticketbuch_datei_ist_gueltiges_json(tmp_path):
    pfad = tmp_path / "tickets.json"
    buch = Ticketbuch(pfad, jetzt=Uhr())
    tid = buch.ausgeben(auftrag_hash="auftrag-1")
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    assert daten["v"] == 1
    assert daten["tickets"][tid]["auftrag_hash"] == "auftrag-1"
    assert daten["tickets"][tid]["verbraucht"] is False
    assert daten["tickets"][tid]["ablauf_ts"] == 50_300.0


def test_ticketbuch_schreibt_atomar_kein_direktes_ziel(tmp_path, monkeypatch):
    """Waehrend des Schreibens wird nie direkt auf die Zieldatei geschrieben:
    jeder Schreibvorgang geht ueber eine temporaere Datei im selben
    Verzeichnis plus os.replace."""
    pfad = tmp_path / "tickets.json"
    ersetzt = []
    orig_replace = os.replace

    def spione_replace(src, dst):
        ersetzt.append((Path(src), Path(dst)))
        return orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", spione_replace)
    buch = Ticketbuch(pfad, jetzt=Uhr())
    buch.ausgeben(auftrag_hash="auftrag-1")
    assert len(ersetzt) == 1
    src, dst = ersetzt[0]
    assert dst == pfad
    assert src.parent == pfad.parent  # selbes Verzeichnis: rename ist atomar
    assert src.name != pfad.name


def test_ticketbuch_kein_zustand_aus_dem_request(tmp_path):
    """Frist und Ablauf kommen aus Konfiguration und Zeitquelle -- dafuer
    gibt es in den oeffentlichen Methoden keinen Parameter."""
    uhr = Uhr()
    buch = Ticketbuch(tmp_path / "tickets.json", frist_s=300.0, jetzt=uhr)
    tid = buch.ausgeben(auftrag_hash="auftrag-1")
    with pytest.raises(TypeError):
        buch.ausgeben(auftrag_hash="a", ablauf_ts=9e18)
    with pytest.raises(TypeError):
        buch.einloesen(tid, auftrag_hash="auftrag-1", frist_s=9e18)
    # Das Ticket selbst traegt den Wert aus Zeitquelle plus Frist.
    daten = json.loads((tmp_path / "tickets.json").read_text())
    assert daten["tickets"][tid]["ablauf_ts"] == 50_300.0


def test_ticket_paralleles_einloesen_genau_einmal(tmp_path):
    buch = Ticketbuch(tmp_path / "tickets.json", jetzt=Uhr(), log=Aufzeichner())
    tid = buch.ausgeben(auftrag_hash="auftrag-1")
    erfolge, fehler = [], []
    start = threading.Barrier(9)

    def arbeiter():
        start.wait()
        try:
            buch.einloesen(tid, auftrag_hash="auftrag-1")
            erfolge.append(1)
        except MarkenFehler:
            fehler.append(1)

    threads = [threading.Thread(target=arbeiter) for _ in range(8)]
    for t in threads:
        t.start()
    start.wait()
    for t in threads:
        t.join()
    assert len(erfolge) == 1
    assert len(fehler) == 7


def test_ticket_audit_sieht_ausgabe_einloesung_und_ablehnung(tmp_path):
    log = Aufzeichner()
    buch = Ticketbuch(tmp_path / "tickets.json", jetzt=Uhr(), log=log)
    tid = buch.ausgeben(auftrag_hash="auftrag-1")
    buch.einloesen(tid, auftrag_hash="auftrag-1")
    with pytest.raises(MarkenFehler):
        buch.einloesen(tid, auftrag_hash="auftrag-1")
    assert log.handlungen("broker_ticket") == [
        "ausgabe", "einloesung", "ablehnung"]
