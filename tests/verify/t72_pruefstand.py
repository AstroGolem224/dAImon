#!/usr/bin/env python3
"""Pruefstand fuer T-7.2 -- Redaktion VOR dem Schreiben.

Geprueft wird die AKZEPTANZLISTE von T-7.2 (Implementierungsplan Z. 1886 ff.)
und der Verifikationsabsatz ab Z. 1908, Kriterium fuer Kriterium, ohne
`&&`-Verkettung. Jedes Kriterium rechnet einzeln ab; ein rotes Kriterium
verhindert nicht die Messung der uebrigen. Ein Kriterium OHNE Messung zaehlt
in der Bilanz als rot.

  K1  Die Redaktion laeuft VOR dem Schreiben, nicht als Nachbearbeitung
  K2  Anwendungs-Denylist: gar nicht erfasst -- auch nicht in Zwischendatei,
      Log oder Temp-Verzeichnis
  K3  Zuordnung ueber die `.desktop`-Kennung, nicht ueber den Fenstertitel
  K4  DRM-Pruefung nach Design 4.4 greift zusaetzlich
  K5  Zeitlich begrenzter Privatmodus schreibt nichts
  K6  Abgeschaltete Bildschirmwahrnehmung faellt auf `transient` (7.2d)
  K7  Es gibt keinen zweiten Weg ins Archiv
  K8  Die Pruefung VOR DEM DIFF urteilt wie die vor dem Schreiben

DREI REGELN DIESES REPOS SCHLAGEN HIER DURCH:

**Kanarienvogel plus Positivkontrolle.** Jede Negativmessung ("die Zeichenkette
steht nirgends") hat ihre Positivkontrolle mit DERSELBEN Zeichenkette aus einer
NICHT gelisteten Anwendung. Ohne sie waere "nicht gefunden" auch dann gruen,
wenn die Erfassung gar nicht lief -- der Fehler, der dem Builder am 17.08.
viermal an einem Tag passiert ist.

**Jede Manipulation wird gewogen.** Wo dieser Pruefstand etwas veraendert, um
zu sehen, ob es auffaellt, vergleicht `manipulieren()` die sha256 vorher und
nachher und faellt laut, wenn sie gleich ist.

**Eine Messung ist ein Zeitpunkt, kein Zeitfenster.** Es gibt hier kein
`--since`. Der Bezugspunkt jeder Messung ist die ANTWORT des Dienstes auf die
Zeile, die er gerade bearbeitet hat -- das Archiv steht im Autocommit, nach der
Antwort ist geschrieben oder eben nicht. Gemessen wird daran ein fester Stand:
Zeilenzahl, `sqlite_sequence`, Dateigroesse, `st_mtime_ns` und sha256 von
`archiv.db` und `archiv.db-wal`, dazu ein Byte-Streifzug ueber den GANZEN
Arbeitsbaum des Laufs und ueber die Prozessausgabe des Dienstes.

WAS `sqlite_sequence` HIER LEISTET: die Tabelle traegt `AUTOINCREMENT`. Eine
Zeile, die geschrieben und sofort wieder geloescht wuerde -- also eine
Redaktion, die NACH dem Schreiben liefe --, laesst den Zaehler stehen, wo sie
war. Ein unveraenderter Zaehler ist damit der Beleg, dass es die Zeile nie
gegeben hat, und nicht nur, dass sie jetzt weg ist.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Sequence

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8")

# Anhang D kennt T-7.2.v nicht (dort stehen 36 Verifizierer, die Phase 7 ist
# nicht darunter). Die Mutanten sind deshalb hier gesetzt, jeder an genau ein
# Akzeptanzkriterium gebunden.
MUTANTEN_GRENZEN = {
    "denylist-greift-nicht": "K2 (dazu K1/K3/K8 -- jede Sperre ueber die Liste)",
    "kennung-ueber-rohklasse": "K3 (und die Vorbedingung von K8)",
    "unbekanntes-fenster-durchgelassen": "K3",
    "drm-ignoriert": "K4",
    "privatmodus-ohne-wirkung": "K5",
    "wahrnehmung-egal": "K6",
    "rohkopie-vor-der-redaktion": "K1/K2/K7 (und jede andere Sperre)",
    "art-nicht-an-unit-gebunden": "K7",
}

# Der Kanarienvogel. DIESELBE Zeichenkette fuer die Negativmessung und ihre
# Positivkontrolle -- so verlangt es der Verifikationsabsatz.
KANARI = "KANARIENVOGEL-T72-4d1f7a29-GEHEIM"
KANARI_KENNUNG = "KANARIENVOGEL-T72-kennung-8b31e0"
KANARI_TITEL_REIN = "KANARIENVOGEL-T72-titel-rein-1c7d44"
KANARI_TITEL_RAUS = "KANARIENVOGEL-T72-titel-raus-5ea092"
KANARI_LEER = "KANARIENVOGEL-T72-ohne-klasse-90fb13"
KANARI_DRM = "KANARIENVOGEL-T72-drm-3a5c81"
KANARI_PRIVAT = "KANARIENVOGEL-T72-privat-77aa25"
KANARI_AUGEN = "KANARIENVOGEL-T72-augen-6d0e94"
KANARI_ART = "KANARIENVOGEL-T72-art-2f81b6"

# Die Klassen. `keepassxc` steht woertlich in config/redaktion.yaml,
# `tarnkappe` NUR ueber die `.desktop`-Datei unten, `harmlos-app` nirgends.
KLASSE_GELISTET = "keepassxc"
KLASSE_TARNKAPPE = "tarnkappe"
KLASSE_TARNKAPPE_FREMD = "tarnkappe-fremd"
KLASSE_HARMLOS = "harmlos-app"

EYES = "daimon-eyes.service"
FOCUS = "daimon-focus.service"
EARS = "daimon-ears.service"

BEOBACHTUNG_S = 25.0

# `redaktion.WAHRNEHMUNG_CACHE_S` = 5,0 s und `pause.HERZSCHLAG_FRIST_S` =
# 5,0 s. Wer den Zustand der Wahrnehmung umschaltet, muss beide ueberholen --
# sonst misst er den zwischengespeicherten Stand von vorhin.
WAHRNEHMUNG_WARTEN_S = 5.4

DESKTOP_GELISTET = """[Desktop Entry]
Type=Application
Name=Tresor mit anderer Fensterklasse
Exec=/bin/true
StartupWMClass=tarnkappe
"""

DESKTOP_HARMLOS = """[Desktop Entry]
Type=Application
Name=Harmlos
Exec=/bin/true
StartupWMClass=harmlos-app
"""


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
        print("\nBilanz T-7.2:")
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
    try:
        return hashlib.sha256(pfad.read_bytes()).hexdigest()
    except OSError:
        return "-"


def manipulieren(pfad: Path, aenderung, beschreibung: str) -> None:
    """Eine Manipulation, die nachweislich Bytes bewegt."""
    vorher = summe(pfad)
    aenderung()
    nachher = summe(pfad)
    if vorher == nachher:
        raise RuntimeError(
            f"POSITIVKONTROLLE GESCHEITERT: '{beschreibung}' hat {pfad} nicht "
            f"veraendert (sha256 vorher {vorher} == nachher {nachher}). Der "
            f"folgende Befund waere nicht gemessen, sondern erfunden.")
    print(f"Manipulation '{beschreibung}': sha256 {vorher[:12]} -> {nachher[:12]}")


def startzeit(pid: int) -> str | None:
    lauf = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    wert = lauf.stdout.strip()
    return wert if lauf.returncode == 0 and wert else None


class Prozessgruppe:
    """Eigene Sitzung, mit Nachweis, dass die PID beim Toeten noch dieselbe
    ist."""

    def __init__(self, befehl: Sequence[str], cwd: Path,
                 env: dict[str, str]) -> None:
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
        return self.log.read()

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


def warte_auf(bedingung, timeout_s: float = BEOBACHTUNG_S):
    ende = time.monotonic() + timeout_s
    letzter = None
    while time.monotonic() < ende:
        letzter = bedingung()
        if letzter:
            return letzter
        time.sleep(0.1)
    return letzter


def lade_modul(pruefling: Path, name: str) -> ModuleType:
    """Ein Modul AUS DEM PRUEFLING, nicht aus dem Arbeitsbaum."""
    if str(pruefling) not in sys.path:
        sys.path.insert(0, str(pruefling))
    importlib.invalidate_caches()
    modul = importlib.import_module(name)
    quelle = Path(modul.__file__ or "").resolve()
    try:
        quelle.relative_to(pruefling.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Import entkam dem Pruefling: {quelle}") from exc
    return modul


# -- Der Arbeitsplatz eines Laufs -------------------------------------------

class Werkbank:
    """Ein vollstaendig eigener XDG-Baum, ein echter Dienst darin.

    ALLES, was der Dienst schreiben koennte, liegt unter `self.lauf`: sein
    Laufzeitverzeichnis, sein Zustand, seine Zwischenablage, sein `TMPDIR`,
    seine Datenbank. Der Streifzug am Ende geht ueber genau diesen Baum -- ein
    Kanarienvogel in einer Zwischendatei, einem Log oder einem Temp-Verzeichnis
    faellt damit auf.
    """

    def __init__(self, pruefling: Path, wurzel: Path) -> None:
        self.pruefling = pruefling
        self.lauf = wurzel / "lauf"
        self.runtime = self.lauf / "runtime"
        self.archiv = self.lauf / "archiv" / "archiv.db"
        self.anwendungen = self.lauf / "data" / "applications"
        for pfad in (self.runtime, self.archiv.parent, self.anwendungen,
                     self.lauf / "state", self.lauf / "config",
                     self.lauf / "cache", self.lauf / "tmp",
                     self.lauf / "leer", self.lauf / "heim"):
            pfad.mkdir(parents=True, exist_ok=True)
        (self.anwendungen / "org.keepassxc.KeePassXC.desktop").write_text(
            DESKTOP_GELISTET, encoding="utf-8")
        (self.anwendungen / "harmlos-app.desktop").write_text(
            DESKTOP_HARMLOS, encoding="utf-8")
        self.dienst: Prozessgruppe | None = None
        self.melder: ModuleType | None = None
        self.pause: ModuleType | None = None
        # Die Augen "sehen", solange nichts anderes gesagt ist. Ihr
        # Lebenszeichen verfaellt nach `pause.HERZSCHLAG_FRIST_S` = 5 s; ohne
        # Auffrischen vor JEDER Sonde faerbte irgendwann der Ablauf einer Uhr
        # die Messung und nicht die Redaktion.
        self.wahrnehmung = True

    # -- Umgebung ----------------------------------------------------------

    def umgebung(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "HOME": str(self.lauf / "heim"),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "XDG_STATE_HOME": str(self.lauf / "state"),
            "XDG_CONFIG_HOME": str(self.lauf / "config"),
            "XDG_CACHE_HOME": str(self.lauf / "cache"),
            "XDG_DATA_HOME": str(self.lauf / "data"),
            "XDG_DATA_DIRS": str(self.lauf / "leer"),
            "TMPDIR": str(self.lauf / "tmp"),
            "PYTHONPATH": str(self.pruefling),
            # Der Fokusdienst dieser Maschine darf den Lauf nicht faerben.
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.lauf}/kein-dbus",
        })
        return env

    # -- Der Dienst --------------------------------------------------------

    def unit_setzen(self, unit: str) -> None:
        (self.runtime / "t72_unit").write_text(unit + "\n", encoding="utf-8")

    def lebenszeichen(self) -> None:
        """Das Lebenszeichen der Augen auffrischen -- mit ihrer eigenen
        Vorrichtung, nicht mit einer nachgebauten Datei."""
        assert self.pause is not None
        if self.wahrnehmung:
            self.pause.herzschlag(self.runtime,
                                  datei=self.pause.WAHRNEHMUNG_DATEI)

    def starten(self, treiber: Path) -> None:
        self.unit_setzen(EYES)
        self.dienst = Prozessgruppe(
            [sys.executable, str(treiber), str(self.pruefling),
             str(self.runtime), str(self.archiv)],
            self.pruefling, self.umgebung())

    def bereit(self) -> dict | None:
        """Antwortet der Dienst? Mit einer Zeile, die nichts ablegen kann.

        Der Typ wird vor allem anderen geprueft (`ipc.pruefe_typ`), diese Zeile
        kommt also nie bis zur Redaktion -- sie ist der Beleg, dass Socket und
        Schleife stehen, und sonst nichts.
        """
        if not (self.runtime / "recorder.sock").exists():
            return None
        antwort = self.senden({"v": 1, "typ": "kein-echter-typ"})
        return antwort if antwort.get("grund") == "typ" else None

    def stoppen(self) -> None:
        if self.dienst is not None:
            self.dienst.stop()
            self.dienst = None

    # -- Melden ------------------------------------------------------------

    def senden(self, nachricht: dict) -> dict:
        assert self.melder is not None
        return self.melder.senden(self.runtime, nachricht, timeout_s=10.0)

    def melde_ocr(self, text: str, *, klasse: str, drm: bool = False) -> dict:
        assert self.melder is not None
        return self.melder.melde_ocr(self.runtime, text, klasse=klasse,
                                     drm=drm, timeout_s=10.0)

    def melde_titel(self, text: str, *, klasse: str) -> dict:
        assert self.melder is not None
        return self.melder.melde_titel(self.runtime, text, klasse=klasse,
                                       timeout_s=10.0)

    # -- Messen ------------------------------------------------------------

    def dateizustand(self) -> dict[str, tuple]:
        """Der feste Bezugspunkt: Groesse, `st_mtime_ns` und sha256.

        `-shm` bleibt draussen: das ist der gemeinsame Index von WAL, den auch
        ein LESER anfasst. Was ein Schreibvorgang bewegt, steht in `-wal` und
        spaetestens beim Checkpoint in der Datenbank selbst.
        """
        stand: dict[str, tuple] = {}
        for endung in ("", "-wal"):
            pfad = Path(str(self.archiv) + endung)
            try:
                s = pfad.stat()
                stand[endung or "db"] = (True, s.st_size, s.st_mtime_ns,
                                         summe(pfad))
            except OSError:
                stand[endung or "db"] = (False, 0, 0, "-")
        return stand

    def db_stand(self) -> tuple[int, int]:
        """(Zeilenzahl, `sqlite_sequence`). Lesend, ueber eine eigene
        Verbindung."""
        if not self.archiv.exists():
            return (0, 0)
        db = sqlite3.connect(f"file:{self.archiv}?mode=ro", uri=True)
        try:
            zeilen = int(db.execute("SELECT COUNT(*) FROM archiv").fetchone()[0])
            zeile = db.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='archiv'").fetchone()
            return zeilen, int(zeile[0]) if zeile else 0
        finally:
            db.close()

    def texte(self) -> list[str]:
        if not self.archiv.exists():
            return []
        db = sqlite3.connect(f"file:{self.archiv}?mode=ro", uri=True)
        try:
            return [str(z[0]) for z in db.execute(
                "SELECT text FROM archiv ORDER BY id").fetchall()]
        finally:
            db.close()

    def streifzug(self, nadel: str) -> list[str]:
        """Jede Datei des Laufs plus die Prozessausgabe, byteweise."""
        treffer: list[str] = []
        roh = nadel.encode("utf-8")
        for pfad in sorted(self.lauf.rglob("*")):
            if pfad.is_symlink() or not pfad.is_file():
                continue
            try:
                daten = pfad.read_bytes()
            except OSError:
                continue
            if roh in daten:
                treffer.append(str(pfad.relative_to(self.lauf)))
        if self.dienst is not None and nadel in self.dienst.ausgabe():
            treffer.append("<ausgabe des dienstes>")
        return treffer


# -- Eine Sonde -------------------------------------------------------------

@dataclass
class Sonde:
    antwort: dict
    zustand_gleich: bool
    zeilen_vor: int
    zeilen_nach: int
    seq_vor: int
    seq_nach: int
    treffer: list[str]
    vorher: dict
    nachher: dict


def sonde(werk: Werkbank, nadel: str, senden) -> Sonde:
    """Eine Meldung, gemessen an einem festen Stand vorher und nachher."""
    werk.lebenszeichen()
    zeilen_vor, seq_vor = werk.db_stand()
    vorher = werk.dateizustand()
    antwort = senden()
    nachher = werk.dateizustand()
    zeilen_nach, seq_nach = werk.db_stand()
    return Sonde(antwort=antwort, zustand_gleich=(vorher == nachher),
                 zeilen_vor=zeilen_vor, zeilen_nach=zeilen_nach,
                 seq_vor=seq_vor, seq_nach=seq_nach,
                 treffer=werk.streifzug(nadel), vorher=vorher, nachher=nachher)


def gesperrt(bericht: Bericht, kriterium: str, name: str, s: Sonde,
             *, grund: str | None = None) -> None:
    """Was fuer JEDE Sperre gelten muss -- ohne Selbstauskunft als Beleg."""
    bericht.pruefe(kriterium, s.treffer == [],
                   f"{name}: der Kanarienvogel steht NIRGENDS im Baum des "
                   f"Laufs (Datenbank, WAL, Zwischendatei, Log, Temp): "
                   f"{s.treffer!r}")
    bericht.pruefe(kriterium, s.zeilen_nach == s.zeilen_vor,
                   f"{name}: Zeilenzahl unveraendert "
                   f"({s.zeilen_vor} -> {s.zeilen_nach})")
    bericht.pruefe(kriterium, s.zustand_gleich,
                   f"{name}: Groesse, Zeitstempel und sha256 von archiv.db und "
                   f"-wal unveraendert: {s.vorher!r} -> {s.nachher!r}")
    if grund is not None:
        bericht.pruefe(kriterium, s.antwort.get("grund") == grund,
                       f"{name}: der Dienst nennt den Grund {grund!r} "
                       f"(Diagnose, nicht Beleg): {s.antwort!r}")


def angekommen(bericht: Bericht, kriterium: str, name: str, s: Sonde) -> None:
    """Die Positivkontrolle: derselbe Weg, und diesmal MUSS es ankommen."""
    bericht.pruefe(kriterium, int(s.antwort.get("id") or 0) > 0,
                   f"{name}: der Dienst legt ab und nennt eine id: {s.antwort!r}")
    bericht.pruefe(kriterium, s.zeilen_nach == s.zeilen_vor + 1,
                   f"{name}: genau eine Zeile mehr "
                   f"({s.zeilen_vor} -> {s.zeilen_nach})")
    bericht.pruefe(kriterium, s.seq_nach == s.seq_vor + 1,
                   f"{name}: sqlite_sequence ist um eins gewachsen "
                   f"({s.seq_vor} -> {s.seq_nach})")
    bericht.pruefe(kriterium, any("archiv.db" in t for t in s.treffer),
                   f"{name}: der Streifzug findet die Zeichenkette in der "
                   f"Datenbank -- die Suche greift ueberhaupt: {s.treffer!r}")


# -- K1 / K2 ----------------------------------------------------------------

def pruefe_k1_k2(werk: Werkbank, bericht: Bericht) -> None:
    """Der Kanarienvogel, in beide Richtungen, mit DERSELBEN Zeichenkette."""
    try:
        werk.unit_setzen(EYES)

        s = sonde(werk, KANARI,
                  lambda: werk.melde_ocr(KANARI, klasse=KLASSE_GELISTET))
        gesperrt(bericht, "K2", "gelistete Anwendung", s, grund="denylist")
        # K1: nicht "wieder weg", sondern "nie dagewesen".
        bericht.pruefe("K1", s.seq_nach == s.seq_vor,
                       f"gelistete Anwendung: sqlite_sequence steht still "
                       f"({s.seq_vor} -> {s.seq_nach}) -- es hat nie eine Zeile "
                       f"gegeben, die man haette nachtraeglich loeschen koennen")

        # Positivkontrolle, DIESELBE Zeichenkette, nicht gelistete Anwendung.
        p = sonde(werk, KANARI,
                  lambda: werk.melde_ocr(KANARI, klasse=KLASSE_HARMLOS))
        angekommen(bericht, "K2", "Positivkontrolle nicht gelistete Anwendung", p)
        bericht.pruefe("K1", p.seq_nach == p.seq_vor + 1,
                       f"Positivkontrolle: sqlite_sequence laeuft, wenn wirklich "
                       f"geschrieben wird ({p.seq_vor} -> {p.seq_nach}) -- der "
                       f"Zaehler oben stand still, weil nichts geschrieben wurde")
        bericht.pruefe("K2", KANARI in werk.texte(),
                       "Positivkontrolle: die Zeichenkette steht als Text in der "
                       "Tabelle")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K1", f"Kanarienmessung fehlgeschlagen: {exc!r}")
        bericht.fehler("K2", f"Kanarienmessung fehlgeschlagen: {exc!r}")


# -- K3 ---------------------------------------------------------------------

def pruefe_k3(werk: Werkbank, bericht: Bericht) -> None:
    """Die Kennung entscheidet, nicht der Titel -- in beide Richtungen."""
    try:
        werk.unit_setzen(EYES)

        # (a) Die Klasse steht in KEINER Liste; nur die `.desktop`-Datei
        #     bindet sie an eine gelistete Kennung.
        s = sonde(werk, KANARI_KENNUNG,
                  lambda: werk.melde_ocr(KANARI_KENNUNG,
                                         klasse=KLASSE_TARNKAPPE))
        gesperrt(bericht, "K3", "Klasse nur ueber StartupWMClass gelistet", s,
                 grund="denylist")

        # Positivkontrolle: eine Klasse, die dieselbe Form hat und NICHT in der
        # `.desktop`-Karte steht, kommt an. Damit ist belegt, dass die Sperre
        # oben aus der Karte kam und nicht aus der Zeichenkette.
        p = sonde(werk, KANARI_KENNUNG,
                  lambda: werk.melde_ocr(KANARI_KENNUNG,
                                         klasse=KLASSE_TARNKAPPE_FREMD))
        angekommen(bericht, "K3",
                   "Positivkontrolle: aehnliche Klasse ohne .desktop-Eintrag", p)

        # Der Fenstertitel gehoert dem Fokusdienst -- die Augen bekommen den
        # `caption` absichtlich nicht (T-7.1b). Also spricht hier der, der ihn
        # hat.
        werk.unit_setzen(FOCUS)

        # (b) Ein gelistetes Programm luegt sich mit fremdem Titel HINEIN.
        s = sonde(werk, KANARI_TITEL_REIN,
                  lambda: werk.melde_titel(
                      f"Firefox -- {KANARI_TITEL_REIN}",
                      klasse=KLASSE_GELISTET))
        gesperrt(bericht, "K3", "gelistetes Programm mit fremdem Titel", s,
                 grund="denylist")

        # (c) Ein harmloses Programm luegt sich mit fremdem Titel HERAUS.
        p = sonde(werk, KANARI_TITEL_RAUS,
                  lambda: werk.melde_titel(
                      f"org.keepassxc.KeePassXC -- {KANARI_TITEL_RAUS}",
                      klasse=KLASSE_HARMLOS))
        angekommen(bericht, "K3",
                   "harmloses Programm mit gelistetem Namen im Titel", p)

        # (d) Ohne Kennung wird gesperrt, nicht durchgelassen.
        werk.unit_setzen(EYES)
        s = sonde(werk, KANARI_LEER,
                  lambda: werk.senden({"v": 1, "typ": "archiv", "art": "ocr",
                                       "text": KANARI_LEER, "klasse": ""}))
        gesperrt(bericht, "K3", "Fenster ohne Kennung", s, grund="kennung_fehlt")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K3", f"Kennungsmessung fehlgeschlagen: {exc!r}")


# -- K4 ---------------------------------------------------------------------

def pruefe_k4(werk: Werkbank, bericht: Bericht) -> None:
    """DRM greift ZUSAETZLICH -- also auch bei nicht gelisteter Anwendung."""
    try:
        werk.unit_setzen(EYES)
        s = sonde(werk, KANARI_DRM,
                  lambda: werk.melde_ocr(KANARI_DRM, klasse=KLASSE_HARMLOS,
                                         drm=True))
        gesperrt(bericht, "K4", "nicht gelistete Anwendung mit drm=True", s,
                 grund="drm")

        p = sonde(werk, KANARI_DRM,
                  lambda: werk.melde_ocr(KANARI_DRM, klasse=KLASSE_HARMLOS,
                                         drm=False))
        angekommen(bericht, "K4",
                   "Positivkontrolle: dieselbe Anwendung ohne DRM", p)
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K4", f"DRM-Messung fehlgeschlagen: {exc!r}")


# -- K5 ---------------------------------------------------------------------

def pruefe_k5(werk: Werkbank, redaktion: ModuleType, bericht: Bericht) -> None:
    """Privatmodus: schreibt nichts, und er ist zeitlich begrenzt."""
    try:
        werk.unit_setzen(EYES)
        ablauf = redaktion.privat_setzen(werk.runtime, 300.0)
        datei = werk.runtime / redaktion.PRIVAT_DATEI
        bericht.pruefe("K5", datei.is_file(),
                       f"der Privatmodus liegt im Laufzeitverzeichnis: {datei}")
        modus = datei.stat().st_mode & 0o777
        bericht.pruefe("K5", modus == 0o600,
                       f"die Datei des Privatmodus ist 0600, ist {oct(modus)}")
        bericht.pruefe("K5", redaktion.privat_bis(werk.runtime) == ablauf,
                       f"der Prueferling liest seinen eigenen Ablauf zurueck: "
                       f"{redaktion.privat_bis(werk.runtime)!r} vs {ablauf!r}")

        s = sonde(werk, KANARI_PRIVAT,
                  lambda: werk.melde_ocr(KANARI_PRIVAT, klasse=KLASSE_HARMLOS))
        gesperrt(bericht, "K5", "Privatmodus an", s, grund="privatmodus")

        # ZEITLICH BEGRENZT: ein abgelaufener Privatmodus sperrt nicht mehr.
        # Das ist zugleich die Positivkontrolle -- dieselbe Zeichenkette,
        # dieselbe Anwendung, nur der Modus ist vorbei.
        redaktion.privat_setzen(werk.runtime, 0.0)
        time.sleep(0.05)
        p = sonde(werk, KANARI_PRIVAT,
                  lambda: werk.melde_ocr(KANARI_PRIVAT, klasse=KLASSE_HARMLOS))
        angekommen(bericht, "K5",
                   "Positivkontrolle: nach Ablauf wird wieder geschrieben", p)
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K5", f"Privatmodus-Messung fehlgeschlagen: {exc!r}")


# -- K6 ---------------------------------------------------------------------

def pruefe_k6(werk: Werkbank, pause: ModuleType, bericht: Bericht) -> None:
    """Abgeschaltete Bildschirmwahrnehmung -> `transient` (Design 7.2d).

    Gemessen am Lebenszeichen der AUGEN, das der Dienst selbst auswertet --
    nicht an einer Selbstauskunft und nicht an `systemctl`.
    """
    try:
        werk.unit_setzen(EYES)
        werk.wahrnehmung = False
        pause.herzschlag_loeschen(werk.runtime, datei=pause.WAHRNEHMUNG_DATEI)
        bericht.pruefe("K6",
                       not (werk.runtime / pause.WAHRNEHMUNG_DATEI).exists(),
                       "das Lebenszeichen der Augen ist weg")
        time.sleep(WAHRNEHMUNG_WARTEN_S)

        s = sonde(werk, KANARI_AUGEN,
                  lambda: werk.melde_ocr(KANARI_AUGEN, klasse=KLASSE_HARMLOS))
        gesperrt(bericht, "K6", "Wahrnehmung aus", s, grund="wahrnehmung_aus")

        # Zurueck auf "die Augen sehen": das Lebenszeichen muss WAEHREND der
        # ganzen Wartezeit frisch bleiben (Frist 5 s), und der Zwischenspeicher
        # der Redaktion (ebenfalls 5 s) muss ueberholt werden.
        werk.wahrnehmung = True
        ende = time.monotonic() + WAHRNEHMUNG_WARTEN_S
        while time.monotonic() < ende:
            werk.lebenszeichen()
            time.sleep(0.25)
        p = sonde(werk, KANARI_AUGEN,
                  lambda: werk.melde_ocr(KANARI_AUGEN, klasse=KLASSE_HARMLOS))
        angekommen(bericht, "K6",
                   "Positivkontrolle: mit frischem Lebenszeichen wieder "
                   "geschrieben", p)
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K6", f"Wahrnehmungsmessung fehlgeschlagen: {exc!r}")


# -- K7 ---------------------------------------------------------------------

def pruefe_k7_quelle(pruefling: Path, bericht: Bericht) -> None:
    """Gibt es einen zweiten Weg ins Archiv? Gesucht wird JEDER Schreiber."""
    try:
        quellen = sorted(p for p in (pruefling / "daimon").rglob("*.py")
                         if "__pycache__" not in p.parts)
        bericht.pruefe("K7", len(quellen) > 5,
                       f"Positivkontrolle: die Quellensuche findet ueberhaupt "
                       f"Dateien ({len(quellen)})")

        schreiber: list[str] = []
        inserts: list[str] = []
        for pfad in quellen:
            text = pfad.read_text(encoding="utf-8", errors="replace")
            rel = str(pfad.relative_to(pruefling))
            for nummer, zeile in enumerate(text.splitlines(), start=1):
                if zeile.lstrip().startswith("#"):
                    continue
                if "archiv.schreiben(" in zeile:
                    schreiber.append(f"{rel}:{nummer}")
                if "INSERT INTO archiv " in zeile:
                    inserts.append(f"{rel}:{nummer}")

        bericht.pruefe("K7", len(schreiber) == 1,
                       f"genau EIN Aufruf von Archiv.schreiben im Produktbaum: "
                       f"{schreiber!r}")
        bericht.pruefe("K7", bool(schreiber) and schreiber[0].startswith(
            "daimon/recorder/daemon.py"),
                       f"und er sitzt im Archivdienst hinter der Redaktion: "
                       f"{schreiber!r}")
        bericht.pruefe("K7", all(i.startswith("daimon/recorder/store.py")
                                 for i in inserts) and bool(inserts),
                       f"jedes INSERT in die Tabelle steht im Archivmodul "
                       f"selbst: {inserts!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K7", f"Quellensuche fehlgeschlagen: {exc!r}")


def pruefe_k7_art(werk: Werkbank, bericht: Bericht) -> None:
    """Die Art gehoert zum Absender.

    `transkript` fuehrt auf `urteil_ton()`, und das kennt weder Denylist noch
    DRM. Duerfte der Augendienst seine Meldung als `transkript` deklarieren,
    ginge jeder Bildschirmtext an der Fensterpruefung vorbei -- der zweite Weg
    ins Archiv, ohne eine zweite Zeile Code.
    """
    try:
        werk.unit_setzen(EYES)
        s = sonde(werk, KANARI_ART,
                  lambda: werk.senden({"v": 1, "typ": "archiv",
                                       "art": "transkript",
                                       "text": KANARI_ART,
                                       "klasse": KLASSE_GELISTET}))
        gesperrt(bericht, "K7", "Augendienst deklariert OCR als Transkript", s,
                 grund="art_nicht_erlaubt")

        werk.unit_setzen(FOCUS)
        t = sonde(werk, KANARI_ART,
                  lambda: werk.senden({"v": 1, "typ": "archiv", "art": "ocr",
                                       "text": KANARI_ART,
                                       "klasse": KLASSE_HARMLOS}))
        gesperrt(bericht, "K7", "Fokusdienst meldet OCR", t,
                 grund="art_nicht_erlaubt")

        # Positivkontrolle: derselbe Satz aus der Unit, der die Art gehoert,
        # kommt an. Ohne sie waere "abgewiesen" nicht von "Transport kaputt"
        # zu unterscheiden.
        werk.unit_setzen(EARS)
        p = sonde(werk, KANARI_ART,
                  lambda: werk.senden({"v": 1, "typ": "archiv",
                                       "art": "transkript",
                                       "text": KANARI_ART}))
        angekommen(bericht, "K7",
                   "Positivkontrolle: der Ohren-Dienst darf das Transkript", p)
        werk.unit_setzen(EYES)
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K7", f"Art-Messung fehlgeschlagen: {exc!r}")


# -- K8 ---------------------------------------------------------------------

def pruefe_k8(pruefling: Path, arbeit: Path, bericht: Bericht) -> None:
    """Eine Regel, nicht zwei.

    Akzeptanz T-7.2, zweiter Punkt: die Anwendungen der Denylist werden „gar
    nicht erfasst", und die Pruefung sitzt „vor dem Diff UND vor dem
    Schreiben". Dritter Punkt: die Zuordnung geht ueber die
    `.desktop`-Kennung. Beides zusammen heisst: was die Redaktion ueber die
    Kennung sperrt, muss auch die Gatterkette vor dem Diff sperren -- sonst
    wird der Bildschirm eines Passwortmanagers gelesen, in den
    Quarantaene-Kontextspeicher gelegt und erst am Archiv gestoppt.

    Gemessen an den echten Vorrichtungen des Prueflings: `eyes.change.Kette`
    (vor dem Diff), `eyes.context.Kontextspeicher` (der Live-Kontext) und
    `recorder.redaktion.Redaktion` (vor dem Schreiben). Die Liste holt sich
    dieser Pruefstand NICHT selbst, sondern nimmt die, die der Augendienst
    seinen beiden Vorrichtungen gibt (`eyes.daemon._denylist_aus_datei`) --
    sonst schriebe er vor, WO repariert werden muss. Ob jemand die Zuordnung
    in die Liste hineinrechnet oder in die Vorrichtung, ist seine Sache; diese
    Messung sieht beides.
    """
    try:
        import numpy as np

        change = lade_modul(pruefling, "daimon.eyes.change")
        context = lade_modul(pruefling, "daimon.eyes.context")
        augen = lade_modul(pruefling, "daimon.eyes.daemon")
        redaktion = lade_modul(pruefling, "daimon.recorder.redaktion")
        config = lade_modul(pruefling, "daimon.common.config")

        quelle, herkunft = config.denylist_laden(config.denylist_pfade())
        liste = list(augen._denylist_aus_datei())
        bericht.pruefe("K8", bool(quelle) and herkunft is not None,
                       f"Positivkontrolle: es gibt eine Denylist, und sie kommt "
                       f"aus {herkunft!r} ({len(quelle)} Eintraege)")
        bericht.pruefe("K8", bool(liste),
                       f"Positivkontrolle: der Augendienst gibt seinen "
                       f"Vorrichtungen eine nicht leere Liste "
                       f"({len(liste)} Eintraege)")

        kennungen = {KLASSE_TARNKAPPE: "org.keepassxc.KeePassXC"}
        red = redaktion.Redaktion(denylist=quelle, runtime_dir=arbeit / "k8",
                                  kennungen=kennungen)
        kette = change.Kette(tor=lambda: True, denylist=liste)
        speicher = context.Kontextspeicher(verzeichnis=arbeit / "k8" / "ctx",
                                           denylist=liste)
        rahmen = np.zeros((90, 160, 3), dtype=np.uint8)

        def fenster(klasse: str):
            return change.Fenster(x=0, y=0, breite=160, hoehe=90, klasse=klasse)

        # Positivkontrolle ZUERST: eine woertlich gelistete Klasse wird von
        # allen dreien gesperrt. Ohne sie waere "die Kette sperrt nicht" nicht
        # von "die Kette misst nichts" zu unterscheiden.
        befund = kette.verarbeiten(rahmen, fenster(KLASSE_GELISTET))
        bericht.pruefe("K8", befund.grund == change.GRUND_DENYLIST,
                       f"Positivkontrolle: die Gatterkette weist die woertlich "
                       f"gelistete Klasse ab: grund={befund.grund!r}")
        bericht.pruefe(
            "K8",
            speicher.hinzufuegen(context.ART_OCR, KLASSE_GELISTET, "x") is False,
            "Positivkontrolle: der Kontextspeicher laesst die woertlich "
            "gelistete Klasse nicht herein")
        bericht.pruefe("K8",
                       red.urteil(KLASSE_GELISTET).grund ==
                       redaktion.GRUND_DENYLIST,
                       "Positivkontrolle: die Redaktion sperrt sie ebenfalls")

        # Die Messung: dieselbe Anwendung, erkannt ueber ihre `.desktop`-Kennung.
        urteil = red.urteil(KLASSE_TARNKAPPE)
        bericht.pruefe("K8", urteil.grund == redaktion.GRUND_DENYLIST,
                       f"Vorbedingung: die Redaktion sperrt die Klasse ueber "
                       f"ihre .desktop-Kennung: {urteil!r}")

        befund = kette.verarbeiten(rahmen, fenster(KLASSE_TARNKAPPE))
        bericht.pruefe(
            "K8", befund.grund == change.GRUND_DENYLIST,
            f"die Gatterkette VOR DEM DIFF sperrt dieselbe Anwendung: "
            f"grund={befund.grund!r} -- sie vergleicht die rohe Fensterklasse "
            f"und kennt die .desktop-Zuordnung nicht, die Akzeptanzpunkt 3 "
            f"verlangt; damit wird ein gelistetes Fenster gelesen und geOCRt, "
            f"und nur der Archiveintrag faellt weg")
        bericht.pruefe(
            "K8",
            speicher.hinzufuegen(context.ART_OCR, KLASSE_TARNKAPPE, "x") is False,
            "der Quarantaene-Kontextspeicher sperrt dieselbe Anwendung "
            "(sonst steht der Text des Passwortmanagers im Live-Kontext)")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K8", f"Messung der zwei Fassungen fehlgeschlagen: {exc!r}")


# -- Rahmen -----------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pruefling", type=Path)
    args = parser.parse_args(argv)
    pruefling = args.pruefling.resolve()
    treiber = Path(__file__).resolve().parent / "t72_dienst.py"
    bericht = Bericht()

    print(f"Pruefling: {pruefling}")
    print(f"Treiber:   {treiber}")
    print(f"Mutanten-Zuordnung: {json.dumps(MUTANTEN_GRENZEN, ensure_ascii=False)}")

    with tempfile.TemporaryDirectory(prefix="t72-") as tmp:
        arbeit = Path(tmp)
        werk = Werkbank(pruefling, arbeit)
        # Der Pruefstand selbst arbeitet in derselben Umgebung wie der Dienst:
        # sonst laese er die Denylist des Nutzers statt der des Prueflings.
        os.environ.update(werk.umgebung())

        try:
            melder = lade_modul(pruefling, "daimon.recorder.melder")
            redaktion = lade_modul(pruefling, "daimon.recorder.redaktion")
            pause = lade_modul(pruefling, "daimon.recorder.pause")
        except Exception as exc:  # noqa: BLE001
            for kriterium in KRITERIEN:
                bericht.fehler(kriterium, f"Pruefling nicht ladbar: {exc!r}")
            return bericht.bilanz()
        werk.melder = melder
        werk.pause = pause

        # Das Lebenszeichen der Augen steht VOR dem Start: sonst faellt der
        # erste Blick der Redaktion auf "Wahrnehmung aus" und faerbt alles.
        werk.lebenszeichen()

        pruefe_k7_quelle(pruefling, bericht)
        pruefe_k8(pruefling, arbeit, bericht)

        try:
            werk.starten(treiber)
            if not warte_auf(werk.bereit):
                for kriterium in ("K1", "K2", "K3", "K4", "K5", "K6", "K7"):
                    bericht.fehler(
                        kriterium,
                        f"der Archivdienst wurde nicht bereit; Ausgabe: "
                        f"{werk.dienst.ausgabe()[-1500:] if werk.dienst else ''}")
                return bericht.bilanz()
            bericht.pruefe("K1", werk.dienst is not None and werk.dienst.lebt(),
                           "der echte Archivdienst laeuft und antwortet")

            pruefe_k1_k2(werk, bericht)
            pruefe_k3(werk, bericht)
            pruefe_k4(werk, bericht)
            pruefe_k7_art(werk, bericht)
            pruefe_k5(werk, redaktion, bericht)
            # K6 zuletzt: es schaltet die Wahrnehmung ab und muss danach zweimal
            # den Zwischenspeicher der Redaktion ueberholen.
            pruefe_k6(werk, pause, bericht)

            bericht.pruefe("K1", werk.dienst is not None and werk.dienst.lebt(),
                           "der Dienst hat den ganzen Lauf ueberlebt")
        except Exception as exc:  # noqa: BLE001
            bericht.fehler("K1", f"Lauf gescheitert: {exc!r}")
        finally:
            try:
                werk.stoppen()
            except Exception as exc:  # noqa: BLE001
                bericht.fehler("K1", f"Aufraeumen: {exc!r}")

    return bericht.bilanz()


if __name__ == "__main__":
    raise SystemExit(main())
