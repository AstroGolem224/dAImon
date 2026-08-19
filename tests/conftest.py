"""Gemeinsame Vorkehrungen fuer alle Pruefstaende.

Bisher gab es keine. Diese Datei entsteht fuer EINE Regel, und die hat einen
gemessenen Anlass.

**Kein Pruefstand schreibt in das Journal dieser Maschine.**

`Audit.verankern()` ruft `systemd-cat` und legt den Kettenkopf ins
System-Journal. Am 17.08. haben Zwischenfassungen der Anker-Pruefstaende
genau das getan: rund dreissig Zeilen `AUDIT-ANKER seq=0 head=` im Journal
dieser Maschine, Koepfe von Ketten aus `tmp_path`, die es nie gegeben hat.

Das war frueher folgenlos, weil niemand die Anker las. Seit dem 17.08. liest
sie jemand: der Hub prueft seine Kette beim Start gegen
`anker_aus_journal()`. Ein Anker soll bezeugen, dass ein bestimmter
Kettenkopf zu einer bestimmten Zeit existierte -- einer aus einem
Wegwerfverzeichnis bezeugt nichts und verwaessert genau die Aussage, fuer die
der zweite Strom gebaut wurde. Die dreissig liegen dort jetzt dreissig Tage;
`anker_aus_journal` sieht so weit zurueck.

**Zur Ehrlichkeit dieser Datei:** die erste Fassung behauptete hier,
`tests/test_audit.py` schreibe seit jeher 16 Anker je Lauf. Das war ein
Messfehler -- verglichen wurden zwei verschiedene Zeitfenster (`--since
-1min` gegen `--since -2min`), nicht zwei Zustaende. Gegengeprueft: ohne
diese Fixture schreibt `test_audit.py` NULL Anker. Die Vorkehrung bleibt
trotzdem, denn der Anlass ist echt eingetreten -- nur war der Verursacher
ein anderer.

Deshalb faengt diese Fixture `_ins_journal` global ab. Sie greift AUTOMATISCH
in jedem Test -- eine Vorkehrung, an die man sich erinnern muss, ist keine.
"""
from __future__ import annotations

import pytest

from daimon.hub import audit as _audit


@pytest.fixture(autouse=True)
def kein_journal(monkeypatch):
    """`verankern()` schreibt im Pruefstand nirgendwohin.

    Wer den ANKER selbst pruefen will, uebergibt `journal=` -- der Parameter
    existiert genau dafuer und geht an dieser Fixture vorbei.
    """
    monkeypatch.setattr(_audit, "_ins_journal", lambda text: None)


def eigene_unit(tmp_path) -> str:
    """Unter welcher systemd-Unit dieser Pruefstand laeuft -- echt gemessen.

    Seit dem 19.08. tragen fuenf Hub-Endpunkte und die Broker-Sockets
    Unit-Allowlisten (T-4.5). Ein Pruefstand laeuft unter keiner davon; er
    muss sich also selbst eintragen, sonst prueft er einen Weg, den es im
    Betrieb nicht gibt.

    Diese Funktion steht hier und ist KEINE autouse-Fixture, und beides mit
    Absicht:

    * hier, weil drei Dateien sie brauchen und drei Kopien drei Fassungen
      derselben Regel waeren;
    * nicht automatisch, weil eine Fixture, die still jede Allowlist aufmacht,
      genau die Umgehung waere, gegen die die Listen gebaut sind. Wer sie
      braucht, ruft sie und schreibt dazu, welche Liste er weitet.

    Gemessen wird ueber eine echte Verbindung und `ipc.peer_of` -- dieselbe
    Kette wie im Betrieb. Die Funktion zu ersetzen waere der Fehler, an dem
    `test_hub_kontext.py` seinerzeit den Befund nicht finden konnte.
    """
    import socket

    from daimon.common import ipc

    pfad = tmp_path / "unitmessung.sock"
    pfad.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    srv.listen(1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.connect(str(pfad))
            conn, _ = srv.accept()
            with conn:
                return ipc.peer_of(conn, "messung").unit
    finally:
        srv.close()
        pfad.unlink(missing_ok=True)
