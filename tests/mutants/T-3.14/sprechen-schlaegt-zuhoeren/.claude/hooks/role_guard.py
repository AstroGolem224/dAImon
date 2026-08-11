#!/usr/bin/env python3
"""PreToolUse-Hook: setzt die Rollen-Pfadlisten aus .claude/roles.toml durch.

Das ist der Mechanismus, den Review-Runde 5 vermisst hat. Ohne ihn ist
"der Builder darf tests/verify/ nicht schreiben" eine Absichtserklaerung.

Installation in .claude/settings.json:

    "hooks": {
      "PreToolUse": [
        { "matcher": "Write|Edit|NotebookEdit",
          "hooks": [{ "type": "command",
                      "command": "python3 .claude/hooks/role_guard.py" }] },
        { "matcher": "Bash",
          "hooks": [{ "type": "command",
                      "command": "python3 .claude/hooks/role_guard.py" }] }
      ]
    }

Rolle kommt aus DAIMON_ROLE. Fehlt sie, wird alles Schreibende abgelehnt.
Fail closed.
"""

import json
import os
import re
import sys
import tomllib
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROLES = REPO / ".claude" / "roles.toml"

# Bash-Kommandos, die schreiben koennen. Absichtlich grosszuegig: lieber eine
# Rueckfrage zu viel als eine umgangene Grenze.
WRITING_CMD = re.compile(
    r"(^|[|;&]|\s)(tee|dd|truncate|install|cp|mv|rm|rmdir|ln|sed\s+-i|"
    r"perl\s+-i|chmod|chown|touch|mkdir|git\s+(checkout|restore|apply|add))\b"
    r"|>\s*\S|>>\s*\S"
)


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def allow_through() -> None:
    # Kein Urteil: die normale Berechtigungslogik entscheidet weiter.
    sys.exit(0)


def load_role() -> tuple[str, dict]:
    role = os.environ.get("DAIMON_ROLE", "unknown").strip() or "unknown"
    try:
        with ROLES.open("rb") as fh:
            cfg = tomllib.load(fh)
    except OSError as exc:
        deny(f"role_guard: {ROLES} nicht lesbar ({exc}). Fail closed.")
    spec = cfg.get("roles", {}).get(role)
    if spec is None:
        deny(f"role_guard: unbekannte Rolle {role!r}. Fail closed.")
    return role, spec


def relative(path_str: str) -> str | None:
    """Pfad relativ zum Repo, oder None wenn ausserhalb."""
    try:
        p = Path(path_str).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        # resolve(strict=False) folgt Symlinks -- genau das wollen wir hier,
        # sonst umgeht ein Symlink die Pfadliste.
        return str(p.resolve().relative_to(REPO))
    except ValueError:
        return None


def blocked(rel: str, spec: dict) -> str | None:
    """None = erlaubt, sonst die Regel, die es verboten hat.

    Auswertung wie die Policy-Engine des Projekts (Design 6.5): erst deny,
    dann allow, erster Treffer gewinnt. Zwei Faelle:

      * Rolle mit gezielten deny-Mustern (builder, reviewer): Treffer verbietet.
        Ein allow = "**" ist nur die Vorgabe fuer alles Uebrige, keine Ausnahme.
      * Rolle mit deny = ["**"] (investigator, unknown): default-deny.
        Erlaubt ist nur, was ein ausdrueckliches allow-Muster trifft.
    """
    denies = spec.get("deny", [])
    allows = spec.get("allow", [])
    catchall_deny = "**" in denies

    for pat in denies:
        if pat == "**":
            continue
        if fnmatch(rel, pat) or fnmatch(rel, pat.rstrip("*").rstrip("/") + "/*"):
            return pat

    if catchall_deny:
        for pat in allows:
            if fnmatch(rel, pat) or fnmatch(rel, pat.rstrip("*").rstrip("/") + "/*"):
                return None
        return "**"

    return None


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        allow_through()

    tool = event.get("tool_name", "")
    inp = event.get("tool_input", {}) or {}
    role, spec = load_role()

    targets: list[str] = []
    if tool in ("Write", "Edit", "NotebookEdit"):
        if fp := inp.get("file_path") or inp.get("notebook_path"):
            targets.append(fp)
    elif tool == "Bash":
        cmd = inp.get("command", "")
        if not WRITING_CMD.search(cmd):
            allow_through()
        # Jedes Wort, das wie ein Pfad in geschuetzte Bereiche aussieht.
        for m in re.finditer(r"[\w./~-]+", cmd):
            tok = m.group(0)
            if "/" in tok or tok.endswith((".sh", ".toml", ".py")):
                targets.append(tok)
    else:
        allow_through()

    for t in targets:
        rel = relative(t)
        if rel is None:
            continue
        if pat := blocked(rel, spec):
            deny(
                f"role_guard: Rolle {role!r} darf {rel!r} nicht schreiben "
                f"(Regel {pat!r} aus .claude/roles.toml). "
                f"Verifizierer und Mutanten gehoeren der Rolle 'reviewer'."
            )

    allow_through()


if __name__ == "__main__":
    main()
