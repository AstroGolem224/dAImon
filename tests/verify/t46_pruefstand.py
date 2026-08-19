#!/usr/bin/env python3
"""Pruefstand fuer T-4.6 — Audit mit Hash-Kette und Verankerung.

Geprueft wird die AKZEPTANZLISTE von T-4.6 (Implementierungsplan, Z. 1244 ff.)
und Design 7.6, Kriterium fuer Kriterium, ohne `&&`-Verkettung. Jedes
Kriterium rechnet einzeln ab; ein rotes Kriterium verhindert nicht die Messung
der uebrigen.

  K1  JSONL unter $XDG_STATE_HOME/daimon/audit/, 0700/0600
  K2  `seq` und `prev_hash` je Datensatz
  K3  Kettenkopf periodisch UND bei jeder Rotation ins Journal verankert
  K4  Die Pruefung liest BEIDE Stroeme und meldet eine ersetzte Datei
  K5  Drei benannte Pruefstellen, keine davon mit Modelltext im Prozess
  K6  Alle Pflichtfelder aus Design 7.6
  K7  Redaktion nach HERKUNFT, nicht nach Katalogflag
  K8  Rotation traegt den letzten Hash in die neue Datei

Zwei Regeln dieses Repos schlagen hier durch:

**Jede Manipulation wird gewogen.** Am 17.08. hat eine Positivkontrolle des
Builders nichts veraendert -- das `replace` traf nicht, die Kopie blieb
byteweise identisch, und der Pruefer meldete brav `ok`. Deshalb vergleicht
`manipulieren()` die sha256 der Datei VOR und NACH dem Eingriff und faellt
laut, wenn sie gleich ist. "Nichts gefunden" ist damit von "nicht gemessen"
unterscheidbar.

**Eine Messung ist ein Zeitpunkt.** Es gibt hier kein `--since`-Fenster gegen
ein zweites Fenster. Der zweite Strom laeuft waehrend des Laufs vollstaendig
ueber eine abgefangene Journal-Datei: `systemd-cat` schreibt hinein,
`journalctl` liest daraus, und beide Attrappen haben eine Positivkontrolle,
dass das Abfangen ueberhaupt greift. Der Bezugspunkt ist der Dateiinhalt,
nicht die Uhr. Nebenwirkung mit Absicht: der Lauf schreibt keine Anker seiner
synthetischen Ketten ins echte Journal des Nutzers.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8")

# Anhang D bindet vier Mutanten an dieses Kriteriengeruest.
MUTANTEN_GRENZEN = {
    "kette-ohne-journal-anker": "K3/K4",
    "tainted-im-klartext": "K7",
    "redaktion-nur-bei-sensitive": "K7",
    "rotation-traegt-hash-nicht-weiter": "K8",
}

# Design 7.6, woertlich. `tool_use_id` steht dort neben den fuenf, die die
# Akzeptanzliste ausdruecklich nennt.
PFLICHTFELDER = ("prompt_shown", "params_hash", "mark_id", "initiator",
                 "turn_id", "tool_use_id", "outcome")

BEOBACHTUNG_S = 25.0

STUB_SYSTEMD_CAT = '''#!/usr/bin/env python3
"""Attrappe fuer systemd-cat: schreibt in DAIMON_STUB_JOURNAL statt ins Journal."""
import os
import sys

argv = sys.argv[1:]
tag = ""
for i, a in enumerate(argv):
    if a in ("-t", "--identifier") and i + 1 < len(argv):
        tag = argv[i + 1]
    elif a.startswith("--identifier="):
        tag = a.split("=", 1)[1]
text = sys.stdin.read()
with open(os.environ["DAIMON_STUB_JOURNAL"], "a", encoding="utf-8") as fh:
    for zeile in text.splitlines():
        fh.write(tag + "\\t" + zeile + "\\n")
'''

STUB_JOURNALCTL = '''#!/usr/bin/env python3
"""Attrappe fuer journalctl: liest DAIMON_STUB_JOURNAL, filtert nach -t."""
import os
import sys

argv = sys.argv[1:]
tag = None
for i, a in enumerate(argv):
    if a in ("-t", "--identifier") and i + 1 < len(argv):
        tag = argv[i + 1]
    elif a.startswith("--identifier="):
        tag = a.split("=", 1)[1]
try:
    with open(os.environ["DAIMON_STUB_JOURNAL"], encoding="utf-8") as fh:
        zeilen = fh.read().splitlines()
except OSError:
    zeilen = []
for zeile in zeilen:
    t, _, text = zeile.partition("\\t")
    if tag is None or t == tag:
        print(text)
'''


@dataclass(frozen=True)
class Ergebnis:
    kriterium: str
    ok: bool
    text: str


class Bericht:
    def __init__(self) -> None:
        self.ergebnisse: list[Ergebnis] = []

    def pruefe(self, kriterium: str, bedingung: bool, text: str) -> bool:
        ok = bool(bedingung)
        self.ergebnisse.append(Ergebnis(kriterium, ok, text))
        if not ok:
            print(f"FAIL {kriterium}: {text}", file=sys.stderr)
        return ok

    def fehler(self, kriterium: str, text: str) -> None:
        self.pruefe(kriterium, False, text)

    def bilanz(self) -> int:
        print("\nBilanz T-4.6:")
        rot_gesamt = 0
        gruppen: dict[str, list[Ergebnis]] = defaultdict(list)
        for ergebnis in self.ergebnisse:
            gruppen[ergebnis.kriterium].append(ergebnis)
        for kriterium in KRITERIEN:
            werte = gruppen.get(kriterium, [])
            rot = sum(not wert.ok for wert in werte)
            rot_gesamt += rot
            print(f"{kriterium}: {len(werte)} Pruefungen, {rot} rot")
            if not werte:
                # Ein Kriterium ohne eine einzige Messung ist kein gruenes
                # Kriterium. Es ist ein nicht gemessenes.
                rot_gesamt += 1
                print(f"{kriterium}: NICHT GEMESSEN -- zaehlt als rot")
        return 1 if rot_gesamt else 0


# -- Werkzeug ---------------------------------------------------------------

def summe(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def manipulieren(pfad: Path, aenderung, beschreibung: str) -> None:
    """Eine Manipulation, die nachweislich Bytes bewegt.

    Der Fehler vom 17.08.: ein `replace`, das nicht traf, eine byteweise
    identische Kopie, und ein Pruefer, der `ok` meldete. Wer hier nichts
    veraendert, bekommt keinen gruenen Lauf, sondern einen Abbruch.
    """
    vorher = summe(pfad)
    aenderung()
    nachher = summe(pfad)
    if vorher == nachher:
        raise RuntimeError(
            f"POSITIVKONTROLLE GESCHEITERT: '{beschreibung}' hat {pfad} nicht "
            f"veraendert (sha256 vorher {vorher} == nachher {nachher}). Der "
            f"folgende Befund waere nicht gemessen, sondern erfunden.")
    print(f"Manipulation '{beschreibung}': sha256 {vorher[:12]} -> {nachher[:12]}")


def zeilen(pfad: Path) -> list[str]:
    return [z for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]


def lade_audit_modul(pruefling: Path) -> ModuleType:
    sys.path.insert(0, str(pruefling))
    importlib.invalidate_caches()
    for name in tuple(sys.modules):
        if name == "daimon" or name.startswith("daimon."):
            del sys.modules[name]
    modul = importlib.import_module("daimon.hub.audit")
    quelle = Path(modul.__file__ or "").resolve()
    try:
        quelle.relative_to(pruefling.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Import entkam dem Pruefling: {quelle}") from exc
    return modul


def satz_felder(**zusatz) -> dict:
    felder = {
        "prompt_shown": "Vorschau",
        "params_hash": "sha256:0000",
        "mark_id": "marke-1",
        "initiator": "user",
        "turn_id": "runde-1",
        "tool_use_id": "werkzeug-1",
        "outcome": "ok",
    }
    felder.update(zusatz)
    return felder


def kette_schreiben(modul: ModuleType, verzeichnis: Path, anzahl: int,
                    marke: str = "marke"):
    buch = modul.Audit.oeffnen(verzeichnis)
    for nummer in range(anzahl):
        buch.schreiben(ts=1_700_000_000.0 + nummer,
                       **satz_felder(mark_id=f"{marke}-{nummer}"))
    return buch


def startzeit(pid: int) -> str | None:
    lauf = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    wert = lauf.stdout.strip()
    return wert if lauf.returncode == 0 and wert else None


class Prozessgruppe:
    """Eigene Sitzung, mit Nachweis, dass die PID beim Toeten noch dieselbe ist."""

    def __init__(self, befehl: Sequence[str], cwd: Path, env: dict[str, str]) -> None:
        self.log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        self.prozess = subprocess.Popen(
            list(befehl), cwd=cwd, env=env, stdout=self.log,
            stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.pid = self.prozess.pid
        self.start = startzeit(self.pid)
        if self.start is None:
            raise RuntimeError(f"Startzeit von PID {self.pid} nicht lesbar")

    def lebt(self) -> bool:
        return self.prozess.poll() is None

    def ausgabe(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return self.log.read()[-4000:]

    def stop(self) -> None:
        if not self.lebt():
            return
        if startzeit(self.pid) != self.start:
            raise RuntimeError(f"PID {self.pid} vor kill wiederverwendet")
        if os.getpgid(self.pid) != self.pid:
            raise RuntimeError(f"PID {self.pid} ist nicht Leiter ihrer Gruppe")
        os.killpg(self.pid, signal.SIGTERM)
        try:
            self.prozess.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            if startzeit(self.pid) != self.start:
                raise RuntimeError(f"PID {self.pid} vor SIGKILL wiederverwendet")
            os.killpg(self.pid, signal.SIGKILL)
            self.prozess.wait(timeout=10.0)


class Attrappen:
    """`systemd-cat` schreibt in eine Datei, `journalctl` liest sie zurueck."""

    def __init__(self, wurzel: Path) -> None:
        self.bin = wurzel / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.journal = wurzel / "journal.txt"
        self.journal.write_text("", encoding="utf-8")
        for name, quelle in (("systemd-cat", STUB_SYSTEMD_CAT),
                             ("journalctl", STUB_JOURNALCTL)):
            pfad = self.bin / name
            pfad.write_text(quelle, encoding="utf-8")
            pfad.chmod(0o755)

    def umgebung(self, **zusatz) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["DAIMON_STUB_JOURNAL"] = str(self.journal)
        env.update({k: str(v) for k, v in zusatz.items()})
        return env

    def anker(self, tag: str = "daimon-audit") -> list[str]:
        texte = []
        for zeile in self.journal.read_text(encoding="utf-8").splitlines():
            t, _, text = zeile.partition("\t")
            if t == tag:
                texte.append(text)
        return texte

    def kontrolle(self, bericht: Bericht, kriterium: str) -> None:
        """Ohne diese Kontrolle waere jede spaetere Ankermessung wertlos."""
        marke = "AUDIT-ANKER seq=0 head=attrappen-positivkontrolle"
        env = self.umgebung()
        subprocess.run(["systemd-cat", "-t", "daimon-audit"],
                       input=marke.encode("utf-8"), env=env, timeout=30,
                       check=True)
        subprocess.run(["systemd-cat", "-t", "fremder-tag"],
                       input=b"FREMD", env=env, timeout=30, check=True)
        gelesen = subprocess.run(
            ["journalctl", "--user", "-t", "daimon-audit", "--since", "-30d",
             "--output", "cat", "--no-pager"],
            env=env, capture_output=True, text=True, timeout=30)
        zeilen_ = gelesen.stdout.splitlines()
        bericht.pruefe(kriterium, marke in zeilen_,
                       f"Positivkontrolle Attrappe: geschriebener Anker kommt "
                       f"zurueck; gelesen {zeilen_!r}")
        bericht.pruefe(kriterium, "FREMD" not in zeilen_,
                       f"Positivkontrolle Attrappe: fremder Tag wird gefiltert; "
                       f"gelesen {zeilen_!r}")
        # Den Kontrollanker wieder entfernen: er darf keine spaetere Messung
        # tragen. Danach ist die Datei nachweislich leer.
        self.journal.write_text("", encoding="utf-8")
        bericht.pruefe(kriterium, self.anker() == [],
                       "Attrappen-Journal ist vor der ersten Messung leer")


# -- K1 ---------------------------------------------------------------------

def pruefe_k1(modul: ModuleType, pruefling: Path, arbeit: Path,
              attrappen: Attrappen, bericht: Bericht) -> None:
    try:
        verzeichnis = arbeit / "k1" / "audit"
        kette_schreiben(modul, verzeichnis, 3)
        datei = verzeichnis / modul.DATEI

        bericht.pruefe("K1", datei.is_file(), f"{datei} existiert")
        modus_dir = verzeichnis.stat().st_mode & 0o777
        modus_datei = datei.stat().st_mode & 0o777
        bericht.pruefe("K1", modus_dir == 0o700,
                       f"Verzeichnisrechte {oct(modus_dir)}, erwartet 0o700")
        bericht.pruefe("K1", modus_datei == 0o600,
                       f"Dateirechte {oct(modus_datei)}, erwartet 0o600")

        # Positivkontrolle der Rechtemessung: ein absichtlich offenes
        # Verzeichnis daneben muss als offen gemessen werden. Sonst waere
        # "0700 gemessen" nicht von "immer 0700 gemeldet" unterscheidbar.
        offen = arbeit / "k1" / "offen"
        offen.mkdir(parents=True)
        offen.chmod(0o755)
        bericht.pruefe("K1", (offen.stat().st_mode & 0o777) == 0o755,
                       "Positivkontrolle: die Rechtemessung sieht auch 0o755")

        # JSONL: jede Zeile ein JSON-Objekt.
        objekte = []
        for nummer, zeile in enumerate(zeilen(datei), start=1):
            try:
                objekte.append(json.loads(zeile))
            except ValueError as exc:
                bericht.fehler("K1", f"Zeile {nummer} ist kein JSON: {exc}")
        bericht.pruefe("K1", len(objekte) == 3,
                       f"3 Zeilen JSONL erwartet, {len(objekte)} gelesen")

        # Der ZULAUF: wer waehlt den Pfad? Die CLI, ohne --verzeichnis.
        zuhause = arbeit / "k1" / "xdg"
        (zuhause / "state").mkdir(parents=True)
        lauf = subprocess.run(
            [sys.executable, "-m", "daimon.hub.audit", "--verify"],
            cwd=pruefling, text=True, capture_output=True, timeout=120,
            env=attrappen.umgebung(XDG_STATE_HOME=zuhause / "state",
                                   PYTHONPATH=pruefling))
        try:
            befund = json.loads(lauf.stdout)
        except ValueError:
            bericht.fehler("K1", f"CLI-Ausgabe ist kein JSON: {lauf.stdout[-800:]}"
                                 f" / stderr {lauf.stderr[-800:]}")
            return
        erwartet = str(zuhause / "state" / "daimon" / "audit" / modul.DATEI)
        bericht.pruefe("K1", befund.get("datei") == erwartet,
                       f"CLI legt die Kette nach {befund.get('datei')!r}, "
                       f"erwartet {erwartet!r} (XDG_STATE_HOME/daimon/audit/)")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K1", f"Ort-/Rechtemessung fehlgeschlagen: {exc!r}")


# -- K2 ---------------------------------------------------------------------

def pruefe_k2(modul: ModuleType, arbeit: Path, bericht: Bericht) -> None:
    try:
        verzeichnis = arbeit / "k2" / "audit"
        kette_schreiben(modul, verzeichnis, 100)
        datei = verzeichnis / modul.DATEI
        rohzeilen = zeilen(datei)
        bericht.pruefe("K2", len(rohzeilen) == 100,
                       f"100 Datensaetze erwartet, {len(rohzeilen)} geschrieben")

        saetze = [json.loads(z) for z in rohzeilen]
        nummern = [s.get("seq") for s in saetze]
        bericht.pruefe("K2", nummern == list(range(1, len(saetze) + 1)),
                       f"seq laeuft nicht 1..{len(saetze)}: {nummern[:5]}...{nummern[-3:]}")
        bericht.pruefe("K2", all("prev_hash" in s for s in saetze),
                       "jeder Datensatz traegt prev_hash")
        bericht.pruefe("K2", saetze[0].get("prev_hash") == "",
                       f"erster prev_hash ist leer, ist {saetze[0].get('prev_hash')!r}")

        # Unabhaengig nachgerechnet: sha256 ueber die Zeile, wie sie in der
        # Datei steht. Der Pruefstand nimmt dafuer NICHT die Funktion des
        # Prueflings -- sonst pruefte er eine Formel gegen sich selbst.
        falsch = []
        for nummer in range(1, len(saetze)):
            eigen = "sha256:" + hashlib.sha256(
                rohzeilen[nummer - 1].encode("utf-8")).hexdigest()
            if saetze[nummer].get("prev_hash") != eigen:
                falsch.append(nummer + 1)
        bericht.pruefe("K2", not falsch,
                       f"prev_hash entspricht nicht sha256 der Vorgaengerzeile "
                       f"bei seq {falsch[:5]}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K2", f"Kettenmessung fehlgeschlagen: {exc!r}")


# -- K3 ---------------------------------------------------------------------

def pruefe_k3(modul: ModuleType, pruefling: Path, arbeit: Path,
              attrappen: Attrappen, bericht: Bericht) -> None:
    """Verankerung: in-process bei Rotation, live beim Hub-Start."""
    try:
        verzeichnis = arbeit / "k3" / "audit"
        buch = kette_schreiben(modul, verzeichnis, 5)
        gesammelt: list[str] = []

        kopf_vorher = buch.kopf
        buch.verankern(journal=gesammelt.append)
        bericht.pruefe("K3", len(gesammelt) == 1,
                       f"verankern() schreibt genau einen Journal-Satz, "
                       f"geschrieben {len(gesammelt)}")
        bericht.pruefe("K3", gesammelt and f"head={kopf_vorher}" in gesammelt[-1],
                       f"Anker traegt den Kettenkopf {kopf_vorher!r}: {gesammelt[-1:]!r}")

        # Positivkontrolle des Sammlers: nach einem weiteren Datensatz muss
        # der Anker ANDERS aussehen. Ein Sammler, der immer dasselbe sieht,
        # sieht nichts.
        buch.schreiben(ts=1_700_000_500.0, **satz_felder(mark_id="marke-neu"))
        buch.verankern(journal=gesammelt.append)
        bericht.pruefe("K3", len(gesammelt) == 2 and gesammelt[0] != gesammelt[1],
                       f"Positivkontrolle: zweiter Anker unterscheidet sich: {gesammelt!r}")

        kopf_vor_rotation = buch.kopf
        vor_rotation = len(gesammelt)
        buch.rotieren(journal=gesammelt.append)
        bericht.pruefe("K3", len(gesammelt) == vor_rotation + 1,
                       f"Rotation verankert genau einmal, "
                       f"{len(gesammelt) - vor_rotation} Anker geschrieben")
        bericht.pruefe(
            "K3", len(gesammelt) > vor_rotation
            and f"head={kopf_vor_rotation}" in gesammelt[-1],
            f"Rotationsanker traegt den Kopf VOR der Rotation "
            f"{kopf_vor_rotation!r}: {gesammelt[-1:]!r}")

        # Periodisch: das Intervall ist eine endliche Zahl, und der Faden, der
        # es benutzt, wird beim Start gezogen (live unten gemessen).
        daemon = importlib.import_module("daimon.hub.daemon")
        intervall = getattr(daemon, "AUDIT_ANKER_INTERVALL_S", None)
        bericht.pruefe(
            "K3", isinstance(intervall, (int, float)) and 0 < float(intervall) <= 86400.0,
            f"AUDIT_ANKER_INTERVALL_S ist eine endliche Periode <= 24 h, ist {intervall!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K3", f"Verankerung in-process fehlgeschlagen: {exc!r}")

    pruefe_k3_live(modul, pruefling, arbeit, attrappen, bericht)


def hub_starten(pruefling: Path, arbeit: Path, attrappen: Attrappen,
                name: str) -> tuple[Prozessgruppe, Path, Path]:
    wurzel = arbeit / name
    laufzeit = wurzel / "rt"
    zustand = wurzel / "state"
    laufzeit.mkdir(parents=True)
    zustand.mkdir(parents=True)
    env = attrappen.umgebung(XDG_STATE_HOME=zustand, XDG_RUNTIME_DIR=wurzel,
                             PYTHONPATH=pruefling)
    prozess = Prozessgruppe(
        [sys.executable, "-m", "daimon.hub.daemon", "--runtime-dir", str(laufzeit)],
        pruefling, env)
    return prozess, laufzeit, zustand


def warte_auf(bedingung, timeout_s: float = BEOBACHTUNG_S):
    ende = time.monotonic() + timeout_s
    letzter = None
    while time.monotonic() < ende:
        letzter = bedingung()
        if letzter:
            return letzter
        time.sleep(0.2)
    return letzter


def schnappschuss(laufzeit: Path) -> dict | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(laufzeit / "state.sock"))
            zeile = sock.makefile("rb").readline()
        return json.loads(zeile) if zeile else None
    except (OSError, ValueError):
        return None


def pruefe_k3_live(modul: ModuleType, pruefling: Path, arbeit: Path,
                   attrappen: Attrappen, bericht: Bericht) -> None:
    """Der ZULAUF: verankert im Betrieb ueberhaupt jemand?

    Nicht der Pruefstand ruft `verankern()`, sondern der Hub, den ein Nutzer
    startet. Gemessen wird an der abgefangenen Journal-Datei -- ein fester
    Bezugspunkt, kein Zeitfenster.
    """
    prozess = None
    try:
        vorher = len(attrappen.anker())
        prozess, laufzeit, zustand = hub_starten(pruefling, arbeit, attrappen, "k3live")
        neue = warte_auf(lambda: attrappen.anker()[vorher:])
        bericht.pruefe("K3", bool(neue),
                       f"Der gestartete Hub verankert von selbst; Anker seit "
                       f"Messbeginn: {neue!r}; Hub-Ausgabe: {prozess.ausgabe()[-800:]}")
        if neue:
            bericht.pruefe("K3", all(z.startswith(modul.ANKER_PRAEFIX) for z in neue),
                           f"Anker tragen das Praefix {modul.ANKER_PRAEFIX!r}: {neue!r}")
            bericht.pruefe("K3", any("head=" in z for z in neue),
                           f"Anker nennt den Kettenkopf: {neue!r}")
        bericht.pruefe("K3", prozess.lebt(), "Hub laeuft nach der Verankerung weiter")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K3", f"Live-Verankerung fehlgeschlagen: {exc!r}")
    finally:
        if prozess is not None:
            try:
                prozess.stop()
            except Exception as exc:  # noqa: BLE001
                bericht.fehler("K3", f"Hub-Aufraeumen: {exc!r}")


# -- K4 ---------------------------------------------------------------------

def pruefe_k4(modul: ModuleType, arbeit: Path, bericht: Bericht) -> None:
    """Die vier vorgeschriebenen Manipulationen, jede einzeln, jede gewogen."""
    try:
        verzeichnis = arbeit / "k4" / "audit"
        buch = kette_schreiben(modul, verzeichnis, 100)
        datei = verzeichnis / modul.DATEI

        # Die Anker kommen aus dem Pruefling selbst, nicht aus einer Rechnung
        # des Pruefstands. Ein Audit, das nicht verankert, hat hier keine.
        gesammelt: list[str] = []
        buch.verankern(journal=gesammelt.append)
        anker = {z.split("head=", 1)[1].strip() for z in gesammelt if "head=" in z}
        bericht.pruefe("K4", bool(anker),
                       f"Der Pruefling liefert mindestens einen Anker: {gesammelt!r}")

        unberuehrt = arbeit / "k4" / "unberuehrt.jsonl"
        shutil.copy2(datei, unberuehrt)

        befund = modul.pruefe(verzeichnis, anker=anker)
        bericht.pruefe("K4", befund.get("ok") is True,
                       f"Positivkontrolle: die unberuehrte Kette ist ok: {befund!r}")
        bericht.pruefe("K4", befund.get("saetze") == 100,
                       f"Positivkontrolle: 100 Saetze gelesen, {befund.get('saetze')!r}")
        bericht.pruefe("K4", befund.get("anker_getroffen", 0) >= 1,
                       f"Positivkontrolle: mindestens ein Anker liegt in der Kette: {befund!r}")

        def zuruecksetzen() -> None:
            shutil.copy2(unberuehrt, datei)
            if summe(datei) != summe(unberuehrt):
                raise RuntimeError("Ruecksetzen der Kette misslungen")

        # (a) eine Zeile geaendert
        def aendern() -> None:
            alle = zeilen(datei)
            satz = json.loads(alle[41])
            satz["outcome"] = "failed" if satz.get("outcome") != "failed" else "denied"
            alle[41] = json.dumps(satz, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False)
            datei.write_text("\n".join(alle) + "\n", encoding="utf-8")

        manipulieren(datei, aendern, "eine Zeile geaendert")
        befund = modul.pruefe(verzeichnis, anker=anker)
        bericht.pruefe("K4", befund.get("ok") is False,
                       f"geaenderte Zeile wird erkannt: {befund!r}")
        zuruecksetzen()

        # (b) eine Zeile geloescht
        def loeschen() -> None:
            alle = zeilen(datei)
            del alle[17]
            datei.write_text("\n".join(alle) + "\n", encoding="utf-8")

        manipulieren(datei, loeschen, "eine Zeile geloescht")
        befund = modul.pruefe(verzeichnis, anker=anker)
        bericht.pruefe("K4", befund.get("ok") is False,
                       f"geloeschte Zeile wird erkannt: {befund!r}")
        zuruecksetzen()

        # (c) zwei Zeilen getauscht
        def tauschen() -> None:
            alle = zeilen(datei)
            alle[30], alle[31] = alle[31], alle[30]
            datei.write_text("\n".join(alle) + "\n", encoding="utf-8")

        manipulieren(datei, tauschen, "zwei Zeilen getauscht")
        befund = modul.pruefe(verzeichnis, anker=anker)
        bericht.pruefe("K4", befund.get("ok") is False,
                       f"vertauschte Zeilen werden erkannt: {befund!r}")
        zuruecksetzen()

        # (d) die ganze Datei durch eine NEU GERECHNETE, in sich stimmige
        # Kette ersetzt. Sie wird mit dem Pruefling selbst gebaut, damit sie
        # wirklich stimmig ist -- nur die Anker koennen sie noch verraten.
        # Andere Marken: die Ersatzkette muss ANDERE Bytes haben, sonst waere
        # sie keine Ersetzung. Genau das hat die Waage oben beim ersten Lauf
        # gefangen -- eine bytegleiche "Ersetzung" haette hier gruen gemeldet.
        ersatz = arbeit / "k4" / "ersatz"
        ersatzbuch = ersatz / "audit"
        kette_schreiben(modul, ersatzbuch, 100, marke="fremde-marke")
        ersatzdatei = ersatzbuch / modul.DATEI

        def ersetzen() -> None:
            shutil.copy2(ersatzdatei, datei)

        manipulieren(datei, ersetzen, "ganze Datei durch neue stimmige Kette ersetzt")

        # Erst der Beweis, dass die Ersatzkette GEGEN SICH SELBST stimmt --
        # sonst waere unklar, ob die Anker sie fangen oder die Kettenpruefung.
        ohne_anker = modul.pruefe(verzeichnis, anker=set())
        kettenfehler = [f for f in ohne_anker.get("fehler", []) if "Anker" not in f]
        bericht.pruefe("K4", not kettenfehler,
                       f"Die Ersatzkette ist in sich stimmig -- die Kettenpruefung "
                       f"allein findet nichts: {kettenfehler!r}")
        befund = modul.pruefe(verzeichnis, anker=anker)
        bericht.pruefe("K4", befund.get("ok") is False,
                       f"ersetzte Datei wird gegen die Journal-Anker erkannt: {befund!r}")
        bericht.pruefe("K4", befund.get("anker_getroffen") == 0,
                       f"kein Anker liegt mehr in der Kette: {befund!r}")
        bericht.pruefe(
            "K4", any("Anker" in f for f in befund.get("fehler", [])),
            f"der Befund benennt den zweiten Strom: {befund.get('fehler')!r}")
        zuruecksetzen()

        # Beide Stroeme, nicht einer: eine unberuehrte Kette OHNE Anker ist
        # ausdruecklich kein `ok`.
        blind = modul.pruefe(verzeichnis, anker=set())
        bericht.pruefe("K4", blind.get("ok") is False,
                       f"ohne Anker meldet die Pruefung ihre Blindheit: {blind!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K4", f"Manipulationsmessung fehlgeschlagen: {exc!r}")


# -- K5 ---------------------------------------------------------------------

def pruefe_k5_cli(modul: ModuleType, pruefling: Path, arbeit: Path,
                  attrappen: Attrappen, bericht: Bericht) -> None:
    """Pruefstelle 3: `daimon-audit --verify` fuer den Nutzer."""
    try:
        verzeichnis = arbeit / "k5cli" / "audit"
        buch = kette_schreiben(modul, verzeichnis, 10)
        datei = verzeichnis / modul.DATEI
        # Der Anker geht durch die ECHTE Verankerung in die abgefangene
        # Journal-Datei; die CLI liest ihn ueber `journalctl` zurueck.
        env = attrappen.umgebung(PYTHONPATH=pruefling)
        vorher = len(attrappen.anker())
        lauf = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]);"
             "from daimon.hub.audit import Audit;"
             "print(Audit.oeffnen(sys.argv[2]).verankern())",
             str(pruefling), str(verzeichnis)],
            cwd=pruefling, env=env, text=True, capture_output=True, timeout=120)
        bericht.pruefe("K5", lauf.returncode == 0,
                       f"Verankerung fuer die CLI-Messung: {lauf.stdout[-400:]} "
                       f"{lauf.stderr[-400:]}")
        bericht.pruefe("K5", len(attrappen.anker()) > vorher,
                       f"Positivkontrolle: der Anker liegt im Journal: "
                       f"{attrappen.anker()[vorher:]!r}")

        def cli() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-m", "daimon.hub.audit", "--verify",
                 "--verzeichnis", str(verzeichnis)],
                cwd=pruefling, env=env, text=True, capture_output=True, timeout=120)

        gut = cli()
        bericht.pruefe("K5", gut.returncode == 0,
                       f"CLI --verify meldet die heile Kette mit Exit 0, ist "
                       f"{gut.returncode}: {gut.stdout[-600:]} {gut.stderr[-400:]}")

        def kaputt_machen() -> None:
            alle = zeilen(datei)
            satz = json.loads(alle[3])
            satz["initiator"] = "manipuliert"
            alle[3] = json.dumps(satz, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False)
            datei.write_text("\n".join(alle) + "\n", encoding="utf-8")

        manipulieren(datei, kaputt_machen, "CLI-Gegenprobe: eine Zeile geaendert")
        schlecht = cli()
        bericht.pruefe("K5", schlecht.returncode != 0,
                       f"CLI --verify meldet die gerissene Kette mit Exit != 0, "
                       f"ist {schlecht.returncode}: {schlecht.stdout[-600:]}")
        del buch
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K5", f"CLI-Pruefstelle fehlgeschlagen: {exc!r}")


def pruefe_k5_timer(pruefling: Path, bericht: Bericht) -> None:
    """Pruefstelle 2: der taegliche systemd-Timer."""
    try:
        dienst = pruefling / "config/systemd/daimon-audit-verify.service"
        timer = pruefling / "config/systemd/daimon-audit-verify.timer"
        bericht.pruefe("K5", dienst.is_file(), f"{dienst} existiert")
        bericht.pruefe("K5", timer.is_file(), f"{timer} existiert")
        if not (dienst.is_file() and timer.is_file()):
            return
        dienst_text = dienst.read_text(encoding="utf-8")
        timer_text = timer.read_text(encoding="utf-8")
        exec_zeilen = " ".join(
            z.strip() for z in dienst_text.splitlines()
            if z.strip().startswith("ExecStart") or z.strip().endswith("\\")
            or "daimon.hub.audit" in z or "--verify" in z)
        bericht.pruefe("K5", "daimon.hub.audit" in dienst_text,
                       f"der Timer-Dienst ruft das Audit auf: {exec_zeilen[:300]!r}")
        bericht.pruefe("K5", "--verify" in dienst_text,
                       f"der Timer-Dienst prueft (--verify): {exec_zeilen[:300]!r}")
        bericht.pruefe("K5", "daimon.mind" not in dienst_text,
                       "der Timer-Dienst laeuft nicht im Mind-Prozess")
        bericht.pruefe("K5", re.search(r"^OnCalendar\s*=\s*daily\s*$", timer_text,
                                       re.MULTILINE) is not None,
                       f"der Timer laeuft taeglich (OnCalendar=daily)")
        bericht.pruefe("K5", "[Install]" in timer_text,
                       "der Timer hat [Install] -- ohne das zieht ihn niemand")
        bericht.pruefe("K5", re.search(r"^Unit\s*=\s*daimon-audit-verify\.service",
                                       timer_text, re.MULTILINE) is not None,
                       "der Timer zieht genau diesen Dienst")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K5", f"Timer-Pruefstelle fehlgeschlagen: {exc!r}")


def pruefe_k5_kein_modelltext(pruefling: Path, bericht: Bericht) -> None:
    """"Keine davon in einem Prozess mit Modelltext" -- gemessen an sys.modules."""
    programm = ("import importlib, json, sys;"
                "sys.path.insert(0, sys.argv[1]);"
                "importlib.import_module(sys.argv[2]);"
                "print(json.dumps(sorted(m for m in sys.modules "
                "if m.startswith('daimon.mind'))))")
    try:
        def module(name: str) -> list[str]:
            lauf = subprocess.run([sys.executable, "-c", programm,
                                   str(pruefling), name],
                                  cwd=pruefling, text=True, capture_output=True,
                                  timeout=120)
            if lauf.returncode != 0:
                raise RuntimeError(f"{name}: {lauf.stderr[-600:]}")
            return json.loads(lauf.stdout)

        # Positivkontrolle ZUERST: der Detektor sieht Modelltext, wenn welcher
        # da ist. Ohne sie waere die leere Liste unten nicht auswertbar.
        mit = module("daimon.mind.router")
        bericht.pruefe("K5", bool(mit),
                       f"Positivkontrolle: der Detektor sieht Mind-Module: {mit!r}")
        ohne = module("daimon.hub.audit")
        bericht.pruefe("K5", ohne == [],
                       f"das Audit zieht keinen Modelltext in seinen Prozess: {ohne!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K5", f"Modelltext-Messung fehlgeschlagen: {exc!r}")


def pruefe_k5_hub(modul: ModuleType, pruefling: Path, arbeit: Path,
                  attrappen: Attrappen, bericht: Bericht) -> None:
    """Pruefstelle 1: der Hub beim Start, mit Abweichung als dringender Blase.

    Reihenfolge mit Absicht: erst die Messung, dann die Positivkontrolle. Eine
    Kontrolle, die vorher liefe, saesse als Blase im Zustand und faerbte die
    Messung gruen.
    """
    prozess = None
    try:
        wurzel = arbeit / "k5hub"
        laufzeit = wurzel / "rt"
        zustand = wurzel / "state"
        audit = zustand / "daimon" / "audit"
        audit.mkdir(parents=True)
        buch = kette_schreiben(modul, audit, 5)
        datei = audit / modul.DATEI
        gesammelt: list[str] = []
        buch.verankern(journal=gesammelt.append)

        def reissen() -> None:
            alle = zeilen(datei)
            satz = json.loads(alle[2])
            satz["outcome"] = "denied"
            alle[2] = json.dumps(satz, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False)
            datei.write_text("\n".join(alle) + "\n", encoding="utf-8")

        manipulieren(datei, reissen, "Kette vor dem Hub-Start gerissen")

        # Es muss etwas zu melden GEBEN, sonst waere die Blasenmessung unten
        # nicht auswertbar: eine fehlende Blase haette dann zwei moegliche
        # Ursachen, und die Messung koennte zwischen ihnen nicht trennen.
        anker = {z.split("head=", 1)[1].strip() for z in gesammelt if "head=" in z}
        vorbefund = modul.pruefe(audit, anker=anker)
        kettenfehler = [f for f in vorbefund.get("fehler", []) if "Anker" not in f]
        bericht.pruefe("K5", bool(kettenfehler),
                       f"Vorbedingung: die Kette, die der Hub gleich liest, ist "
                       f"nachweislich gerissen: {vorbefund!r}")

        laufzeit.mkdir(parents=True)
        env = attrappen.umgebung(XDG_STATE_HOME=zustand, XDG_RUNTIME_DIR=wurzel,
                                 PYTHONPATH=pruefling)
        vorher = len(attrappen.anker())
        prozess = Prozessgruppe(
            [sys.executable, "-m", "daimon.hub.daemon", "--runtime-dir", str(laufzeit)],
            pruefling, env)

        # Der Hub hat die Kette angefasst, sobald sein Anker liegt: `pruefe`
        # laeuft VOR `verankern` (daemon.audit_verankern). Fester Bezugspunkt.
        neue = warte_auf(lambda: attrappen.anker()[vorher:])
        bericht.pruefe("K5", bool(neue),
                       f"der Hub prueft und verankert beim Start: {neue!r}; "
                       f"Ausgabe {prozess.ausgabe()[-600:]}")

        zustand_jetzt = warte_auf(lambda: schnappschuss(laufzeit))
        bericht.pruefe("K5", zustand_jetzt is not None,
                       "der Hub beantwortet state.sock (Messapparat steht)")
        blase = (zustand_jetzt or {}).get("bubble")
        bericht.pruefe(
            "K5", isinstance(blase, dict) and blase.get("urgent") is True,
            f"der Hub meldet die gerissene Kette als dringende Blase "
            f"(Design 7.6: \"Bubble mit hoher Dringlichkeit\"); "
            f"bubble={blase!r}")

        # Positivkontrolle NACH der Messung: der Apparat kann eine dringende
        # Blase ueberhaupt sehen.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(laufzeit / "hookbridge.sock"))
            sock.sendall(json.dumps({
                "v": 1, "type": "hook",
                "payload": {"hook_event_name": "Notification",
                            "session_id": "t46-kontrolle",
                            "notification_type": "permission_prompt",
                            "message": "Positivkontrolle"},
            }).encode() + b"\n")
        kontrolle = warte_auf(
            lambda: (schnappschuss(laufzeit) or {}).get("bubble"), timeout_s=10.0)
        bericht.pruefe(
            "K5", isinstance(kontrolle, dict) and kontrolle.get("urgent") is True,
            f"Positivkontrolle: eine dringende Blase ist im Schnappschuss "
            f"sichtbar, wenn sie gesetzt wird: {kontrolle!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K5", f"Hub-Pruefstelle fehlgeschlagen: {exc!r}")
    finally:
        if prozess is not None:
            try:
                prozess.stop()
            except Exception as exc:  # noqa: BLE001
                bericht.fehler("K5", f"Hub-Aufraeumen: {exc!r}")


# -- K6 ---------------------------------------------------------------------

def pruefe_k6(modul: ModuleType, arbeit: Path, bericht: Bericht) -> None:
    try:
        verzeichnis = arbeit / "k6" / "audit"
        buch = modul.Audit.oeffnen(verzeichnis)

        satz = buch.schreiben(ts=1_700_000_000.0, **satz_felder())
        bericht.pruefe("K6", isinstance(satz, dict),
                       "Positivkontrolle: der vollstaendige Datensatz wird angenommen")
        geschrieben = json.loads(zeilen(verzeichnis / modul.DATEI)[0])
        for feld in PFLICHTFELDER:
            bericht.pruefe("K6", feld in geschrieben,
                           f"Feld {feld!r} steht im geschriebenen Datensatz: "
                           f"{sorted(geschrieben)!r}")

        # Jedes Pflichtfeld EINZELN: genau dieses eine fehlt, alle anderen da.
        for feld in PFLICHTFELDER:
            felder = satz_felder()
            felder.pop(feld)
            try:
                buch.schreiben(ts=1_700_000_001.0, **felder)
                bericht.fehler("K6", f"fehlendes Pflichtfeld {feld!r} wurde "
                                     f"angenommen statt abgewiesen")
            except modul.AuditFehler as exc:
                bericht.pruefe("K6", feld in str(exc),
                               f"Abweisung nennt das fehlende Feld {feld!r}: {exc}")
            except Exception as exc:  # noqa: BLE001
                bericht.fehler("K6", f"fehlendes {feld!r}: unerwarteter Fehler {exc!r}")

        # `outcome` ist eine endliche Menge, `unknown` gehoert ausdruecklich dazu.
        try:
            buch.schreiben(ts=1_700_000_002.0, **satz_felder(outcome="unknown"))
            bericht.pruefe("K6", True, "outcome='unknown' ist ein zulaessiger Ausgang")
        except Exception as exc:  # noqa: BLE001
            bericht.fehler("K6", f"outcome='unknown' wurde abgewiesen: {exc!r}")
        try:
            buch.schreiben(ts=1_700_000_003.0, **satz_felder(outcome="erfunden"))
            bericht.fehler("K6", "erfundener outcome wurde angenommen")
        except modul.AuditFehler:
            bericht.pruefe("K6", True, "erfundener outcome wird abgewiesen")
        except Exception as exc:  # noqa: BLE001
            bericht.fehler("K6", f"erfundener outcome: unerwarteter Fehler {exc!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K6", f"Pflichtfeldmessung fehlgeschlagen: {exc!r}")


# -- K7 ---------------------------------------------------------------------

def pruefe_k7(modul: ModuleType, arbeit: Path, bericht: Bericht) -> None:
    """Redaktion nach HERKUNFT: `tainted` genuegt, ein Katalogflag ist keine
    Bedingung. Das Feld heisst hier absichtlich so, dass kein Katalog es
    fuehren wuerde."""
    try:
        verzeichnis = arbeit / "k7" / "audit"
        buch = modul.Audit.oeffnen(verzeichnis)

        geheim = "BILDSCHIRMTEXT-4f2a9c-GEHEIM"
        harmlos = "HARMLOSER-WERT-91b7e3"
        vorschau = "VORSCHAUTEXT-77d1a0"
        buch.schreiben(
            ts=1_700_000_000.0, tainted=("fremder_bildschirm",),
            **satz_felder(prompt_shown=vorschau,
                          fremder_bildschirm=geheim,
                          notiz=harmlos))
        text = (verzeichnis / modul.DATEI).read_text(encoding="utf-8")

        # Positivkontrolle ZUERST: die Klartextsuche findet Klartext. Ohne
        # sie waere "geheim nicht gefunden" nicht von "gar nichts geschrieben"
        # zu unterscheiden.
        bericht.pruefe("K7", harmlos in text,
                       f"Positivkontrolle: ein nicht als tainted gemeldeter Wert "
                       f"steht im Klartext in der Datei -- die Suche greift")
        bericht.pruefe("K7", geheim not in text,
                       f"der tainted-Wert steht NICHT im Klartext in {modul.DATEI}")
        bericht.pruefe("K7", vorschau not in text,
                       "prompt_shown steht nicht im Klartext")

        satz = json.loads(zeilen(verzeichnis / modul.DATEI)[0])
        abdruck = satz.get("fremder_bildschirm", "")
        erwartet = "sha256:" + hashlib.sha256(geheim.encode("utf-8")).hexdigest()
        bericht.pruefe("K7", isinstance(abdruck, str) and abdruck.startswith("<redacted:"),
                       f"der tainted-Wert ist redigiert: {abdruck!r}")
        bericht.pruefe("K7", erwartet in str(abdruck),
                       f"die Redaktion traegt den sha256 des Originals: {abdruck!r}")
        bericht.pruefe("K7", f"len={len(geheim)}" in str(abdruck),
                       f"die Redaktion traegt die Laenge {len(geheim)}: {abdruck!r}")
        bericht.pruefe("K7", str(satz.get("prompt_shown", "")).startswith("<redacted:"),
                       f"prompt_shown ist immer redigiert: {satz.get('prompt_shown')!r}")
        bericht.pruefe("K7", satz.get("notiz") == harmlos,
                       f"ein nicht gemeldeter Wert bleibt unveraendert: "
                       f"{satz.get('notiz')!r}")

        # Das Katalogflag ist eine ZUSAETZLICHE Bedingung: derselbe Wert, jetzt
        # ausdruecklich als nicht-sensibel etikettiert, bleibt redigiert.
        buch.schreiben(
            ts=1_700_000_001.0, tainted=("fremder_bildschirm",),
            **satz_felder(fremder_bildschirm=geheim, sensitive=False,
                          katalog_flag="nicht_sensitive"))
        zweiter = json.loads(zeilen(verzeichnis / modul.DATEI)[1])
        bericht.pruefe(
            "K7", str(zweiter.get("fremder_bildschirm", "")).startswith("<redacted:"),
            f"auch mit sensitive=False bleibt der tainted-Wert redigiert: "
            f"{zweiter.get('fremder_bildschirm')!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K7", f"Redaktionsmessung fehlgeschlagen: {exc!r}")


# -- K8 ---------------------------------------------------------------------

def pruefe_k8(modul: ModuleType, arbeit: Path, bericht: Bericht) -> None:
    try:
        verzeichnis = arbeit / "k8" / "audit"
        buch = kette_schreiben(modul, verzeichnis, 5)
        datei = verzeichnis / modul.DATEI
        kopf_vorher = buch.kopf
        alt_summe = summe(datei)
        alte_zeilen = zeilen(datei)

        gesammelt: list[str] = []
        ziel = buch.rotieren(journal=gesammelt.append)

        bericht.pruefe("K8", Path(ziel).is_file(),
                       f"die alte Datei liegt unter {ziel}")
        bericht.pruefe("K8", summe(Path(ziel)) == alt_summe,
                       "die rotierte Datei ist byteweise die alte")
        bericht.pruefe("K8", datei.is_file(), f"{datei} beginnt neu")

        neue_zeilen = zeilen(datei)
        bericht.pruefe("K8", len(neue_zeilen) == 1,
                       f"die neue Datei beginnt mit genau einem Kopfsatz, "
                       f"{len(neue_zeilen)} Zeilen")
        kopfsatz = json.loads(neue_zeilen[0])
        bericht.pruefe("K8", kopfsatz.get("prev_hash") == kopf_vorher,
                       f"der Kopfsatz traegt den letzten Hash der alten Datei: "
                       f"prev_hash={kopfsatz.get('prev_hash')!r}, "
                       f"erwartet {kopf_vorher!r}")
        # Nachgerechnet, unabhaengig vom Pruefling.
        eigen = "sha256:" + hashlib.sha256(
            alte_zeilen[-1].encode("utf-8")).hexdigest()
        bericht.pruefe("K8", kopfsatz.get("prev_hash") == eigen,
                       f"der uebernommene Hash ist der sha256 der letzten alten "
                       f"Zeile: {kopfsatz.get('prev_hash')!r} vs {eigen!r}")
        bericht.pruefe("K8", kopfsatz.get("seq") == 5,
                       f"der Kopfsatz behaelt die Nummer, ist {kopfsatz.get('seq')!r}")
        bericht.pruefe("K8", kopfsatz.get("rotation_von") == Path(ziel).name,
                       f"der Kopfsatz nennt die alte Datei: "
                       f"{kopfsatz.get('rotation_von')!r}")

        # Die Kette laeuft nach der Rotation weiter, ohne Bruch.
        buch.schreiben(ts=1_700_000_900.0, **satz_felder(mark_id="nach-rotation"))
        anker = {z.split("head=", 1)[1].strip() for z in gesammelt if "head=" in z}
        buch.verankern(journal=gesammelt.append)
        anker |= {z.split("head=", 1)[1].strip() for z in gesammelt if "head=" in z}
        befund = modul.pruefe(verzeichnis, anker=anker)
        kettenfehler = [f for f in befund.get("fehler", []) if "Anker" not in f]
        bericht.pruefe("K8", not kettenfehler,
                       f"die Kette nach der Rotation ist stimmig: {kettenfehler!r}")

        modus = (verzeichnis / modul.DATEI).stat().st_mode & 0o777
        bericht.pruefe("K8", modus == 0o600,
                       f"auch die neue Datei ist 0600, ist {oct(modus)}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K8", f"Rotationsmessung fehlgeschlagen: {exc!r}")


# -- Rahmen -----------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pruefling", type=Path)
    args = parser.parse_args(argv)
    pruefling = args.pruefling.resolve()
    bericht = Bericht()

    print(f"Pruefling: {pruefling}")
    print(f"Mutanten-Zuordnung: {json.dumps(MUTANTEN_GRENZEN, ensure_ascii=False)}")

    with tempfile.TemporaryDirectory(prefix="t46-") as tmp:
        arbeit = Path(tmp)
        attrappen = Attrappen(arbeit / "attrappen")
        attrappen.kontrolle(bericht, "K3")

        try:
            modul = lade_audit_modul(pruefling)
        except Exception as exc:  # noqa: BLE001
            for kriterium in KRITERIEN:
                bericht.fehler(kriterium, f"daimon.hub.audit nicht ladbar: {exc!r}")
            return bericht.bilanz()

        pruefe_k1(modul, pruefling, arbeit, attrappen, bericht)
        pruefe_k2(modul, arbeit, bericht)
        pruefe_k3(modul, pruefling, arbeit, attrappen, bericht)
        pruefe_k4(modul, arbeit, bericht)
        pruefe_k5_cli(modul, pruefling, arbeit, attrappen, bericht)
        pruefe_k5_timer(pruefling, bericht)
        pruefe_k5_kein_modelltext(pruefling, bericht)
        pruefe_k5_hub(modul, pruefling, arbeit, attrappen, bericht)
        pruefe_k6(modul, arbeit, bericht)
        pruefe_k7(modul, arbeit, bericht)
        pruefe_k8(modul, arbeit, bericht)

    return bericht.bilanz()


if __name__ == "__main__":
    raise SystemExit(main())
