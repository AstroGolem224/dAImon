#!/usr/bin/env python3
"""Ersetzt echte restore_token-Werte in den DBus-Mitschnitten durch Platzhalter.

Warum das noetig ist: T-1.4 hat gemessen, dass ein *fremder* restore_token vom
Portal **ohne Dialog** akzeptiert wird -- der Token ist nicht an das
Session-Objekt gebunden. Damit ist er eine Faehigkeit, kein Protokolleintrag.
Die Mitschnitte gehoeren ins Repo, die Tokenwerte nicht. Das Repo ist oeffentlich.

Gleiche Tokens bekommen gleiche Platzhalter (TOKEN-A, TOKEN-B, ...), damit die
Mitschnitte weiter auswertbar bleiben: man sieht nach wie vor, welcher Lauf
welchen Token hineingegeben und welchen er zurueckbekommen hat.

Idempotent: bereits ersetzte Platzhalter werden nicht erneut angefasst.
"""

import re
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"

# dbus-monitor schreibt den Wert in die Zeile nach `string "restore_token"`.
PAIR = re.compile(
    r'(string "restore_token"\s*\n\s*variant\s+string ")([^"]*)(")'
)
PLACEHOLDER = re.compile(r"^<TOKEN-[A-Z]+ redigiert>$")


def main():
    if not RUNS.is_dir():
        raise SystemExit(f"{RUNS} fehlt")

    mapping: dict[str, str] = {}
    names = iter(string.ascii_uppercase)
    touched = 0

    for path in sorted(RUNS.glob("*.log")):
        text = path.read_text(encoding="utf-8", errors="replace")

        def sub(m):
            nonlocal touched
            value = m.group(2)
            if not value or PLACEHOLDER.match(value):
                return m.group(0)
            if value not in mapping:
                mapping[value] = f"<TOKEN-{next(names)} redigiert>"
            touched += 1
            return m.group(1) + mapping[value] + m.group(3)

        new = PAIR.sub(sub, text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(f"{path.name}: bereinigt")

    print(f"{touched} Vorkommen ersetzt, {len(mapping)} verschiedene Tokens")

    # Selbstpruefung: kein Mitschnitt darf danach noch einen echten Wert tragen.
    rest = []
    for path in sorted(RUNS.glob("*.log")):
        for m in PAIR.finditer(path.read_text(encoding="utf-8", errors="replace")):
            if m.group(2) and not PLACEHOLDER.match(m.group(2)):
                rest.append(f"{path.name}: {m.group(2)!r}")
    if rest:
        print("FEHLGESCHLAGEN, noch echte Werte vorhanden:", *rest, sep="\n  ")
        return 1
    print("Selbstpruefung bestanden: kein Klartext-Token mehr in runs/*.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
