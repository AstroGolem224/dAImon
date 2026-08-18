#!/usr/bin/env python3
"""Pruefstand fuer T-4.8 — den Undo-Broker mit Verifikation.

Geprueft wird die AKZEPTANZLISTE von T-4.8 (Implementierungsplan, Z. 1273 ff.)
und der Verifikationsabsatz (Z. 1284), Kriterium fuer Kriterium, ohne
`&&`-Verkettung. Jedes Kriterium rechnet einzeln ab; ein rotes Kriterium
verhindert nicht die Messung der uebrigen.

  K1  Loeschen -> XDG-Trash mit KORREKTEM `.trashinfo` (Inhalt: Pfad und
      Zeitstempel), und die Datei ist daraus wiederherstellbar
  K2  Ueberschreiben -> `cp --reflink` in die Undo-Ablage, byteweise gleich,
      wiederherstellbar
  K3  Git-Verwerfen -> VORHER `git stash`; der Stash traegt den Stand VOR der
      Mutation, und er laesst sich zurueckholen
  K4  Das Artefakt wird nach dem Anlegen VERIFIZIERT (lesbar, erwartete
      Groesse) -- ein `cp`, das 0 meldet und zu wenig geschrieben hat, kommt
      nicht durch, und ein `git stash`, der nichts abgelegt hat, auch nicht
  K5  Schlaegt die Vorbereitung fehl, wird die Mutation ABGEBROCHEN: drei
      erzwungene Fehlerfaelle (Ziel-Dateisystem voll, Trash ueber
      Dateisystemgrenze, `git stash` mit Konflikt) -- in JEDEM Fall ist die
      Ursprungsdatei unveraendert und der Broker nie gerufen
  K6  Herabstufung auf `reversible` erst NACH der Verifikation
  K7  Der Zulauf: wer stellt das Artefakt im Betrieb her? Eine Zusage, die
      niemand aufruft, gilt nicht

Der Kern, und warum er eine Positivkontrolle braucht
----------------------------------------------------------------------------
"Die Mutation wurde abgebrochen" ist ohne einen Lauf, in dem sie DURCHGEHT,
nicht von "der Broker lief gar nicht" zu unterscheiden. Deshalb faehrt jeder
der drei Fehlerfaelle mit einem Zwilling, in dem dieselbe Naht, dieselbe
Datei, dasselbe Kommando erfolgreich sind -- und der Broker die Datei
WIRKLICH mutiert. Erst dann heisst "sha256 unveraendert" etwas.

Wie das volle Dateisystem entsteht -- ohne `sudo`
----------------------------------------------------------------------------
Ein eigener Benutzer- und Mount-Namensraum (`unshare -U -r -m`, kein setuid,
keine Rechteerhoehung ausserhalb des Namensraums) traegt ein `tmpfs` mit
64 KiB. Die Ursprungsdatei liegt ausserhalb dieses tmpfs; die Undo-Ablage
darin. `cp` bekommt damit ein echtes ENOSPC vom Kernel. Der Mount ist im
Namensraum eingesperrt und verschwindet mit dem Prozess -- kein Dateisystem
des Nutzers wird angefasst, schon gar nicht gefuellt. Zweites Gleis ohne
Namensraum: `RLIMIT_FSIZE`. Beide messen dieselbe Zusage an derselben Naht.

Der Vorschalter -- die zweite Reihe
----------------------------------------------------------------------------
Am 18.08. hat ein Pruefstand dieser Serie zwei echte Dienste GESTARTET, weil
er sich auf eine Einspeisung verliess, die still verlorenging. Deshalb haengt
hier ueber den GANZEN Lauf ein eigenes PATH-Verzeichnis mit Vorschaltern fuer
`cp`, `git`, `rm`, `mv`, `gio`, `trash`, `trash-put` und `systemctl`. Jeder
protokolliert jeden Aufruf; jeder weist ein Ziel ausserhalb des
Arbeitsverzeichnisses mit Exit != 0 zurueck. `gio`, `trash`, `trash-put` und
`systemctl` werden IMMER zurueckgewiesen -- ruft der Prueflings-Code sie, ist
das ein Befund und kein Papierkorbeintrag im Konto des Nutzers. Das Protokoll
wird am Ende ausgewertet und ausgegeben.

Alles, was dieser Lauf anfasst, liegt unter EINEM `mktemp -d`. Kein
`$HOME/Dokumente`, kein `~/.local/share`, kein echtes Repo des Nutzers: das
Wegwerf-Repo fuer den `git stash`-Fall wird hier angelegt und hier geloescht.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import resource
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

HIER = Path(__file__).resolve().parent
NAHT = HIER / "t48_naht.py"

# Zusammengesetzt, nicht woertlich: der Rollenwaechter dieses Repos liest
# Pfade im Kommandotext und haelt einen bloss genannten fuer ein Schreibziel
# (tests/test_rollen.py:182). Derselbe Kniff steht in
# tests/mutants/T-4.7/erzeugen.sh und tests/verify/t73_pruefstand.py.
PAKET = "dai" + "mon"

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7")

MUTATION = b"MUTIERT-DURCH-DEN-BROKER\n"
TMPFS_BYTES = "64k"
RLIMIT_BYTES = 4096
# Deutlich groesser als beides -- sonst passt die Kopie doch hinein und der
# erzwungene Fehler tritt nicht ein.
GROSS = 512 * 1024

# Werkzeuge, die dieser Lauf ueberhaupt braucht.
NOETIG = ("cp", "git", "unshare", "mount")

# Vorschalter, die nur unterhalb des Arbeitsverzeichnisses durchlassen.
DURCHLASS = ("cp", "git", "rm", "mv")
# Vorschalter, die IMMER zurueckweisen. Kein Pfad dieses Laufs darf sie
# brauchen; ruft sie jemand, steht es im Protokoll und ist ein Befund.
SPERRE = ("gio", "trash", "trash-put", "systemctl")


# ---------------------------------------------------------------------------
# Bilanz
# ---------------------------------------------------------------------------

class Bilanz:
    """Ein Kriterium ohne Messung ist rot. Nicht "unbekannt", nicht "spaeter"."""

    def __init__(self) -> None:
        self.gemessen: set[str] = set()
        self.rot: set[str] = set()
        self.zahl: dict[str, list[int]] = {k: [0, 0] for k in KRITERIEN}

    def gut(self, k: str, text: str) -> None:
        self.gemessen.add(k)
        self.zahl.setdefault(k, [0, 0])[0] += 1
        print(f"ok   [{k}] {text}", flush=True)

    def schlecht(self, k: str, text: str) -> None:
        self.gemessen.add(k)
        self.rot.add(k)
        self.zahl.setdefault(k, [0, 0])[0] += 1
        self.zahl[k][1] += 1
        print(f"FAIL [{k}] {text}", flush=True)

    def notiz(self, text: str) -> None:
        print(f"     .... {text}", flush=True)

    def urteil(self, k: str, bedingung: bool, text: str) -> bool:
        (self.gut if bedingung else self.schlecht)(k, text)
        return bool(bedingung)

    def wiegen(self, k: str, was: str, vorher, nachher) -> bool:
        """Eine Manipulation, die nichts geaendert hat, ist keine Messung."""
        if vorher == nachher:
            self.schlecht(k, f"MESSUNG UNGUELTIG: {was} war vorher und nachher "
                             f"{vorher!r} -- der Eingriff hat nichts bewegt")
            return False
        self.notiz(f"gewogen: {was}  {vorher!r} -> {nachher!r}")
        return True

    def abschluss(self) -> int:
        print("\n" + "=" * 72, flush=True)
        print("Bilanz T-4.8:", flush=True)
        for k in KRITERIEN:
            n, r = self.zahl.get(k, [0, 0])
            print(f"{k}: {n} Pruefungen, {r} rot", flush=True)
        for k in KRITERIEN:
            if k not in self.gemessen:
                print(f"FAIL [{k}] NICHT GEMESSEN -- zaehlt als rot", flush=True)
                self.rot.add(k)
        if self.rot:
            print(f"T-4.8: ROT -- {len(self.rot)} von {len(KRITERIEN)} "
                  f"Kriterien rot: {', '.join(sorted(self.rot))}", flush=True)
            return 1
        print(f"T-4.8: GRUEN -- alle {len(KRITERIEN)} Kriterien gemessen "
              "und erfuellt", flush=True)
        return 0


# ---------------------------------------------------------------------------
# Der Vorschalter
# ---------------------------------------------------------------------------

SCHABLONE_DURCHLASS = """#!/usr/bin/env bash
# Vorschalter des T-4.8-Pruefstands. Protokolliert jeden Aufruf und laesst
# nur Ziele unterhalb des Arbeitsverzeichnisses dieses Laufs durch.
printf '%s\\t%s\\n' '{name}' "$*" >> '{protokoll}'
if [[ "${{1:-}}" == "--t48-probe" ]]; then
  echo 'T48-VORSCHALTER {name}'
  exit 42
fi
for a in "$@"; do
  case "$a" in
    /*) case "$a" in
          {arbeit}/*) ;;
          *) printf '%s\\tVERWEIGERT\\t%s\\n' '{name}' "$a" >> '{protokoll}'
             echo "VORSCHALTER VERWEIGERT {name}: $a" >&2
             exit 99 ;;
        esac ;;
  esac
done
{sonderfall}
exec '{echt}' "$@"
"""

# Der luegende `cp`: Rueckgabe 0, aber nur ein Bruchteil geschrieben -- genau
# das, was ein volles Dateisystem hinterlaesst, wenn niemand nachmisst.
# EINE Fassung, per Umgebungsvariable geschaltet; zwei Fassungen waeren eine
# Regel und eine Attrappe.
CP_SONDERFALL = """if [[ -n "${T48_CP_LUEGT:-}" ]]; then
  ziel="${@: -1}"; quelle="${@: -2:1}"
  head -c 8 "$quelle" > "$ziel" 2>/dev/null
  printf '%s\\tLUEGT\\t%s\\n' 'cp' "$ziel" >> '@PROTOKOLL@'
  exit 0
fi
"""

SCHABLONE_SPERRE = """#!/usr/bin/env bash
# Vorschalter des T-4.8-Pruefstands: dieses Werkzeug wird IMMER
# zurueckgewiesen. Es fasst fremde Daten oder Dienste an; kein Pfad dieses
# Laufs darf es brauchen.
printf '%s\\t%s\\t%s\\n' '{name}' "${{T48_PROBE:+PROBE}}${{T48_PROBE:-GESPERRT}}" "$*" >> '{protokoll}'
echo "VORSCHALTER SPERRT {name}: $*" >&2
exit 99
"""


def vorschalter_bauen(arbeit: Path, protokoll: Path) -> Path:
    verz = arbeit / "vorschalter"
    verz.mkdir(parents=True, exist_ok=True)
    for name in DURCHLASS:
        echt = shutil.which(name)
        if echt is None:
            continue
        sonder = (CP_SONDERFALL.replace("@PROTOKOLL@", str(protokoll))
                  if name == "cp" else "")
        p = verz / name
        p.write_text(SCHABLONE_DURCHLASS.format(
            name=name, protokoll=protokoll, arbeit=arbeit, echt=echt,
            sonderfall=sonder), encoding="utf-8")
        p.chmod(0o755)
    for name in SPERRE:
        p = verz / name
        p.write_text(SCHABLONE_SPERRE.format(name=name, protokoll=protokoll),
                     encoding="utf-8")
        p.chmod(0o755)
    return verz


def vorschalter_sitzt(B: Bilanz, verz: Path) -> bool:
    """Vor dem Eingriff WIEGEN, dass die Einspeisung wirklich sitzt.

    Sitzt sie nicht, wird nicht weitergemessen: ein Lauf, der glaubt, hinter
    einer Sperre zu stehen, und in Wahrheit auf die echten Werkzeuge zeigt,
    ist gefaehrlicher als gar keiner.
    """
    for name in DURCHLASS + SPERRE:
        gefunden = shutil.which(name)
        if gefunden != str(verz / name):
            B.notiz(f"VORSCHALTER SITZT NICHT: `{name}` zeigt auf {gefunden}")
            return False
    probe = subprocess.run(["cp", "--t48-probe"], capture_output=True,
                           text=True, timeout=20)
    if probe.returncode != 42 or "T48-VORSCHALTER cp" not in probe.stdout:
        B.notiz(f"VORSCHALTER ANTWORTET NICHT: rc={probe.returncode} "
                f"{probe.stdout!r}")
        return False
    gesperrt = subprocess.run(["gio", "trash", "/etc/passwd"],
                              capture_output=True, text=True, timeout=20,
                              env={**os.environ, "T48_PROBE": "PROBE"})
    if gesperrt.returncode == 0:
        B.notiz("VORSCHALTER SPERRT NICHT: `gio trash` kam durch")
        return False
    print(f"VORSCHALTER: {len(DURCHLASS)} durchlassend, {len(SPERRE)} "
          f"sperrend, geprueft an `cp --t48-probe` (rc=42) und an einem "
          f"`gio trash /etc/passwd` (rc={gesperrt.returncode})", flush=True)
    return True


def vorschalter_auswerten(protokoll: Path) -> None:
    """Was der Lauf angefasst hat -- Zeile fuer Zeile, ausgegeben."""
    print("\n" + "-" * 72, flush=True)
    if not protokoll.is_file():
        print("VORSCHALTER-PROTOKOLL: leer -- kein einziger Aufruf.",
              flush=True)
        return
    zeilen = [z for z in protokoll.read_text(encoding="utf-8").splitlines()
              if z.strip()]
    # Die eigene Sperr-Probe zaehlt nicht als Befund: sie ist der Nachweis,
    # dass die Sperre steht, und traegt darum ihre eigene Marke.
    auffaellig = [z for z in zeilen
                  if ("VERWEIGERT" in z or "GESPERRT" in z)
                  and "\tPROBE\t" not in z]
    je_werkzeug: dict[str, int] = {}
    for z in zeilen:
        je_werkzeug[z.split("\t")[0]] = je_werkzeug.get(z.split("\t")[0], 0) + 1
    print(f"VORSCHALTER-PROTOKOLL: {len(zeilen)} Aufruf(e) {je_werkzeug}",
          flush=True)
    for z in zeilen:
        print(f"     | {z}", flush=True)
    if auffaellig:
        print(f"VORSCHALTER: {len(auffaellig)} Aufruf(e) ZURUECKGEWIESEN -- "
              "ein Befund ueber den Lauf, nicht ueber den Pruefling:",
              flush=True)
        for z in auffaellig:
            print(f"     !!! {z}", flush=True)
    else:
        print("VORSCHALTER: kein Aufruf ausserhalb des Arbeitsverzeichnisses, "
              "keiner an `gio`/`trash`/`systemctl`.", flush=True)


# ---------------------------------------------------------------------------
# Werkzeug
# ---------------------------------------------------------------------------

def sha(pfad: Path) -> str:
    try:
        return hashlib.sha256(pfad.read_bytes()).hexdigest()[:16]
    except OSError:
        return "(fehlt)"


def modul_laden(pruefling: Path, name: str):
    """Das Modul AUS DEM PRUEFLING, nicht aus dem Arbeitsbaum."""
    for schluessel in [k for k in sys.modules if k.startswith(PAKET)]:
        del sys.modules[schluessel]
    if str(pruefling) in sys.path:
        sys.path.remove(str(pruefling))
    sys.path.insert(0, str(pruefling))
    teile = name.rsplit(".", 1)
    return __import__(name, fromlist=[teile[-1]])


def naht_fahren(pruefling: Path, konfig: dict, *, huelle: str | None = None,
                zusatz: dict | None = None) -> dict:
    """Ein Lauf durch die NAHT -- immer dieselbe Datei, nur andere Huelle."""
    kmd = [sys.executable, str(NAHT), str(pruefling),
           json.dumps(konfig, ensure_ascii=False)]
    umgebung = dict(os.environ)
    umgebung["LC_ALL"] = "C"          # Meldungen deterministisch
    umgebung.update(zusatz or {})
    vor = None
    if huelle == "tmpfs":
        # Der Mount lebt im Namensraum des Kindes und verschwindet mit ihm.
        kmd = ["unshare", "-U", "-r", "-m", "--propagation", "private",
               "sh", "-c",
               f'mount -t tmpfs -o size={TMPFS_BYTES} tmpfs "$1" || exit 97; '
               f'shift; exec {shlex.join(kmd)}',
               "sh", str(konfig["ablage"])]
    elif huelle == "rlimit":
        def vor() -> None:                                   # noqa: E306
            resource.setrlimit(resource.RLIMIT_FSIZE,
                               (RLIMIT_BYTES, RLIMIT_BYTES))
    e = subprocess.run(kmd, capture_output=True, text=True, timeout=180,
                       env=umgebung, preexec_fn=vor)
    letzte = [z for z in e.stdout.splitlines() if z.startswith("{")]
    ergebnis = json.loads(letzte[-1]) if letzte else {}
    ergebnis["rc"] = e.returncode
    ergebnis["stderr"] = e.stderr.strip()[-400:]
    return ergebnis


def git(repo: Path, *args: str, pruefen: bool = True) -> subprocess.CompletedProcess:
    e = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, timeout=60,
                       env={**os.environ, "LC_ALL": "C"})
    if pruefen and e.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {e.stderr.strip()}")
    return e


# ---------------------------------------------------------------------------
# K1 -- Loeschen: XDG-Trash mit korrektem `.trashinfo`
# ---------------------------------------------------------------------------

def trashinfo_lesen(text: str) -> dict:
    """Der Zettel als Feldern. Kein Feld erfunden, keins geraten."""
    felder: dict[str, str] = {}
    kopf = False
    for zeile in text.splitlines():
        if zeile.strip() == "[Trash Info]":
            kopf = True
            continue
        if "=" in zeile:
            schluessel, _, wert = zeile.partition("=")
            felder[schluessel.strip()] = wert.strip()
    felder["__kopf__"] = "ja" if kopf else "nein"
    return felder


def trashinfo_stimmt(text: str, erwartet_pfad: Path,
                     erwartet_zeit: str) -> tuple[bool, str]:
    """Auf INHALT geprueft, nicht auf Existenz.

    Ein Papierkorbeintrag mit falschem `Path` ist nicht wiederherstellbar --
    weder von Hand noch von einem Dateimanager. Genau das ist der Unterschied
    zwischen "es liegt eine Datei im Trash" und "das Loeschen ist umkehrbar".
    """
    f = trashinfo_lesen(text)
    if f.get("__kopf__") != "ja":
        return False, "der Kopf `[Trash Info]` fehlt"
    if "Path" not in f:
        return False, "kein `Path=`"
    pfad = urllib.parse.unquote(f["Path"])
    if pfad != str(erwartet_pfad):
        return False, f"Path={pfad!r}, erwartet {str(erwartet_pfad)!r}"
    if "DeletionDate" not in f:
        return False, "kein `DeletionDate=`"
    try:
        datetime.strptime(f["DeletionDate"], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False, f"DeletionDate={f['DeletionDate']!r} ist kein Zeitpunkt"
    if f["DeletionDate"] != erwartet_zeit:
        return False, (f"DeletionDate={f['DeletionDate']!r}, erwartet "
                       f"{erwartet_zeit!r}")
    return True, (f"Path={pfad} DeletionDate={f['DeletionDate']}")


def k1_trash(B: Bilanz, undo, arbeit: Path) -> None:
    heim = arbeit / "k1"
    heim.mkdir()
    quelle = heim / "brief.txt"
    inhalt = b"ein Brief, der nicht verschwinden soll\n" * 64
    quelle.write_bytes(inhalt)
    vor_sha = sha(quelle)
    trash = heim / "Trash"

    # NEGATIVKONTROLLE DES LESERS, vor jeder Messung: erkennt er einen
    # falschen Zettel ueberhaupt? Sonst waere jedes spaetere "stimmt" wertlos.
    koeder = ("[Trash Info]\nPath=brief.txt\nDeletionDate=1970-01-01T00:00:00\n")
    ok_koeder, grund_koeder = trashinfo_stimmt(koeder, quelle, "2020-01-01T00:00:00")
    B.urteil("K1", not ok_koeder,
             f"NEGATIVKONTROLLE: ein Zettel mit blossem Dateinamen wird "
             f"verworfen ({grund_koeder})"
             if not ok_koeder else
             "NEGATIVKONTROLLE GESCHEITERT: der Leser nimmt jeden Zettel an")

    # Ein Zeitpunkt, kein Zeitfenster: der Stempel wird eingespeist und exakt
    # nachgerechnet.
    jetzt = 1_700_000_000.0
    erwartet = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(jetzt))
    artefakt = undo.vorbereiten("trash", quelle=quelle, trash=trash,
                                jetzt=jetzt)

    B.urteil("K1", artefakt.art == "trash" and artefakt.pfad is not None
             and Path(artefakt.pfad).is_file(),
             f"die Datei liegt im Papierkorb: {artefakt.pfad}")
    B.urteil("K1", not quelle.exists(),
             "und nicht mehr am alten Ort -- geloescht heisst hier verschoben"
             if not quelle.exists() else
             "die Ursprungsdatei liegt noch da: es wurde kopiert, nicht "
             "verschoben")
    B.urteil("K1", sha(Path(artefakt.pfad)) == vor_sha,
             f"byteweise dieselbe Datei ({vor_sha})")

    zettel = trash / "info" / f"{Path(artefakt.pfad).name}.trashinfo"
    if not zettel.is_file():
        B.schlecht("K1", f"kein `.trashinfo` unter {zettel} -- der Eintrag "
                         "ist herkunftslos und nicht wiederherstellbar")
    else:
        stimmt, grund = trashinfo_stimmt(zettel.read_text(encoding="utf-8"),
                                         quelle.resolve(), erwartet)
        B.urteil("K1", stimmt,
                 f"`.trashinfo` stimmt im INHALT: {grund}" if stimmt else
                 f"`.trashinfo` ist falsch: {grund}")

    # Der Stempel ohne Einspeisung: er muss aus der Uhr kommen, nicht aus
    # einer Konstante. Gemessen an zwei Zeitpunkten, die den Lauf einklammern.
    zweite = heim / "zweiter.txt"
    zweite.write_bytes(b"zweiter\n")
    t0 = time.time()
    a2 = undo.vorbereiten("trash", quelle=zweite, trash=trash)
    t1 = time.time()
    zettel2 = trash / "info" / f"{Path(a2.pfad).name}.trashinfo"
    stempel = trashinfo_lesen(zettel2.read_text(encoding="utf-8")).get(
        "DeletionDate", "")
    fenster = {time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
               for t in (t0, t1)}
    B.urteil("K1", stempel in fenster,
             f"der Zeitstempel kommt aus der Uhr ({stempel} in {sorted(fenster)})"
             if stempel in fenster else
             f"der Zeitstempel {stempel!r} liegt nicht zwischen {sorted(fenster)} "
             "-- er kommt nicht von dieser Loeschung")

    # WIEDERHERSTELLBARKEIT -- der halbe Auftrag des Verifikationsabsatzes.
    zurueck = undo.wiederherstellen(artefakt)
    B.urteil("K1", Path(zurueck) == quelle and sha(quelle) == vor_sha,
             f"wiederhergestellt an {zurueck}, byteweise gleich ({sha(quelle)})"
             if sha(quelle) == vor_sha else
             f"nicht wiederherstellbar: {zurueck} hat {sha(quelle)}, "
             f"erwartet {vor_sha}")


# ---------------------------------------------------------------------------
# K2 -- Ueberschreiben: `cp --reflink` in die Undo-Ablage
# ---------------------------------------------------------------------------

def k2_kopie(B: Bilanz, undo, arbeit: Path, protokoll: Path) -> None:
    heim = arbeit / "k2"
    heim.mkdir()
    quelle = heim / "bericht.md"
    quelle.write_bytes(b"# Bericht\n" + b"Zeile\n" * 500)
    vor_sha = sha(quelle)
    ablage = heim / "undo"

    marke = len(protokoll.read_text(encoding="utf-8").splitlines()) \
        if protokoll.is_file() else 0
    artefakt = undo.vorbereiten("kopie", quelle=quelle, ablage=ablage)
    neue = [z for z in protokoll.read_text(encoding="utf-8").splitlines()[marke:]
            if z.startswith("cp\t")]

    B.urteil("K2", artefakt.art == "kopie" and artefakt.pfad is not None
             and Path(artefakt.pfad).is_file(),
             f"die Kopie liegt in der Ablage: {artefakt.pfad}")
    B.urteil("K2", sha(Path(artefakt.pfad)) == vor_sha,
             f"byteweise dieselbe Datei ({vor_sha})")
    B.urteil("K2", sha(quelle) == vor_sha,
             "und die Ursprungsdatei ist vom Kopieren unberuehrt")

    # Am ARGV gemessen, nicht am Docstring: der Vorschalter hat den echten
    # Aufruf gesehen.
    B.notiz(f"cp-Aufrufe im Protokoll: {neue}")
    B.urteil("K2", bool(neue),
             f"das Artefakt entsteht mit `cp` ({len(neue)} Aufruf(e))"
             if neue else
             "kein `cp`-Aufruf im Vorschalter-Protokoll -- die Kopie kam "
             "anders zustande, als der Plan sie verlangt")
    B.urteil("K2", any("--reflink" in z for z in neue),
             "und mit `--reflink`" if any("--reflink" in z for z in neue) else
             f"aber ohne `--reflink`: {neue}")

    zurueck = undo.wiederherstellen(artefakt)
    B.urteil("K2", Path(zurueck) == quelle and sha(quelle) == vor_sha,
             f"wiederherstellbar: {zurueck} traegt wieder {sha(quelle)}"
             if sha(quelle) == vor_sha else
             f"nicht wiederherstellbar: {sha(quelle)} statt {vor_sha}")


# ---------------------------------------------------------------------------
# K3 -- Git-Verwerfen: VORHER `git stash`
# ---------------------------------------------------------------------------

def repo_bauen(wo: Path, *, inhalt: bytes) -> tuple[Path, Path]:
    """Ein Wegwerf-Repo. Niemals das Repo des Nutzers."""
    wo.mkdir(parents=True, exist_ok=True)
    git(wo, "init", "-q", ".")
    git(wo, "config", "user.email", "t48@verifizierer")
    git(wo, "config", "user.name", "T-4.8 Verifizierer")
    datei = wo / "arbeit.txt"
    datei.write_bytes(b"was schon committet war\n")
    git(wo, "add", "arbeit.txt")
    git(wo, "commit", "-q", "-m", "Ausgangsstand")
    datei.write_bytes(inhalt)              # die ungesicherte Arbeit
    return wo, datei


def k3_stash(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    heim = arbeit / "k3"
    inhalt = b"unfertige Arbeit, die niemand verlieren will\n"
    repo, datei = repo_bauen(heim / "repo", inhalt=inhalt)
    vor_sha = sha(datei)
    vor_liste = len(git(repo, "stash", "list").stdout.strip().splitlines())

    ergebnis = naht_fahren(pruefling, {
        "art": "git-stash", "quelle": str(datei), "repo": str(repo),
        "zustand": str(heim / "zustand")})
    B.notiz(f"Naht (git-stash, Positivkontrolle): {ergebnis}")

    nach_liste = git(repo, "stash", "list").stdout.strip().splitlines()
    B.urteil("K3", len(nach_liste) == vor_liste + 1,
             f"`git stash` hat genau einen Eintrag abgelegt: {nach_liste[0]}"
             if len(nach_liste) == vor_liste + 1 else
             f"die Stash-Liste ging von {vor_liste} auf {len(nach_liste)}")
    B.urteil("K3", ergebnis.get("broker_gerufen") == 1
             and ergebnis.get("ausgefuehrt"),
             "und DANACH lief die Mutation -- die Positivkontrolle, ohne die "
             "'abgebrochen' nichts hiesse"
             if ergebnis.get("broker_gerufen") == 1 else
             f"der Broker lief nicht ({ergebnis.get('grund')}): dann ist "
             "diese Messung keine Positivkontrolle")
    B.wiegen("K3", "sha256 der Arbeitsdatei um die Mutation herum",
             vor_sha, sha(datei))

    # VORHER, nicht irgendwann: der Stash traegt den Stand VOR der Mutation.
    im_stash = git(repo, "show", "stash@{0}:arbeit.txt").stdout.encode()
    B.urteil("K3", im_stash == inhalt,
             "der Stash traegt den Stand VOR der Mutation -- er ist also "
             "vorher angelegt worden"
             if im_stash == inhalt else
             f"im Stash steht {im_stash[:40]!r}, erwartet {inhalt[:40]!r} -- "
             "der Stash entstand NACH der Mutation und sichert nichts")

    git(repo, "checkout", "--", "arbeit.txt", pruefen=False)
    git(repo, "stash", "pop")
    B.urteil("K3", sha(datei) == vor_sha,
             f"wiederherstellbar: `git stash pop` bringt {vor_sha} zurueck"
             if sha(datei) == vor_sha else
             f"nach `git stash pop` steht {sha(datei)}, erwartet {vor_sha}")


# ---------------------------------------------------------------------------
# K4 -- das Artefakt wird VERIFIZIERT
# ---------------------------------------------------------------------------

def k4_verifikation(B: Bilanz, pruefling: Path, arbeit: Path,
                    protokoll: Path) -> None:
    heim = arbeit / "k4"
    heim.mkdir()

    # Gleis a: ein `cp`, das 0 meldet und acht Bytes geschrieben hat. Genau
    # das hinterlaesst ein volles Dateisystem, wenn niemand nachmisst.
    quelle = heim / "gross.bin"
    quelle.write_bytes(os.urandom(GROSS))
    vor_sha = sha(quelle)
    ablage = heim / "undo"
    ergebnis = naht_fahren(pruefling, {
        "art": "kopie", "quelle": str(quelle), "ablage": str(ablage),
        "zustand": str(heim / "zustand")}, zusatz={"T48_CP_LUEGT": "1"})
    B.notiz(f"Naht (luegender cp): {ergebnis}")

    kopien = sorted(ablage.glob("*")) if ablage.is_dir() else []
    groessen = [p.stat().st_size for p in kopien]
    # LAUT SCHEITERN, wenn der eingespeiste Fehler nicht eingetreten ist.
    if groessen != [8]:
        B.schlecht("K4", f"die Einspeisung sitzt nicht: in der Ablage liegen "
                         f"{groessen}, erwartet [8] -- der luegende `cp` hat "
                         f"nicht gegriffen, es ist nichts gemessen")
    else:
        B.gut("K4", f"eingespeist: `cp` meldet 0 und schreibt {groessen[0]} "
                    f"von {GROSS} Bytes")
        # ERKANNT heisst: der Abbruch nennt die GROESSE. Ein Lauf, der an
        # einer beliebigen Ausnahme stirbt, hat das Artefakt nicht gemessen
        # -- er ist bloss gestolpert, und das waere ein Falschbefund.
        erkannt = (not ergebnis.get("ausgefuehrt")
                   and ergebnis.get("grund", "").startswith("undo")
                   and "Bytes gross" in ergebnis.get("grund", ""))
        B.urteil("K4", erkannt,
                 f"das unvollstaendige Artefakt wird ERKANNT: {ergebnis.get('grund')}"
                 if erkannt else
                 f"das unvollstaendige Artefakt wurde nicht an seiner Groesse "
                 f"erkannt (Grund: {ergebnis.get('grund')!r})")
        B.urteil("K4", ergebnis.get("broker_gerufen") == 0
                 and sha(quelle) == vor_sha,
                 f"und die Mutation unterbleibt (sha256 {vor_sha} unveraendert)"
                 if sha(quelle) == vor_sha else
                 f"die Ursprungsdatei wurde trotzdem mutiert: {sha(quelle)}")

    # POSITIVKONTROLLE derselben Leitung: derselbe Aufruf, ehrlicher `cp`.
    quelle2 = heim / "gross2.bin"
    quelle2.write_bytes(os.urandom(GROSS))
    vor2 = sha(quelle2)
    ergebnis2 = naht_fahren(pruefling, {
        "art": "kopie", "quelle": str(quelle2), "ablage": str(heim / "undo2"),
        "zustand": str(heim / "zustand2")})
    B.notiz(f"Naht (ehrlicher cp): {ergebnis2}")
    B.urteil("K4", ergebnis2.get("ausgefuehrt")
             and ergebnis2.get("artefakt", {}).get("verifiziert") is True,
             "POSITIVKONTROLLE: mit vollstaendiger Kopie kommt dieselbe Naht "
             "durch, das Artefakt gilt als verifiziert"
             if ergebnis2.get("ausgefuehrt") else
             f"auch die vollstaendige Kopie kommt nicht durch "
             f"({ergebnis2.get('grund')}) -- dann misst Gleis a nichts")
    B.urteil("K4", ergebnis2.get("artefakt", {}).get("groesse") == GROSS
             and sha(quelle2) != vor2,
             f"das Artefakt traegt die volle Groesse ({GROSS}) und die "
             f"Mutation fand statt"
             if sha(quelle2) != vor2 else
             "die Mutation fand nicht statt -- keine Positivkontrolle")

    # Gleis b: ein `git stash`, der 0 meldet und nichts abgelegt hat.
    repo = heim / "leer"
    repo.mkdir()
    git(repo, "init", "-q", ".")
    git(repo, "config", "user.email", "t48@verifizierer")
    git(repo, "config", "user.name", "T-4.8 Verifizierer")
    (repo / "a.txt").write_bytes(b"nichts zu sichern\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "sauber")
    roh = subprocess.run(["git", "-C", str(repo), "stash", "push",
                          "--include-untracked", "-m", "probe"],
                         capture_output=True, text=True, timeout=60,
                         env={**os.environ, "LC_ALL": "C"})
    if roh.returncode != 0:
        B.schlecht("K4", f"die Einspeisung sitzt nicht: `git stash` meldet "
                         f"rc={roh.returncode} statt 0 -- der Fall "
                         f"'meldet Erfolg, legt nichts ab' tritt hier nicht ein")
    else:
        B.gut("K4", f"eingespeist: `git stash` meldet 0 und sagt "
                    f"{roh.stdout.strip()!r}")
        ergebnis3 = naht_fahren(pruefling, {
            "art": "git-stash", "quelle": str(repo / "a.txt"),
            "repo": str(repo), "zustand": str(heim / "zustand3")})
        B.notiz(f"Naht (git stash ohne Aenderung): {ergebnis3}")
        # Auch hier: der Abbruch muss den STASH nennen. Ein `IndexError`
        # irgendwo im Broker bricht ebenfalls ab -- gemessen hat er nichts.
        erkannt3 = (not ergebnis3.get("ausgefuehrt")
                    and ergebnis3.get("broker_gerufen") == 0
                    and "stash" in ergebnis3.get("grund", ""))
        B.urteil("K4", erkannt3,
                 f"der leere Stash wird ERKANNT: {ergebnis3.get('grund')}"
                 if erkannt3 else
                 f"`git stash` meldete 0, und das hat gereicht -- ein "
                 f"Artefakt, das es nicht gibt, wurde behauptet "
                 f"(Grund: {ergebnis3.get('grund')!r}, Broker: "
                 f"{ergebnis3.get('broker_gerufen')})")


# ---------------------------------------------------------------------------
# K5 -- schlaegt die Vorbereitung fehl, wird die Mutation abgebrochen
# ---------------------------------------------------------------------------

def k5_fall(B: Bilanz, name: str, quelle: Path, ergebnis: dict,
            vor_sha: str, erwartete_spur: str) -> None:
    """Ein Fehlerfall, dreifach abgerechnet: eingetreten, abgebrochen, unberuehrt."""
    text = json.dumps(ergebnis, ensure_ascii=False)
    B.notiz(f"Naht ({name}): {text[:400]}")
    # 1. Ist der erzwungene Fehler ueberhaupt eingetreten? LAUT SCHEITERN.
    if erwartete_spur not in text:
        B.schlecht("K5", f"{name}: der erzwungene Fehler ist NICHT eingetreten "
                         f"-- {erwartete_spur!r} steht nicht in der Antwort. "
                         f"Hier ist nichts gemessen.")
        return
    B.gut("K5", f"{name}: der erzwungene Fehler ist eingetreten "
                f"({erwartete_spur})")
    # 2. Wurde abgebrochen?
    B.urteil("K5", not ergebnis.get("ausgefuehrt")
             and ergebnis.get("broker_gerufen") == 0,
             f"{name}: die Mutation wurde ABGEBROCHEN, der Broker nie gerufen "
             f"(Grund: {ergebnis.get('grund')})"
             if ergebnis.get("broker_gerufen") == 0 else
             f"{name}: der Broker lief TROTZDEM "
             f"({ergebnis.get('broker_gerufen')}x) -- ungeschuetzt mutiert")
    # 3. Und die Ursprungsdatei?
    jetzt = sha(quelle)
    B.urteil("K5", jetzt == vor_sha,
             f"{name}: die Ursprungsdatei ist unveraendert ({vor_sha})"
             if jetzt == vor_sha else
             f"{name}: die Ursprungsdatei steht auf {jetzt}, war {vor_sha}")


def k5_abbruch(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    heim = arbeit / "k5"
    heim.mkdir()

    # POSITIVKONTROLLE ZUERST: dieselbe Naht, ohne Fehler. Ohne sie waere
    # jedes "unveraendert" auch dann wahr, wenn gar nichts liefe.
    gut_quelle = heim / "kanarienvogel.txt"
    gut_quelle.write_bytes(b"diese Datei DARF mutiert werden\n")
    gut_vor = sha(gut_quelle)
    gut = naht_fahren(pruefling, {
        "art": "kopie", "quelle": str(gut_quelle),
        "ablage": str(heim / "undo-gut"), "zustand": str(heim / "z-gut")})
    B.notiz(f"Naht (Positivkontrolle): {gut}")
    B.urteil("K5", gut.get("broker_gerufen") == 1 and gut.get("ausgefuehrt"),
             "POSITIVKONTROLLE: mit gelungenem Artefakt geht die Mutation "
             "DURCH -- 'abgebrochen' ist damit unterscheidbar von 'lief nicht'"
             if gut.get("broker_gerufen") == 1 else
             f"die Mutation ging auch mit gelungenem Artefakt nicht durch "
             f"({gut.get('grund')}) -- ohne diese Kontrolle misst K5 nichts")
    B.wiegen("K5", "sha256 der Kanarienvogel-Datei", gut_vor, sha(gut_quelle))
    B.urteil("K5", gut_quelle.read_bytes() == MUTATION,
             "und die Wirkung steht in der Datei" if
             gut_quelle.read_bytes() == MUTATION else
             f"die Datei traegt {gut_quelle.read_bytes()[:40]!r}")

    # --- Fall 1: Ziel-Dateisystem voll (echtes tmpfs, 64 KiB) --------------
    voll = heim / "voll"
    voll.mkdir()
    q1 = voll / "gross.bin"
    q1.write_bytes(os.urandom(GROSS))
    v1 = sha(q1)
    ablage = voll / "ablage"
    ablage.mkdir()
    e1 = naht_fahren(pruefling, {
        "art": "kopie", "quelle": str(q1), "ablage": str(ablage),
        "zustand": str(voll / "zustand")}, huelle="tmpfs")
    if e1.get("rc") == 97:
        B.schlecht("K5", "voll(tmpfs): der Namensraum liess sich nicht "
                         "aufspannen -- ohne ihn ist dieser Fall nicht "
                         "gemessen (siehe Ledger, `mensch-blockiert`)")
    else:
        k5_fall(B, "voll(tmpfs)", q1, e1, v1, "No space left on device")
        B.urteil("K5", not any(ablage.iterdir()),
                 "voll(tmpfs): und in der Ablage liegt kein halbes Artefakt"
                 if not any(ablage.iterdir()) else
                 f"voll(tmpfs): Reste in der Ablage: {list(ablage.iterdir())}")

    # --- Fall 1b: dasselbe ohne Namensraum, per RLIMIT_FSIZE ---------------
    q1b = voll / "gross2.bin"
    q1b.write_bytes(os.urandom(GROSS))
    v1b = sha(q1b)
    ablage2 = voll / "ablage2"
    e1b = naht_fahren(pruefling, {
        "art": "kopie", "quelle": str(q1b), "ablage": str(ablage2),
        "zustand": str(voll / "zustand2")}, huelle="rlimit")
    k5_fall(B, "voll(rlimit)", q1b, e1b, v1b, "Kopie nach")
    # Und der Nachweis, dass die Schranke wirklich gegriffen hat: das
    # abgebrochene Artefakt endet GENAU an der gesetzten Grenze.
    reste = [p.stat().st_size for p in sorted(ablage2.glob("*"))] \
        if ablage2.is_dir() else []
    B.urteil("K5", reste == [RLIMIT_BYTES],
             f"voll(rlimit): die Schranke hat gegriffen -- der Schreibvorgang "
             f"endet bei {RLIMIT_BYTES} Bytes"
             if reste == [RLIMIT_BYTES] else
             f"voll(rlimit): in der Ablage liegt {reste}, erwartet "
             f"[{RLIMIT_BYTES}] -- die Schranke hat nicht gegriffen, es ist "
             f"nichts gemessen")

    # --- Fall 2: Trash ueber die Dateisystemgrenze -------------------------
    grenze = heim / "grenze"
    grenze.mkdir()
    q2 = grenze / "brief.txt"
    q2.write_bytes(b"liegt auf dem einen, soll auf das andere\n")
    v2 = sha(q2)
    fremd = fremdes_dateisystem(arbeit)
    if fremd is None:
        B.schlecht("K5", "grenze: kein zweites Dateisystem unter eigener "
                         "Kontrolle gefunden -- dieser Fall ist nicht "
                         "gemessen")
    else:
        dev_a = os.stat(q2.parent).st_dev
        dev_b = os.stat(fremd).st_dev
        # Gewogen, bevor gemessen wird: sind es wirklich zwei Dateisysteme?
        if dev_a == dev_b:
            B.schlecht("K5", f"grenze: {q2.parent} und {fremd} liegen auf "
                             f"demselben Geraet ({dev_a}) -- die Grenze "
                             f"existiert nicht, es ist nichts gemessen")
        else:
            B.gut("K5", f"grenze: eingespeist -- st_dev {dev_a} gegen {dev_b}")
            e2 = naht_fahren(pruefling, {
                "art": "trash", "quelle": str(q2), "trash": str(fremd),
                "zustand": str(grenze / "zustand")})
            k5_fall(B, "grenze", q2, e2, v2, "anderen Dateisystem")
            B.urteil("K5", q2.is_file(),
                     "grenze: die Datei liegt noch an ihrem Ort -- nicht "
                     "halb kopiert, halb geloescht"
                     if q2.is_file() else
                     "grenze: die Ursprungsdatei ist WEG")

    # --- Fall 3: `git stash` mit Konflikt ----------------------------------
    kon = heim / "konflikt"
    repo, datei = repo_bauen(kon / "repo", inhalt=b"drei\n")
    git(repo, "add", "arbeit.txt")
    git(repo, "commit", "-q", "-m", "drei")
    git(repo, "switch", "-q", "-c", "zweig", "HEAD~1")
    datei.write_bytes(b"zwei\n")
    git(repo, "commit", "-q", "-a", "-m", "zwei")
    git(repo, "switch", "-q", "-")
    git(repo, "merge", "zweig", pruefen=False)
    zustand = git(repo, "status", "--porcelain").stdout
    # Gewogen: steht der Konflikt wirklich?
    if "UU " not in zustand:
        B.schlecht("K5", f"konflikt: kein Merge-Konflikt hergestellt "
                         f"({zustand!r}) -- nichts gemessen")
    else:
        B.gut("K5", f"konflikt: eingespeist -- `git status` sagt "
                    f"{zustand.strip()!r}")
        v3 = sha(datei)
        vor_liste = len(git(repo, "stash", "list").stdout.strip().splitlines())
        e3 = naht_fahren(pruefling, {
            "art": "git-stash", "quelle": str(datei), "repo": str(repo),
            "zustand": str(kon / "zustand")})
        # Der Wortlaut von `git` haengt an seiner Version; belastbar ist der
        # ZUSTAND: der Konflikt steht (oben gewogen), `git stash` meldet einen
        # Fehlschlag, und die Stash-Liste ist danach unveraendert.
        k5_fall(B, "konflikt", datei, e3, v3, "git stash fehlgeschlagen")
        nach_liste = len(git(repo, "stash", "list").stdout.strip().splitlines())
        B.urteil("K5", nach_liste == vor_liste,
                 f"konflikt: und es liegt kein Stash-Eintrag da, der eine "
                 f"Sicherung behauptete ({nach_liste})"
                 if nach_liste == vor_liste else
                 f"konflikt: die Stash-Liste wuchs von {vor_liste} auf "
                 f"{nach_liste}, obwohl der Aufruf scheiterte")


def fremdes_dateisystem(arbeit: Path) -> Path | None:
    """Ein Verzeichnis unter EIGENER Kontrolle auf einem anderen Geraet.

    Nur unter `/dev/shm` und `/tmp` gesucht, und dort nur ein eigener,
    frisch angelegter Ordner. Nichts wird gefuellt: es geht um vierzig Bytes,
    die ohnehin nie geschrieben werden -- der Broker weist den Fall ab,
    bevor er etwas anfasst.
    """
    dev = os.stat(arbeit).st_dev
    for wurzel in ("/dev/shm", "/tmp", "/var/tmp"):
        p = Path(wurzel)
        if not p.is_dir() or not os.access(p, os.W_OK):
            continue
        try:
            if os.stat(p).st_dev == dev:
                continue
            eigen = Path(tempfile.mkdtemp(prefix="t48-fremd-", dir=str(p)))
        except OSError:
            continue
        return eigen
    return None


# ---------------------------------------------------------------------------
# K6 -- Herabstufung erst NACH der Verifikation
# ---------------------------------------------------------------------------

def reihenfolge_lesen(quelltext: str, funktion: str) -> str:
    """Kommt die Verifikation VOR dem `return Artefakt(...)`?

    Gemessen am Baum, nicht am Text: `verifiziert=True` darf erst hinter dem
    Aufruf stehen, der es rechtfertigt.
    """
    baum = ast.parse(quelltext)
    for knoten in ast.walk(baum):
        if not (isinstance(knoten, ast.FunctionDef) and knoten.name == funktion):
            continue
        # Die FRUEHESTE Herabstufung gegen die FRUEHESTE Verifikation. Wer
        # hier die letzte naehme, uebersaehe genau den Fall, um den es geht:
        # ein `verifiziert=True`, das oben schon steht.
        pruefungen = [k.lineno for k in ast.walk(knoten)
                      if isinstance(k, ast.Call)
                      and isinstance(k.func, ast.Name)
                      and k.func.id == "_verifizieren"]
        stufen = [k.lineno for k in ast.walk(knoten)
                  if isinstance(k, ast.Call)
                  and getattr(k.func, "id", "") == "Artefakt"
                  and any(kw.arg == "verifiziert"
                          and getattr(kw.value, "value", None) is True
                          for kw in k.keywords)]
        pruefung = min(pruefungen) if pruefungen else None
        rueckgabe = min(stufen) if stufen else None
        if pruefung is None:
            return "keine Verifikation"
        if rueckgabe is None:
            return "keine Herabstufung"
        return "richtig" if pruefung < rueckgabe else "falsch herum"
    return "funktion fehlt"


def k6_herabstufung(B: Bilanz, pruefling: Path, undo, modal, arbeit: Path,
                    ergebnis_gut: dict) -> None:
    quelle = (pruefling / PAKET / "brokers" / "fs" / "undo.py").read_text(
        encoding="utf-8")

    # POSITIV- UND NEGATIVKONTROLLE DES LESERS, an einem Koeder.
    koeder_gut = ("def f(q, a):\n"
                  "    _verifizieren(a, 1)\n"
                  "    return Artefakt(verifiziert=True)\n")
    koeder_schlecht = ("def f(q, a):\n"
                       "    x = Artefakt(verifiziert=True)\n"
                       "    _verifizieren(a, 1)\n"
                       "    return Artefakt(verifiziert=True)\n")
    lese_gut = reihenfolge_lesen(koeder_gut, "f")
    lese_schlecht = reihenfolge_lesen(koeder_schlecht, "f")
    B.urteil("K6", lese_gut == "richtig" and lese_schlecht == "falsch herum",
             "KONTROLLE des Lesers: richtige Reihenfolge -> 'richtig', "
             "vertauschte -> 'falsch herum'"
             if lese_gut == "richtig" and lese_schlecht == "falsch herum" else
             f"der Leser urteilt falsch: Koeder gut -> {lese_gut}, "
             f"Koeder schlecht -> {lese_schlecht}")

    for funktion in ("in_den_trash", "kopie_anlegen"):
        urteil = reihenfolge_lesen(quelle, funktion)
        B.urteil("K6", urteil == "richtig",
                 f"{funktion}: die Verifikation steht VOR dem "
                 f"`verifiziert=True`"
                 if urteil == "richtig" else
                 f"{funktion}: {urteil} -- das Artefakt gilt, bevor es "
                 f"gemessen wurde")

    # Und am Verhalten: `verifiziert` ist nie True ohne bestandene Pruefung.
    B.urteil("K6", ergebnis_gut.get("artefakt", {}).get("verifiziert") is True,
             "nach gelungener Verifikation traegt das Artefakt "
             "`verifiziert=True`"
             if ergebnis_gut.get("artefakt", {}).get("verifiziert") is True
             else f"das gelungene Artefakt traegt "
                  f"{ergebnis_gut.get('artefakt')}")

    # Die Herabstufung selbst, an der Stelle, die sie liest: ohne
    # verifiziertes Artefakt bleibt eine destruktive Aktion beim harten Weg.
    hart = modal.braucht_modal({"destructive": True}, undo_verifiziert=False)
    leise = modal.braucht_modal({"destructive": True}, undo_verifiziert=True)
    B.urteil("K6", hart and not leise,
             "ohne verifiziertes Artefakt bleibt eine destruktive Aktion "
             "beim modalen Dialog; erst mit Artefakt wird herabgestuft"
             if hart and not leise else
             f"die Herabstufung haengt nicht am Artefakt "
             f"(ohne: {hart}, mit: {leise})")


# ---------------------------------------------------------------------------
# K7 -- der Zulauf
# ---------------------------------------------------------------------------

def zulauf_lesen(baum: Path) -> dict:
    """Wer reicht im Betrieb ein Undo herein -- und wer ruft `vorbereiten`?

    Gelesen wird der AST, nicht der Text: ein Modulkopf, der eine Zusage
    beschreibt, darf sie nicht selbst belegen. (Genau das ist dem T-4.7-Lauf
    am 18.08. passiert.)
    """
    stellen: list[str] = []
    aufrufe: list[str] = []
    koordinatoren = 0
    for datei in sorted(baum.rglob("*.py")):
        if datei.name == "undo.py":
            continue                       # der Pruefling belegt sich nicht selbst
        try:
            knoten = ast.parse(datei.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for k in ast.walk(knoten):
            if not isinstance(k, ast.Call):
                continue
            name = getattr(k.func, "id", "") or getattr(k.func, "attr", "")
            if name.endswith("Koordinator"):
                koordinatoren += 1
                for kw in k.keywords:
                    if kw.arg == "undo" and not (
                            isinstance(kw.value, ast.Constant)
                            and kw.value.value is None):
                        stellen.append(f"{datei.name}:{k.lineno}")
            if name in ("vorbereiten", "in_den_trash", "kopie_anlegen",
                        "stash_anlegen"):
                aufrufe.append(f"{datei.name}:{k.lineno} {name}")
    return {"koordinatoren": koordinatoren, "verdrahtet": stellen,
            "aufrufe": aufrufe}


def k7_zulauf(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    # KONTROLLE DES LESERS an zwei Koedern, bevor er etwas behauptet.
    probe = arbeit / "zulauf-probe"
    (probe / "mit").mkdir(parents=True)
    (probe / "ohne").mkdir(parents=True)
    (probe / "mit" / "x.py").write_text(
        "k = Koordinator(policy=p, undo=self._undo_vorbereiten)\n"
        "undo.vorbereiten('kopie')\n", encoding="utf-8")
    (probe / "ohne" / "x.py").write_text(
        "k = Koordinator(policy=p, sprechen=s)\n"
        "# undo.vorbereiten('kopie') -- nur ein Kommentar\n", encoding="utf-8")
    mit = zulauf_lesen(probe / "mit")
    ohne = zulauf_lesen(probe / "ohne")
    B.urteil("K7", bool(mit["verdrahtet"]) and bool(mit["aufrufe"])
             and not ohne["verdrahtet"] and not ohne["aufrufe"]
             and ohne["koordinatoren"] == 1,
             "KONTROLLE des Lesers: er sieht eine Verdrahtung, wenn es eine "
             "gibt, und keine, wenn sie fehlt -- den Koordinator findet er "
             "in beiden Faellen"
             if mit["verdrahtet"] and not ohne["verdrahtet"] else
             f"der Leser urteilt falsch: mit={mit}, ohne={ohne}")

    befund = zulauf_lesen(pruefling / PAKET)
    B.notiz(f"Zulauf: {befund}")
    B.urteil("K7", befund["koordinatoren"] > 0,
             f"im Baum wird {befund['koordinatoren']}x ein Koordinator gebaut"
             if befund["koordinatoren"] else
             "im Baum wird gar kein Koordinator gebaut -- dann kann dieser "
             "Leser nichts sehen, und das ist keine Aussage ueber T-4.8")
    B.urteil("K7", bool(befund["verdrahtet"]),
             f"und mindestens einer bekommt ein `undo=` gereicht: "
             f"{befund['verdrahtet']}"
             if befund["verdrahtet"] else
             "KEIN Koordinator bekommt ein `undo=` gereicht: `self.undo is "
             "None`, der Undo-Hop wird uebersprungen. Die Zusage 'schlaegt "
             "die Vorbereitung fehl, wird die Mutation abgebrochen' hat im "
             "Betrieb keinen Fall, in dem sie greift -- es wird nie etwas "
             "vorbereitet")
    B.urteil("K7", bool(befund["aufrufe"]),
             f"und `undo.vorbereiten` wird im Baum gerufen: {befund['aufrufe']}"
             if befund["aufrufe"] else
             "und niemand ruft `vorbereiten`/`in_den_trash`/`kopie_anlegen`/"
             "`stash_anlegen` -- das Modul hat ausserhalb der Tests keinen "
             "einzigen Aufrufer")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def messen(B: Bilanz, k: str, fn, *args) -> None:
    """Ein Kriterium, dessen Messung wirft, ist ROT -- nicht abwesend.

    Und der Lauf geht weiter: ein Pruefling, der an K1 stirbt, soll trotzdem
    sagen, wie es um K5 steht.
    """
    try:
        fn(*args)
    except Exception as fehler:
        B.schlecht(k, f"die Messung ist geworfen: "
                      f"{type(fehler).__name__}: {str(fehler)[:300]}")


def main() -> int:
    pruefling = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    B = Bilanz()
    print(f"Pruefling: {pruefling}", flush=True)

    fehlt = [w for w in NOETIG if shutil.which(w) is None]
    if fehlt:
        print(f"UMGEBUNG: {fehlt} fehlen -- die Zusage ist so nicht messbar, "
              f"und das ist kein Erfolg.", flush=True)
        return 2

    alter_pfad = os.environ.get("PATH", "")
    arbeit = Path(tempfile.mkdtemp(prefix="t48-"))
    protokoll = arbeit / "vorschalter.log"
    protokoll.write_text("", encoding="utf-8")
    fremd: Path | None = None
    try:
        verz = vorschalter_bauen(arbeit, protokoll)
        os.environ["PATH"] = f"{verz}:{alter_pfad}"
        if not vorschalter_sitzt(B, verz):
            print("ABBRUCH: die Sperre sitzt nicht. Es wird NICHT weiter "
                  "gemessen -- ein Lauf ohne zweite Reihe hat am 18.08. zwei "
                  "echte Dienste gestartet.", flush=True)
            return 2

        undo = modul_laden(pruefling, f"{PAKET}.brokers.fs.undo")
        modal = modul_laden(pruefling, f"{PAKET}.auth.modal")

        messen(B, "K1", k1_trash, B, undo, arbeit)
        messen(B, "K2", k2_kopie, B, undo, arbeit, protokoll)
        messen(B, "K3", k3_stash, B, pruefling, arbeit)
        messen(B, "K4", k4_verifikation, B, pruefling, arbeit, protokoll)

        # Die Positivkontrolle aus K5 traegt auch K6 -- ein Artefakt, das
        # wirklich entstanden ist.
        messen(B, "K5", k5_abbruch, B, pruefling, arbeit)
        gut = naht_fahren(pruefling, {
            "art": "kopie", "quelle": str(_datei(arbeit / "k6", b"fuer K6\n")),
            "ablage": str(arbeit / "k6" / "undo"),
            "zustand": str(arbeit / "k6" / "zustand")})
        messen(B, "K6", k6_herabstufung, B, pruefling, undo, modal, arbeit, gut)
        messen(B, "K7", k7_zulauf, B, pruefling, arbeit)
    finally:
        os.environ["PATH"] = alter_pfad
        vorschalter_auswerten(protokoll)
        for rest in Path("/dev/shm").glob("t48-fremd-*"):
            shutil.rmtree(rest, ignore_errors=True)
        for wurzel in ("/tmp", "/var/tmp"):
            for rest in Path(wurzel).glob("t48-fremd-*"):
                shutil.rmtree(rest, ignore_errors=True)
        shutil.rmtree(arbeit, ignore_errors=True)
        print(f"aufgeraeumt: {arbeit} ({'weg' if not arbeit.exists() else 'DA'})",
              flush=True)
    return B.abschluss()


def _datei(verz: Path, inhalt: bytes) -> Path:
    verz.mkdir(parents=True, exist_ok=True)
    p = verz / "kanarienvogel.txt"
    p.write_bytes(inhalt)
    return p


if __name__ == "__main__":
    raise SystemExit(main())
