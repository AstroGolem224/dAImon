#!/usr/bin/env python3
"""Sagt mit dem EXIT-CODE, ob gerade ein Verifiziererlauf im Baum arbeitet.

    tools/baum_frei.py && systemctl --user restart daimon-face.service

Warum es das gibt
----------------------------------------------------------------------------
Am 31.08. und 02.09. wurde jeweils ein Dienst neu gestartet, waehrend eine
fremde Messung lief. Beide Male hatte ich vorher „geprueft" -- mit einem
`pgrep`, dessen Ergebnis in einem `echo "(nichts = frei)"` endete. Der Text
stand da, ob der `pgrep` nun etwas fand oder nicht. Eine Pruefung, deren
Ausgang den weiteren Ablauf nicht aendert, ist keine Pruefung, sondern
Verzierung.

Deshalb hier ein Exit-Code und kein Text: `&&` entscheidet, nicht das Auge.

Zwei Fallen, beide teuer bezahlt
----------------------------------------------------------------------------
1. **Selbsttreffer.** `pgrep -f "tests/verify"` findet die eigene Shell, denn
   das Muster steht in ihrer Kommandozeile. Zweimal hat ein `kill` auf so
   einen Treffer die eigene Sitzung erschossen. Darum wird hier der eigene
   Prozess samt aller Vorfahren ausgenommen -- nicht per `grep -v zsh`,
   sondern ueber die echte Elternkette.
2. **Lesen ist kein Lauf.** `cat tests/verify/T-3.14.sh` darf nicht bremsen.
   Es zaehlt nur, was einen Interpreter VOR dem Pfad hat. Dieselbe Regel wie
   in `tests/verify/freeze.sh` und `.claude/hooks/role_guard.py`; hier steht
   sie ein drittes Mal, weil ein Shell-Skript, ein Python-Hook und dieses
   Werkzeug keinen gemeinsamen Ort haben, den alle drei laden koennten.
"""
import argparse
import os
import re
import sys
from pathlib import Path, PurePath

# Ein Verifiziererpfad, als GANZES Argument. `/proc/<pid>/cmdline` trennt die
# Argumente mit Null-Bytes; wer sie zu einem String zusammenklebt, verliert
# genau die Grenze, an der eine Commit-Botschaft aufhoert und ein Pfad
# anfaengt. Deshalb wird hier je Argument geprueft und nicht im Fliesstext.
PFAD = re.compile(
    r"(?:^|/)tests/verify/"
    r"(?:T-[0-9][^/]*\.sh|t[0-9]+[a-z]?_pruefstand\.py|meta\.sh|freeze\.sh)$"
)
INTERPRETER = {"bash", "sh", "zsh", "dash", "python", "python3", "env"}
SPERRDATEI = "daimon-verify.lock"


def eigene_kette(pid: int = None) -> set:
    """Der eigene Prozess und alle Vorfahren.

    Ohne das meldet das Werkzeug sich selbst als laufenden Verifizierer,
    sobald es aus einer Shell gestartet wird, deren Kommandozeile den Pfad
    enthaelt -- und das ist bei `tools/baum_frei.py && ...` immer der Fall.
    """
    kette, p = set(), os.getpid() if pid is None else pid
    while p and p > 1 and p not in kette:
        kette.add(p)
        try:
            felder = Path(f"/proc/{p}/stat").read_text().rsplit(")", 1)[1].split()
            p = int(felder[1])
        except (OSError, IndexError, ValueError):
            break
    return kette


def prozesse_lesen() -> list:
    """`[(pid, [argv...]), ...]` aus /proc -- als LISTE, nicht als String.

    Nicht `pgrep`: das gibt weder die Argumentgrenzen noch die eigene
    Elternkette her, und beide braucht dieses Werkzeug.
    """
    aus = []
    for eintrag in Path("/proc").iterdir():
        if not eintrag.name.isdigit():
            continue
        try:
            roh = (eintrag / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [t.decode("utf-8", "replace") for t in roh.split(b"\0") if t]
        if argv:
            aus.append((int(eintrag.name), argv))
    return aus


def ist_lauf(argv: list) -> bool:
    """Fuehrt dieses argv einen Verifizierer AUS?

    Zwei Formen zaehlen: der Verifizierer als erstes Argument
    (`./tests/verify/T-3.14.sh`), oder ein Interpreter irgendwo davor
    (`bash -x ...`, `timeout 60 bash ...`, `/repo/.venv/bin/python -B ...`).

    Was NICHT zaehlt: der Pfad als Teil eines laengeren Arguments. Eine
    Commit-Botschaft ist EIN Argument, und `PFAD` ist an das Ende verankert
    -- damit faellt sie strukturell heraus und nicht ueber eine Ausnahme.
    """
    if not argv:
        return False
    if PFAD.search(argv[0]):
        return True
    gesehen = False
    for wort in argv:
        if PurePath(wort).name in INTERPRETER:
            gesehen = True
        elif gesehen and PFAD.search(wort):
            return True
    return False


def laeufe_finden(prozesse: list, ausnehmen: set) -> list:
    """Die reine Regel, getrennt von /proc -- nur so ist sie pruefbar.

    Ein Werkzeug, das seine Entscheidung erst im Betrieb faellt, koennte
    seinen eigenen Fehler nie zeigen.
    """
    return [(pid, argv) for pid, argv in prozesse
            if pid not in ausnehmen and ist_lauf(argv)]


def sperre_gehalten() -> str:
    """Auskunft des Halters, oder `""`. Dieselbe Datei, die `freeze.sh` nimmt.

    Fehlt sie oder ist sie unlesbar, gilt das als frei -- wie im Hook: eine
    kaputte Sperrdatei soll die Arbeit nicht anhalten.
    """
    import fcntl

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
    return ""


def demo() -> None:
    ich = os.getpid()
    # Positivkontrolle zuerst: erkennt die Regel einen Lauf ueberhaupt? Ohne
    # sie waere jeder „frei"-Fall unten gruen, weil nichts je trifft.
    echte = [
        (11, ["bash", "tests/verify/T-3.14.sh"]),
        (12, ["bash", "-x", "tests/verify/T-3.13b.sh"]),
        (13, ["timeout", "5400", "bash", "tests/verify/freeze.sh", "T-3.14"]),
        (14, ["/repo/.venv/bin/python", "-B", "-P",
              "/repo/tests/verify/t313b_pruefstand.py"]),
        (15, ["bash", "/mnt/data/AI/repos/dAImon/tests/verify/meta.sh", "T-3.9"]),
        (16, ["./tests/verify/T-9.2.sh"]),
    ]
    for pid, argv in echte:
        assert laeufe_finden([(pid, argv)], set()), f"nicht erkannt: {argv}"

    # Und was KEIN Lauf ist. Ein Waechter, der hier anschlaegt, blockiert den
    # Alltag und wird abgeschaltet -- dann schuetzt er gar nichts mehr.
    harmlos = [
        ["cat", "tests/verify/T-3.14.sh"],
        ["grep", "-n", "foo", "tests/verify/meta.sh"],
        # Der Pfad steckt IN einem Argument, nicht als Argument. Genau daran
        # ist die erste Fassung dieses Werkzeugs gescheitert.
        ["git", "commit", "-m", "bash tests/verify/T-3.14.sh lief gruen"],
        ["python3", "-m", "pytest", "tests/test_rollen.py"],
        ["vim", "tests/verify/freeze.sh"],
        ["ls", "-la"],
        ["bash", "tests/verify/FROZEN"],
        [],
    ]
    for argv in harmlos:
        assert not laeufe_finden([(99, argv)], set()), f"Falschalarm: {argv}"

    # Der Selbsttreffer: dasselbe argv, einmal fremd und einmal eigen.
    selbst = [(ich, ["bash", "tests/verify/T-3.14.sh"])]
    assert laeufe_finden(selbst, set()), "die Regel trifft das argv"
    assert not laeufe_finden(selbst, {ich}), "der eigene Prozess muss raus"

    # Die Elternkette enthaelt mindestens den eigenen Prozess, und der Aufbau
    # bricht nicht ab -- ein `while` ueber /proc/stat ist die Stelle, an der
    # eine Endlosschleife ohne Test unbemerkt bliebe.
    kette = eigene_kette()
    assert ich in kette and len(kette) >= 1, kette
    assert eigene_kette(1) <= {1}, "PID 1 hat keine Vorfahren"

    # Und der Weg vom echten /proc bis zur Regel: laeuft er ueberhaupt durch?
    # Ohne diesen Schritt pruefen alle Faelle oben nur erfundene Listen.
    echt = prozesse_lesen()
    assert echt and all(isinstance(a, list) and a for _, a in echt), "argv leer"
    assert any(PurePath(a[0]).name for _, a in echt)

    print("Selbsttest ok.")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--still", action="store_true",
                   help="nichts ausgeben, nur der Exit-Code zaehlt")
    p.add_argument("--selbsttest", action="store_true")
    args = p.parse_args()
    if args.selbsttest:
        demo()
        return 0

    gefunden = laeufe_finden(prozesse_lesen(), eigene_kette())
    halter = sperre_gehalten()
    if not gefunden and not halter:
        if not args.still:
            print("Baum frei: kein Verifiziererlauf, Sperre frei.")
        return 0
    if not args.still:
        if gefunden:
            print("BELEGT -- Verifiziererlauf:", file=sys.stderr)
            for pid, zeile in gefunden:
                print(f"  {pid} {zeile[:100]}", file=sys.stderr)
        if halter:
            print(f"BELEGT -- Lauf-Sperre gehalten: {halter}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
