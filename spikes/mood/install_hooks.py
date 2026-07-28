#!/usr/bin/env python3
"""T-1.5 — traegt den Hook-Rekorder in ~/.claude/settings.json ein und wieder aus.

    python3 install_hooks.py --on     # eintragen
    python3 install_hooks.py --off    # entfernen
    python3 install_hooks.py --status # nur zeigen

Das ist eine Aenderung an der globalen Konfiguration von Matthias, nicht an
diesem Repo. Deshalb drei Auflagen, die das Skript selbst durchsetzt:

  * Es MERGT. Bestehende Hooks -- der Read-Limiter unter PreToolUse und
    inject-workflow-rules.sh unter UserPromptSubmit -- bleiben unangetastet.
  * Jeder eigene Eintrag traegt MARKER im Befehl. --off entfernt genau die
    markierten Eintraege und nichts sonst.
  * Vor jedem Schreiben wird eine Sicherung mit Zeitstempel angelegt.

Der Befehl endet auf `|| true`, damit ein Fehler im Rekorder niemals einen
Werkzeugaufruf blockiert. Bei PreToolUse wuerde ein Exit != 0 genau das tun.
"""

import argparse
import json
import shutil
import time
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
HERE = Path(__file__).resolve().parent
MARKER = "daimon-t15-mood"

# Alle acht Ereignisse aus dem Soll-Mapping (docs/PHASE3-original.md 4) plus
# SubagentStop. SubagentStop steht NICHT im Mapping -- gerade deshalb wird es
# mitgeschnitten: taucht es auf, ist das eine Luecke im Mapping und damit ein
# Ergebnis des Spikes. Dasselbe gilt fuer StopFailure und PostToolUseFailure,
# von denen unklar ist, ob es sie als Hook-Ereignis ueberhaupt gibt.
EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "StopFailure",
    "SubagentStop",
    "SessionEnd",
]

COMMAND = (
    f"jq -c -f {HERE / 'record.jq'} >> {HERE / 'events.jsonl'} "
    f"2>/dev/null || true  # {MARKER}"
)


def load():
    if not SETTINGS.exists():
        return {}
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def save(data):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = SETTINGS.with_suffix(f".json.bak-{stamp}")
    if SETTINGS.exists():
        shutil.copy2(SETTINGS, backup)
        print(f"Sicherung: {backup}")
    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")


def is_ours(group):
    return any(MARKER in h.get("command", "") for h in group.get("hooks", []))


def turn_on(data):
    hooks = data.setdefault("hooks", {})
    added = []
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        if any(is_ours(g) for g in groups):
            continue
        group = {"hooks": [{"type": "command", "command": COMMAND}]}
        if event in ("PreToolUse", "PostToolUse"):
            group["matcher"] = ".*"
        groups.append(group)
        added.append(event)
    return added


def turn_off(data):
    hooks = data.get("hooks", {})
    removed = []
    for event in list(hooks):
        keep = [g for g in hooks[event] if not is_ours(g)]
        if len(keep) != len(hooks[event]):
            removed.append(event)
        if keep:
            hooks[event] = keep
        else:
            del hooks[event]
    return removed


def status(data):
    hooks = data.get("hooks", {})
    if not hooks:
        print("keine Hooks eingetragen")
        return
    for event, groups in hooks.items():
        for group in groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                mine = " <== T-1.5" if MARKER in cmd else ""
                print(f"  {event:18s} matcher={group.get('matcher', '-'):6s} "
                      f"{cmd[:70]}{mine}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--on", action="store_true")
    g.add_argument("--off", action="store_true")
    g.add_argument("--status", action="store_true")
    args = ap.parse_args()

    data = load()
    if args.status:
        status(data)
        return

    changed = turn_on(data) if args.on else turn_off(data)
    if not changed:
        print("nichts zu tun")
        return
    save(data)
    print(("eingetragen: " if args.on else "entfernt: ") + ", ".join(changed))
    print()
    status(load())


if __name__ == "__main__":
    main()
