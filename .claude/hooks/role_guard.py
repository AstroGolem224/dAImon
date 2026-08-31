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

import fcntl
import json
import os
import re
import shlex
import sys
import tomllib
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROLES = REPO / ".claude" / "roles.toml"

# ---------------------------------------------------------------------------
# Bash: Ausfuehrung von Aenderung trennen.
#
# Bis zum 18.08. lief das in zwei groben Schritten: ein Regex entschied, ob das
# Kommando ueberhaupt schreiben kann (`>\s*\S` -- also auch `2>&1`), und traf
# er, galt JEDES pfadaehnliche Wort im Kommandotext als Schreibziel. Der
# Builder ist daran sechsmal haengengeblieben, jedes Mal bei einem lesenden
# Kommando.
#
# Jetzt wird das Kommando zerlegt und ein Ziel nur dort gesucht, wo tatsaechlich
# geschrieben wird: hinter einer Schreib-Umleitung und hinter einem schreibenden
# Verb. Die Verbliste ist unveraendert -- neu ist nur, WO nach ihr gesucht wird.
# ---------------------------------------------------------------------------

SCHREIBVERBEN = {
    "tee", "dd", "truncate", "install", "cp", "mv", "rm", "rmdir", "ln",
    "chmod", "chown", "touch", "mkdir",
}
# Quelle ... Ziel: nur das letzte Argument wird geschrieben.
NUR_LETZTES = {"cp", "mv", "ln", "install"}
# Schreiben erst mit In-Place-Schalter.
INPLACE_VERBEN = {"sed", "perl"}
GIT_SCHREIBEND = {"checkout", "restore", "apply", "add"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}

SCHREIB_UMLEITUNG = {">", ">>", ">|", "&>", "&>>", ">&"}
LESE_UMLEITUNG = {"<", "<<", "<<<", "<&", "<>"}
UMLEITUNG = SCHREIB_UMLEITUNG | LESE_UMLEITUNG
TRENNER = {";", ";;", "&", "&&", "|", "||", "|&", "(", ")"}

HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
PFADWORT = re.compile(r"[\w./~-]+")


def _ohne_heredoc(text: str) -> str:
    """Den Rumpf eines Heredocs entfernen: das ist Text, kein Kommando.

    Anlass 17.08.: ein `cat <<EOF`, dessen Rumpf Verifiziererpfade nannte,
    galt als Schreibversuch auf genau diese Pfade.
    """
    zeilen = text.split("\n")
    aus: list[str] = []
    i = 0
    while i < len(zeilen):
        zeile = zeilen[i]
        aus.append(zeile)
        marken = [m.group(2) for m in HEREDOC.finditer(zeile)]
        i += 1
        for marke in marken:
            while i < len(zeilen) and zeilen[i].strip() != marke:
                i += 1
            i += 1  # die Schlussmarke selbst
    return "\n".join(aus)


def _lex(text: str) -> list[str]:
    """Zerlegen wie eine Shell: Anfuehrungszeichen halten zusammen,
    Operatoren stehen fuer sich."""
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _ketten(toks: list[str]) -> list[list[str]]:
    """An `;`, `|`, `&&`, `(` ... in einzelne Kommandos zerlegen."""
    ketten: list[list[str]] = [[]]
    for t in toks:
        if t in TRENNER:
            ketten.append([])
        else:
            ketten[-1].append(t)
    return [k for k in ketten if k]


def _umleitungen(toks: list[str]) -> tuple[list[str], list[str]]:
    """(Worte ohne Umleitungen, Ziele der Schreib-Umleitungen).

    `2>&1` und `2>/dev/null` landen hier -- das erste als Dateideskriptor-
    Kopie (kein Ziel), das zweite mit Ziel `/dev/null` ausserhalb des Repos.
    """
    worte: list[str] = []
    ziele: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        nachfolger = toks[i + 1] if i + 1 < len(toks) else None
        if t.isdigit() and nachfolger in UMLEITUNG:
            i += 1  # der Dateideskriptor vor der Umleitung
            continue
        if t in SCHREIB_UMLEITUNG:
            if nachfolger is not None and not (
                t == ">&" and (nachfolger.isdigit() or nachfolger == "-")
            ):
                ziele.append(nachfolger)
            i += 2
            continue
        if t in LESE_UMLEITUNG:
            i += 2
            continue
        worte.append(t)
        i += 1
    return worte, ziele


def _zieltokens(args: list[str]) -> list[str]:
    """Aus Argumenten die, die ein Pfad sein koennen. `of=x`, `--file=x`
    zaehlen mit ihrem Wert, Schalter und blosse Zahlen nicht."""
    aus = []
    for a in args:
        if "=" in a:
            a = a.split("=", 1)[1]
        elif a.startswith("-"):
            continue
        if a and not a.isdigit():
            aus.append(a)
    return aus


def _ziele_der_verben(worte: list[str], tiefe: int) -> list[str]:
    """Jedes Wort, das GENAU ein schreibendes Verb ist, oeffnet die Zielsuche
    auf seinen Argumenten. Deshalb faellt `xargs rm x` mit auf -- und der Text
    einer Commit-Botschaft nicht, denn der ist ein einzelnes Wort."""
    ziele: list[str] = []
    for i, wort in enumerate(worte):
        args = worte[i + 1:]
        if wort in SHELLS and tiefe < 3:
            if "-c" in args:
                unterkommando = args[args.index("-c") + 1:]
                if unterkommando:
                    ziele += _schreibziele(unterkommando[0], tiefe + 1)
        elif wort == "eval" and tiefe < 3:
            if args:
                ziele += _schreibziele(args[0], tiefe + 1)
        elif wort == "git":
            unterbefehle = [a for a in args if not a.startswith("-")]
            if unterbefehle and unterbefehle[0] in GIT_SCHREIBEND:
                ziele += _zieltokens(unterbefehle[1:])
        elif wort in INPLACE_VERBEN:
            if any(a.startswith("-i") or a == "--in-place" for a in args):
                ziele += _zieltokens(args)
        elif wort in SCHREIBVERBEN:
            kandidaten = _zieltokens(args)
            if wort in NUR_LETZTES:
                ziele += kandidaten[-1:]
            else:
                ziele += kandidaten
    return ziele


def _grobe_ziele(kommando: str) -> list[str]:
    """Rueckfall fuer unzerlegbaren Text (offenes Anfuehrungszeichen): der alte,
    grobe Weg. Lieber eine Rueckfrage zu viel als eine umgangene Grenze."""
    return [m.group(0) for m in PFADWORT.finditer(kommando)
            if "/" in m.group(0) or m.group(0).endswith((".sh", ".toml", ".py"))]


def _schreibziele(kommando: str, tiefe: int = 0) -> list[str]:
    # Rueckwaerts-Anfuehrung ist eine Kommandosubstitution: als eigenes
    # Kommando behandeln, sonst ginge `echo `rm x`` durch.
    text = _ohne_heredoc(kommando).replace("`", ";")
    try:
        toks = _lex(text)
    except ValueError:
        return _grobe_ziele(kommando)
    if "xargs" in toks and any(
        t in SCHREIBVERBEN or t in INPLACE_VERBEN for t in toks
    ):
        # Die Ziele von `xargs rm` stehen in der Standardeingabe und sind aus
        # dem Text nicht bestimmbar. Fuer diesen Fall der grobe Weg.
        return _grobe_ziele(kommando)
    ziele: list[str] = []
    for kette in _ketten(toks):
        worte, umgeleitet = _umleitungen(kette)
        ziele += umgeleitet
        ziele += _ziele_der_verben(worte, tiefe)
    if any("$" in z for z in ziele):
        # Ein Ziel hinter einer Variablen (`rm $Z`) steht im Text nicht.
        # Auch hier der grobe Weg statt einer Luecke.
        return _grobe_ziele(kommando)
    return ziele



# ---------------------------------------------------------------------------
# Ein Verifiziererlauf zur Zeit.
#
# Am 30.08. fuhren zwei Sitzungen gleichzeitig Verifizierer im selben Baum.
# `tests/verify/freeze.sh` haelt seither selbst eine Sperre -- die greift aber
# nur gegen ein zweites freeze.sh. Der Vorfall hatte eine andere Bauform: ein
# BLANKER `bash tests/verify/T-3.14.sh` neben einem laufenden Freeze. Die
# Verifizierer koennen die Sperre nicht selbst nehmen, sie sind eingefroren --
# eine Zeile in jedem von ihnen hiesse dreissig Neueinfrierungen.
#
# Diese Stelle sieht jedes Bash-Kommando eines Agenten, bevor es laeuft. Sie
# ist damit der einzige Ort, an dem sich die Luecke ohne das schliessen laesst.
#
# Warum das hier zaehlt: `tests/verify/meta.sh:62` wertet jeden Nicht-Null-Exit
# eines Fixture-Laufs als „Mutante erkannt". Fremdlast laesst einen Mutanten aus
# dem falschen Grund scheitern, und er wird als erkannt verbucht. Falsches
# GRUEN -- der Fehler zeigt in die harmlose Richtung und faellt keiner
# Auswertung auf, die nur nach „alle erkannt" schaut.
#
# FAIL OPEN, anders als die Rollenpruefung. Eine nicht lesbare Sperrdatei ist
# kein Grund, die Arbeit im Repo anzuhalten: der Schaden einer verpassten
# Ablehnung ist eine verschmutzte Messung, aergerlich und nachtraeglich
# erkennbar. Der Schaden einer falschen Ablehnung waere ein Repo, in dem
# niemand mehr etwas ausfuehren kann.
VERIFIZIERER = re.compile(
    r"^tests/verify/(?:T-[0-9][^/]*\.sh|t[0-9]+_pruefstand\.py|meta\.sh|freeze\.sh)$"
)
INTERPRETER = SHELLS | {"python", "python3", "env"}
SPERRDATEI = "daimon-verify.lock"


def _verifiziererpfad(wort: str) -> str | None:
    rel = relative(wort)
    return rel if rel and VERIFIZIERER.match(rel) else None


def _laufender_verifizierer(kommando: str) -> str | None:
    """Der Pfad, wenn dieses Kommando einen Verifizierer AUSFUEHRT.

    Eine Kette zaehlt, wenn ihr erstes Wort selbst der Verifizierer ist
    (`./tests/verify/T-3.14.sh`) oder wenn sie einen Interpreter enthaelt und
    danach einen Verifiziererpfad (`bash -x tests/verify/T-3.14.sh`,
    `timeout 60 bash tests/verify/freeze.sh T-0.8`).

    `cat tests/verify/T-3.14.sh` faellt damit NICHT auf -- Lesen ist kein Lauf.
    Und eine Commit-Botschaft, die einen Verifizierer nennt, ist nach `_lex`
    ein einziges Token und trifft das Muster nicht; dieselbe Bauform, die
    `test_eine_commit_botschaft_darf_einen_verifizierer_nennen` schon schuetzt.
    """
    try:
        toks = _lex(_ohne_heredoc(kommando))
    except ValueError:
        # Unzerlegbar. Hier durchlassen: fail open gilt fuer diese Pruefung,
        # anders als bei `_schreibziele`, wo der grobe Weg greift.
        return None
    for kette in _ketten(toks):
        worte, _ = _umleitungen(kette)
        if not worte:
            continue
        if pfad := _verifiziererpfad(worte[0]):
            return pfad
        interpreter_gesehen = False
        for wort in worte:
            if Path(wort).name in INTERPRETER:
                interpreter_gesehen = True
            elif interpreter_gesehen and (pfad := _verifiziererpfad(wort)):
                return pfad
    return None


def _sperre_gehalten() -> str | None:
    """Auskunft des Halters, oder None -- frei ODER unlesbar (fail open).

    Dieselbe Datei, die `tests/verify/freeze.sh` nimmt. Gelesen wird sie nur,
    wenn `flock` fehlschlaegt: dann haelt sie jemand, und dann steht dort, wer.
    """
    pfad = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / SPERRDATEI
    try:
        with pfad.open("a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.seek(0)
                return fh.read().strip() or "(der Halter nennt sich nicht)"
            fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        pass
    return None

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
        kommando = inp.get("command", "")
        if lauf := _laufender_verifizierer(kommando):
            if halter := _sperre_gehalten():
                deny(
                    f"role_guard: {lauf} jetzt nicht starten -- es laeuft schon "
                    f"ein Verifiziererlauf: {halter}. Zwei Laeufe messen "
                    f"einander mit, und meta.sh verbucht einen dadurch "
                    f"gescheiterten Mutanten als ERKANNT. Warte, bis er durch "
                    f"ist."
                )
        targets = _schreibziele(kommando)
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
