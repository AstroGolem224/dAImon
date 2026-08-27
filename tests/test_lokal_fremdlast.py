"""Der lokale Weg unter Fremdlast: wiederholt er, haelt die Obergrenze, und
sagt er den UNTERSCHIED?

Warum diese Datei nicht mit einer Attrappe auskommt
----------------------------------------------------------------------------
Der Befund, um den es geht, entsteht im Draht und nicht im Broker: laeuft ein
zweiter Client auf demselben Ollama-Modell, kommt die Antwort SPAET, nicht
falsch. Am 27.08. auf dieser Maschine gemessen, `qwen3.8-heretic:27b-96k`,
`OLLAMA_NUM_PARALLEL=2`:

| Lage | Ergebnis |
|---|---|
| zwei Fremdclients, kurze Anfrage | HTTP 200 nach 49,7 s |
| drei Fremdclients, kurze Anfrage | HTTP 200 nach 79,7 s |
| 14 gleichzeitig gegen `MAX_QUEUE=8` | 14x HTTP 200 nach ~62 s, **kein 503** |
| 20-s-Frist unter Last, zweimal | zweimal `TimeoutError` |
| dieselbe Frage nach Ende der Last | HTTP 200 nach 8,4 s |
| Port ohne Dienst | `URLError [Errno 111]` nach 0,0 s |

Ollama liefert also KEINEN eigenen Fehler fuer "belegt". Der einzige
Unterschied, den ein Aufrufer sieht, ist die Zeit -- und genau daran haengt
hier alles: eine Zeitueberschreitung ist `modell_beschaeftigt`, ein
verweigerter Verbindungsaufbau bleibt `modell_weg`. Eine Attrappe, die eine
Ausnahme wirft, koennte diesen Unterschied bloss behaupten. Deshalb steht in
jedem Test unten ein ECHTER HTTP-Dienst auf Loopback, und der Broker geht mit
seinem echten `http_post` hin.

Die Positivkontrolle ist nicht Beiwerk: `test_POSITIVKONTROLLE_*` faehrt
dieselbe Vorrichtung schnell und erwartet `ok`. Ohne sie hiesse jedes Rot der
anderen Tests nur "irgendetwas schlaegt fehl".
"""
from __future__ import annotations

import io
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from daimon.brokers.lokal import broker as B
from daimon.common.logging import get_logger

KOERPER = {"model": "claude-sonnet-4-5", "max_tokens": 64,
           "system": "Du bist Nordom.",
           "messages": [{"role": "user", "content": "wie spaet ist es"}]}

ANTWORT = json.dumps({"model": "m", "done": True,
                      "message": {"role": "assistant",
                                  "content": "Es ist kurz nach drei."}})


class Endpunkt:
    """Ein echter HTTP-Dienst auf 127.0.0.1, der sich stellen kann wie ein
    belegtes Ollama: `verzoegerung_s` je Aufruf, aus einer Liste, damit ein
    erster Versuch haengen und ein zweiter durchkommen kann."""

    def __init__(self, verzoegerungen: list[float]) -> None:
        self.verzoegerungen = list(verzoegerungen)
        self.aufrufe = 0
        self._sperre = threading.Lock()
        aussen = self

        class Griff(BaseHTTPRequestHandler):
            def do_POST(self) -> None:      # noqa: N802 -- http.server-Vorgabe
                with aussen._sperre:
                    i = aussen.aufrufe
                    aussen.aufrufe += 1
                laenge = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(laenge)
                warte = (aussen.verzoegerungen[i]
                         if i < len(aussen.verzoegerungen)
                         else aussen.verzoegerungen[-1])
                time.sleep(warte)
                roh = ANTWORT.encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(roh)))
                    self.end_headers()
                    self.wfile.write(roh)
                except OSError:             # der Client ist weg -- genau der Fall
                    pass

            def log_message(self, *a):      # noqa: A003 -- still bleiben
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Griff)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self._srv.server_address[:2]
        return f"http://{host}:{port}/api/chat"

    def schliessen(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture
def endpunkt():
    gebaut: list[Endpunkt] = []

    def bauen(verzoegerungen):
        e = Endpunkt(verzoegerungen)
        gebaut.append(e)
        return e

    yield bauen
    for e in gebaut:
        e.schliessen()


def toter_port() -> str:
    """Eine Adresse, an der sicher nichts lauscht: gebunden, gelesen,
    geschlossen. Kein geratener Port -- ein geratener kann belegt sein."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/api/chat"


class Hub:
    """Das Ticketbuch, so weit es der Broker sieht. Zaehlt Einloesungen --
    die Zahl ist die Zusage aus Schritt 3: eine Anfrage, ein Ticket."""

    def __init__(self) -> None:
        self.eingeloest: list[str] = []

    def __call__(self, pfad, anfrage, **kwargs):
        if anfrage.get("art") == "einloesen":
            self.eingeloest.append(anfrage.get("ticket", ""))
            return {"v": 1, "ok": True}
        return {"v": 1, "ok": False, "grund": "unbekannte_art"}


def _tags(url, *, timeout_s=0.0):
    return 200, json.dumps({"models": [{"model": "m:1"}]})


def broker(ziel: str, *, hub=None, **kwargs):
    """Echtes `http_post`, echter Draht. Nur Ticketbuch und `/api/tags` sind
    ersetzt -- beide haben mit der Fremdlast nichts zu tun."""
    return B.LokalBroker(
        log=get_logger("test", socket_path="/nicht/da", stream=io.StringIO()),
        hub_socket="/nicht/da", hub_anfrage=hub or Hub(), http_get=_tags,
        ziel=ziel, modell="m:1", **kwargs)


def frage(b, ticket="t-1"):
    return b.anfrage({"v": 1, "art": "anfrage", "ticket": ticket,
                      "koerper": KOERPER})


# -- Die Positivkontrolle: die Vorrichtung liefert wirklich ----------------

def test_POSITIVKONTROLLE_die_vorrichtung_traegt_eine_antwort(endpunkt):
    """Derselbe echte Dienst, nur schnell. Ohne diesen Lauf hiesse jedes Rot
    unten bloss "irgendetwas geht nicht" -- und ein Prueferfehler saehe
    genauso aus wie ein Befund."""
    e = endpunkt([0.0])
    a = frage(broker(e.url, timeout_s=5.0, gesamt_s=10.0))
    assert a["ok"] is True, a
    assert a["antwort"]["content"][0]["text"] == "Es ist kurz nach drei."
    assert e.aufrufe == 1


# -- Belegt ist nicht weg --------------------------------------------------

def test_ein_belegtes_modell_heisst_beschaeftigt_und_nicht_weg(endpunkt):
    """DER Unterschied. Bis zum 27.08. hiessen beide `modell_weg`, und die
    Betriebsdiagnose schickte einen zu einem toten Dienst, waehrend die GPU
    lief."""
    e = endpunkt([9.0])
    a = frage(broker(e.url, timeout_s=0.4, gesamt_s=1.6, rueckstau_s=0.05))
    assert a["ok"] is False
    assert a["grund"] == "modell_beschaeftigt", a


def test_GEGENPROBE_ein_wirklich_weggefallener_endpunkt_bleibt_modell_weg():
    """Die andere Haelfte derselben Unterscheidung -- und der Grund, warum der
    Test darueber etwas aussagt. Waere hier auch `modell_beschaeftigt`
    herausgekommen, hiesse der neue Grund nur anders."""
    a = frage(broker(toter_port(), timeout_s=0.4, gesamt_s=2.0,
                     rueckstau_s=0.01))
    assert a["ok"] is False
    assert a["grund"] == "modell_weg", a


# -- Wiederholung ----------------------------------------------------------

def test_es_wird_wirklich_wiederholt(endpunkt):
    """Gezaehlt am Endpunkt und nicht am Broker: er soll nicht ueber sich
    selbst berichten."""
    e = endpunkt([9.0])
    frage(broker(e.url, timeout_s=0.4, gesamt_s=3.0, rueckstau_s=0.05,
                 versuche=3))
    assert e.aufrufe >= 2, f"kein zweiter Versuch, nur {e.aufrufe}"


def test_ein_vorruebergehend_belegtes_modell_wird_beim_zweiten_mal_bedient(
        endpunkt):
    """Der Fall, um den es ueberhaupt geht: die Fremdlast endet, und die
    Anfrage kommt trotzdem noch durch -- ohne dass der Nutzer nachfragen
    muss."""
    e = endpunkt([9.0, 0.0])
    a = frage(broker(e.url, timeout_s=0.5, gesamt_s=20.0, rueckstau_s=0.05))
    assert a["ok"] is True, a
    assert e.aufrufe == 2, e.aufrufe


# -- Die Obergrenzen -------------------------------------------------------

def test_die_gesamtfrist_haelt(endpunkt):
    """Die Zusage, ohne die Wiederholung nur heisst "spaeter aufgeben": der
    Mind wartet dahinter, und seine Leitung hat ihre eigene Frist."""
    e = endpunkt([30.0])
    b = broker(e.url, timeout_s=1.0, gesamt_s=2.5, rueckstau_s=0.05,
               versuche=99)
    t0 = time.monotonic()
    a = frage(b)
    dauer = time.monotonic() - t0
    assert a["grund"] == "modell_beschaeftigt", a
    assert dauer < 2.5 + 1.0, f"{dauer:.1f} s -- die Gesamtfrist hat nicht gehalten"


def test_die_versuchszahl_haelt(endpunkt):
    """Sonst laege die Obergrenze allein an der Uhr, und ein schnell
    abreissender Endpunkt wuerde in einer Schleife gehaemmert."""
    e = endpunkt([9.0])
    a = frage(broker(e.url, timeout_s=0.3, gesamt_s=60.0, rueckstau_s=0.01,
                     versuche=2))
    assert a["versuche"] == 2, a
    assert e.aufrufe == 2, e.aufrufe


def test_der_rueckstau_waechst_und_wird_nicht_durchgereicht(endpunkt):
    """Gemessen an den Pausen, nicht am Code: zwischen Versuch 1/2 und 2/3
    muss die zweite Pause die groessere sein."""
    pausen: list[float] = []
    e = endpunkt([9.0])
    broker(e.url, timeout_s=0.2, gesamt_s=60.0, rueckstau_s=0.05, versuche=3,
           schlafen=pausen.append).anfrage(
        {"v": 1, "art": "anfrage", "ticket": "t-1", "koerper": KOERPER})
    assert pausen == [0.05, 0.1], pausen


# -- Das Ticketbuch --------------------------------------------------------

def test_eine_wiederholung_zieht_KEIN_zweites_ticket(endpunkt):
    """Die Wiederholung sitzt hinter der Einloesung: derselbe bezahlte
    Auftrag, ein zweites Mal versucht. Saesse sie davor, kostete eine Antwort
    drei Tickets -- und das Kontingent waere nach einem belegten Modell leer,
    obwohl nie eine Antwort kam."""
    hub = Hub()
    e = endpunkt([9.0])
    a = frage(broker(e.url, hub=hub, timeout_s=0.3, gesamt_s=3.0,
                     rueckstau_s=0.05, versuche=3))
    assert a["grund"] == "modell_beschaeftigt"
    assert e.aufrufe >= 2, "gar nicht wiederholt -- der Test misst nichts"
    assert hub.eingeloest == ["t-1"], hub.eingeloest


def test_das_fenster_zaehlt_die_anfrage_einmal(endpunkt):
    """Dasselbe fuer die zweite Schranke: `hoechstens` je Fenster zaehlt
    Anfragen, nicht Versuche."""
    e = endpunkt([9.0])
    b = broker(e.url, timeout_s=0.3, gesamt_s=3.0, rueckstau_s=0.05,
               versuche=3, hoechstens=2)
    frage(b, "t-1")
    assert len(b._fenster) == 1, list(b._fenster)


# -- Der Grund ist gefuehrt ------------------------------------------------

def test_der_neue_grund_steht_in_der_liste():
    """`_nein` prueft gegen `GRUENDE`. Ein Grund, der dort fehlt, faellt erst
    im Betrieb auf -- als AssertionError im Broker-Faden."""
    assert "modell_beschaeftigt" in B.GRUENDE
    assert "modell_weg" in B.GRUENDE


def test_die_gesamtfrist_bleibt_unter_der_frist_der_leitung():
    """Die Obergrenzen werden zusammengerechnet und nicht einzeln gesetzt: der
    Mind wartet 180 s auf `lokal.sock` (`mind/daemon.py: hub_anfrage`), und
    `bediene()` gibt derselben Verbindung 180 s. Wer `GESAMT_S` darueber
    hebt, rechnet in eine Leitung, die niemand mehr liest."""
    from daimon.mind import daemon as M
    import inspect
    leitung = inspect.signature(M.hub_anfrage).parameters["timeout_s"].default
    assert B.GESAMT_S < leitung, (B.GESAMT_S, leitung)
    assert "conn.settimeout(180.0)" in inspect.getsource(B.bediene)
