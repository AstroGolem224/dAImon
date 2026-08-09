#!/usr/bin/env python3
"""Die Hook-Bridge in `~/.claude/settings.json` eintragen -- wiederholbar.

Warum es dieses Skript gibt
----------------------------------------------------------------------------
`config/claude-hooks.json` liegt seit T-0.11 im Repo, und am 09.08. stellte
sich heraus: es war nie eingetragen. In `settings.json` standen neun Hooks,
alle schrieben nur nach `spikes/mood/events.jsonl` (Mood-Spike aus T-1.5),
keiner meldete an die Bridge. Der Hub sah `units: {}`, das Pet stand auf
`sleeping` -- das Kernversprechen des Projekts lief nie. Ein Handgriff, den
man einmal vergisst, ist ein Handgriff, der ein Skript verdient.

Das Token wird NICHT eingebacken
----------------------------------------------------------------------------
Die Bridge erzeugt es bei jedem Start neu (`hookbridge/bridge.py`). Ein
eingesetzter Wert waere nach dem naechsten Neustart still ungueltig -- die
Hooks liefen weiter, die Bridge antwortete 401, und niemand erfuehre davon.
Deshalb steht im Kommando `$(cat "$XDG_RUNTIME_DIR/daimon/hook-token")`.

Was es NICHT anfasst
----------------------------------------------------------------------------
Fremde Hooks. Sie bleiben stehen, auch die des Mood-Spikes. Das Skript fuegt
je Ereignis genau eine Gruppe hinzu und erkennt seine eigene an der URL --
zweimal aufgerufen aendert es beim zweiten Mal nichts.

Aufruf:
    tools/hooks_installieren.py            # eintragen (legt eine Sicherung an)
    tools/hooks_installieren.py --pruefen  # nur sagen, was fehlt
    tools/hooks_installieren.py --entfernen
    tools/hooks_installieren.py --selbsttest
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VORLAGE = REPO / "config" / "claude-hooks.json"
ZIEL = Path.home() / ".claude" / "settings.json"
SICHERUNG = ZIEL.with_suffix(".json.vor-daimon-hooks")
# Woran das Skript seine eigenen Eintraege erkennt. Die URL, nicht das ganze
# Kommando: aendert sich spaeter eine Kleinigkeit daran, sollen alte Eintraege
# trotzdem gefunden und ersetzt werden.
MERKMAL = "/hook"
TOKEN_ZUR_LAUFZEIT = 'Bearer $(cat "$XDG_RUNTIME_DIR/daimon/hook-token")'


def kommandos() -> dict[str, str]:
    """Je Ereignis das Kommando aus der Vorlage, mit Token zur Laufzeit."""
    vorlage = json.loads(VORLAGE.read_text(encoding="utf-8"))["hooks"]
    return {
        ereignis: gruppen[0]["hooks"][0]["command"].replace(
            "Bearer __TOKEN__", TOKEN_ZUR_LAUFZEIT)
        for ereignis, gruppen in vorlage.items()
    }


def _ist_unserer(hook: dict) -> bool:
    befehl = hook.get("command", "")
    return MERKMAL in befehl and "hook-token" in befehl


def einbauen(einstellungen: dict) -> tuple[dict, list[str]]:
    """Reine Funktion: neue Einstellungen und die geaenderten Ereignisse."""
    neu = copy.deepcopy(einstellungen)
    hooks = neu.setdefault("hooks", {})
    geaendert = []
    for ereignis, befehl in kommandos().items():
        gruppen = hooks.setdefault(ereignis, [])
        vorhanden = [h for g in gruppen for h in g.get("hooks", [])
                     if _ist_unserer(h)]
        if any(h.get("command") == befehl for h in vorhanden):
            continue
        # Eine aeltere eigene Fassung wird ERSETZT, nicht ergaenzt: sonst
        # meldet jeder Hook zweimal, und der Hub zaehlt doppelt.
        for h in vorhanden:
            h["command"] = befehl
        if not vorhanden:
            gruppen.append({"hooks": [{"type": "command", "command": befehl}]})
        geaendert.append(ereignis)
    return neu, geaendert


def ausbauen(einstellungen: dict) -> tuple[dict, list[str]]:
    neu = copy.deepcopy(einstellungen)
    entfernt = []
    for ereignis, gruppen in list(neu.get("hooks", {}).items()):
        uebrig = []
        for gruppe in gruppen:
            behalten = [h for h in gruppe.get("hooks", []) if not _ist_unserer(h)]
            if len(behalten) != len(gruppe.get("hooks", [])):
                entfernt.append(ereignis)
            if behalten:
                gruppe["hooks"] = behalten
                uebrig.append(gruppe)
        if uebrig:
            neu["hooks"][ereignis] = uebrig
        else:
            del neu["hooks"][ereignis]
    return neu, entfernt


def schreiben(daten: dict) -> None:
    # Sicherung nur beim ersten Mal: die zweite ueberschriebe die Fassung von
    # VOR dem ersten Lauf, also genau die, die man zurueckhaben will.
    if ZIEL.exists() and not SICHERUNG.exists():
        SICHERUNG.write_bytes(ZIEL.read_bytes())
        print(f"Sicherung: {SICHERUNG}")
    vorlaeufig = ZIEL.with_suffix(".json.neu")
    vorlaeufig.write_text(json.dumps(daten, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    vorlaeufig.replace(ZIEL)
    ZIEL.chmod(0o600)


def selbsttest() -> int:
    """Der eine Lauf, der faellt, wenn die Zusammenfuehrung kaputtgeht."""
    fremd = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "echo fremd"}]}]}}
    einmal, geaendert = einbauen(fremd)
    assert geaendert, "nichts eingebaut"
    zweimal, nochmal = einbauen(einmal)
    assert nochmal == [], f"nicht wiederholbar: {nochmal}"
    assert zweimal == einmal, "zweiter Lauf hat etwas veraendert"
    assert any("echo fremd" == h["command"]
               for g in zweimal["hooks"]["Stop"] for h in g["hooks"]), \
        "fremder Hook verloren"
    zurueck, entfernt = ausbauen(einmal)
    assert entfernt, "nichts entfernt"
    assert zurueck == fremd, f"Ausbau stellt nicht den Anfang her: {zurueck}"
    assert TOKEN_ZUR_LAUFZEIT in next(iter(kommandos().values())), \
        "Token wird eingebacken statt gelesen"
    print("Selbsttest grün")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pruefen", action="store_true",
                    help="nur melden, was fehlt; nichts schreiben")
    ap.add_argument("--entfernen", action="store_true")
    ap.add_argument("--selbsttest", action="store_true")
    args = ap.parse_args()

    if args.selbsttest:
        return selbsttest()
    if not ZIEL.exists():
        print(f"{ZIEL} gibt es nicht -- Claude Code ist hier nicht eingerichtet.",
              file=sys.stderr)
        return 1
    alt = json.loads(ZIEL.read_text(encoding="utf-8"))

    if args.entfernen:
        neu, betroffen = ausbauen(alt)
        wort = "entfernt aus"
    else:
        neu, betroffen = einbauen(alt)
        wort = "eingetragen für"

    if not betroffen:
        print("Nichts zu tun; die Hooks stehen schon so in der Datei.")
        return 0
    liste = ", ".join(sorted(set(betroffen)))
    if args.pruefen:
        print(f"Offen -- {wort}: {liste}")
        return 1
    schreiben(neu)
    print(f"{wort}: {liste}")
    print("Wirksam ab der nächsten Claude-Code-Sitzung.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
