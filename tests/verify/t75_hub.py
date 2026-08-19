#!/usr/bin/env python3
"""Startet den ECHTEN Hub des Prueflings -- Treiber fuer T-7.5.v.

    t75_hub.py <pruefling> <runtime-dir> [<unit> ...]

Aufgerufen wird `daimon.hub.daemon.Hub` mit `start()`, also die Verdrahtung des
Betriebs: dasselbe `_gate_teile()` mit `Kontextspeicher`, `Archivsuche`,
`Deklassifizierung` und `audit_buch()`, derselbe `kontext.sock` mit derselben
Unit-Allowlist. Der Pruefstand baut davon NICHTS nach.

GENAU EIN EINGRIFF: die Unit-Allowlist von `kontext.sock` kann gesetzt werden.
Ohne Argument bleibt sie, wie sie im Prueflingscode steht
(`daemon.KONTEXT_UNITS`, heute `("daimon-mind.service",)`). Mit Argumenten wird
sie VOR `start()` ersetzt -- und nur damit.

Warum ueberhaupt: der Weg des Archivtreffers zum Modell laeuft ueber
`kontext.sock`, und nur `daimon-mind` darf dort sprechen. Ein Verifizierer
braucht also entweder eine transiente Unit dieses Namens -- das waere ein
Eingriff in den `daimon-mind.service` dieser Maschine -- oder er misst die
Grenze anders. Dieser Treiber misst sie anders, und zwar in beide Richtungen:

  * Ein Hub OHNE Argument weist den Pruefstand ab. Das ist die ECHTE Allowlist
    gegen die ECHTE Unit-Aufloesung (`ipc.peer_of` ueber `SO_PEERPIDFD` und den
    cgroup-Pfad) -- gemessen, nicht angenommen.
  * Ein Hub MIT der Unit des Pruefstands als Allowlist bedient ihn. Das ist die
    Positivkontrolle: sie belegt, dass Socket, Gate, Archivsuche und Antwortweg
    stehen und dass die Abweisung oben aus der Allowlist kam und nicht aus
    einem kaputten Transport.

Was damit NICHT gemessen ist: dass die Unit des echten Mind-Prozesses
`daimon-mind.service` HEISST. Das ist der Zulauf von T-5.9b.

**Dieser Treiber ist die zweite Fassung von `t59_hub.py` und das mit Absicht.**
`T-5.9.*` gehoert einem anderen, abgeschlossenen Auftrag und wird von dieser
Sitzung nicht angefasst; ein gemeinsamer Helfer haette ihn geaendert. Die
Doppelung steht im Ledger unter „Grenzen".

Der Eingriff wird GEWOGEN: sha256 der Allowlist vorher und nachher. Bleibt sie
gleich, bricht der Treiber ab -- eine Positivkontrolle, die nichts veraendert
hat, meldet sonst brav `ok`.
"""
from __future__ import annotations

import hashlib
import sys
import threading
from pathlib import Path


def _summe(werte) -> str:
    return hashlib.sha256(repr(tuple(werte)).encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    pruefling, runtime = argv[0], argv[1]
    units = tuple(argv[2:])
    sys.path.insert(0, pruefling)

    from daimon.hub import daemon

    if units:
        vorher = _summe(daemon.KONTEXT_UNITS)
        daemon.KONTEXT_UNITS = units
        nachher = _summe(daemon.KONTEXT_UNITS)
        if vorher == nachher:
            print(f"POSITIVKONTROLLE GESCHEITERT: die Allowlist von "
                  f"kontext.sock ist unveraendert (sha256 {vorher} == "
                  f"{nachher}). Ein Hub, der ohnehin schon {units!r} erlaubt, "
                  f"waere keine Gegenprobe zur Abweisung.", file=sys.stderr)
            return 3
        print(f"Allowlist gesetzt: sha256 {vorher[:12]} -> {nachher[:12]} "
              f"({units!r})", file=sys.stderr)
    else:
        print(f"Allowlist unberuehrt: {daemon.KONTEXT_UNITS!r}", file=sys.stderr)
    sys.stderr.flush()

    hub = daemon.Hub(runtime_dir=Path(runtime))
    hub.start()
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
