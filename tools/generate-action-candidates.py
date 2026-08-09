#!/usr/bin/env python3
"""T-4.1 — Aktionskandidaten aus kglobalaccel einsammeln.

Was diese Datei erzeugt, ist KEINE Whitelist
----------------------------------------------------------------------------
`config/actions/candidates.yaml` ist eine Vorschlagsliste und sonst nichts.
Jeder Eintrag traegt `status: candidate`, und nichts im System liest diese
Datei als Erlaubnis -- die tatsaechliche Whitelist entsteht in T-4.2 von Hand,
in einer eigenen Datei. Wer diesen Generator so erweitert, dass er `approved`
schreiben kann, hat die Handpruefung abgeschafft, nicht automatisiert.

Deshalb weigert sich das Skript, eine vorhandene Datei zu ueberschreiben, in
der ein anderer Status steht: dort haette jemand von Hand entschieden, und ein
Generatorlauf darf eine Entscheidung nicht stillschweigend zuruecknehmen.

Warum kein Zeitstempel drin steht
----------------------------------------------------------------------------
Die Akzeptanz verlangt Idempotenz. Ein `generated_at` waere bei jedem Lauf
anders, die Datei damit bei jedem Lauf "geaendert" -- und ein Diff, der immer
rauscht, ist ein Diff, den niemand mehr liest. Sortiert wird ebenfalls, aus
demselben Grund: die Reihenfolge von DBus ist nicht zugesagt.

Warum System-Python und nicht das venv
----------------------------------------------------------------------------
`gi` (PyGObject) liegt im System, nicht im venv dieses Projekts (dessen
`dependencies` sind absichtlich leer). Der Shebang zeigt deshalb auf
`python3`. Ein `gdbus`-Aufruf mit Textparser waere die Alternative gewesen --
und haette bei einem Namen mit Anfuehrungszeichen still das Falsche gelesen.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ZIEL = REPO / "config" / "actions" / "candidates.yaml"

DIENST = "org.kde.kglobalaccel"
PFAD = "/kglobalaccel"
SCHNITTSTELLE = "org.kde.KGlobalAccel"

KOPF = """\
# Aktionskandidaten aus kglobalaccel -- ERZEUGT von tools/generate-action-candidates.py.
#
# Das hier ist KEINE Whitelist. Kein Eintrag ist ausfuehrbar, kein Dienst liest
# diese Datei als Erlaubnis. Die gepruefte Liste entsteht in T-4.2 von Hand
# unter config/actions/core.yaml.
#
# Neu erzeugen: tools/generate-action-candidates.py
# Sortiert und ohne Zeitstempel, damit ein zweiter Lauf keinen Diff erzeugt.
version: 1
quelle: kglobalaccel
actions:
"""


def aktionen_lesen() -> list[dict]:
    """Alle Aktionen aller Komponenten, als flache Liste.

    `allActionsForComponent` liefert dieselbe Menge wie `shortcutNames()` --
    zusaetzlich aber die Klarnamen, und die sind das, was ein Mensch bei der
    Handpruefung in T-4.2 tatsaechlich liest.
    """
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def ruf(methode: str, argumente):
        return bus.call_sync(DIENST, PFAD, SCHNITTSTELLE, methode, argumente,
                             None, Gio.DBusCallFlags.NONE, 10_000, None).unpack()[0]

    gefunden: list[dict] = []
    for komponente in ruf("allMainComponents", None):
        # actionId ist IMMER vierteilig: [Komponente, Aktion, Klarname der
        # Komponente, Klarname der Aktion]. Bei einer Komponente sind die
        # Aktionsfelder leer.
        kennung = list(komponente) + ["", "", "", ""]
        kennung = kennung[:4]
        for aktion in ruf("allActionsForComponent",
                          GLib.Variant("(as)", [kennung])):
            a = list(aktion) + ["", "", "", ""]
            gefunden.append({
                "id": f"{a[0]}:{a[1]}",
                "component": a[0],
                "component_friendly": a[2],
                "action": a[1],
                "action_friendly": a[3],
                "status": "candidate",
            })
    return gefunden


def yaml_wert(wert: str) -> str:
    """Ein Skalar, immer in doppelten Anfuehrungszeichen.

    Kein YAML-Modul: das Projekt hat absichtlich keine Laufzeitabhaengigkeiten
    (`pyproject.toml`, `dependencies = []`), und die Struktur hier ist eine
    Liste flacher Abbildungen. Immer zu quoten ist die Variante, die bei
    Doppelpunkten, fuehrenden Zeichen und leeren Werten nicht nachdenken muss.

    ponytail: eigener Serialisierer. Obergrenze: sobald verschachtelte oder
    mehrzeilige Werte dazukommen, gehoert `ruamel`/`PyYAML` her -- nicht ein
    zweiter Sonderfall in dieser Funktion.
    """
    text = str(wert)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    # Steuerzeichen wuerden die Datei unlesbar machen, ohne dass man es sieht.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "?", text)
    return f'"{text}"'


def rendern(aktionen: list[dict]) -> str:
    zeilen = [KOPF]
    for a in sorted(aktionen, key=lambda x: (x["component"], x["action"])):
        zeilen.append(f"  - id: {yaml_wert(a['id'])}\n")
        for feld in ("component", "component_friendly", "action",
                     "action_friendly"):
            zeilen.append(f"    {feld}: {yaml_wert(a[feld])}\n")
        # Der Status steht als LETZTES und immer wortwoertlich: er ist die
        # Aussage dieser Datei, nicht ein Feld unter vielen.
        zeilen.append("    status: candidate\n")
    return "".join(zeilen)


def fremde_status(pfad: Path) -> list[str]:
    """Statuswerte in einer vorhandenen Datei, die nicht `candidate` sind."""
    if not pfad.is_file():
        return []
    werte = re.findall(r"^\s*status:\s*(\S+)\s*$", pfad.read_text(encoding="utf-8"),
                       re.MULTILINE)
    return sorted({w.strip('"\'') for w in werte if w.strip('"\'') != "candidate"})


def selbsttest() -> int:
    beispiele = [
        {"id": 'kwin:Sag "hallo"', "component": "kwin",
         "component_friendly": "KWin", "action": 'Sag "hallo"',
         "action_friendly": "Zeile\nUmbruch", "status": "candidate"},
        {"id": "a:b", "component": "a", "component_friendly": "",
         "action": "b", "action_friendly": "", "status": "candidate"},
    ]
    text = rendern(beispiele)
    assert text == rendern(list(reversed(beispiele))), "Reihenfolge nicht stabil"
    assert '\\"hallo\\"' in text, "Anfuehrungszeichen nicht maskiert"
    assert "Zeile\\nUmbruch" in text, "Zeilenumbruch nicht maskiert"
    assert text.count("status: candidate") == len(beispiele), "Status fehlt"
    assert "approved" not in text, "der Generator kennt kein approved"
    assert '""' in text, "leerer Wert nicht darstellbar"
    # Ein Lauf gegen sich selbst: was gerendert wurde, gilt als candidate.
    tmp = Path("/tmp") / f"t41-selbsttest-{len(text)}.yaml"
    tmp.write_text(text, encoding="utf-8")
    assert fremde_status(tmp) == [], "eigene Ausgabe faellt durch die Statuspruefung"
    tmp.write_text(text.replace("status: candidate", "status: approved", 1),
                   encoding="utf-8")
    assert fremde_status(tmp) == ["approved"], "fremder Status wird nicht erkannt"
    tmp.unlink()
    print("Selbsttest grün")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ziel", type=Path, default=ZIEL)
    ap.add_argument("--stdout", action="store_true",
                    help="nur ausgeben, nichts schreiben")
    ap.add_argument("--force", action="store_true",
                    help="auch ueberschreiben, wenn ein fremder Status drinsteht")
    ap.add_argument("--selbsttest", action="store_true")
    args = ap.parse_args()

    if args.selbsttest:
        return selbsttest()

    try:
        aktionen = aktionen_lesen()
    except Exception as fehler:  # kein laufendes kglobalaccel, kein gi, kein Bus
        print(f"kglobalaccel nicht lesbar: {fehler}", file=sys.stderr)
        return 1
    if not aktionen:
        # Eine leere Liste zu schreiben waere schlimmer als nichts zu tun: sie
        # saehe aus wie ein Ergebnis.
        print("Keine Aktionen gemeldet -- nichts geschrieben.", file=sys.stderr)
        return 1

    text = rendern(aktionen)
    if args.stdout:
        sys.stdout.write(text)
        return 0

    fremd = fremde_status(args.ziel)
    if fremd and not args.force:
        print(f"{args.ziel} enthaelt bereits {', '.join(fremd)} -- das ist eine "
              f"Handentscheidung. Mit --force ueberschreiben, sonst nichts getan.",
              file=sys.stderr)
        return 1

    args.ziel.parent.mkdir(parents=True, exist_ok=True)
    alt = args.ziel.read_text(encoding="utf-8") if args.ziel.is_file() else None
    args.ziel.write_text(text, encoding="utf-8")
    komponenten = len({a["component"] for a in aktionen})
    zustand = "unveraendert" if alt == text else ("neu" if alt is None else "aktualisiert")
    print(f"{args.ziel}: {len(aktionen)} Kandidaten aus {komponenten} Komponenten "
          f"({zustand}), alle mit status: candidate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
