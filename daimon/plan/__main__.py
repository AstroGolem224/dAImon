"""T-8.3 -- die CLI des Zeitplaners.

    python -m daimon.plan --neu "Tee" --in "45m"
    python -m daimon.plan --neu "Zahnarzt" --am "2026-08-24 08:00"
    python -m daimon.plan --liste [--status offen]
    python -m daimon.plan --loeschen 3
    python -m daimon.plan --fokus 45
    python -m daimon.plan --fokus-stop
    python -m daimon.plan --status

Die CLI spricht mit dem laufenden Dienst ueber `plan-anfrage.sock` -- nicht
mit der Datenbank. Zwei Schreiber auf `plan.db` waeren genau die Grenze, die
der Dienst zieht. Die CLI ist ein dummer Zeiger: sie baut die Anfrage und
druckt die Antwort.

Die Marke ist `user_ptt`: wer diese CLI aufruft, IST der Nutzer an der
Tastatur -- dieselbe Vertrauensstufe wie der Taster. Sie entscheidet nur
darueber, ob der Titel spaeter gesprochen werden darf, und selbst dann geht
er durch `sprechtext.pruefe`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys

from daimon.common.config import load as load_config
from daimon.plan.daemon import CLI_UNIT, SCHREIBENDE_ARTEN

MAX_ZEILE = 1 << 20
SOCKET_NAME = "plan-anfrage.sock"

# Marker gegen die Endlosschleife: im zweiten Anlauf steht er in der Umgebung.
SCOPE_MARKER = "DAIMON_PLAN_CLI_SCOPE"


def anfrage(pfad: str, nutzlast: dict, *, timeout_s: float = 10.0) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout_s)
    try:
        c.connect(pfad)
        c.sendall(json.dumps(nutzlast, ensure_ascii=False).encode() + b"\n")
        roh = c.makefile("rb").readline(MAX_ZEILE)
    except OSError as exc:
        return {"v": 1, "ok": False, "grund": "dienst_weg", "meldung": str(exc)[:160]}
    finally:
        c.close()
    try:
        antwort = json.loads(roh)
    except (json.JSONDecodeError, ValueError):
        return {"v": 1, "ok": False, "grund": "dienst_weg",
                "meldung": "Antwort unlesbar"}
    return antwort if isinstance(antwort, dict) else {
        "v": 1, "ok": False, "grund": "dienst_weg", "meldung": "kein Objekt"}


def in_eigene_scope(argv: list[str]) -> None:
    """Sich selbst nach `daimon-plan-cli.scope` umhaengen -- und nur dann.

    Der Dienst laesst SCHREIBENDE Anfragen nur aus benannten Units zu
    (`daemon.SCHREIB_UNITS`). Eine Liste kann `app-<hash>.scope` nicht
    nennen, also nennt sich die CLI selbst: `systemd-run --scope` mit festem
    Namen, und danach steht der Name im Peer-Credential des Sockets.

    Kehrt bei Erfolg NIE zurueck (`execvp`). Gibt es kein `systemd-run` oder
    keinen Nutzerbus (Container, `su`, Notsitzung), laeuft die CLI in Ruhe
    weiter und bekommt vom Dienst ein sauberes `fremde_unit` -- eine Absage
    ist besser als ein stiller Umweg, und besser als eine dbus-Fehlermeldung
    an einer Stelle, an der niemand dbus erwartet.

    ponytail: ein fester Unit-Name, keine Eindeutigkeit. Decke: zwei
    GLEICHZEITIGE Schreibaufrufe -- der zweite scheitert an
    "unit already exists". Ausbaupfad, wenn das je vorkommt: Zufallssuffix
    plus `@`-Template in `SCHREIB_UNITS`.
    """
    bus = (os.environ.get("DBUS_SESSION_BUS_ADDRESS")
           or os.path.exists(os.path.join(
               os.environ.get("XDG_RUNTIME_DIR", ""), "bus")))
    if (os.environ.get(SCOPE_MARKER) or not bus
            or shutil.which("systemd-run") is None):
        return
    os.environ[SCOPE_MARKER] = "1"
    try:
        os.execvp("systemd-run",
                  ["systemd-run", "--user", "--quiet", "--collect", "--scope",
                   f"--unit={CLI_UNIT}", "--",
                   sys.executable, "-m", "daimon.plan", *argv])
    except OSError:
        os.environ.pop(SCOPE_MARKER, None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon Zeitplaner -- CLI (T-8.3)")
    ap.add_argument("--socket", default=None)
    ap.add_argument("--neu", metavar="TITEL", default=None)
    zeitpunkt = ap.add_mutually_exclusive_group()
    zeitpunkt.add_argument("--in", dest="rein", metavar="DAUER",
                           default=None, help='z.B. "45m", "2h", "in 45 minuten"')
    zeitpunkt.add_argument("--am", dest="am", metavar="WANN",
                           default=None, help='z.B. "morgen um 8", "2026-08-24 08:00"')
    ap.add_argument("--liste", action="store_true")
    ap.add_argument("--status", dest="filter_status", default=None)
    ap.add_argument("--loeschen", metavar="ID", type=int, default=None)
    ap.add_argument("--fokus", metavar="MINUTEN", type=float, default=None)
    ap.add_argument("--fokus-stop", action="store_true")
    ap.add_argument("--zustand", action="store_true", help="Dienst-Zaehler")
    roh_argv = list(sys.argv[1:] if argv is None else argv)
    args = ap.parse_args(argv)

    pfad = args.socket
    if pfad is None:
        pfad = str(load_config(make_dirs=False).runtime_dir / SOCKET_NAME)

    if args.neu is not None:
        wann = args.am or (f"in {args.rein}" if args.rein else None)
        if args.rein and " " not in args.rein.strip():
            # "45m" -> "in 45 minuten", "2h" -> "in 2 stunden"
            roh = args.rein.strip().lower()
            einheit = ("stunden" if roh.endswith(("h", "std")) else
                       "sekunden" if roh.endswith("s") else "minuten")
            zahl = roh.rstrip("abcdefghijklmnopqrstuvwxyz")
            wann = f"in {zahl} {einheit}"
        if not wann:
            print(json.dumps({"ok": False, "grund": "zeitpunkt_fehlt",
                              "meldung": "--in oder --am fehlt"}))
            return 2
        nutzlast = {"v": 1, "art": "neu", "titel": args.neu, "wann": wann,
                    "marke": "user_ptt"}
    elif args.liste:
        nutzlast = {"v": 1, "art": "liste",
                    "status": args.filter_status}
    elif args.loeschen is not None:
        nutzlast = {"v": 1, "art": "loeschen", "id": args.loeschen}
    elif args.fokus is not None:
        nutzlast = {"v": 1, "art": "fokus_start", "minuten": args.fokus}
    elif args.fokus_stop:
        nutzlast = {"v": 1, "art": "fokus_stop"}
    elif args.zustand:
        nutzlast = {"v": 1, "art": "status"}
    else:
        ap.print_help(sys.stderr)
        return 2

    if nutzlast["art"] in SCHREIBENDE_ARTEN:
        in_eigene_scope(roh_argv)   # kehrt im Erfolgsfall nicht zurueck

    antwort = anfrage(pfad, nutzlast)
    print(json.dumps(antwort, ensure_ascii=False, indent=2))
    return 0 if antwort.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
