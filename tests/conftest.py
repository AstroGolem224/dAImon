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
