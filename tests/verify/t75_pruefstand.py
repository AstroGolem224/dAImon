#!/usr/bin/env python3
"""Pruefstand fuer T-7.5 -- die Archivsuche mit Deklassifizierung.

Geprueft wird die AKZEPTANZLISTE von T-7.5 (Implementierungsplan Z. 1969 ff.)
und ihr Verifikationsabsatz, dazu Design 1.1 (Nicht-Ziel „Automatisches
Durchsuchen des Archivs durch das Modell") und 5.2 (Senkentabelle) -- Kriterium
fuer Kriterium, ohne `&&`-Verkettung. Jedes Kriterium rechnet einzeln ab; ein
rotes Kriterium verhindert nicht die Messung der uebrigen. Ein Kriterium OHNE
Messung zaehlt in der Bilanz als rot.

  K1  Volltextsuche ueber OCR-Text, Fenstertitel und Transkripte
      (Akzeptanz 1)
  K2  Ohne frische Rundenmarke erreicht kein Archivtreffer das Modell
      (Akzeptanz 2, Verifikationsabsatz)
  K3  Ohne erkennbaren Bezug kein Archivtreffer -- und der Zeitbezug VERENGT
      die Bedingung, er lockert sie nicht (Akzeptanz 2)
  K4  Mit Marke und Bezug kommt NUR DER TREFFER, nicht seine Umgebung
      (Akzeptanz 2, Verifikationsabsatz) -- eine Mengenaussage, deshalb liegen
      fuenf unterscheidbare Kanarienvoegel im Archiv
  K5  Ein Treffer bleibt `tainted`, auch aus der eigenen Datenbank, und die
      Senkentabelle aus T-3.13b sperrt ihn gegen Durchgang 1 (Akzeptanz 3)
  K6  Proaktives Verhalten sieht das Archiv NICHT -- gemessen AN DER
      DATENBANK, nicht am Router (Akzeptanz 4, Design 1.1)
  K7  Die Suche laeuft nur auf Nachfrage, nie von selbst -- ebenfalls an der
      Datenbank (Akzeptanz 5)
  K8  Live-Kontext und Archivtreffer kommen aus DEMSELBEN `freigeben()`, unter
      DEMSELBEN Schein, mit EINER Einloesung -- und beide erreichen den
      Modellkoerper von Durchgang 2 (Plan T-7.5, Vorab-Festlegung)

DIE ZENTRALE PRUEFFRAGE DIESES AUFTRAGS:

    Erreicht ein Archivtreffer das Modell ohne frische Rundenmarke -- und wenn
    er sie hat, kommt dann nur der Treffer oder auch seine Umgebung?

Sie wird an der NAHT gemessen und nicht am Stueck: die Kanarienvoegel gehen mit
dem ECHTEN `Archiv` in die ECHTE Datenbank, eine ECHTE Rundenmarke entsteht
ueber den ECHTEN `auth.sock` des ECHTEN Hubs, und gefragt wird ueber
`kontext.sock` -- den Weg, den `Router._api` im Betrieb nimmt. Gesucht wird in
den ROHEN Antwortbytes; das ist, was der Mind bekaeme.

VIER AUFLAGEN DIESES AUFTRAGS SCHLAGEN HIER DURCH:

**Kanarienvogel plus Positivkontrolle, beide gewogen.** „Er kam nicht an" ist
ohne Gegenprobe nicht von „die Vorrichtung lief nicht" zu unterscheiden. Jede
Sperre hier hat ihre Gegenprobe mit DENSELBEN Zeichenketten auf DEMSELBEN Weg,
und zwischen beiden ist genau eine Bedingung anders.

**„Nur der Treffer, nicht die Umgebung" ist eine Mengenaussage.** Deshalb
liegen FUENF unterscheidbare Kanarienvoegel als Nachbarn im Archiv, zwei davor
und zwei danach. Ohne sie waere „die Umgebung kam nicht mit" nicht messbar --
und die Positivkontrolle daneben belegt, dass genau diese Nachbarn unter einer
Frage nach IHNEN sehr wohl herauskommen. Ein leeres Archiv wuerde sonst
dieselbe gruene Zahl liefern.

**Jede Manipulation wird gewogen.** Der einzige Eingriff dieses Verifizierers
ist die Unit-Allowlist von `kontext.sock` (siehe `t75_hub.py`); sie wird ueber
sha256 vorher/nachher gewogen, und der Treiber bricht ab, wenn sie sich nicht
geaendert hat. Der Produktcode wird NICHT angefasst.

**Eine Messung ist ein Zeitpunkt, kein Zeitfenster.** Kein `--since`. Der
Bezugspunkt jeder Naht-Messung ist die ANTWORT des Hubs auf genau die Zeile,
die er gerade bearbeitet hat. Die Datenbank-Spur (K6, K7) wird unmittelbar VOR
und unmittelbar NACH genau einem Aufruf abgelesen -- eine Klammer um einen
Aufruf, kein Fenster ueber einen Zeitraum.

WIE „AN DER DATENBANK, NICHT AM ROUTER" GEMESSEN WIRD (K6, K7):
Ueber einen `sys.addaudithook` auf `sqlite3.connect`. Der sitzt UNTERHALB des
Produktcodes: er sieht jede Oeffnung der Archivdatenbank, gleich welcher Weg
sie ausgeloest hat, und laesst sich von keinem Zaehler und keinem
Selbstbericht des Prueflings taeuschen. Seine eigene Positivkontrolle steht
daneben -- eine echte Suche MUSS die Spur bewegen, sonst misst sie nichts.

DIE HUERDE UND DER GEWAEHLTE WEG (ausfuehrlich im Ledger):
`kontext.sock` laesst nur `daimon-mind.service` durch, und dieser Dienst ist
auf dieser Maschine eingerichtet -- eine transiente Unit desselben Namens waere
ein Eingriff in den Betrieb. Gemessen wird deshalb mit ZWEI Huben:

  Hub A  unberuehrte Allowlist -> weist den Pruefstand ab.
  Hub B  Allowlist auf die Unit des Pruefstands gesetzt -> bedient ihn.

Nicht gemessen ist damit, dass die Unit des echten Mind-Prozesses
`daimon-mind.service` HEISST. Das ist der Zulauf von T-5.9b.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Sequence

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8")

# Anhang D kennt T-7.5.v nicht (dort stehen 36 Verifizierer; T-7.5 ist nicht
# darunter). Die Liste ist deshalb hier gesetzt, jeder Mutant an genau ein
# Kriterium gebunden. Sie wird bei jedem Lauf mit ausgegeben.
MUTANTEN_GRENZEN = {
    "archiv-ohne-schein": "K2 (das Archiv als zweite Tuer)",
    "archiv-ohne-marke": "K2",
    "archiv-ohne-zeitbezug": "K3, K7",
    "zeitbezug-immer-erkannt": "K3",
    "zeitbezug-nie-erkannt": "K1, K4 (er toetet die Positivkontrollen)",
    "titel-nicht-durchsucht": "K1",
    "treffer-mit-umgebung": "K4",
    "treffer-trusted": "K5",
    "proaktiv-sucht": "K6",
    "archiv-eigener-schein": "K8",
    "router-verwirft-archiv": "K8",
}

# Die Kanarienvoegel. Reine alphanumerische Wortmarken -- der FTS5-Tokenizer
# `unicode61` macht daraus je EIN Token, und keiner ist Praefix eines anderen.
# Damit ist „nur der Treffer" ueberhaupt als Menge entscheidbar.
KANARI_TREFFER = "KANARIT75TREFFER7b3e91"
KANARI_VOR1 = "KANARIT75VORHER1c0d42"
KANARI_VOR2 = "KANARIT75VORHER2e8f13"
KANARI_NACH1 = "KANARIT75DANACH1a5b76"
KANARI_NACH2 = "KANARIT75DANACH2d9c04"
KANARI_TITEL = "KANARIT75TITEL4c7d55"
KANARI_STT = "KANARIT75TRANSKRIPT77aa8e"

# Der Live-Kontext derselben Runde. Er liegt in der Quarantaene aus T-5.7 und
# nicht im Archiv -- die beiden Wege muessen unterscheidbar bleiben.
KANARI_LIVE = "KANARIT75LIVEBILDSCHIRM3f2a10"

# Die Umgebung des Treffers, in der Reihenfolge, in der sie im Archiv liegt.
UMGEBUNG = (KANARI_VOR1, KANARI_VOR2, KANARI_NACH1, KANARI_NACH2)

FENSTER = "harmlos-app"

# Vier Aeusserungen, und jede unterscheidet sich von der naechsten in genau
# einer Bedingung.
#
#   FRAGE_ARCHIV      Bildschirmbezug JA, Zeitbezug JA  -> Live UND Archiv
#   FRAGE_NUR_JETZT   Bildschirmbezug JA, Zeitbezug NEIN-> nur Live
#   FRAGE_OHNE_BEZUG  Bildschirmbezug NEIN, Zeitbezug JA-> gar nichts
#
# Der Suchbegriff entsteht aus der Aeusserung (`suche.suchbegriff`), deshalb
# steht die Wortmarke des Treffers darin. Gesucht wird sie danach in der
# ANTWORT -- eine Wortmarke in der Frage sagt nichts darueber, ob sie
# herauskommt.
FRAGE_ARCHIV = f"was stand vorhin auf dem bildschirm {KANARI_TREFFER}"
FRAGE_TITEL = f"was stand vorhin im fenster {KANARI_TITEL}"
FRAGE_STT = f"was hatte ich vorhin am bildschirm {KANARI_STT}"
FRAGE_NUR_JETZT = f"was ist auf dem bildschirm zu sehen {KANARI_TREFFER}"
FRAGE_OHNE_BEZUG = f"erzaehl mir vorhin einen witz ueber {KANARI_TREFFER}"
FRAGE_UMGEBUNG = (f"was stand vorhin auf dem bildschirm "
                  f"{KANARI_VOR1} {KANARI_NACH2}")

BEOBACHTUNG_S = 30.0

# Attrappe fuer `systemd-cat`: der Hub verankert seine Audit-Kette im Journal.
# Ohne diese Attrappe schriebe JEDER Lauf dieses Verifizierers Anker der
# synthetischen Ketten ins echte Journal des Nutzers (Uebergabe 17.08. §5).
STUB_SYSTEMD_CAT = """#!/usr/bin/env python3
import os
import sys

with open(os.environ["DAIMON_STUB_JOURNAL"], "a", encoding="utf-8") as fh:
    fh.write(sys.stdin.read())
"""

STUB_JOURNALCTL = """#!/usr/bin/env python3
import os
import sys

try:
    with open(os.environ["DAIMON_STUB_JOURNAL"], encoding="utf-8") as fh:
        sys.stdout.write(fh.read())
except OSError:
    pass
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
        print("\nBilanz T-7.5:")
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


# -- Die Spur an der Datenbank ----------------------------------------------

class Spur:
    """Jede Oeffnung der Archivdatenbank, gezaehlt UNTERHALB des Produktcodes.

    `sys.addaudithook` sieht `sqlite3.connect` mit dem Pfad, gleich welcher
    Weg ihn ausgeloest hat. Ein Zaehler im Pruefling waere ein Selbstbericht
    (Regel 9); dieser hier laesst sich nicht abschalten und nicht umgehen --
    ausser durch eine zweite Datenbankbibliothek, und die gibt es nicht.

    Die Positivkontrolle der Spur selbst steht in `pruefe_k7`: eine ECHTE
    Suche muss sie bewegen. Ohne diese Kontrolle waere „null Oeffnungen"
    genau der Falschbefund, den dieses Repo viermal an einem Tag hatte.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.oeffnungen: list[str] = []
        sys.addaudithook(self._hook)

    def _hook(self, event: str, args) -> None:
        if event.startswith("sqlite3.connect") and args:
            ziel = str(args[0])
            if self.name in ziel:
                self.oeffnungen.append(ziel)

    def stand(self) -> int:
        return len(self.oeffnungen)


# -- Werkzeug ---------------------------------------------------------------

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
    import importlib

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


def _zulauf(pfad: Path) -> dict:
    """Was ruft diese Datei wirklich? Ueber den Syntaxbaum, nicht ueber Text.

    Eine Textsuche findet auch das Wort im Kommentar, in dem jemand erklaert,
    dass er es NICHT tut -- genau der Fall in `mind/proactive.py`, dessen
    Modulkopf die Deklassifizierung ausdruecklich verneint.
    """
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    aufrufe: set[str] = set()
    importe: list[str] = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Call):
            ziel = knoten.func
            if isinstance(ziel, ast.Attribute):
                aufrufe.add(ziel.attr)
            elif isinstance(ziel, ast.Name):
                aufrufe.add(ziel.id)
            # `getattr(self._quellen, "kontext", None)` ist ein Aufruf, der
            # seinen Namen als Zeichenkette traegt.
            if (isinstance(ziel, ast.Name) and ziel.id == "getattr"
                    and len(knoten.args) >= 2
                    and isinstance(knoten.args[1], ast.Constant)
                    and isinstance(knoten.args[1].value, str)):
                aufrufe.add(knoten.args[1].value)
        elif isinstance(knoten, ast.Import):
            for name in knoten.names:
                importe.append(name.name)
        elif isinstance(knoten, ast.ImportFrom):
            woher = knoten.module or ""
            for name in knoten.names:
                importe.append(f"{woher}.{name.name}")
    return {"aufrufe": aufrufe, "importe": importe}


class Spion:
    """Reicht an die ECHTE Vorrichtung durch und merkt sich, WAS sie sah.

    Kein Ersatz: dahinter arbeitet das Original weiter. Gemessen wird, welchen
    Schein das Gate an welchen Speicher gibt -- das ist die Frage von K8 und
    von aussen sonst nicht sichtbar.
    """

    def __init__(self, echt: Any) -> None:
        self.echt = echt
        self.scheine: list[Any] = []
        self.anfragen: list[Any] = []

    def freigeben(self, schein, *args, **kwargs):
        self.scheine.append(schein)
        self.anfragen.append(args[0] if args else None)
        return self.echt.freigeben(schein, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.echt, name)


class Markenspion:
    """Dasselbe fuer das Markenbuch: wie oft wird in einer Runde eingeloest?"""

    def __init__(self, echt: Any) -> None:
        self.echt = echt
        self.einloesungen: list[str] = []

    def einloesen(self, turn_id):
        self.einloesungen.append(turn_id)
        return self.echt.einloesen(turn_id)

    def __getattr__(self, name):
        return getattr(self.echt, name)


# -- Der Arbeitsplatz eines Hubs --------------------------------------------

class Werkbank:
    """Ein vollstaendig eigener XDG-Baum, ein ECHTER Hub darin.

    Alles, was der Hub anfasst, liegt unter `self.lauf`: Laufzeitverzeichnis,
    Zustand (Kontextspeicher, Audit-Kette), Daten (das Archiv), Konfiguration,
    Heim. Das Journal ist abgefangen -- ein Verifizierer, der Anker seiner
    synthetischen Ketten ins echte Journal schreibt, verdirbt jede spaetere
    Messung des Nutzers.
    """

    def __init__(self, pruefling: Path, wurzel: Path, name: str) -> None:
        self.pruefling = pruefling
        self.lauf = wurzel / name
        self.runtime = self.lauf / "rt"
        self.state = self.lauf / "state"
        self.daten = self.lauf / "data"
        self.bin = self.lauf / "bin"
        self.journal = self.lauf / "journal.txt"
        for pfad in (self.runtime, self.state, self.bin, self.lauf / "config",
                     self.lauf / "cache", self.daten, self.lauf / "heim"):
            pfad.mkdir(parents=True, exist_ok=True)
        self.journal.write_text("", encoding="utf-8")
        for datei, quelle in (("systemd-cat", STUB_SYSTEMD_CAT),
                              ("journalctl", STUB_JOURNALCTL)):
            pfad = self.bin / datei
            pfad.write_text(quelle, encoding="utf-8")
            pfad.chmod(0o755)
        self.kontext_verzeichnis = self.state / "daimon" / "context"
        self.archiv_datei = self.daten / "daimon" / "archiv.db"
        self.dienst: Prozessgruppe | None = None
        self.anfragen: list[tuple[str, bytes]] = []

    def umgebung(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "HOME": str(self.lauf / "heim"),
            "XDG_RUNTIME_DIR": str(self.lauf),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.lauf / "config"),
            "XDG_CACHE_HOME": str(self.lauf / "cache"),
            "XDG_DATA_HOME": str(self.daten),
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "DAIMON_STUB_JOURNAL": str(self.journal),
            "PYTHONPATH": str(self.pruefling),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.lauf}/kein-dbus",
        })
        return env

    # -- Die Kanarienvoegel ins Archiv -------------------------------------

    def archiv_fuellen(self, store: ModuleType) -> list[int]:
        """Mit der ECHTEN Vorrichtung des Prueflings.

        Die Reihenfolge ist die Umgebung: zwei Nachbarn davor, der Treffer,
        zwei Nachbarn danach. Ein Schreiber, der den Treffer mit seinem
        Zeitkontext herausgibt, faellt genau daran auf.
        """
        archiv = store.Archiv(pfad=self.archiv_datei)
        archiv.migrieren()
        ids = []
        jetzt = time.time()
        for schritt, (art, text, fenster) in enumerate((
                ("ocr", KANARI_VOR1, FENSTER),
                ("ocr", KANARI_VOR2, FENSTER),
                ("ocr", KANARI_TREFFER, FENSTER),
                ("ocr", KANARI_NACH1, FENSTER),
                ("ocr", KANARI_NACH2, FENSTER),
                ("titel", "", KANARI_TITEL),
                ("transkript", KANARI_STT, ""))):
            ids.append(archiv.schreiben(art, text, fenster=fenster,
                                        ts=jetzt - 100.0 + schritt))
        # SAUBER SCHLIESSEN. Das Archiv laeuft in WAL; eine lesende Verbindung
        # mit `mode=ro` braucht danach entweder ein `-shm` oder eine
        # eingecheckpointete Datei. Der Recorder haelt sie im Betrieb offen --
        # hier gibt es keinen Recorder, also wird eingecheckpointet.
        archiv.schliessen()
        return ids

    def kanari_live_ablegen(self, speicherklasse) -> bool:
        speicher = speicherklasse(verzeichnis=self.kontext_verzeichnis)
        return bool(speicher.hinzufuegen("ocr", FENSTER, KANARI_LIVE))

    # -- Der Hub -----------------------------------------------------------

    def starten(self, treiber: Path, units: Sequence[str] = ()) -> None:
        self.dienst = Prozessgruppe(
            [sys.executable, str(treiber), str(self.pruefling),
             str(self.runtime), *units],
            self.pruefling, self.umgebung())

    def stoppen(self) -> None:
        if self.dienst is not None:
            self.dienst.stop()
            self.dienst = None

    def bereit(self) -> dict | None:
        """Antwortet der Hub? Gemessen an `state.sock` -- der traegt KEINE
        Allowlist und sagt damit nur eines: der Prozess horcht."""
        if not (self.runtime / "state.sock").exists():
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(5.0)
                sock.connect(str(self.runtime / "state.sock"))
                zeile = sock.makefile("rb").readline()
            return json.loads(zeile) if zeile else None
        except (OSError, ValueError):
            return None

    def diag(self) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(self.runtime / "diag.sock"))
            return json.loads(sock.makefile("rb").readline())

    def auth(self, typ: str, payload: dict) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(self.runtime / "auth.sock"))
            sock.sendall(json.dumps({"v": 1, "type": typ,
                                     "payload": payload}).encode() + b"\n")
            time.sleep(0.4)

    def kontext(self, text: str, marke: str) -> bytes | None:
        """Eine Anfrage ueber `kontext.sock`. `None` = Verbindung abgewiesen.

        Die ROHEN Bytes der Antwort werden aufgehoben: die Kanarienvoegel
        werden darin gesucht, nicht in einem geparsten Feld. Was das Modell
        bekaeme, sind diese Bytes.
        """
        nachricht = {"v": 1, "art": "deklassifizieren", "text": text}
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10.0)
        try:
            sock.connect(str(self.runtime / "kontext.sock"))
            sock.sendall(json.dumps(nachricht).encode() + b"\n")
            roh = sock.makefile("rb").readline(1 << 20)
        except OSError:
            return None
        finally:
            sock.close()
        if not roh:
            return None
        self.anfragen.append((marke, roh))
        return roh


def antwort(roh: bytes | None) -> dict:
    if not roh:
        return {}
    try:
        wert = json.loads(roh)
    except ValueError:
        return {}
    return wert if isinstance(wert, dict) else {}


def _fehlt(roh: bytes | None, *woerter: str) -> bool:
    return roh is not None and all(w.encode() not in roh for w in woerter)


# -- Das Gate im Prozess, mit echten Teilen ---------------------------------

def gate_bauen(declassify: ModuleType, marks: ModuleType, context: ModuleType,
               suche: ModuleType, arbeit: Path, name: str, archiv_datei: Path):
    """Ein ECHTES Gate mit ECHTEM Markenbuch, ECHTEM Kontextspeicher und
    ECHTER Archivsuche. Spione reichen durch, sie ersetzen nichts."""
    buch = marks.MarkenBuch()
    speicher = context.Kontextspeicher(verzeichnis=arbeit / name / "ctx")
    speicher.hinzufuegen("ocr", FENSTER, KANARI_LIVE)
    speicher_spion = Spion(speicher)
    archiv_spion = Spion(suche.Archivsuche(pfad=archiv_datei))
    marken_spion = Markenspion(buch)
    gate = declassify.Deklassifizierung(marken=marken_spion,
                                        speicher=speicher_spion,
                                        archiv=archiv_spion)
    return SimpleNamespace(gate=gate, buch=buch, marken=marken_spion,
                           speicher=speicher_spion, archiv=archiv_spion)


def _texte(eintraege) -> str:
    return json.dumps([str(getattr(e, "value", e)) for e in eintraege],
                      ensure_ascii=False)


# -- K1 ---------------------------------------------------------------------

def pruefe_k1(declassify: ModuleType, marks: ModuleType, context: ModuleType,
              suche: ModuleType, arbeit: Path, archiv_datei: Path,
              bericht: Bericht) -> None:
    """Volltextsuche ueber OCR-Text, Fenstertitel und Transkripte
    (Akzeptanz 1).

    Drei Arten, drei eigene Wortmarken, drei eigene Runden -- durch das ECHTE
    Gate, weil die Suche laut Akzeptanz 2 nur dort herauskommt. Ein
    Verifizierer, der `Archivsuche` direkt fragte, mass die Suche ohne ihre
    Bedingung.
    """
    faelle = (("OCR-Text", FRAGE_ARCHIV, KANARI_TREFFER),
              ("Fenstertitel", FRAGE_TITEL, KANARI_TITEL),
              ("Transkript", FRAGE_STT, KANARI_STT))
    for nummer, (was, frage, kanari) in enumerate(faelle):
        try:
            teile = gate_bauen(declassify, marks, context, suche, arbeit,
                               f"k1{nummer}", archiv_datei)
            turn = f"t75-k1-{nummer}"
            teile.buch.ausgeben(quelle="auth", turn_id=turn)
            frei = teile.gate.freigeben(aeusserung=frage, turn_id=turn)
            inhalt = _texte(frei.archiv)
            bericht.pruefe("K1", kanari in inhalt,
                           f"{was}: die Wortmarke {kanari!r} ist ueber die "
                           f"Volltextsuche auffindbar: {inhalt[:300]!r}")
            bericht.pruefe("K1", int(frei.umfang.get("archiv", 0)) == 1,
                           f"{was}: der Umfang nennt genau einen Archivtreffer:"
                           f" {frei.umfang!r}")
        except Exception as exc:  # noqa: BLE001
            bericht.fehler("K1", f"{was}: Suche fehlgeschlagen: {exc!r}")

    # Die drei Arten sind DREI und nicht eine: dieselbe Vorrichtung, drei
    # verschiedene Spalten (`text` fuer OCR und Transkript, `fenster` fuer den
    # Titel) und drei verschiedene `art`-Werte.
    try:
        teile = gate_bauen(declassify, marks, context, suche, arbeit, "k1x",
                           archiv_datei)
        teile.buch.ausgeben(quelle="auth", turn_id="t75-k1-x")
        frei = teile.gate.freigeben(aeusserung=FRAGE_TITEL,
                                    turn_id="t75-k1-x")
        inhalt = _texte(frei.archiv)
        bericht.pruefe("K1", "titel" in inhalt,
                       f"der Titeltreffer traegt seine Art: {inhalt[:200]!r}")
        bericht.pruefe("K1", KANARI_TREFFER not in inhalt,
                       f"und er ist NICHT der OCR-Treffer -- die drei Arten "
                       f"sind unterscheidbar: {inhalt[:200]!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K1", f"Artenmessung fehlgeschlagen: {exc!r}")


# -- K2 ---------------------------------------------------------------------

def pruefe_k2_naht(hub_b: Werkbank, bericht: Bericht) -> None:
    """Ohne frische Rundenmarke erreicht kein Archivtreffer das Modell.

    An der NAHT gemessen, an den ROHEN Antwortbytes -- das ist, was der Mind
    bekaeme. Die Positivkontrolle dazu ist r4 in `pruefe_k4`; ohne sie waere
    diese Sperre auch dann gruen, wenn die Suche gar nicht liefe.
    """
    try:
        roh = [r for m, r in hub_b.anfragen if m.startswith("r1")]
        roh = roh[-1] if roh else None
        a = antwort(roh)
        bericht.pruefe("K2", a.get("ok") is False,
                       f"r1 ohne Rundenmarke: keine Freigabe: {a!r}")
        bericht.pruefe("K2", a.get("grund") == "keine_marke",
                       f"und der Hub nennt 'keine_marke' (Diagnose, nicht "
                       f"Beleg): {a!r}")
        bericht.pruefe("K2", _fehlt(roh, KANARI_TREFFER),
                       f"der Archivtreffer steht NICHT in den Antwortbytes: "
                       f"{roh!r}")
        bericht.pruefe("K2", _fehlt(roh, *UMGEBUNG),
                       f"und seine Umgebung ebenso wenig: {roh!r}")
        bericht.pruefe("K2", _fehlt(roh, KANARI_LIVE),
                       f"und der Live-Kontext auch nicht: {roh!r}")

        # Die verbrauchte Marke ist der zweite Weg in denselben Zustand: eine
        # Marke, die es GAB, gilt kein zweites Mal.
        roh5 = [r for m, r in hub_b.anfragen if m.startswith("r5")]
        roh5 = roh5[-1] if roh5 else None
        a5 = antwort(roh5)
        bericht.pruefe("K2", a5.get("ok") is False,
                       f"r5 mit verbrauchter Marke: keine Freigabe: {a5!r}")
        bericht.pruefe("K2", _fehlt(roh5, KANARI_TREFFER, *UMGEBUNG),
                       f"und kein Archivtreffer in den Bytes: {roh5!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K2", f"Naht-Messung ohne Marke fehlgeschlagen: {exc!r}")


def pruefe_k2_tuer(declassify: ModuleType, suche: ModuleType,
                   archiv_datei: Path, bericht: Bericht) -> None:
    """Das Archiv als zweite Tuer, aktiv angegriffen.

    Der Weg aus der Quarantaene ist das Gate. Wer die `Archivsuche` direkt
    fragt, braucht einen Schein -- und der entsteht nur in `declassify`. Die
    Leseversuche hier sind dieselben, an denen der Kontextspeicher am 16.08.
    gescheitert ist (`SimpleNamespace(turn_id="x")` genuegte ihm).
    """
    try:
        archiv = suche.Archivsuche(pfad=archiv_datei)

        class Freigabeschein:  # noqa: D401 - eine Attrappe mit passendem Namen
            def __init__(self) -> None:
                self.turn_id = ""

        versuche = [
            ("None", None),
            ("True", True),
            ("die Zahl 1", 1),
            ("ein nacktes Objekt", object()),
            ("ein Namespace mit turn_id (der Fehler vom 16.08.)",
             SimpleNamespace(turn_id="x")),
            ("ein dict mit turn_id", {"turn_id": "x"}),
            ("ein Schein mit leerer turn_id",
             declassify.Freigabeschein(turn_id="")),
            ("eine gleichnamige Attrappe ohne turn_id", Freigabeschein()),
        ]
        for name, schein in versuche:
            try:
                ergebnis = archiv.freigeben(schein, FRAGE_ARCHIV)
                bericht.fehler("K2", f"Leseversuch {name} hat Treffer "
                                     f"geliefert: {ergebnis!r}")
            except suche.QuarantaeneFehler:
                bericht.pruefe("K2", True,
                               f"Leseversuch {name} scheitert am fehlenden "
                               f"Freigabeschein")
            except Exception as exc:  # noqa: BLE001
                bericht.fehler("K2", f"Leseversuch {name}: unerwarteter "
                                     f"Fehler {exc!r}")

        # Positivkontrolle: der ECHTE Schein oeffnet. Ohne sie waere
        # "scheitert" nicht von "das Archiv ist leer" zu unterscheiden.
        echt = archiv.freigeben(declassify.Freigabeschein(turn_id="t75-echt"),
                                FRAGE_ARCHIV)
        bericht.pruefe("K2", KANARI_TREFFER in _texte(echt),
                       f"Positivkontrolle: mit echtem Freigabeschein kommt der "
                       f"Treffer heraus: {_texte(echt)[:200]!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K2", f"Tuermessung fehlgeschlagen: {exc!r}")


# -- K3 ---------------------------------------------------------------------

def pruefe_k3(declassify: ModuleType, hub_b: Werkbank, bericht: Bericht) -> None:
    """Ohne erkennbaren Bezug kein Archivtreffer -- und der Zeitbezug VERENGT.

    Zwei Sperren, und sie sind verschieden:

      r2  kein BILDSCHIRMbezug -> das Gate gibt gar nichts frei
      r3  Bildschirmbezug, aber kein ZEITbezug -> Live-Kontext ja, Archiv nein

    r3 ist die eigentliche Aussage dieses Kriteriums: die Archivsuche lockert
    die Bedingung des Gates nicht, sie verengt sie ein zweites Mal. Und r3 ist
    zugleich seine eigene Positivkontrolle -- derselbe Weg, dieselbe Marke,
    dieselbe Datenbank, nur eine andere Frage, und der Live-Kanarienvogel
    kommt sehr wohl an.
    """
    try:
        roh2 = [r for m, r in hub_b.anfragen if m.startswith("r2")]
        roh2 = roh2[-1] if roh2 else None
        a2 = antwort(roh2)
        bericht.pruefe("K3", a2.get("ok") is False,
                       f"r2 ohne Bildschirmbezug: keine Freigabe: {a2!r}")
        bericht.pruefe("K3", a2.get("grund") == "kein_bildschirmbezug",
                       f"der Hub nennt 'kein_bildschirmbezug': {a2!r}")
        bericht.pruefe("K3", _fehlt(roh2, KANARI_TREFFER, *UMGEBUNG),
                       f"kein Archivtreffer in den Antwortbytes: {roh2!r}")

        roh3 = [r for m, r in hub_b.anfragen if m.startswith("r3")]
        roh3 = roh3[-1] if roh3 else None
        a3 = antwort(roh3)
        bericht.pruefe("K3", a3.get("ok") is True,
                       f"r3 mit Bildschirmbezug, ohne Zeitbezug: das Gate gibt "
                       f"frei: {a3!r}")
        bericht.pruefe("K3", roh3 is not None and KANARI_LIVE.encode() in roh3,
                       f"Positivkontrolle: der LIVE-Kanarienvogel kommt an -- "
                       f"die Vorrichtung lief: {roh3!r}")
        bericht.pruefe("K3", _fehlt(roh3, KANARI_TREFFER, *UMGEBUNG),
                       f"aber KEIN Archivtreffer: ohne Zeitbezug wird nicht "
                       f"gesucht: {roh3!r}")
        bericht.pruefe("K3", list(a3.get("archiv") or []) == [],
                       f"das Archivfeld der Antwort ist leer: "
                       f"{a3.get('archiv')!r}")
        bericht.pruefe("K3", "archiv" not in (a3.get("umfang") or {}),
                       f"und der Umfang nennt gar keine Archivmenge -- die "
                       f"Suche lief nicht, sie fand nur nichts: "
                       f"{a3.get('umfang')!r}")

        # Die Liste ist bewusst eng, in beide Richtungen.
        for satz in ("mach das nochmal", "zeig mir das wieder",
                     "hast du schon angefangen", "wie spaet ist es"):
            bericht.pruefe("K3", declassify.zeitbezug(satz) is False,
                           f"{satz!r} ist kein Zeitbezug")
        for satz in (FRAGE_ARCHIV, "was war gestern auf dem monitor",
                     "zeig mir das fenster von letzter woche"):
            bericht.pruefe("K3", declassify.zeitbezug(satz) is True,
                           f"Positivkontrolle: {satz!r} IST ein Zeitbezug")
        bericht.pruefe("K3", declassify.zeitbezug(FRAGE_NUR_JETZT) is False,
                       f"und {FRAGE_NUR_JETZT!r} fragt nach JETZT, nicht nach "
                       f"der Vergangenheit")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K3", f"Bezugsmessung fehlgeschlagen: {exc!r}")


# -- K4 ---------------------------------------------------------------------

def pruefe_k4(hub_b: Werkbank, bericht: Bericht) -> dict:
    """Mit Marke und Bezug kommt NUR DER TREFFER, nicht seine Umgebung.

    Die Positivkontrolle des ganzen Pruefstands -- ohne sie waere jede Sperre
    oben auch dann gruen, wenn gar nichts liefe. Und eine MENGENAUSSAGE: vier
    unterscheidbare Nachbarn liegen im selben Archiv, zwei davor und zwei
    danach. „Die Umgebung kam nicht mit" ist erst damit messbar.
    """
    ergebnis: dict = {}
    try:
        roh = [r for m, r in hub_b.anfragen if m.startswith("r4")]
        roh = roh[-1] if roh else None
        a = antwort(roh)
        ergebnis = a
        bericht.pruefe("K4", a.get("ok") is True,
                       f"r4 mit Marke UND beidem Bezug: freigegeben: {a!r}")
        bericht.pruefe("K4", roh is not None and KANARI_TREFFER.encode() in roh,
                       f"Positivkontrolle: der Archivtreffer steht in den "
                       f"Antwortbytes -- er kommt beim Modell an: {roh!r}")
        bericht.pruefe("K4", roh is not None and KANARI_LIVE.encode() in roh,
                       f"und der Live-Kontext derselben Runde ebenso: {roh!r}")
        for nachbar in UMGEBUNG:
            bericht.pruefe("K4", _fehlt(roh, nachbar),
                           f"der Nachbar {nachbar!r} kommt NICHT mit: {roh!r}")
        bericht.pruefe("K4", int((a.get("umfang") or {}).get("archiv", -1)) == 1,
                       f"der Umfang nennt genau EINEN Archivtreffer: "
                       f"{a.get('umfang')!r}")
        bericht.pruefe("K4", len(list(a.get("archiv") or [])) == 1,
                       f"und das Archivfeld traegt genau einen Eintrag: "
                       f"{a.get('archiv')!r}")
        bericht.pruefe("K4", bool(a.get("turn_id")),
                       f"die Antwort nennt die turn_id des Hubs: "
                       f"{a.get('turn_id')!r}")
        bericht.pruefe("K4", a.get("senke") == "durchgang2",
                       f"die Freigabe nennt ihre Senke: {a.get('senke')!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K4", f"Positivmessung fehlgeschlagen: {exc!r}")
    return ergebnis


def pruefe_k4_umgebung_existiert(declassify: ModuleType, marks: ModuleType,
                                 context: ModuleType, suche: ModuleType,
                                 arbeit: Path, archiv_datei: Path,
                                 bericht: Bericht) -> None:
    """DIE Gegenprobe zur Mengenaussage: die Nachbarn sind auffindbar.

    Ohne sie waere „die Umgebung kam nicht mit" von „die Umgebung liegt gar
    nicht im Archiv" nicht zu unterscheiden -- und ein leeres Archiv lieferte
    dieselbe gruene Zahl. Gefragt wird nach den Nachbarn, auf demselben Weg,
    mit demselben Gate.
    """
    try:
        teile = gate_bauen(declassify, marks, context, suche, arbeit, "k4u",
                           archiv_datei)
        teile.buch.ausgeben(quelle="auth", turn_id="t75-k4u")
        frei = teile.gate.freigeben(aeusserung=FRAGE_UMGEBUNG,
                                    turn_id="t75-k4u")
        inhalt = _texte(frei.archiv)
        for nachbar in (KANARI_VOR1, KANARI_NACH2):
            bericht.pruefe("K4", nachbar in inhalt,
                           f"Positivkontrolle: {nachbar!r} liegt im Archiv und "
                           f"ist unter einer Frage nach IHM auffindbar: "
                           f"{inhalt[:300]!r}")
        bericht.pruefe("K4", KANARI_TREFFER not in inhalt,
                       f"und die Frage nach den Nachbarn holt den Treffer "
                       f"nicht mit -- gesucht wird, nicht gebloettert: "
                       f"{inhalt[:300]!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K4", f"Umgebungskontrolle fehlgeschlagen: {exc!r}")


# -- K5 ---------------------------------------------------------------------

def pruefe_k5(declassify: ModuleType, marks: ModuleType, context: ModuleType,
              suche: ModuleType, taint: ModuleType, protocol: ModuleType,
              arbeit: Path, archiv_datei: Path, bericht: Bericht) -> None:
    """Ein Treffer bleibt `tainted`, auch aus der eigenen Datenbank.

    Nachgewiesen an der ECHTEN Senkentabelle aus T-3.13b (`taint.pruefe_senke`)
    und nicht an einem Feldwert: eine Markierung, die keine Senke sperrt, ist
    Zierrat.
    """
    try:
        teile = gate_bauen(declassify, marks, context, suche, arbeit, "k5",
                           archiv_datei)
        teile.buch.ausgeben(quelle="auth", turn_id="t75-k5")
        frei = teile.gate.freigeben(aeusserung=FRAGE_ARCHIV,
                                    turn_id="t75-k5")
        bericht.pruefe("K5", bool(frei.archiv),
                       f"Positivkontrolle: es wurde ueberhaupt ein Treffer "
                       f"freigegeben: {frei.umfang!r}")
        for eintrag in frei.archiv:
            bericht.pruefe("K5",
                           getattr(eintrag, "mark", None) is protocol.Mark.TAINTED,
                           f"jeder Archivtreffer ist tainted: "
                           f"{getattr(eintrag, 'mark', None)!r}")
            try:
                taint.pruefe_senke(eintrag, senke="durchgang1")
                bericht.fehler("K5", "die Senkentabelle liess einen "
                                     "Archivtreffer in den werkzeugfaehigen "
                                     "Durchgang 1")
            except taint.SenkenFehler:
                bericht.pruefe("K5", True,
                               "die Senkentabelle aus T-3.13b sperrt ihn gegen "
                               "Durchgang 1")
            try:
                taint.pruefe_senke(eintrag, senke="durchgang2")
                bericht.pruefe("K5", True,
                               "Positivkontrolle: in Durchgang 2 ist er "
                               "erlaubt")
            except taint.SenkenFehler as exc:
                bericht.fehler("K5", f"Durchgang 2 hat ihn abgelehnt: {exc!r}")
            # Und gegen die Senke, die `tainted` ungefragt vorlesen wuerde.
            try:
                taint.pruefe_senke(eintrag, senke="tts_ungefragt")
                bericht.fehler("K5", "ein Archivtreffer duerfte UNGEFRAGT "
                                     "vorgelesen werden")
            except taint.SenkenFehler:
                bericht.pruefe("K5", True,
                               "und gegen `tts_ungefragt` ebenso -- ein per OCR "
                               "erfasstes Passwort wird nicht vorgelesen")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K5", f"Markierungsmessung fehlgeschlagen: {exc!r}")


# -- K6 ---------------------------------------------------------------------

def pruefe_k6(declassify: ModuleType, marks: ModuleType, context: ModuleType,
              suche: ModuleType, proactive: ModuleType, pruefling: Path,
              arbeit: Path, archiv_datei: Path, spur: Spur,
              bericht: Bericht) -> None:
    """Proaktives Verhalten sieht das Archiv NICHT (Akzeptanz 4, Design 1.1).

    GEMESSEN AN DER DATENBANK, nicht am Router: die Spur zaehlt jede
    `sqlite3.connect` auf die Archivdatei, unterhalb des Produktcodes. Die
    Klammer liegt um GENAU EINEN Aufruf -- ein Zeitpunkt, kein Zeitfenster.
    """
    try:
        teile = gate_bauen(declassify, marks, context, suche, arbeit, "k6",
                           archiv_datei)
        teile.buch.ausgeben(quelle="auth", turn_id="t75-proaktiv")

        vorher = spur.stand()
        try:
            teile.gate.freigeben(aeusserung=FRAGE_ARCHIV,
                                 turn_id="t75-proaktiv", proaktiv=True)
            bericht.fehler("K6", "ein proaktiver Aufruf MIT gueltiger Marke, "
                                 "Bildschirm- und Zeitbezug hat freigegeben")
        except declassify.GateFehler as exc:
            bericht.pruefe("K6", exc.grund == declassify.GRUND_PROAKTIV,
                           f"proaktiv wird mit {declassify.GRUND_PROAKTIV!r} "
                           f"abgelehnt, ist {exc.grund!r}")
        nachher = spur.stand()
        bericht.pruefe("K6", nachher == vorher,
                       f"und die Archivdatenbank wird dabei NICHT geoeffnet "
                       f"({vorher} -> {nachher} Oeffnungen)")
        bericht.pruefe("K6", teile.archiv.scheine == [],
                       f"die Archivsuche wurde nicht einmal gefragt: "
                       f"{teile.archiv.scheine!r}")

        # Positivkontrolle: derselbe Aufruf ohne `proaktiv` oeffnet sie. Damit
        # ist belegt, dass die Null oben an `proaktiv` lag und an nichts sonst.
        vorher2 = spur.stand()
        frei = teile.gate.freigeben(aeusserung=FRAGE_ARCHIV,
                                    turn_id="t75-proaktiv")
        nachher2 = spur.stand()
        bericht.pruefe("K6", nachher2 > vorher2,
                       f"Positivkontrolle: derselbe Aufruf ohne proaktiv "
                       f"oeffnet die Datenbank ({vorher2} -> {nachher2})")
        bericht.pruefe("K6", KANARI_TREFFER in _texte(frei.archiv),
                       f"und gibt den Treffer heraus: "
                       f"{_texte(frei.archiv)[:200]!r}")

        # Ein ECHTER proaktiver Anlass, gefahren mit der echten Vorrichtung.
        vorher3 = spur.stand()
        prox = proactive.Proaktiv()
        vorschlaege = [prox.melden(anlass, f"t75-{anlass}")
                       for anlass in ("agent_wartet", "build_kaputt")]
        nachher3 = spur.stand()
        bericht.pruefe("K6", any(v is not None for v in vorschlaege),
                       f"Positivkontrolle: der proaktive Pfad hat ueberhaupt "
                       f"etwas entschieden: {vorschlaege!r}")
        bericht.pruefe("K6", nachher3 == vorher3,
                       f"ein echter proaktiver Anlass oeffnet die "
                       f"Archivdatenbank nicht ({vorher3} -> {nachher3})")

        # Der ZULAUF, ueber den Syntaxbaum statt ueber Textsuche: `proactive.py`
        # ERKLAERT im Modulkopf, dass es keine Deklassifizierung macht, und eine
        # Textsuche findet ihre eigene Verneinung.
        prox_zulauf = _zulauf(pruefling / "daimon" / "mind" / "proactive.py")
        hub_zulauf = _zulauf(pruefling / "daimon" / "hub" / "daemon.py")
        archivimporte = [i for i in prox_zulauf["importe"]
                         if "suche" in i or "Archivsuche" in i
                         or "declassify" in i or "store" in i]
        bericht.pruefe("K6", not archivimporte,
                       f"der proaktive Pfad importiert weder Archiv noch Gate: "
                       f"{archivimporte!r}")
        for name in ("freigeben", "suchen", "kontext", "Archivsuche"):
            bericht.pruefe("K6", name not in prox_zulauf["aufrufe"],
                           f"und ruft {name!r} nicht: "
                           f"{sorted(prox_zulauf['aufrufe'])!r}")
        # Positivkontrolle der Suche: dort, wo die Archivsuche WIRKLICH
        # verdrahtet wird, findet dieselbe Messung sie.
        bericht.pruefe("K6", "Archivsuche" in hub_zulauf["aufrufe"],
                       f"Positivkontrolle: dieselbe Messung findet die "
                       f"Verdrahtung im Hub -- sie greift ueberhaupt: "
                       f"{'Archivsuche' in hub_zulauf['aufrufe']}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K6", f"Proaktivmessung fehlgeschlagen: {exc!r}")


# -- K7 ---------------------------------------------------------------------

def pruefe_k7(declassify: ModuleType, marks: ModuleType, context: ModuleType,
              suche: ModuleType, router_modul: ModuleType,
              answer_modul: ModuleType, arbeit: Path, archiv_datei: Path,
              spur: Spur, bericht: Bericht) -> None:
    """Die Suche laeuft nur auf Nachfrage, nie von selbst (Akzeptanz 5).

    Auch das an der DATENBANK: jede Aeusserung, die die Bedingung nicht
    erfuellt, laesst die Archivdatei ungeoeffnet. „Sie fand nichts" und „sie
    lief nicht" sind damit unterscheidbar -- der Unterschied ist der ganze
    Punkt von Design 1.1.
    """
    try:
        # DIE POSITIVKONTROLLE DER SPUR SELBST, und sie steht zuerst. Eine
        # Spur, die nichts sieht, meldet fuer jede Sperre unten brav Null.
        teile = gate_bauen(declassify, marks, context, suche, arbeit, "k7",
                           archiv_datei)
        teile.buch.ausgeben(quelle="auth", turn_id="t75-k7-echt")
        vorher = spur.stand()
        teile.gate.freigeben(aeusserung=FRAGE_ARCHIV, turn_id="t75-k7-echt")
        nachher = spur.stand()
        bericht.pruefe("K7", nachher > vorher,
                       f"Positivkontrolle: eine echte Suche BEWEGT die Spur "
                       f"({vorher} -> {nachher}) -- sie misst ueberhaupt")

        # Jetzt die Sperren, jede mit ihrer eigenen Klammer um genau einen
        # Aufruf.
        faelle = (
            ("ohne Marke", FRAGE_ARCHIV, None, False),
            ("ohne Bildschirmbezug", FRAGE_OHNE_BEZUG, "t75-k7-a", False),
            ("ohne Zeitbezug", FRAGE_NUR_JETZT, "t75-k7-b", True),
            ("beilaeufige Frage", "wie spaet ist es", "t75-k7-c", False),
        )
        for name, frage, turn, erwartet_ok in faelle:
            teile = gate_bauen(declassify, marks, context, suche, arbeit,
                               f"k7-{name.replace(' ', '-')}", archiv_datei)
            if turn:
                teile.buch.ausgeben(quelle="auth", turn_id=turn)
            vor = spur.stand()
            try:
                frei = teile.gate.freigeben(aeusserung=frage, turn_id=turn)
                gab_frei = True
            except declassify.GateFehler:
                frei, gab_frei = None, False
            nach = spur.stand()
            bericht.pruefe("K7", gab_frei is erwartet_ok,
                           f"{name}: das Gate gibt {'frei' if erwartet_ok else 'nichts frei'}"
                           f" (ist: {gab_frei})")
            bericht.pruefe("K7", nach == vor,
                           f"{name}: die Archivdatenbank bleibt ungeoeffnet "
                           f"({vor} -> {nach})")
            if frei is not None:
                bericht.pruefe("K7", list(frei.archiv) == [],
                               f"{name}: und die Trefferliste ist leer: "
                               f"{frei.archiv!r}")

        # Der Router fragt das Gate nur auf dem API-Weg. Der lokale Weg und der
        # Aktionsweg fragen es nicht einmal -- gemessen an der echten
        # Vorrichtung, nicht an einem Kommentar.
        freigabe = {"v": 1, "ok": True, "turn_id": "t75-router",
                    "umfang": {"ocr": 1, "archiv": 1}, "senke": "durchgang2",
                    "eintraege": [KANARI_LIVE], "archiv": [KANARI_TREFFER]}
        quellen = Quellen(freigabe)
        router = router_modul.Router(
            quellen=quellen, mind=answer_modul.Durchgang2(mind=Aufzeichner()))
        for text in ("wie spaet ist es", "mach das fenster zu"):
            router.frage({"v": 1, "art": "frage", "text": text,
                          "marke": "user_ptt"})
        bericht.pruefe("K7", quellen.gefragt == [],
                       f"weder der lokale noch der Aktionsweg fragen das Gate: "
                       f"{quellen.gefragt!r}")
        router.frage({"v": 1, "art": "frage", "text": FRAGE_ARCHIV,
                      "marke": "user_ptt"})
        bericht.pruefe("K7", quellen.gefragt == [FRAGE_ARCHIV],
                       f"Positivkontrolle: die Archivfrage fragt es -- die "
                       f"Messung greift: {quellen.gefragt!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K7", f"Nachfragemessung fehlgeschlagen: {exc!r}")


# -- K8 ---------------------------------------------------------------------

class Aufzeichner:
    """Ein Mind, der nichts kann ausser sich merken, was er sehen wuerde."""

    def __init__(self) -> None:
        self.koerper: list[tuple[str, dict]] = []

    def frage_api(self, frage: str, kontext: dict | None = None) -> dict:
        self.koerper.append((frage, kontext or {}))
        return {"v": 1, "ok": True, "antwort": "ok", "status": 200}


class Quellen:
    """Die Quellen des Routers, so knapp wie moeglich. `kontext` antwortet
    wie der Hub -- mit einer Freigabe."""

    def __init__(self, freigabe: dict) -> None:
        self.freigabe = freigabe
        self.gefragt: list[str] = []

    def uhrzeit(self) -> str:
        return "12:00"

    def lautstaerke(self) -> dict:
        return {"prozent": 30, "stumm": False}

    def sitzung(self) -> dict:
        return {"sitzungen": 0, "mood": "idle", "session_id": None}

    def fenster(self) -> list[dict]:
        return [{"titel": "Konsole", "app_id": "t75gibtesnicht"}]

    def kontext(self, text: str) -> dict:
        self.gefragt.append(text)
        return dict(self.freigabe)


def pruefe_k8_eine_handlung(declassify: ModuleType, marks: ModuleType,
                            context: ModuleType, suche: ModuleType,
                            arbeit: Path, archiv_datei: Path,
                            bericht: Bericht) -> None:
    """Live-Kontext und Archivtreffer kommen aus DEMSELBEN `freigeben()`.

    Der Plan verlangt das ausdruecklich, und der Grund steht im Gate: eine
    Handlung, eine Freigabe. Ein zweiter Schein waere ein zweiter Tastendruck
    fuer dieselbe Frage, ohne dass der Nutzer erfuehre warum -- und er waere
    eine zweite Stelle, an der die Bedingung stehen muss.
    """
    try:
        teile = gate_bauen(declassify, marks, context, suche, arbeit, "k8",
                           archiv_datei)
        teile.buch.ausgeben(quelle="auth", turn_id="t75-k8")
        frei = teile.gate.freigeben(aeusserung=FRAGE_ARCHIV, turn_id="t75-k8")

        bericht.pruefe("K8", len(teile.speicher.scheine) == 1
                       and len(teile.archiv.scheine) == 1,
                       f"Live-Kontext und Archiv wurden je genau einmal "
                       f"gefragt: {len(teile.speicher.scheine)} / "
                       f"{len(teile.archiv.scheine)}")
        if teile.speicher.scheine and teile.archiv.scheine:
            bericht.pruefe("K8",
                           teile.archiv.scheine[0] is teile.speicher.scheine[0],
                           f"und zwar mit DEMSELBEN Schein (Identitaet): "
                           f"{teile.archiv.scheine[0]!r} vs "
                           f"{teile.speicher.scheine[0]!r}")
            bericht.pruefe("K8",
                           getattr(teile.archiv.scheine[0], "turn_id", None)
                           == "t75-k8",
                           f"der Schein traegt die Rundenmarke dieser Runde: "
                           f"{teile.archiv.scheine[0]!r}")
        bericht.pruefe("K8", teile.marken.einloesungen == ["t75-k8"],
                       f"und die Marke wurde GENAU EINMAL eingeloest: "
                       f"{teile.marken.einloesungen!r}")
        bericht.pruefe("K8", teile.archiv.anfragen == [FRAGE_ARCHIV],
                       f"die Archivsuche bekam die Aeusserung des Nutzers: "
                       f"{teile.archiv.anfragen!r}")

        # Getrennte Felder: der Empfaenger soll sagen koennen, ob ein Satz von
        # jetzt oder von vorgestern stammt.
        bericht.pruefe("K8", KANARI_LIVE in _texte(frei.eintraege)
                       and KANARI_LIVE not in _texte(frei.archiv),
                       f"der Live-Kontext steht in `eintraege` und nicht in "
                       f"`archiv`: {_texte(frei.eintraege)[:160]!r}")
        bericht.pruefe("K8", KANARI_TREFFER in _texte(frei.archiv)
                       and KANARI_TREFFER not in _texte(frei.eintraege),
                       f"und der Archivtreffer umgekehrt: "
                       f"{_texte(frei.archiv)[:160]!r}")
        bericht.pruefe("K8", int(frei.umfang.get("archiv", -1)) == 1
                       and int(frei.umfang.get("ocr", -1)) == 1,
                       f"der Umfang nennt beide Quellen einzeln: "
                       f"{frei.umfang!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K8", f"Handlungsmessung fehlgeschlagen: {exc!r}")


def pruefe_k8_modellkoerper(router_modul: ModuleType, answer_modul: ModuleType,
                            bericht: Bericht) -> None:
    """Und beide erreichen den Modellkoerper von Durchgang 2.

    Gemessen an den ECHTEN Vorrichtungen: `Router` und `Durchgang2` des
    Prueflings, dazwischen nichts Nachgebautes. Der Aufzeichner sitzt an der
    Stelle, an der sonst der Egress-Weg beginnt. Ohne diese Messung waere
    „der Treffer geht durchs Gate" eine Aussage ueber ein Feld, das niemand
    liest.
    """
    try:
        freigabe = {"v": 1, "ok": True, "turn_id": "t75-router",
                    "umfang": {"ocr": 1, "archiv": 1}, "senke": "durchgang2",
                    "eintraege": [KANARI_LIVE], "archiv": [KANARI_TREFFER]}
        aufzeichner = Aufzeichner()
        quellen = Quellen(freigabe)
        router = router_modul.Router(
            quellen=quellen, mind=answer_modul.Durchgang2(mind=aufzeichner))

        wege = {}
        for text in ("wie spaet ist es", FRAGE_ARCHIV):
            wege[text] = router.frage({"v": 1, "art": "frage", "text": text,
                                       "marke": "user_ptt"})
        api = wege[FRAGE_ARCHIV]
        bericht.pruefe("K8", api.get("weg") == "api",
                       f"Vorbedingung: die Archivfrage geht den API-Weg: "
                       f"{api!r}")
        bericht.pruefe("K8", api.get("durchgang") == answer_modul.DURCHGANG
                       and answer_modul.DURCHGANG == 2,
                       f"und die Antwort kommt aus Durchgang 2: "
                       f"{api.get('durchgang')!r}")
        bericht.pruefe("K8", bool(aufzeichner.koerper),
                       f"Positivkontrolle: es sind Modellkoerper entstanden: "
                       f"{len(aufzeichner.koerper)}")
        mit_treffer = [f for f, k in aufzeichner.koerper
                       if KANARI_TREFFER in json.dumps(k, ensure_ascii=False)]
        bericht.pruefe("K8", mit_treffer == [FRAGE_ARCHIV],
                       f"der Archivtreffer steht in GENAU dem Koerper der "
                       f"Archivfrage und in keinem anderen: {mit_treffer!r}")
        # `Durchgang2` legt den Kontext des Routers unter `kontext` ab -- die
        # Felder darin sind die des Routers.
        koerper = dict(aufzeichner.koerper[-1][1]) if aufzeichner.koerper else {}
        innen = koerper.get("kontext")
        innen = innen if isinstance(innen, dict) else koerper
        bericht.pruefe("K8", KANARI_TREFFER in json.dumps(
            innen.get("archiv", []), ensure_ascii=False),
                       f"und zwar im Feld `archiv`, getrennt vom Bildschirm "
                       f"von jetzt: {koerper!r}")
        bericht.pruefe("K8", KANARI_LIVE in json.dumps(
            innen.get("bildschirm", []), ensure_ascii=False),
                       f"Positivkontrolle: der Live-Kontext steht im Feld "
                       f"`bildschirm`: {koerper!r}")
        bericht.pruefe(
            "K8", KANARI_TREFFER not in json.dumps(wege["wie spaet ist es"],
                                                   ensure_ascii=False),
            f"und die beilaeufige Frage traegt ihn nicht: "
            f"{wege['wie spaet ist es']!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K8", f"Modellkoerpermessung fehlgeschlagen: {exc!r}")


# -- Die Naht ---------------------------------------------------------------

def naht_fahren(hub_a: Werkbank, hub_b: Werkbank, bericht: Bericht) -> None:
    """Fuenf Anfragen ueber `kontext.sock`, in fester Reihenfolge.

    Jede Messung hat ihren Bezugspunkt in der Antwort auf genau die Zeile, die
    der Hub gerade bearbeitet hat -- ein Zeitpunkt, kein Zeitfenster.
    """
    bericht.pruefe("K2", (hub_a.runtime / "kontext.sock").exists(),
                   "Hub A: kontext.sock liegt im Laufzeitverzeichnis")
    bericht.pruefe("K2", hub_a.bereit() is not None,
                   "Positivkontrolle: Hub A horcht und antwortet auf "
                   "state.sock -- er laeuft, wenn er gleich abweist")
    roh_a = hub_a.kontext(FRAGE_ARCHIV, "A: fremde Unit")
    bericht.pruefe("K2", roh_a is None,
                   f"Hub A mit unberuehrter Allowlist weist die fremde Unit "
                   f"des Pruefstands ab (keine Antwort): {roh_a!r}")

    # Die Folge ist so gelegt, dass am Ende KEINE Marke mehr offen ist. Das
    # ist keine Kosmetik: `MarkenBuch` fuehrt mehrere offene Runden zugleich,
    # und `aktuelle()` nimmt die juengste gueltige. Eine Marke, die eine
    # abgelehnte Anfrage ueberlebt hat (r2 -- sie gehoert dem, was gelingt),
    # wuerde r5 sonst still bedienen, und die Sperre waere ungemessen.
    #
    #   r1  keine Marke ausgegeben
    #   r2  Marke M1, kein Bildschirmbezug   -> abgelehnt, M1 bleibt offen
    #   r3  Marke M1, kein Zeitbezug         -> Live ja, Archiv nein; M1 weg
    #   r4  Marke M2, beide Bezuege          -> Live und Archiv; M2 weg
    #   r5  keine offene Marke mehr          -> abgelehnt

    hub_b.kontext(FRAGE_ARCHIV, "r1: ohne Marke")

    hub_b.auth("intent_mark", {})
    vor_r2 = hub_b.diag()["zaehler"]["rundenmarke"]
    hub_b.kontext(FRAGE_OHNE_BEZUG, "r2: Marke ohne Bildschirmbezug")
    nach_r2 = hub_b.diag()["zaehler"]["rundenmarke"]
    bericht.pruefe("K3",
                   nach_r2.get("eingeloest", 0) == vor_r2.get("eingeloest", 0),
                   f"r2 hat die Marke NICHT eingeloest -- sie gehoert dem, was "
                   f"gelingt ({vor_r2!r} -> {nach_r2!r})")

    # DIESELBE Marke: r2 hat sie nicht verbrannt.
    hub_b.kontext(FRAGE_NUR_JETZT, "r3: Marke, Bildschirm, kein Zeitbezug")
    nach_r3 = hub_b.diag()["zaehler"]["rundenmarke"]
    bericht.pruefe("K3",
                   nach_r3.get("eingeloest", 0) == vor_r2.get("eingeloest", 0) + 1,
                   f"und r3 hat sie dann eingeloest ({nach_r2!r} -> "
                   f"{nach_r3!r})")

    hub_b.auth("intent_mark", {})
    hub_b.kontext(FRAGE_ARCHIV, "r4: Marke und beide Bezuege")

    hub_b.kontext(FRAGE_ARCHIV, "r5: Marke verbraucht")


# -- Rahmen -----------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pruefling", type=Path)
    args = parser.parse_args(argv)
    pruefling = args.pruefling.resolve()
    treiber = Path(__file__).resolve().parent / "t75_hub.py"
    bericht = Bericht()

    print(f"Pruefling: {pruefling}")
    print(f"Treiber:   {treiber}")
    print(f"Mutanten-Zuordnung: {json.dumps(MUTANTEN_GRENZEN, ensure_ascii=False)}")

    # `tempfile` ohne eigenes TMPDIR: der Pfad eines AF_UNIX-Sockets ist auf
    # 108 Byte begrenzt, und ein Laufverzeichnis tief im Arbeitsbaum sprengt
    # das.
    with tempfile.TemporaryDirectory(prefix="t75-") as tmp:
        arbeit = Path(tmp)
        hub_a = Werkbank(pruefling, arbeit, "a")
        hub_b = Werkbank(pruefling, arbeit, "b")

        # Der Pruefstand arbeitet in DERSELBEN Umgebung wie Hub B: sonst
        # laese er das Archiv und den Kontextspeicher des Nutzers statt die
        # des Laufs. Das muss VOR dem ersten Produktimport stehen.
        os.environ.update(hub_b.umgebung())

        spur = Spur("archiv.db")

        try:
            declassify = lade_modul(pruefling, "daimon.hub.declassify")
            marks = lade_modul(pruefling, "daimon.hub.marks")
            context = lade_modul(pruefling, "daimon.eyes.context")
            taint = lade_modul(pruefling, "daimon.common.taint")
            protocol = lade_modul(pruefling, "daimon.common.protocol")
            store = lade_modul(pruefling, "daimon.recorder.store")
            suche = lade_modul(pruefling, "daimon.recorder.suche")
            proactive = lade_modul(pruefling, "daimon.mind.proactive")
            router_modul = lade_modul(pruefling, "daimon.mind.router")
            answer_modul = lade_modul(pruefling, "daimon.mind.answer")
        except Exception as exc:  # noqa: BLE001
            for kriterium in KRITERIEN:
                bericht.fehler(kriterium, f"Pruefling nicht ladbar: {exc!r}")
            return bericht.bilanz()

        # Die Kanarienvoegel ins Archiv, mit der ECHTEN Vorrichtung -- BEVOR
        # der Hub startet. Er oeffnet die Datenbank je Freigabe neu.
        try:
            ids = hub_b.archiv_fuellen(store)
            bericht.pruefe("K4", len(ids) == 7 and all(ids),
                           f"Vorbedingung: sieben Kanarienvoegel liegen im "
                           f"Archiv, der Treffer als dritter von fuenf "
                           f"Nachbarn: {ids!r}")
            bericht.pruefe("K4", hub_b.archiv_datei.exists(),
                           f"Vorbedingung: die Archivdatei liegt da: "
                           f"{hub_b.archiv_datei}")
        except Exception as exc:  # noqa: BLE001
            for kriterium in ("K1", "K2", "K4", "K5"):
                bericht.fehler(kriterium, f"Archiv nicht fuellbar: {exc!r}")
        try:
            gelegt = hub_b.kanari_live_ablegen(context.Kontextspeicher)
            bericht.pruefe("K3", gelegt,
                           "Vorbedingung: der Live-Kanarienvogel liegt im "
                           "Quarantaene-Kontextspeicher von Hub B")
        except Exception as exc:  # noqa: BLE001
            bericht.fehler("K3", f"Live-Kanarienvogel nicht ablegbar: {exc!r}")

        # Die Messungen ohne Hub zuerst: sie brauchen keinen Prozess und sagen
        # bei einem kaputten Pruefling frueh, woran es liegt.
        pruefe_k1(declassify, marks, context, suche, arbeit,
                  hub_b.archiv_datei, bericht)
        pruefe_k2_tuer(declassify, suche, hub_b.archiv_datei, bericht)
        pruefe_k4_umgebung_existiert(declassify, marks, context, suche, arbeit,
                                     hub_b.archiv_datei, bericht)
        pruefe_k5(declassify, marks, context, suche, taint, protocol, arbeit,
                  hub_b.archiv_datei, bericht)
        pruefe_k6(declassify, marks, context, suche, proactive, pruefling,
                  arbeit, hub_b.archiv_datei, spur, bericht)
        pruefe_k7(declassify, marks, context, suche, router_modul,
                  answer_modul, arbeit, hub_b.archiv_datei, spur, bericht)
        pruefe_k8_eine_handlung(declassify, marks, context, suche, arbeit,
                                hub_b.archiv_datei, bericht)
        pruefe_k8_modellkoerper(router_modul, answer_modul, bericht)

        try:
            meine_unit = lade_modul(pruefling, "daimon.common.ipc")._unit(
                os.getpid())
            print(f"Unit des Pruefstands: {meine_unit!r}")
            hub_a.starten(treiber)                  # unberuehrte Allowlist
            hub_b.starten(treiber, [meine_unit])    # Allowlist = diese Unit
            for name, hub in (("A", hub_a), ("B", hub_b)):
                if not warte_auf(hub.bereit):
                    for kriterium in ("K2", "K3", "K4"):
                        bericht.fehler(
                            kriterium,
                            f"Hub {name} wurde nicht bereit; Ausgabe: "
                            f"{hub.dienst.ausgabe()[-1500:] if hub.dienst else ''}")
                    return bericht.bilanz()

            naht_fahren(hub_a, hub_b, bericht)

            # POSITIVKONTROLLE DER JOURNAL-ATTRAPPE. Der Hub verankert seine
            # Audit-Kette ueber `systemd-cat`. Greift die Attrappe im PATH
            # nicht, schreibt JEDER Lauf dieses Verifizierers Anker seiner
            # synthetischen Ketten ins echte Journal des Nutzers und verdirbt
            # dort jede spaetere Pruefung (Uebergabe 17.08. §5; T-5.9.v ist
            # das beim Bau einmal passiert). Bleibt die abgefangene Datei
            # leer, wird dieser Pruefstand rot, statt still zu verschmutzen.
            abgefangen = hub_b.journal.read_text(encoding="utf-8")
            bericht.pruefe("K2", "AUDIT-ANKER" in abgefangen,
                           f"Positivkontrolle: die Journal-Attrappe hat den "
                           f"Anker des Hubs abgefangen -- er ist NICHT im "
                           f"echten Journal gelandet: {abgefangen[:200]!r}")

            pruefe_k2_naht(hub_b, bericht)
            pruefe_k3(declassify, hub_b, bericht)
            pruefe_k4(hub_b, bericht)

            for name, hub in (("A", hub_a), ("B", hub_b)):
                bericht.pruefe("K4", hub.dienst is not None and hub.dienst.lebt(),
                               f"Hub {name} hat den ganzen Lauf ueberlebt")
        except Exception as exc:  # noqa: BLE001
            bericht.fehler("K4", f"Naht-Lauf gescheitert: {exc!r}")
        finally:
            for hub in (hub_a, hub_b):
                try:
                    hub.stoppen()
                except Exception as exc:  # noqa: BLE001
                    bericht.fehler("K4", f"Aufraeumen: {exc!r}")

    return bericht.bilanz()


if __name__ == "__main__":
    raise SystemExit(main())
