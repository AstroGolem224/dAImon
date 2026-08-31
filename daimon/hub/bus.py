"""T-0.9 — Ereignisbus: Hook-Ereignis rein, Mood raus.

Das Mapping ist dasselbe wie im Bestandsdaemon und in T-0.3.t festgeschrieben.
Es wird hier nicht verbessert -- die vier dort dokumentierten Abweichungen
bleiben Abweichungen, bis T-0.7 (Policy) sie entscheidet. Wer sie hier still
repariert, macht die xfail-Tests gruen und nimmt damit die einzige Stelle weg,
an der sie sichtbar sind.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from daimon.common.protocol import Event

Handler = Callable[[Event], None]

# Freier Hook-Text (Assistant-Antwort, Fehlermeldung, Freigabefrage) enthaelt
# Pfade, Codefragmente und Links. In der Sprechblase sind das Zeichenwuesten,
# die den einen lesbaren Satz verdraengen -- und ein Pfad oder Token dort ist
# ausserdem eine Anzeige, die niemand kuratiert hat. Ersetzt wird deshalb
# durch eine Marke, nicht gekuerzt: der Satz bleibt lesbar, und man sieht,
# DASS da etwas stand. Die Reihenfolge ist bindend -- Code zuerst, sonst
# zerlegt die Pfadregel den Inhalt eines Codeblocks.
_ERSETZUNGEN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"```.*?```", re.DOTALL), "[Code]"),
    (re.compile(r"`[^`]+`"), "[Code]"),
    (re.compile(r"\b(?:https?|ftp|file)://\S+"), "[Link]"),
    # Ein Pfad beginnt nach einem Nicht-Wortzeichen -- sonst waere "und/oder"
    # einer.
    (re.compile(r"(?<![\w~.])(?:~|\.{1,2})?/[\w.\-]+(?:/[\w.\-]+)*"), "[Pfad]"),
    (re.compile(r"\b[\w.\-]+\.(?:py|rs|json|toml|md|sh|log|ya?ml|txt|c|h)\b"), "[Pfad]"),
    # Undurchsichtige lange Zeichenkette: Hash, Token, Base64.
    (re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"), "[Wert]"),
]


def platzhalter_setzen(text: str) -> str:
    """Technische Bestandteile durch eine Marke ersetzen."""
    for muster, marke in _ERSETZUNGEN:
        text = muster.sub(marke, text)
    return text


def _kurz(text: str, n: int = 240) -> str:
    text = " ".join(platzhalter_setzen(str(text)).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def mood_of(payload: dict) -> tuple[str | None, dict | None]:
    """Hook-Nutzlast -> (mood, bubble|None).

    Gibt (None, None) fuer unbekannte Ereignisse zurueck. Der Aufrufer darf
    daraufhin NICHTS aendern, auch `rev` nicht -- so festgeschrieben in
    T-0.3.t.
    """
    name = payload.get("hook_event_name", "")

    if name == "SessionStart":
        return "observing", None
    if name == "UserPromptSubmit":
        return "thinking", None
    if name in ("PreToolUse", "PostToolUse", "PostToolBatch"):
        return "working", None

    if name == "Notification":
        art = payload.get("notification_type") or payload.get("matcher") or ""
        if art == "permission_prompt":
            return "needs_input", {
                "title": "braucht dein OK",
                "body": _kurz(payload.get("message") or "Claude wartet auf eine Freigabe."),
                "urgent": True,
            }
        if art == "idle_prompt":
            return "idle", None
        # Ohne notification_type nicht entscheidbar -- siehe die Abweichung
        # NICHT_ENTSCHEIDBAR in spikes/mood/results.json. Bewusst unveraendert.
        return "observing", None

    if name == "Stop":
        msg = (payload.get("last_assistant_message") or "").strip()
        return "done", {
            "title": "fertig",
            "body": _kurz(msg) if msg else "Task durch.",
            "urgent": False,
        }

    if name in ("StopFailure", "PostToolUseFailure"):
        return "failed", {
            "title": "schiefgegangen",
            "body": _kurz(payload.get("error", "unbekannter Fehler")),
            "urgent": True,
        }

    if name == "SessionEnd":
        return "sleeping", None

    return None, None


def projekt_aus_cwd(cwd: str) -> str:
    """Letztes Pfadsegment als Projektname. Reicht fuer focus.project und
    kostet keine Konfiguration."""
    cwd = (cwd or "").rstrip("/")
    return cwd.rsplit("/", 1)[-1] if cwd else ""


class Bus:
    """Verteilt Ereignisse an Abonnenten. Bewusst simpel: der Hub hat eine
    Handvoll Abonnenten im selben Prozess, kein Netzwerk dazwischen."""

    def __init__(self) -> None:
        self._handler: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._handler.append(handler)

    def publish(self, event: Event) -> None:
        for h in list(self._handler):
            # Ein kaputter Abonnent darf die anderen nicht mitreissen. Der
            # Hub haengt an jedem Werkzeugaufruf von Claude Code.
            try:
                h(event)
            except Exception:  # noqa: BLE001
                continue
