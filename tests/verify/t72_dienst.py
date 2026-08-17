#!/usr/bin/env python3
"""Startet den ECHTEN Archivdienst des Prueflings -- Treiber fuer T-7.2.v.

    t72_dienst.py <pruefling> <runtime-dir> <archiv.db>

Aufgerufen wird `daimon.recorder.daemon.main()`, also die Verdrahtung des
Betriebs: dieselbe Denylist aus `denylist_laden(denylist_pfade())`, dieselbe
`Redaktion`, dieselbe `wahrnehmung_an`-Messung am Lebenszeichen der Augen,
dasselbe `Archiv`. Der Pruefstand baut davon NICHTS nach -- er wuerde sonst
seine eigene Verdrahtung pruefen und nicht die des Dienstes.

ZWEI ERSETZUNGEN, und beide liegen ausserhalb von T-7.2:

**1. Die Peer-Unit wird gesetzt statt aufgeloest.** `ipc.accept` loest die
Gegenstelle ueber `SO_PEERPIDFD` und den cgroup-Pfad auf und verlangt eine
Unit aus `ERLAUBTE_UNITS`. Ein Prueflauf laeuft in der Sitzungs-Scope des
Nutzers und traegt keine dieser Units; `daimon-eyes.service` LAEUFT auf dieser
Maschine bereits, eine transiente Unit desselben Namens waere ein Eingriff in
den Betrieb des Nutzers. Der Treiber ruft deshalb das echte `accept` (uid- und
pidfd-Pruefung bleiben) und setzt danach die Unit auf das, was in
`<runtime>/t72_unit` steht. Damit laeuft die Art-je-Unit-Tabelle des Dienstes
ECHT -- der Pruefstand kann `art`-Wahl und Unit gegeneinander stellen --, und
was NICHT gemessen ist, ist die Aufloesung der Unit selbst. Die gehoert
T-7.1.v.

**2. Die automatische Pause (T-7.3) wird stillgelegt.** `Recorder.start()`
faehrt die Automatik EINMAL vor dem Horchen; sie fragt `pw-dump` nach fremden
Mikrofonstroemen und ruft bei einem Treffer `pause.stoppe()` --
`systemctl --user stop daimon-recorder.service daimon-eyes.service`, an den
ECHTEN Units dieser Maschine. Ein Verifizierer, der den Augendienst des
Nutzers abschaltet, ist kein Verifizierer. `fremde_mikrofonstroeme` und
`stoppe` werden deshalb VOR dem Import des Dienstes ersetzt (sie werden dort
als Vorgabewerte gebunden), `ist_konferenz` danach. Der Fokus-Umweg ueber DBus
faellt ins Leere, weil der Pruefstand `DBUS_SESSION_BUS_ADDRESS` verbiegt.

Die Redaktion selbst -- `urteil`, `urteil_ton`, `kennung`, `privat_bis`,
`wahrnehmung_an` -- und der eine `Archiv.schreiben`-Aufruf sind unberuehrt.
Genau dort saesse der Fehler, den dieser Verifizierer sehen soll.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    pruefling, runtime, archiv = argv
    sys.path.insert(0, pruefling)

    # (2) -- VOR dem Import des Dienstes.
    from daimon.recorder import pause

    def _keine_fremden_mikrofone(*_a, **_k):
        return 0

    def _niemals_pausieren(*_a, **_k):
        raise AssertionError(
            "t72_dienst: pause.stoppe wurde gerufen -- der Prueflauf haette "
            "echte Units des Nutzers gestoppt.")

    pause.fremde_mikrofonstroeme = _keine_fremden_mikrofone
    pause.stoppe = _niemals_pausieren
    pause.ist_konferenz = lambda *_a, **_k: False

    from daimon.common import ipc
    from daimon.recorder import daemon

    echtes_accept = ipc.accept
    unit_datei = Path(runtime) / "t72_unit"

    def accept(srv, produzent, *, erlaubte_uid=None, erlaubte_units=None,
               audit=None):
        conn, peer = echtes_accept(srv, produzent, erlaubte_uid=erlaubte_uid,
                                   erlaubte_units=None, audit=audit)
        try:
            unit = unit_datei.read_text(encoding="utf-8").strip()
        except OSError:
            unit = ""
        return conn, ipc.Peer(pid=peer.pid, uid=peer.uid, gid=peer.gid,
                              unit=unit, produzent=peer.produzent)

    ipc.accept = accept
    return daemon.main(["--runtime-dir", runtime, "--archiv", archiv])


if __name__ == "__main__":
    raise SystemExit(main())
