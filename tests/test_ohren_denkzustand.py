"""T-8.8 — der Denkzustand bekommt seinen Absender.

Der Befund, um den es geht: `HubState.voice_denkt_an()` hat genau einen
Ausloeser, das Bus-Ereignis `utterance`, und im Betrieb sendete es niemand.
Gebaut, dokumentiert, mit gruenen Tests belegt -- und ohne Zulauf.

Diese Datei misst deshalb ausschliesslich am AUFRUF: was kommt tatsaechlich
am Socket an, und wie lange dauert es. Nicht am Quelltext -- ein `grep` waere
gruen, sobald irgendwo die Zeichenkette `utterance` steht.

Jeder Pruefschritt hier hat seine Positivkontrolle. "Nichts gesendet" muss
von "nicht gemessen" unterscheidbar sein, sonst meldet die Vorrichtung Ruhe,
waehrend sie selbst kaputt ist.
"""

import io
import json
import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from daimon.common.logging import get_logger
from daimon.ears.daemon import Ohren, ruf_socket, sende_ohne_antwort


# -- Ein Socket, der ANNIMMT und NIE antwortet ---------------------------
#
# Genau das tut `Hub._bediene_produzent`: er liest in einer Schleife und
# schreibt nichts zurueck. Ohne diese Attrappe waere Schritt 1 nicht
# messbar -- gegen einen gar nicht existierenden Socket ist jeder Sendeweg
# schnell, auch der falsche.

class StummerSocket:
    def __init__(self, pfad: Path) -> None:
        self.pfad = pfad
        self.zeilen: list[dict] = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(pfad))
        self._srv.listen(8)
        self._aus = threading.Event()
        self._t = threading.Thread(target=self._laufen, daemon=True)
        self._t.start()

    def _laufen(self) -> None:
        self._srv.settimeout(0.1)
        while not self._aus.is_set():
            try:
                conn, _ = self._srv.accept()
            except (OSError, socket.timeout):
                continue
            threading.Thread(target=self._lesen, args=(conn,),
                             daemon=True).start()

    def _lesen(self, conn: socket.socket) -> None:
        with conn, conn.makefile("rb") as fh:
            for roh in fh:
                roh = roh.strip()
                if not roh:
                    continue
                try:
                    self.zeilen.append(json.loads(roh))
                except ValueError:
                    pass
                # Und jetzt: KEINE Antwort. Wie der Hub.

    def stop(self) -> None:
        self._aus.set()
        self._t.join(timeout=2.0)
        self._srv.close()


@pytest.fixture
def stummer(tmp_path):
    s = StummerSocket(tmp_path / "ears.sock")
    yield s
    s.stop()


# -- Schritt 1: der Sendeweg wartet nicht --------------------------------

def test_sende_ohne_antwort_kehrt_sofort_zurueck(stummer, tmp_path):
    """Gemessen, nicht behauptet -- und gegen einen Socket, der nie antwortet."""
    t0 = time.monotonic()
    ok = sende_ohne_antwort(str(tmp_path / "ears.sock"),
                            {"v": 1, "type": "utterance"}, timeout_s=1.0)
    dauer_s = time.monotonic() - t0

    assert ok is True
    assert dauer_s < 0.05, f"{dauer_s * 1000:.1f} ms -- das ist kein Durchreichen"
    time.sleep(0.2)
    assert stummer.zeilen == [{"v": 1, "type": "utterance"}]


def test_positivkontrolle_ruf_socket_haengt_am_selben_socket(stummer, tmp_path):
    """Die Falle, an der zwei Anlaeufe gescheitert sind, in Zahlen.

    Ohne diesen Test misst der Test darueber nur, dass irgendetwas schnell
    ist. Hier steht, WOGEGEN: derselbe Socket, derselbe Inhalt, der andere
    Weg -- und der laeuft in seine Frist.
    """
    t0 = time.monotonic()
    antwort = ruf_socket(str(tmp_path / "ears.sock"),
                         {"v": 1, "type": "utterance"}, timeout_s=0.6)
    dauer_s = time.monotonic() - t0

    assert dauer_s >= 0.6, f"{dauer_s:.3f} s -- die Attrappe hat geantwortet?"
    assert antwort.get("ok") is False


def test_sende_ohne_antwort_faellt_still_wenn_niemand_hoert(tmp_path):
    """Kein Hub, kein Abbruch. Der Indikator ist Anzeige, nicht Funktion."""
    t0 = time.monotonic()
    assert sende_ohne_antwort(str(tmp_path / "gibt-es-nicht.sock"),
                              {"v": 1, "type": "utterance"}) is False
    assert time.monotonic() - t0 < 0.05


# -- Schritt 3: der Waechter gegen den Rueckfall -------------------------

class Rufe:
    """Alles, was ANTWORTET. Der Denkzustand laeuft absichtlich nicht hier
    durch -- ginge er es, wuerde dieser Test den Rueckfall nie sehen."""

    def __init__(self) -> None:
        self.anfragen: list[tuple[str, dict]] = []

    def __call__(self, pfad: str, anfrage: dict, *,
                 timeout_s: float | None = None) -> dict:
        self.anfragen.append((str(pfad), anfrage))
        art = anfrage.get("art")
        if art == "transkribiere":
            return {"v": 1, "ok": True, "text": "wie spaet ist es"}
        if art == "frage":
            return {"v": 1, "ok": True, "antwort": "Es ist kurz nach drei."}
        if art == "sprich":
            return {"v": 1, "ok": True, "ttfa_ms": 120.0}
        return {"v": 1, "ok": False, "grund": "unbekannte_art"}


def ohren(tmp_path, rufe):
    return Ohren(runtime_dir=tmp_path, erkenner=None, ruf=rufe,
                 log=get_logger("test", socket_path="/nicht/da",
                                stream=io.StringIO()))


def stueck():
    return np.zeros(512, dtype=np.int16)


def test_die_runde_sendet_utterance_vor_dem_modellruf(stummer, tmp_path):
    """Der Zulauf, am Socket gemessen. Und die Reihenfolge dazu.

    `processing` nach dem Modellruf waere wertlos -- gezeigt wuerde ein
    Denken, das schon vorbei ist.
    """
    rufe = Rufe()
    o = ohren(tmp_path, rufe)
    o._runde([stueck()], listening_bei_beginn=True)
    time.sleep(0.2)

    assert stummer.zeilen == [{"v": 1, "type": "utterance"}]

    # Die Reihenfolge: der Sender sitzt VOR `mind.sock`. Messbar daran, dass
    # der Mind-Ruf ueberhaupt kam -- und die Zeile beim Hub schon lag, als
    # die Attrappe antwortete, sonst haette der Sender nach ihr gefeuert.
    arten = [a.get("art") for _, a in rufe.anfragen]
    assert arten == ["transkribiere", "erkennen", "frage", "sprich"]


def test_positivkontrolle_ohne_sender_faellt_der_waechter(stummer, tmp_path,
                                                          monkeypatch):
    """"Nicht gesendet" muss von "nicht gemessen" unterscheidbar sein.

    Der Rueckfall wird hier nachgestellt: der Sender tut nichts mehr. Wenn
    der Test darueber danach immer noch gruen waere, misst er nichts.
    """
    from daimon.ears import daemon as D
    monkeypatch.setattr(D, "sende_ohne_antwort", lambda *a, **k: True)

    o = ohren(tmp_path, Rufe())
    o._runde([stueck()], listening_bei_beginn=True)
    time.sleep(0.2)

    assert stummer.zeilen == []


def test_die_latenzzeile_traegt_die_dauer_des_senders(stummer, tmp_path):
    """T-3.9 misst TTFA erst ab `tts-say`; dieser Sender sitzt davor und ist
    dort blind. Also steht seine Zahl in der Zeile, nicht nur im Journal."""
    o = ohren(tmp_path, Rufe())
    o._runde([stueck()], listening_bei_beginn=True)

    zeile = json.loads((tmp_path / "latenz.jsonl").read_text().splitlines()[0])
    assert isinstance(zeile["utterance_ms"], float)
    assert zeile["utterance_ms"] < 5.0, zeile["utterance_ms"]
