#!/usr/bin/env python3
"""T-1.5 — wertet events.jsonl gegen das Soll-Mapping aus und schreibt results.json.

Soll-Mapping: docs/PHASE3-original.md 4. Es ist eine Behauptung aus der
Planung; dieser Spike prueft sie gegen echte Sitzungen.

"Abweichung" hat drei Formen, und die dritte ist die gefaehrliche:

  1. TOTER_EINTRAG   -- das Mapping kennt ein Ereignis, das nie feuert.
                        Harmlos, aber der Mood ist dann unerreichbar.
  2. LUECKE          -- ein Ereignis feuert, das im Mapping fehlt.
                        Der Client wuesste nicht, was er zeigen soll.
  3. NICHT_ENTSCHEIDBAR -- ein Ereignis feuert, aber die Nutzlast traegt das
                        Merkmal nicht, das das Mapping zum Unterscheiden
                        braucht. Beispiel: Notification ohne
                        notification_type -- dann sind needs_input und idle
                        nicht auseinanderzuhalten, und needs_input ist der
                        Mood, an dem die ganze Idee haengt.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVENTS = HERE / "events.jsonl"

# docs/PHASE3-original.md 4
MAPPING = {
    "SessionStart": "observing",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "Notification:permission_prompt": "needs_input",
    "Notification:idle_prompt": "idle",
    "Stop": "done",
    "StopFailure": "failed",
    "PostToolUseFailure": "failed",
    "SessionEnd": "sleeping",
}
PRIORITY = ["needs_input", "failed", "working", "thinking", "observing",
            "done", "idle", "sleeping"]


def key_of(rec):
    """Der Schluessel, unter dem das Mapping nachschlaegt."""
    event = rec.get("hook_event_name", "?")
    if event == "Notification":
        nt = rec.get("notification_type")
        return f"Notification:{nt}" if nt else "Notification:<ohne notification_type>"
    return event


def load():
    if not EVENTS.exists():
        return []
    rows = []
    for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def classify(events):
    """Grobe Einordnung einer Sitzung -- der Spike verlangt Vielfalt."""
    kinds = {e.get("hook_event_name") for e in events}
    types = {e.get("notification_type") for e in events}
    tags = []
    if "permission_prompt" in types:
        tags.append("mit Freigabe-Prompt")
    if "StopFailure" in kinds:
        tags.append("fehlgeschlagen")
    if "Stop" in kinds:
        tags.append("erfolgreich")
    if "SessionEnd" not in kinds:
        tags.append("ohne SessionEnd")
    if "SubagentStop" in kinds:
        tags.append("mit Subagenten")
    return tags or ["unauffaellig"]


def main():
    rows = load()
    by_session = defaultdict(list)
    for r in rows:
        by_session[r.get("session_id", "<ohne session_id>")].append(r)
    for evs in by_session.values():
        evs.sort(key=lambda r: r.get("wall", 0))

    seen = defaultdict(int)
    for r in rows:
        seen[key_of(r)] += 1

    mismatches = []
    for k in sorted(set(MAPPING) - set(seen)):
        mismatches.append({
            "event": k, "expected": MAPPING[k], "actual": "nie gefeuert",
            "art": "TOTER_EINTRAG",
        })
    for k in sorted(set(seen) - set(MAPPING)):
        art = "NICHT_ENTSCHEIDBAR" if k.startswith("Notification:<") else "LUECKE"
        mismatches.append({
            "event": k, "expected": "kein Mapping-Eintrag",
            "actual": f"{seen[k]}x gefeuert", "art": art,
        })

    # Parallelitaet: ueberlappen sich Sitzungen zeitlich?
    spans = sorted(
        (min(e["wall"] for e in evs), max(e["wall"] for e in evs), sid)
        for sid, evs in by_session.items() if evs
    )
    parallel = sum(
        1 for i, (s, e, _) in enumerate(spans)
        for s2, _, _ in spans[i + 1:] if s2 < e
    )

    sessions = [
        {
            "session_id": sid,
            "events": len(evs),
            "dauer_s": round(evs[-1]["wall"] - evs[0]["wall"], 1),
            "art": classify(evs),
            "abfolge": [key_of(e) for e in evs],
            "moods": [MAPPING.get(key_of(e), "<unbekannt>") for e in evs],
        }
        for sid, evs in sorted(by_session.items(),
                               key=lambda kv: min(e["wall"] for e in kv[1]) if kv[1] else 0)
    ]

    enough = len(sessions) >= 5
    result = {
        "spike": "T-1.5",
        "frage": "Stimmt das Mood-Mapping aus PHASE3-original.md 4 mit echten Sitzungen ueberein?",
        "quelle": "spikes/mood/events.jsonl (per .gitignore ausgeschlossen, enthaelt gekuerzte Prompttexte)",
        "sessions": len(sessions),
        "events_total": len(rows),
        "ereignisse_nach_typ": dict(sorted(seen.items(), key=lambda kv: -kv[1])),
        "parallele_ueberlappungen": parallel,
        "mismatches": mismatches,
        "sitzungen": sessions,
        "prioritaet": PRIORITY,
        "verdict": "pending" if not enough else ("pass" if not any(
            m["art"] == "NICHT_ENTSCHEIDBAR" for m in mismatches) else "pass mit Auflage"),
        "recommendation": (
            f"Noch nicht auswertbar: {len(sessions)} von 5 geforderten Sitzungen. "
            "Der Rekorder laeuft; erneut ausfuehren, sobald mehr Sitzungen vorliegen."
            if not enough else
            "Siehe mismatches. TOTER_EINTRAG heisst: Mood aus dem Mapping streichen oder "
            "anders ausloesen. LUECKE heisst: Mapping ergaenzen. NICHT_ENTSCHEIDBAR heisst: "
            "der Client kann den Mood aus dem Hook allein nicht bestimmen -- das trifft "
            "T-0.7 und muss dort geloest werden."
        ),
        "blocking": False,
    }
    (HERE / "results.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Sitzungen: {len(sessions)} (gefordert >= 5)   Ereignisse: {len(rows)}")
    print(f"parallele Ueberlappungen: {parallel}")
    for k, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}x {k}  -> {MAPPING.get(k, '<KEIN MAPPING>')}")
    if mismatches:
        print("Abweichungen:")
        for m in mismatches:
            print(f"  [{m['art']}] {m['event']}: erwartet {m['expected']}, tatsaechlich {m['actual']}")
    print(f"verdict: {result['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
