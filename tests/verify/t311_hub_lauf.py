#!/usr/bin/env python3
"""Hub-Starter fuer T-3.11: haengt die Ticket-Allowlist auf die Unit DIESES
Laufs um.

WARUM: `daimon.hub.daemon.TICKET_UNITS` laesst an `ticket.sock` genau vier
Units zu (Egress, Lokal-Broker, CLI-Broker, Mind). Der Pruefstand fragt aber
aus SEINER eigenen Unit an -- unter einer interaktiven Sitzung z.B.
`app-com.anthropic.Claude-873053.scope`; der Egress-Prozess des Laufs laeuft
per `systemd-socket-activate` als Kind im selben cgroup und damit unter
derselben Unit. `_horche_einfach` weist beide ab und SCHLIESST die
Verbindung ohne Antwort. Fuer den Fragenden ist das ein
`ConnectionResetError` -- der Pruefstand stuerzte darum ab, statt zu melden,
und alles hinter Kapitel 4 blieb ungefahren.

WAS DAS NICHT TUT: Es schaltet die Unit-Pruefung NICHT ab. Die Liste bleibt
einelementig und traegt eine ECHTE, gemessene Unit; jede fremde Unit wird
weiterhin abgewiesen. `None` (= keine Pruefung) und `()` (= alles gesperrt)
werden ausdruecklich NICHT gesetzt.

WO NICHT: Die Produktionsliste in `daimon/hub/daemon.py` bleibt
byte-identisch. Gesetzt wird nur das Modulattribut des GELADENEN Hubs, und
zwar VOR `main()` -- `Hub.start` liest `TICKET_UNITS` beim Aufsetzen des
Ticket-Threads aus dem Modulnamensraum.

GEMESSEN, NICHT GERATEN: die Unit kommt aus derselben echten
SO_PEERPIDFD-Kette wie im Betrieb (`ipc.peer_of`). Eine Attrappe fuer
`peer_of` raeumte genau die Vorrichtung aus dem Weg, um die es hier geht.
Schlaegt die Messung fehl, bricht dieser Prozess laut ab -- still
weiterlaufen hiesse, den Ticket-Teil ungemessen gruen zu melden.

Vorbild: derselbe Handgriff fuer `TTS_UNITS` im Verifizierer T-3.9.

Aufruf: t311_hub_lauf.py <runtime-dir> [weitere-unit ...]

Die weiteren Units sind die TRANSIENTEN Units, die der Pruefstand im selben
Lauf selbst startet und die ihrerseits ein Ticket einloesen -- heute genau
eine: die echte Egress-Testunit aus Kapitel 9. Ihr Name steht deshalb im
Pruefstand fest, BEVOR der Hub startet; nachtraeglich liesse sich die Liste
nicht mehr aendern, `Hub.start` liest sie einmal.
"""

import os
import socket
import sys
from pathlib import Path

RTDIR = sys.argv[1]
WEITERE = tuple(sys.argv[2:])

from daimon.common import ipc  # noqa: E402
from daimon.hub import daemon as hubmod  # noqa: E402

print("HUB_DATEI=" + os.path.abspath(hubmod.__file__), flush=True)


def eigene_unit() -> str:
    """Unter welcher Unit DIESER Prozess laeuft -- an einer echten Verbindung
    gemessen, ueber genau die Kette, die auch `_horche_einfach` geht."""
    pfad = Path(RTDIR) / "unitmessung.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    srv.listen(1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.connect(str(pfad))
            conn, _ = srv.accept()
            with conn:
                return ipc.peer_of(conn, "unitmessung").unit
    finally:
        srv.close()
        pfad.unlink(missing_ok=True)


try:
    unit = eigene_unit()
except Exception as exc:  # noqa: BLE001 -- der Grund gehoert ins Protokoll
    print("HUB_UNIT_MESSUNG_FEHLGESCHLAGEN=" + repr(exc), flush=True)
    raise SystemExit(3)
if not unit:
    print("HUB_UNIT_MESSUNG_FEHLGESCHLAGEN=leere Unit", flush=True)
    raise SystemExit(3)
print("HUB_UNIT=" + unit, flush=True)

# Sperrt der GELADENE Baum `ticket.sock` ueberhaupt gegen diese Unit? Die
# Antwort kommt aus der Regel des Baums selbst (`ipc.unit_erlaubt`), nicht aus
# einer Annahme des Pruefstands. Das ist die Unterscheidungskontrolle: sie
# belegt, dass die Wand ohne das Umhaengen steht. Ein Gut-Muster ohne
# `TICKET_UNITS` meldet hier folgerichtig `nein`.
vor = getattr(hubmod, "TICKET_UNITS", None)
sperrt = vor is not None and not ipc.unit_erlaubt(unit, vor)
print("HUB_TICKET_UNITS_VOR=" + repr(vor), flush=True)
print("HUB_TICKET_SPERRT_MICH=" + ("ja" if sperrt else "nein"), flush=True)

if vor is not None:
    hubmod.TICKET_UNITS = (unit, *WEITERE)
    print("HUB_TICKET_UNITS_NACH=" + repr(hubmod.TICKET_UNITS), flush=True)
else:
    print("HUB_TICKET_UNITS_NACH=(nicht umgehaengt)", flush=True)

sys.argv = [sys.argv[0], "--runtime-dir", RTDIR]
raise SystemExit(hubmod.main())
