#!/usr/bin/env python3
"""Startet den ECHTEN Archivdienst des Prueflings IN EINER SYSTEMD-SANDBOX --
Treiber fuer T-7.1.v.

    t71_dienst.py <pruefling> <runtime-dir> <archiv.db>

Der Aufruf geht auf `daimon.recorder.daemon.main()`, also die Verdrahtung des
Betriebs: dasselbe `Archiv`, dieselbe `Redaktion`, derselbe `ipc.listen`,
derselbe Aufraeumtakt. Gestartet wird dieser Treiber von
`t71_pruefstand.py` ueber `systemd-run --user` mit den Direktiven aus
`<pruefling>/config/systemd/daimon-recorder.service` -- die Haertung ist
also die des Prueflings und nicht die eines nachgebauten Sandkastens.

ZWEI ERSETZUNGEN, beide ausserhalb von T-7.1, beide hier benannt:

**1. Die automatische Pause (T-7.3) wird stillgelegt.** `Recorder.start()`
faehrt die Automatik EINMAL vor dem Horchen; sie fragt `pw-dump` nach fremden
Mikrofonstroemen und ruft bei einem Treffer `pause.stoppe()` --
`systemctl --user stop daimon-recorder.service daimon-eyes.service`, an den
ECHTEN Units dieser Maschine. Ein Verifizierer, der die Dienste des Nutzers
abschaltet, ist kein Verifizierer. `fremde_mikrofonstroeme` und `stoppe`
werden deshalb VOR dem Import des Dienstes ersetzt (dort werden sie als
Vorgabewerte gebunden), `ist_konferenz` danach. Wird `stoppe` trotzdem
gerufen, stirbt der Treiber laut -- ein stiller Durchlass waere genau die
Sorte Attrappe, die dieses Repo teuer gelernt hat.

**2. Die Peer-Unit-Allowlist wird geoeffnet.** `ipc.accept` loest die
Gegenstelle ueber `SO_PEERPIDFD` auf und verlangt eine Unit aus
`ERLAUBTE_UNITS` (`daimon-eyes`, `daimon-focus`, `daimon-ears`). Der
Pruefstand laeuft in der Sitzungs-Scope des Nutzers und traegt keine davon;
eine transiente Unit eines dieser Namen waere ein Eingriff in den Betrieb.
Der Treiber ruft deshalb das echte `accept` (uid- und pidfd-Pruefung bleiben)
und setzt die Unit danach auf die, die in `<runtime>/t71_unit` steht. Damit
laeuft die Art-je-Unit-Tabelle des Dienstes ECHT; NICHT gemessen ist die
Aufloesung der Unit selbst -- die ist T-7.2.v/T-7.1b.v.

`Archiv`, `Archiv.schreiben`, die Fristen, die Verdraengung und die
Markierung sind unberuehrt. Genau dort saesse der Fehler, den dieser
Verifizierer sehen soll.
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

    # (1) -- VOR dem Import des Dienstes.
    from daimon.recorder import pause

    def _keine_fremden_mikrofone(*_a, **_k):
        return 0

    def _niemals_pausieren(*_a, **_k):
        raise AssertionError(
            "t71_dienst: pause.stoppe wurde gerufen -- der Prueflauf haette "
            "echte Units des Nutzers gestoppt.")

    pause.fremde_mikrofonstroeme = _keine_fremden_mikrofone
    pause.stoppe = _niemals_pausieren
    pause.ist_konferenz = lambda *_a, **_k: False

    from daimon.common import ipc
    from daimon.recorder import daemon

    echtes_accept = ipc.accept
    unit_datei = Path(runtime) / "t71_unit"

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
