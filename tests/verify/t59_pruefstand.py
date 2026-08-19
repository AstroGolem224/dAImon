#!/usr/bin/env python3
"""Pruefstand fuer T-5.9 -- das Deklassifizierungs-Gate.

Geprueft wird die AKZEPTANZLISTE von T-5.9 (Implementierungsplan Z. 1563 ff.)
und ihr Verifikationsabsatz, dazu Design 7.2b und 5.1 -- Kriterium fuer
Kriterium, ohne `&&`-Verkettung. Jedes Kriterium rechnet einzeln ab; ein rotes
Kriterium verhindert nicht die Messung der uebrigen. Ein Kriterium OHNE
Messung zaehlt in der Bilanz als rot.

  K1  Ohne frische Rundenmarke keine Freigabe -- und die `turn_id` wandert
      nicht (Akzeptanz 1, T-5.9b)
  K2  Ein API-Kontingent aus dem Wake-Word deklassifiziert NICHTS
      (Akzeptanz 1, Design 7.2b)
  K3  Mit Marke, aber ohne erkennbaren Bildschirmbezug: keine Freigabe
      (Akzeptanz 1)
  K4  Mit beidem MUSS der Kanarienvogel ankommen -- und die Marke gilt einmal
      (Verifikationsabsatz)
  K5  Abgelaufene Marke: keine Freigabe (Verifikationsabsatz)
  K6  Ohne Nutzerhandlung keine Freigabe, auch nicht proaktiv (Akzeptanz 5)
  K7  Die Quarantaene aus T-5.7, aktiv angegriffen: Leseversuche ohne Schein
      scheitern (Verifikationsabsatz)
  K8  Freigegebener Kontext geht ausschliesslich in Durchgang 2 (Akzeptanz 2)
  K9  Jede Freigabe landet im Audit mit Umfang und `turn_id` (Akzeptanz 4)
  K10 Durchgang 1 bekommt opake Referenzen und `app_id` aus einer
      geschlossenen Aufzaehlung -- keine Fenstertitel (Akzeptanz 3, Design 5.1)

DIE PRUEFFRAGE, die der Builder selbst gestellt hat:

    Kommt Bildschirmkontext ohne Rundenmarke ans Modell?

Sie wird an der NAHT gemessen und nicht am Stueck: ein Kanarienvogel geht mit
dem ECHTEN `Kontextspeicher` in den Quarantaenespeicher, eine ECHTE
Rundenmarke entsteht ueber den ECHTEN `auth.sock` des ECHTEN Hubs, und gefragt
wird ueber `kontext.sock` -- den Weg, den der Mind im Betrieb nimmt. Der
Pruefstand baut davon nichts nach.

DREI REGELN DIESES REPOS SCHLAGEN HIER DURCH:

**Negativkontrolle plus Positivkontrolle, beide gewogen.** "Der Kanarienvogel
kam nicht an" ist ohne Gegenprobe nicht von "die Vorrichtung lief gar nicht"
zu unterscheiden. Jede Sperre hier hat ihre Gegenprobe mit DERSELBEN
Zeichenkette auf DEMSELBEN Weg, und zwar so, dass zwischen beiden genau eine
Bedingung anders ist.

**Jede Manipulation wird gewogen.** Der einzige Eingriff dieses Verifizierers
ist die Unit-Allowlist von `kontext.sock` (siehe `t59_hub.py`); sie wird ueber
sha256 vorher/nachher gewogen, und der Treiber bricht ab, wenn sie sich nicht
geaendert hat.

**Eine Messung ist ein Zeitpunkt, kein Zeitfenster.** Kein `--since`. Der
Bezugspunkt jeder Naht-Messung ist die ANTWORT des Hubs auf genau die Zeile,
die er gerade bearbeitet hat; die Audit-Kette wird als FOLGE gelesen (fuenf
Anfragen, fuenf Datensaetze, in dieser Reihenfolge) und nicht als Zaehlerstand
zwischen zwei Zeitpunkten.

DIE HUERDE UND DER GEWAEHLTE WEG (ausfuehrlich im Ledger):
`kontext.sock` laesst nur `daimon-mind.service` durch, und dieser Dienst
LAEUFT auf dieser Maschine -- eine transiente Unit desselben Namens waere ein
Eingriff in den Betrieb. Gemessen wird deshalb mit ZWEI Huben:

  Hub A  unberuehrte Allowlist -> weist den Pruefstand ab. Das ist die echte
         Allowlist gegen die echte Unit-Aufloesung.
  Hub B  Allowlist auf die Unit des Pruefstands gesetzt -> bedient ihn. Das
         ist die Positivkontrolle, und der ganze Rest der Naht laeuft dort.

Nicht gemessen ist damit, dass die Unit des echten Mind-Prozesses
`daimon-mind.service` HEISST. Das ist der Zulauf von T-5.9b.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import re
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

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10")

# Anhang D kennt T-5.9.v nicht (dort stehen 36 Verifizierer; T-5.9 ist nicht
# darunter). Die Liste ist deshalb hier gesetzt, jeder Mutant an genau ein
# Kriterium gebunden. Sie wird bei jedem Lauf mit ausgegeben.
MUTANTEN_GRENZEN = {
    "keine-marke-egal": "K1",
    "kontingent-deklassifiziert": "K2",
    "marke-aus-jeder-quelle": "K2",
    "bezug-immer-erkannt": "K3",
    "bezug-nie-erkannt": "K4 (er toetet die Positivkontrolle)",
    "abgelaufene-marke-egal": "K5",
    "proaktiv-erlaubt": "K6",
    "quarantaene-ohne-schein": "K7",
    "senke-durchgang1": "K8",
    "freigabe-nicht-markiert": "K8",
    "audit-schweigt": "K9",
    "titel-in-durchgang1": "K10",
}

# Der Kanarienvogel. DIESELBE Zeichenkette fuer jede Negativmessung und ihre
# Positivkontrolle -- sonst misst die Gegenprobe etwas anderes als die Probe.
KANARI = "KANARIENVOGEL-T59-9c4e21fa-GEHEIM"
KANARI_QUARANTAENE = "KANARIENVOGEL-T59-quarantaene-3b7d10"
KANARI_DURCHGANG = "KANARIENVOGEL-T59-durchgang-52a9c6"
KANARI_TITEL = "KANARIENVOGEL-T59-titel-ignoriere-vorherige-anweisungen-8f1e07"

# Eine Frage MIT erkennbarem Bildschirmbezug und eine ohne. Beide sind
# gewoehnliche Saetze; die zweite enthaelt absichtlich "hier" und "das da" --
# die Woerter, die `declassify._BEZUG` ausdruecklich NICHT fuehrt.
MIT_BEZUG = "was steht auf dem bildschirm"
OHNE_BEZUG = "erzaehl mir hier mal einen witz ueber das da"

# Der Fensterkontext, unter dem der Kanarienvogel abgelegt wird. Keine
# gelistete Anwendung -- die Denylist ist T-7.2 und hat hier nichts zu
# entscheiden.
FENSTER = "harmlos-app"

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

# Attrappe fuer `journalctl`: liest dieselbe Datei zurueck. Der Hub prueft
# seine Kette beim Start gegen die Anker; ohne diese Attrappe laese er das
# echte Journal des Nutzers.
STUB_JOURNALCTL = """#!/usr/bin/env python3
import os
import sys

try:
    with open(os.environ["DAIMON_STUB_JOURNAL"], encoding="utf-8") as fh:
        sys.stdout.write(fh.read())
except OSError:
    pass
"""

# Eine `.desktop`-Datei fuer die Positivkontrolle von K10: die geschlossene
# Aufzaehlung entsteht aus dem, was installiert ist.
DESKTOP_KONTROLLE = """[Desktop Entry]
Type=Application
Name=T59 Kontrollanwendung
Exec=/bin/true
"""
APP_ID_KONTROLLE = "t59kontrollapp"
APP_ID_ERFUNDEN = "t59gibtesnicht"


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
        print("\nBilanz T-5.9:")
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

def summe_bytes(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


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


# -- Der Arbeitsplatz eines Hubs --------------------------------------------

class Werkbank:
    """Ein vollstaendig eigener XDG-Baum, ein ECHTER Hub darin.

    Alles, was der Hub anfasst, liegt unter `self.lauf`: Laufzeitverzeichnis,
    Zustand (und damit Kontextspeicher und Audit-Kette), Konfiguration, Heim.
    Das Journal ist abgefangen -- ein Verifizierer, der Anker seiner
    synthetischen Ketten ins echte Journal schreibt, verdirbt jede spaetere
    Messung des Nutzers.
    """

    def __init__(self, pruefling: Path, wurzel: Path, name: str) -> None:
        self.pruefling = pruefling
        self.lauf = wurzel / name
        self.runtime = self.lauf / "rt"
        self.state = self.lauf / "state"
        self.bin = self.lauf / "bin"
        self.journal = self.lauf / "journal.txt"
        for pfad in (self.runtime, self.state, self.bin, self.lauf / "config",
                     self.lauf / "cache", self.lauf / "data",
                     self.lauf / "heim"):
            pfad.mkdir(parents=True, exist_ok=True)
        self.journal.write_text("", encoding="utf-8")
        for datei, quelle in (("systemd-cat", STUB_SYSTEMD_CAT),
                              ("journalctl", STUB_JOURNALCTL)):
            pfad = self.bin / datei
            pfad.write_text(quelle, encoding="utf-8")
            pfad.chmod(0o755)
        self.kontext_verzeichnis = self.state / "daimon" / "context"
        self.audit_datei = self.state / "daimon" / "audit" / "audit.jsonl"
        self.dienst: Prozessgruppe | None = None
        self.anfragen: list[tuple[str, bytes]] = []

    # -- Umgebung ----------------------------------------------------------

    def umgebung(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "HOME": str(self.lauf / "heim"),
            "XDG_RUNTIME_DIR": str(self.lauf),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.lauf / "config"),
            "XDG_CACHE_HOME": str(self.lauf / "cache"),
            "XDG_DATA_HOME": str(self.lauf / "data"),
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "DAIMON_STUB_JOURNAL": str(self.journal),
            "PYTHONPATH": str(self.pruefling),
            # Kein DBus dieser Maschine im Prueflauf.
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.lauf}/kein-dbus",
        })
        return env

    # -- Der Kanarienvogel in die Quarantaene ------------------------------

    def kanari_ablegen(self, speicherklasse, text: str) -> bool:
        """Mit der ECHTEN Vorrichtung des Prueflings, nicht mit einer
        nachgebauten JSON-Datei -- sonst prueft der Pruefstand sein eigenes
        Dateiformat."""
        speicher = speicherklasse(verzeichnis=self.kontext_verzeichnis)
        return bool(speicher.hinzufuegen("ocr", FENSTER, text))

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
        """Eine Meldung des Auth-Agenten -- ueber den echten `auth.sock`."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(self.runtime / "auth.sock"))
            sock.sendall(json.dumps({"v": 1, "type": typ,
                                     "payload": payload}).encode() + b"\n")
            time.sleep(0.4)

    def kontext(self, nachricht: dict, marke: str) -> bytes | None:
        """Eine Anfrage ueber `kontext.sock`. `None` = Verbindung abgewiesen.

        Die ROHEN Bytes der Antwort werden aufgehoben: der Kanarienvogel wird
        darin gesucht, nicht in einem geparsten Feld. Was das Modell bekaeme,
        sind diese Bytes.
        """
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

    # -- Messen ------------------------------------------------------------

    def audit_saetze(self) -> list[dict]:
        try:
            roh = self.audit_datei.read_text(encoding="utf-8")
        except OSError:
            return []
        saetze = []
        for zeile in roh.splitlines():
            if zeile.strip():
                try:
                    saetze.append(json.loads(zeile))
                except ValueError:
                    continue
        return saetze

    def audit_text(self) -> str:
        try:
            return self.audit_datei.read_text(encoding="utf-8")
        except OSError:
            return ""


def antwort(roh: bytes | None) -> dict:
    if not roh:
        return {}
    try:
        wert = json.loads(roh)
    except ValueError:
        return {}
    return wert if isinstance(wert, dict) else {}


# -- K1 ---------------------------------------------------------------------

def pruefe_k1_allowlist(hub_a: Werkbank, hub_b: Werkbank,
                        bericht: Bericht) -> None:
    """Der Weg zum Modell, in beide Richtungen.

    Hub A traegt die unberuehrte Allowlist des Prueflings. Dass er ueberhaupt
    laeuft und horcht, steht daneben (`state.sock`) -- sonst waere
    "abgewiesen" nicht von "nie gestartet" zu unterscheiden.
    """
    try:
        bericht.pruefe("K1", (hub_a.runtime / "kontext.sock").exists(),
                       "Hub A: kontext.sock liegt im Laufzeitverzeichnis")
        bericht.pruefe("K1", hub_a.bereit() is not None,
                       "Positivkontrolle: Hub A horcht und antwortet auf "
                       "state.sock -- er laeuft, wenn er gleich abweist")
        roh = hub_a.kontext({"v": 1, "art": "deklassifizieren",
                             "text": MIT_BEZUG}, "A: fremde Unit")
        bericht.pruefe("K1", roh is None,
                       f"Hub A mit unberuehrter Allowlist weist die fremde "
                       f"Unit des Pruefstands ab (keine Antwort): {roh!r}")

        # Die Gegenprobe: derselbe Weg, dieselbe Zeile, dieselbe Unit --
        # geaendert ist NUR die Allowlist.
        roh_b = hub_b.kontext({"v": 1, "art": "deklassifizieren",
                               "text": MIT_BEZUG}, "r1: ohne Marke")
        bericht.pruefe("K1", roh_b is not None,
                       f"Positivkontrolle: Hub B mit der Unit des Pruefstands "
                       f"in der Allowlist antwortet: {roh_b!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K1", f"Allowlist-Messung fehlgeschlagen: {exc!r}")


def pruefe_k1_naht(hub_b: Werkbank, bericht: Bericht) -> None:
    """Ohne Marke: der Kanarienvogel bleibt drin. Gemessen an den ROHEN
    Antwortbytes -- das ist, was der Mind bekaeme."""
    try:
        roh = hub_b.anfragen[-1][1] if hub_b.anfragen else None
        a = antwort(roh)
        bericht.pruefe("K1", a.get("ok") is False,
                       f"ohne Rundenmarke keine Freigabe: {a!r}")
        bericht.pruefe("K1", a.get("grund") == "keine_marke",
                       f"und der Hub nennt den Grund 'keine_marke' "
                       f"(Diagnose, nicht Beleg): {a!r}")
        bericht.pruefe("K1", roh is not None and KANARI.encode() not in roh,
                       f"der Kanarienvogel steht NICHT in den Antwortbytes: "
                       f"{roh!r}")

        # DIE `turn_id` WANDERT NICHT: ein Absender, der sie nennt (und dazu
        # ein Kontingent), bekommt davon nichts. Beides zugleich ist kein
        # Zufall, sondern der Versuch, die schwaechere Bedingung mitlaufen
        # zu lassen.
        roh2 = hub_b.kontext(
            {"v": 1, "art": "deklassifizieren", "text": MIT_BEZUG,
             "turn_id": "1234567890abcdef1234567890abcdef",
             "kontingent": "beliebige-kontingent-id"},
            "r2: ohne Marke, mit geratener turn_id")
        a2 = antwort(roh2)
        bericht.pruefe("K1", a2.get("ok") is False,
                       f"eine mitgeschickte turn_id erzeugt keine Freigabe: {a2!r}")
        bericht.pruefe("K1", roh2 is not None and KANARI.encode() not in roh2,
                       f"und der Kanarienvogel bleibt drin: {roh2!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K1", f"Naht-Messung ohne Marke fehlgeschlagen: {exc!r}")


def pruefe_k1_modul(declassify: ModuleType, context: ModuleType, arbeit: Path,
                    bericht: Bericht) -> None:
    """Dasselbe am Modul: `freigeben` ohne `turn_id` fasst den Speicher nicht
    an."""
    try:
        speicher = context.Kontextspeicher(verzeichnis=arbeit / "k1" / "ctx")
        speicher.hinzufuegen("ocr", FENSTER, KANARI)
        gate = declassify.Deklassifizierung(marken=_marken_attrappe(),
                                            speicher=speicher)
        try:
            gate.freigeben(aeusserung=MIT_BEZUG, turn_id=None)
            bericht.fehler("K1", "freigeben() ohne turn_id hat NICHT abgelehnt")
        except declassify.GateFehler as exc:
            bericht.pruefe("K1", exc.grund == declassify.GRUND_KEINE_MARKE,
                           f"Modul: ohne turn_id ist der Grund "
                           f"{declassify.GRUND_KEINE_MARKE!r}, ist {exc.grund!r}")
        bericht.pruefe("K1",
                       gate.abgelehnt.get(declassify.GRUND_KEINE_MARKE) == 1,
                       f"die Ablehnung wird gezaehlt: {gate.abgelehnt!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K1", f"Modulmessung ohne Marke fehlgeschlagen: {exc!r}")


def _zulauf(pfad: Path) -> dict:
    """Was ruft diese Datei wirklich? Ueber den Syntaxbaum, nicht ueber Text.

    Eine Textsuche findet auch das Wort im Kommentar, in dem jemand erklaert,
    dass er es NICHT tut -- genau der Fall in `mind/proactive.py`.
    """
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    aufrufe: set[str] = set()
    gate_importe: list[str] = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Call):
            ziel = knoten.func
            if isinstance(ziel, ast.Attribute):
                aufrufe.add(ziel.attr)
            elif isinstance(ziel, ast.Name):
                aufrufe.add(ziel.id)
            # `getattr(self._quellen, "kontext", None)` ist ein Aufruf, der
            # seinen Namen als Zeichenkette traegt. Der Router holt sich das
            # Gate genau so; wer das uebersieht, misst am Betrieb vorbei.
            if (isinstance(ziel, ast.Name) and ziel.id == "getattr"
                    and len(knoten.args) >= 2
                    and isinstance(knoten.args[1], ast.Constant)
                    and isinstance(knoten.args[1].value, str)):
                aufrufe.add(knoten.args[1].value)
        elif isinstance(knoten, ast.Import):
            for name in knoten.names:
                if "declassify" in name.name:
                    gate_importe.append(name.name)
        elif isinstance(knoten, ast.ImportFrom):
            woher = knoten.module or ""
            for name in knoten.names:
                if "declassify" in woher or "Deklassifizierung" in name.name:
                    gate_importe.append(f"{woher}.{name.name}")
    return {"aufrufe": aufrufe, "gate_importe": gate_importe}


def _marken_attrappe():
    """Ein Markenbuch, das JEDE Einloesung annimmt.

    Absicht: es soll NICHT die Marke sein, die eine Freigabe verhindert,
    sondern die gepruefte Bedingung. Wer hier ein strenges Buch einsetzte,
    bekaeme gruene Messungen aus dem falschen Grund.
    """
    return SimpleNamespace(einloesen=lambda turn_id: turn_id,
                           aktuelle=lambda: None)


# -- K2 ---------------------------------------------------------------------

def pruefe_k2(declassify: ModuleType, marks: ModuleType, context: ModuleType,
              hub_b: Werkbank, arbeit: Path, bericht: Bericht) -> None:
    """Das Kontingent aus dem Wake-Word deklassifiziert NICHTS (Design 7.2b).

    Der Punkt, an dem ein zu gutmuetiger Verifizierer gruen meldet: das
    Kontingent ist ein echtes, gueltiges, benutzbares Recht -- nur eben nicht
    dieses. Deshalb steht die Positivkontrolle daneben, dass dasselbe
    Kontingent fuer den Egress-Aufruf einloesbar ist.
    """
    try:
        buch = marks.KontingentBuch()
        wake = buch.ausgeben(quelle="wake_word")
        runde = buch.ausgeben(quelle="rundenmarke")
        bericht.pruefe("K2", isinstance(wake, str) and bool(wake),
                       f"Positivkontrolle: ein Kontingent aus dem Wake-Word "
                       f"entsteht ueberhaupt: {wake!r}")
        bericht.pruefe("K2",
                       buch.erlaubt_deklassifizierung(wake) is False,
                       "ein Kontingent aus dem Wake-Word erlaubt KEINE "
                       "Deklassifizierung")
        bericht.pruefe("K2",
                       buch.erlaubt_deklassifizierung(runde) is False,
                       "auch ein Kontingent aus der Rundenmarke erlaubt keine "
                       "Deklassifizierung -- die Zusage ist konstant")
        bericht.pruefe("K2", buch.erlaubt_aktion(wake) is False,
                       "und keine Aktion")
        try:
            buch.einloesen_fuer_egress(wake)
            bericht.pruefe("K2", True,
                           "Positivkontrolle: dasselbe Kontingent IST fuer den "
                           "Egress-Aufruf einloesbar -- es ist ein echtes "
                           "Recht, nur nicht dieses")
        except Exception as exc:  # noqa: BLE001
            bericht.fehler("K2", f"Positivkontrolle Egress-Einloesung: {exc!r}")

        # Ein Wake-Word kann keine RUNDENMARKE erzeugen -- das ist die
        # strukturelle Zusage, auf die sich `declassify` beruft.
        markenbuch = marks.MarkenBuch()
        try:
            markenbuch.ausgeben(quelle="wake_word", turn_id="t59-wake")
            bericht.fehler("K2", "MarkenBuch.ausgeben nahm quelle='wake_word' an")
        except marks.MarkenFehler:
            bericht.pruefe("K2", True,
                           "MarkenBuch.ausgeben lehnt quelle='wake_word' ab")
        marke = markenbuch.ausgeben(quelle="auth", turn_id="t59-auth")
        bericht.pruefe("K2", getattr(marke, "turn_id", "") == "t59-auth",
                       f"Positivkontrolle: aus quelle='auth' entsteht eine "
                       f"Rundenmarke: {marke!r}")

        # Am Gate selbst, mit dem ECHTEN Markenbuch von eben.
        speicher = context.Kontextspeicher(verzeichnis=arbeit / "k2" / "ctx")
        speicher.hinzufuegen("ocr", FENSTER, KANARI)
        gate = declassify.Deklassifizierung(marken=markenbuch, speicher=speicher)

        try:
            gate.freigeben(aeusserung=MIT_BEZUG, turn_id=None, kontingent=wake)
            bericht.fehler("K2", "Kontingent ohne Marke hat freigegeben")
        except declassify.GateFehler as exc:
            bericht.pruefe("K2", exc.grund == declassify.GRUND_KONTINGENT,
                           f"Kontingent ohne Marke: eigener Grund "
                           f"{declassify.GRUND_KONTINGENT!r}, ist {exc.grund!r}")
        try:
            gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-auth",
                           kontingent=wake)
            bericht.fehler("K2", "Marke UND Kontingent zugleich hat freigegeben")
        except declassify.GateFehler as exc:
            bericht.pruefe("K2", exc.grund == declassify.GRUND_KONTINGENT,
                           f"Marke und Kontingent zugleich: abgelehnt mit "
                           f"{exc.grund!r}")
        bericht.pruefe("K2", markenbuch.aktuelle() == "t59-auth",
                       f"und die Marke ist dabei NICHT verbraucht worden: "
                       f"{markenbuch.aktuelle()!r}")

        # Positivkontrolle: dieselbe Marke, dieselbe Aeusserung, derselbe
        # Speicher -- nur ohne Kontingent. Jetzt MUSS es gehen.
        frei = gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-auth")
        inhalt = json.dumps([str(getattr(e, "value", e)) for e in frei.eintraege])
        bericht.pruefe("K2", KANARI in inhalt,
                       f"Positivkontrolle: ohne Kontingent gibt dasselbe Gate "
                       f"denselben Kanarienvogel frei: {inhalt[:200]!r}")

        # Und an der NAHT: das Protokoll von `kontext.sock` kennt gar kein
        # Kontingentfeld. Die Anfrage r2 hat eines mitgeschickt.
        r2 = [roh for marke_, roh in hub_b.anfragen if marke_.startswith("r2")]
        a2 = antwort(r2[-1] if r2 else None)
        bericht.pruefe("K2", a2.get("ok") is False,
                       f"Naht: eine Anfrage mit Kontingentfeld gibt nichts "
                       f"frei: {a2!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K2", f"Kontingentmessung fehlgeschlagen: {exc!r}")


# -- K3 ---------------------------------------------------------------------

def pruefe_k3(declassify: ModuleType, hub_b: Werkbank, bericht: Bericht) -> None:
    """Mit Marke, ohne Bildschirmbezug: keine Freigabe. Und die Marke bleibt.

    An der NAHT gemessen: eine echte Rundenmarke ueber `auth.sock`, dann eine
    Frage ohne Bezug.
    """
    try:
        vorher = hub_b.diag()["zaehler"]["rundenmarke"]
        hub_b.auth("intent_mark", {})
        nachher = hub_b.diag()["zaehler"]["rundenmarke"]
        bericht.pruefe("K3",
                       nachher.get("ausgegeben", 0) == vorher.get("ausgegeben", 0) + 1,
                       f"Vorbedingung: der Auth-Weg erzeugt genau eine "
                       f"Rundenmarke ({vorher!r} -> {nachher!r})")

        roh = hub_b.kontext({"v": 1, "art": "deklassifizieren",
                             "text": OHNE_BEZUG}, "r3: Marke ohne Bezug")
        a = antwort(roh)
        bericht.pruefe("K3", a.get("ok") is False,
                       f"mit Marke, aber ohne Bildschirmbezug: keine Freigabe: "
                       f"{a!r}")
        bericht.pruefe("K3", a.get("grund") == "kein_bildschirmbezug",
                       f"der Hub nennt 'kein_bildschirmbezug': {a!r}")
        bericht.pruefe("K3", roh is not None and KANARI.encode() not in roh,
                       f"der Kanarienvogel steht nicht in den Antwortbytes: "
                       f"{roh!r}")
        bericht.pruefe("K3",
                       hub_b.diag()["zaehler"]["rundenmarke"].get("eingeloest", 0)
                       == vorher.get("eingeloest", 0),
                       "und die Marke ist dabei NICHT eingeloest worden -- sie "
                       "gehoert dem, was gelingt")

        # Die Liste ist bewusst eng. Woerter, die in jedem zweiten Satz
        # stehen, duerfen den Bezug NICHT erfuellen.
        for satz in ("schau mal hier", "was ist das da", "mach mal was",
                     "wie spaet ist es"):
            bericht.pruefe("K3", declassify.bildschirmbezug(satz) is False,
                           f"{satz!r} ist kein Bildschirmbezug")
        # Positivkontrolle: die Erkennung erkennt ueberhaupt etwas.
        for satz in (MIT_BEZUG, "what is on my screen",
                     "welches fenster ist gerade sichtbar"):
            bericht.pruefe("K3", declassify.bildschirmbezug(satz) is True,
                           f"Positivkontrolle: {satz!r} IST ein "
                           f"Bildschirmbezug")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K3", f"Bezugsmessung fehlgeschlagen: {exc!r}")


# -- K4 ---------------------------------------------------------------------

def pruefe_k4(hub_b: Werkbank, bericht: Bericht) -> dict:
    """Mit beidem MUSS der Kanarienvogel ankommen. Die Positivkontrolle des
    ganzen Pruefstands -- ohne sie waere jede Sperre oben auch dann gruen,
    wenn gar nichts liefe."""
    ergebnis: dict = {}
    try:
        vorher = hub_b.diag()["zaehler"]["rundenmarke"].get("eingeloest", 0)
        roh = hub_b.kontext({"v": 1, "art": "deklassifizieren",
                             "text": MIT_BEZUG}, "r4: Marke und Bezug")
        a = antwort(roh)
        ergebnis = a
        bericht.pruefe("K4", a.get("ok") is True,
                       f"mit Marke UND Bildschirmbezug wird freigegeben: {a!r}")
        bericht.pruefe("K4", roh is not None and KANARI.encode() in roh,
                       f"der Kanarienvogel steht in den Antwortbytes -- er "
                       f"kommt beim Modell an: {roh!r}")
        bericht.pruefe("K4", bool(a.get("turn_id")),
                       f"die Antwort nennt die turn_id des Hubs: "
                       f"{a.get('turn_id')!r}")
        bericht.pruefe("K4", a.get("turn_id") != "1234567890abcdef1234567890abcdef",
                       "und es ist NICHT die, die der Absender geraten hat")
        bericht.pruefe("K4", int((a.get("umfang") or {}).get("ocr", 0)) >= 1,
                       f"der Umfang nennt den freigegebenen Eintrag: "
                       f"{a.get('umfang')!r}")
        bericht.pruefe("K4", a.get("senke") == "durchgang2",
                       f"die Freigabe nennt ihre Senke: {a.get('senke')!r}")
        nachher = hub_b.diag()["zaehler"]["rundenmarke"].get("eingeloest", 0)
        bericht.pruefe("K4", nachher == vorher + 1,
                       f"die Einloesung wird gezaehlt ({vorher} -> {nachher}) "
                       f"-- der Zaehler, der bis zum 17.08. keinen Schreiber "
                       f"hatte")

        # EINMAL. Dieselbe Frage sofort noch einmal: die Marke ist verbraucht,
        # der Hub findet keine offene Runde mehr.
        roh2 = hub_b.kontext({"v": 1, "art": "deklassifizieren",
                              "text": MIT_BEZUG}, "r5: Marke verbraucht")
        a2 = antwort(roh2)
        bericht.pruefe("K4", a2.get("ok") is False,
                       f"dieselbe Frage ein zweites Mal: keine Freigabe mehr "
                       f"{a2!r}")
        bericht.pruefe("K4", a2.get("grund") == "keine_marke",
                       f"eine eingeloeste Marke ist keine offene Runde: {a2!r}")
        bericht.pruefe("K4", roh2 is not None and KANARI.encode() not in roh2,
                       f"und der Kanarienvogel bleibt drin: {roh2!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K4", f"Positivmessung fehlgeschlagen: {exc!r}")
    return ergebnis


# -- K5 ---------------------------------------------------------------------

def pruefe_k5(declassify: ModuleType, marks: ModuleType, context: ModuleType,
              arbeit: Path, bericht: Bericht) -> None:
    """Abgelaufene Marke: keine Freigabe.

    Mit injizierter Uhr, nicht mit `sleep`: die Frist des Hubs ist 120 s, und
    ein Verifizierer, der zwei Minuten wartet, wird nicht gefahren. Gemessen
    wird das ECHTE `MarkenBuch` -- nur seine Zeitquelle kommt von hier.
    """
    try:
        takt = [1000.0]
        buch = marks.MarkenBuch(frist_s=10.0, jetzt=lambda: takt[0])
        speicher = context.Kontextspeicher(verzeichnis=arbeit / "k5" / "ctx")
        speicher.hinzufuegen("ocr", FENSTER, KANARI)
        gate = declassify.Deklassifizierung(marken=buch, speicher=speicher)

        buch.ausgeben(quelle="auth", turn_id="t59-frisch")
        bericht.pruefe("K5", buch.aktuelle() == "t59-frisch",
                       f"Vorbedingung: die frische Marke ist die offene Runde: "
                       f"{buch.aktuelle()!r}")
        # Positivkontrolle ZUERST: vor Ablauf wird freigegeben. Danach ist die
        # Marke verbraucht, deshalb kommt die Ablaufmessung mit einer zweiten.
        frei = gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-frisch")
        inhalt = json.dumps([str(getattr(e, "value", e)) for e in frei.eintraege])
        bericht.pruefe("K5", KANARI in inhalt,
                       f"Positivkontrolle: vor Ablauf gibt dasselbe Gate frei: "
                       f"{inhalt[:200]!r}")

        buch.ausgeben(quelle="auth", turn_id="t59-alt")
        takt[0] += 11.0  # ueber die Frist von 10 s hinaus
        bericht.pruefe("K5", buch.aktuelle() is None,
                       f"nach Ablauf ist keine Runde mehr offen: "
                       f"{buch.aktuelle()!r}")
        try:
            gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-alt")
            bericht.fehler("K5", "eine ABGELAUFENE Marke hat freigegeben")
        except declassify.GateFehler as exc:
            bericht.pruefe("K5",
                           exc.grund == declassify.GRUND_MARKE_UNGUELTIG,
                           f"abgelaufene Marke: {declassify.GRUND_MARKE_UNGUELTIG!r}, "
                           f"ist {exc.grund!r}")
        # Und eine zweite Einloesung derselben Marke ebenso.
        try:
            gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-frisch")
            bericht.fehler("K5", "eine bereits eingeloeste Marke hat "
                                 "ein zweites Mal freigegeben")
        except declassify.GateFehler as exc:
            bericht.pruefe("K5",
                           exc.grund == declassify.GRUND_MARKE_UNGUELTIG,
                           f"zweite Einloesung: {exc.grund!r}")
        # Und eine erfundene.
        try:
            gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-erfunden")
            bericht.fehler("K5", "eine ERFUNDENE turn_id hat freigegeben")
        except declassify.GateFehler as exc:
            bericht.pruefe("K5",
                           exc.grund == declassify.GRUND_MARKE_UNGUELTIG,
                           f"unbekannte turn_id: {exc.grund!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K5", f"Ablaufmessung fehlgeschlagen: {exc!r}")


# -- K6 ---------------------------------------------------------------------

def pruefe_k6(declassify: ModuleType, marks: ModuleType, context: ModuleType,
              pruefling: Path, arbeit: Path, bericht: Bericht) -> None:
    """Ohne Nutzerhandlung keine Freigabe -- auch nicht fuer proaktives
    Verhalten (Akzeptanz 5)."""
    try:
        buch = marks.MarkenBuch()
        buch.ausgeben(quelle="auth", turn_id="t59-proaktiv")
        speicher = context.Kontextspeicher(verzeichnis=arbeit / "k6" / "ctx")
        speicher.hinzufuegen("ocr", FENSTER, KANARI)
        gate = declassify.Deklassifizierung(marken=buch, speicher=speicher)

        try:
            gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-proaktiv",
                           proaktiv=True)
            bericht.fehler("K6", "ein proaktiver Aufruf MIT gueltiger Marke "
                                 "und Bildschirmbezug hat freigegeben")
        except declassify.GateFehler as exc:
            bericht.pruefe("K6", exc.grund == declassify.GRUND_PROAKTIV,
                           f"proaktiv wird mit {declassify.GRUND_PROAKTIV!r} "
                           f"abgelehnt, ist {exc.grund!r}")
        bericht.pruefe("K6", buch.aktuelle() == "t59-proaktiv",
                       "die Marke der Runde ist dabei nicht verbraucht worden")
        # Positivkontrolle: derselbe Aufruf ohne `proaktiv` gibt frei. Damit
        # ist belegt, dass die Ablehnung an `proaktiv` lag und an nichts sonst.
        frei = gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-proaktiv")
        inhalt = json.dumps([str(getattr(e, "value", e)) for e in frei.eintraege])
        bericht.pruefe("K6", KANARI in inhalt,
                       f"Positivkontrolle: derselbe Aufruf ohne proaktiv gibt "
                       f"frei: {inhalt[:200]!r}")

        # Der ZULAUF: ruft der proaktive Pfad das Gate ueberhaupt? Er darf
        # nicht -- und wenn er es eines Tages tut, faellt es hier auf.
        #
        # Gemessen ueber den Syntaxbaum, nicht ueber `in text`: `proactive.py`
        # ERKLAERT im Modulkopf, dass es keine Deklassifizierung macht, und
        # eine Textsuche findet ihre eigene Verneinung.
        proaktiv = _zulauf(pruefling / "daimon" / "mind" / "proactive.py")
        router_zulauf = _zulauf(pruefling / "daimon" / "mind" / "router.py")
        bericht.pruefe("K6", not proaktiv["gate_importe"],
                       f"der proaktive Pfad importiert das Gate nicht: "
                       f"{proaktiv['gate_importe']!r}")
        bericht.pruefe("K6", "freigeben" not in proaktiv["aufrufe"],
                       f"und ruft `freigeben` nicht: "
                       f"{sorted(proaktiv['aufrufe'])!r}")
        bericht.pruefe("K6", "kontext" not in proaktiv["aufrufe"],
                       f"und holt sich auch keinen Kontext ueber den Hub: "
                       f"{sorted(proaktiv['aufrufe'])!r}")
        # Positivkontrolle der Suche: dort, wo der Kontext WIRKLICH geholt
        # wird, findet dieselbe Messung ihn.
        bericht.pruefe("K6", "kontext" in router_zulauf["aufrufe"],
                       f"Positivkontrolle: dieselbe Messung findet den Aufruf "
                       f"im Router -- sie greift ueberhaupt: "
                       f"{'kontext' in router_zulauf['aufrufe']}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K6", f"Proaktivmessung fehlgeschlagen: {exc!r}")


# -- K7 ---------------------------------------------------------------------

def pruefe_k7(declassify: ModuleType, context: ModuleType, arbeit: Path,
              bericht: Bericht) -> None:
    """Die Quarantaene aus T-5.7, aktiv angegriffen.

    Der Verifikationsabsatz von T-5.9 verlangt genau das: direkte
    Leseversuche am Gate ohne Marke muessen scheitern. Gemessen wird an der
    ECHTEN `Kontextspeicher`-Instanz mit dem Kanarienvogel darin.
    """
    try:
        speicher = context.Kontextspeicher(verzeichnis=arbeit / "k7" / "ctx")
        speicher.hinzufuegen("ocr", FENSTER, KANARI_QUARANTAENE)
        bericht.pruefe("K7", speicher.zaehler().get("ocr") == 1,
                       f"Vorbedingung: der Kanarienvogel liegt in der "
                       f"Quarantaene: {speicher.zaehler()!r}")

        class Freigabeschein:  # noqa: D401 - eine Attrappe mit passendem Namen
            def __init__(self) -> None:
                self.turn_id = ""

        versuche = [
            ("gar kein Argument", lambda: speicher.freigeben()),
            ("None", lambda: speicher.freigeben(None)),
            ("True", lambda: speicher.freigeben(True)),
            ("die Zahl 1", lambda: speicher.freigeben(1)),
            ("ein nacktes Objekt", lambda: speicher.freigeben(object())),
            ("ein Namespace mit turn_id (der Fehler vom 16.08.)",
             lambda: speicher.freigeben(SimpleNamespace(turn_id="x"))),
            ("ein dict mit turn_id", lambda: speicher.freigeben({"turn_id": "x"})),
            ("ein Schein mit leerer turn_id",
             lambda: speicher.freigeben(declassify.Freigabeschein(turn_id=""))),
            ("eine gleichnamige Attrappe ohne turn_id",
             lambda: speicher.freigeben(Freigabeschein())),
        ]
        for name, versuch in versuche:
            try:
                ergebnis = versuch()
                bericht.fehler("K7", f"Leseversuch {name} hat Eintraege "
                                     f"geliefert: {ergebnis!r}")
            except context.QuarantaeneFehler:
                bericht.pruefe("K7", True,
                               f"Leseversuch {name} scheitert an der "
                               f"Quarantaene")
            except Exception as exc:  # noqa: BLE001
                bericht.fehler("K7", f"Leseversuch {name}: unerwarteter "
                                     f"Fehler {exc!r}")

        # Jede oeffentliche, argumentlose Vorrichtung wird angefasst: keine
        # gibt Inhalt heraus. `leeren` und `freigeben` bleiben draussen -- die
        # eine zerstoerte den Messgegenstand, die andere IST der Ausgang.
        for name in sorted(n for n in dir(speicher) if not n.startswith("_")):
            if name in ("leeren", "freigeben"):
                continue
            wert = getattr(speicher, name)
            try:
                wert = wert() if callable(wert) else wert
            except TypeError:
                continue
            bericht.pruefe("K7", KANARI_QUARANTAENE not in repr(wert),
                           f"{name} gibt keinen Inhalt heraus: {repr(wert)[:160]}")

        # Positivkontrolle: der ECHTE Schein oeffnet. Ohne sie waere
        # "scheitert" nicht von "der Speicher ist leer" zu unterscheiden.
        schein = declassify.Freigabeschein(turn_id="t59-echt")
        eintraege = speicher.freigeben(schein)
        inhalt = json.dumps([str(e) for liste in eintraege.values()
                             for e in liste])
        bericht.pruefe("K7", KANARI_QUARANTAENE in inhalt,
                       f"Positivkontrolle: mit echtem Freigabeschein kommt der "
                       f"Kanarienvogel heraus: {inhalt[:200]!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K7", f"Quarantaenemessung fehlgeschlagen: {exc!r}")


# -- K8 ---------------------------------------------------------------------

def pruefe_k8_markierung(declassify: ModuleType, marks: ModuleType,
                         context: ModuleType, taint: ModuleType,
                         protocol: ModuleType, arbeit: Path,
                         bericht: Bericht) -> None:
    """Freigegebener Kontext ist `tainted`, und die Senkentabelle sperrt ihn
    gegen Durchgang 1."""
    try:
        buch = marks.MarkenBuch()
        buch.ausgeben(quelle="auth", turn_id="t59-senke")
        speicher = context.Kontextspeicher(verzeichnis=arbeit / "k8" / "ctx")
        speicher.hinzufuegen("ocr", FENSTER, KANARI)
        gate = declassify.Deklassifizierung(marken=buch, speicher=speicher)
        frei = gate.freigeben(aeusserung=MIT_BEZUG, turn_id="t59-senke")

        bericht.pruefe("K8", frei.senke == "durchgang2",
                       f"die Freigabe nennt Durchgang 2 als Senke: "
                       f"{frei.senke!r}")
        bericht.pruefe("K8", bool(frei.eintraege),
                       f"Positivkontrolle: es wurde ueberhaupt etwas "
                       f"freigegeben: {frei.umfang!r}")
        for eintrag in frei.eintraege:
            bericht.pruefe("K8",
                           getattr(eintrag, "mark", None) is protocol.Mark.TAINTED,
                           f"jeder freigegebene Eintrag ist tainted: "
                           f"{getattr(eintrag, 'mark', None)!r}")
            try:
                taint.pruefe_senke(eintrag, senke="durchgang1")
                bericht.fehler("K8", "die Senkentabelle liess einen "
                                     "freigegebenen Eintrag in Durchgang 1")
            except taint.SenkenFehler:
                bericht.pruefe("K8", True,
                               "die Senkentabelle sperrt ihn gegen den "
                               "werkzeugfaehigen Durchgang 1")
            try:
                taint.pruefe_senke(eintrag, senke="durchgang2")
                bericht.pruefe("K8", True,
                               "Positivkontrolle: in Durchgang 2 ist er "
                               "erlaubt")
            except taint.SenkenFehler as exc:
                bericht.fehler("K8", f"Durchgang 2 hat ihn abgelehnt: {exc!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K8", f"Markierungsmessung fehlgeschlagen: {exc!r}")


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
        return [{"titel": KANARI_TITEL, "app_id": APP_ID_ERFUNDEN}]

    def kontext(self, text: str) -> dict:
        self.gefragt.append(text)
        return dict(self.freigabe)


def pruefe_k8_router(router_modul: ModuleType, answer_modul: ModuleType,
                     bericht: Bericht) -> None:
    """Der Weg im Prozess mit dem Modell: freigegebener Kontext taucht NUR
    im werkzeuglosen Durchgang 2 auf.

    Gemessen an den ECHTEN Vorrichtungen: `Router` und `Durchgang2` des
    Prueflings, dazwischen nichts Nachgebautes. Der Aufzeichner sitzt an der
    Stelle, an der sonst der Egress-Weg beginnt.
    """
    try:
        freigabe = {"v": 1, "ok": True, "turn_id": "t59-router",
                    "umfang": {"ocr": 1}, "senke": "durchgang2",
                    "eintraege": [KANARI_DURCHGANG], "archiv": []}
        aufzeichner = Aufzeichner()
        quellen = Quellen(freigabe)
        router = router_modul.Router(
            quellen=quellen, mind=answer_modul.Durchgang2(mind=aufzeichner))

        wege = {}
        for text in ("wie spaet ist es", "mach das fenster zu", MIT_BEZUG):
            wege[text] = router.frage({"v": 1, "art": "frage", "text": text,
                                       "marke": "user_ptt"})

        api = wege[MIT_BEZUG]
        bericht.pruefe("K8", api.get("weg") == "api",
                       f"Vorbedingung: die Bildschirmfrage geht den API-Weg: "
                       f"{api!r}")
        bericht.pruefe("K8", api.get("durchgang") == answer_modul.DURCHGANG,
                       f"und die Antwort kommt aus Durchgang "
                       f"{answer_modul.DURCHGANG}: {api.get('durchgang')!r}")
        bericht.pruefe("K8", answer_modul.DURCHGANG == 2,
                       f"Durchgang2.DURCHGANG ist 2: "
                       f"{answer_modul.DURCHGANG!r}")
        bericht.pruefe("K8", router.deklassifiziert == 1,
                       f"genau eine Freigabe wurde eingebaut: "
                       f"{router.deklassifiziert!r}")

        # Positivkontrolle: der Aufzeichner sieht ueberhaupt etwas. Ohne sie
        # waere "der Kanarienvogel steht nirgends" auch dann gruen, wenn nie
        # ein Modellkoerper entstanden ist.
        bericht.pruefe("K8", bool(aufzeichner.koerper),
                       f"Positivkontrolle: es sind Modellkoerper entstanden: "
                       f"{len(aufzeichner.koerper)}")
        mit_kanari = [frage for frage, _ in
                      ((f, k) for f, k in aufzeichner.koerper
                       if KANARI_DURCHGANG in json.dumps(k, ensure_ascii=False))]
        bericht.pruefe("K8", mit_kanari == [MIT_BEZUG],
                       f"der freigegebene Kontext steht in GENAU dem Koerper "
                       f"der Bildschirmfrage und in keinem anderen: "
                       f"{mit_kanari!r}")
        bericht.pruefe("K8", quellen.gefragt == [MIT_BEZUG],
                       f"und das Gate wurde nur auf dem API-Weg gefragt -- der "
                       f"lokale und der Aktionsweg fragen es nicht: "
                       f"{quellen.gefragt!r}")
        for text in ("wie spaet ist es", "mach das fenster zu"):
            bericht.pruefe(
                "K8", KANARI_DURCHGANG not in json.dumps(wege[text],
                                                         ensure_ascii=False),
                f"{text!r} traegt den freigegebenen Kontext nicht: "
                f"{wege[text]!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K8", f"Routermessung fehlgeschlagen: {exc!r}")


# -- K9 ---------------------------------------------------------------------

def pruefe_k9(hub_b: Werkbank, audit_modul: ModuleType, freigabe: dict,
              arbeit: Path, bericht: Bericht) -> None:
    """Jede Freigabe landet im Audit mit Umfang und `turn_id` (Akzeptanz 4).

    Gemessen an der ECHTEN Kette, die der ECHTE Hub waehrend der Naht
    geschrieben hat -- als FOLGE: fuenf Anfragen, fuenf Datensaetze, in dieser
    Reihenfolge. Kein Zeitfenster, kein Zaehlerstand.
    """
    try:
        # POSITIVKONTROLLE DER ATTRAPPE, und sie steht zuerst. Der Hub
        # verankert seine Kette ueber `systemd-cat`. Greift die Attrappe im
        # PATH nicht, schreibt JEDER Lauf dieses Verifizierers Anker seiner
        # synthetischen Ketten ins echte Journal des Nutzers und verdirbt
        # dort jede spaetere Pruefung (Uebergabe 17.08. §5). Genau das ist
        # beim Bau dieses Pruefstands einmal passiert, mit einem Vorlaeufer
        # ohne Attrappe.
        abgefangen = hub_b.journal.read_text(encoding="utf-8")
        bericht.pruefe("K9", "AUDIT-ANKER" in abgefangen,
                       f"Positivkontrolle: die Journal-Attrappe hat den Anker "
                       f"des Hubs abgefangen -- er ist NICHT im echten Journal "
                       f"gelandet: {abgefangen[:200]!r}")

        saetze = [s for s in hub_b.audit_saetze()
                  if s.get("action_id") == "context.declassify"]
        bericht.pruefe("K9", bool(saetze),
                       f"Positivkontrolle: das Gate schreibt ueberhaupt ins "
                       f"Audit ({len(saetze)} Datensaetze) -- der Anschluss "
                       f"vom 17.08. haelt")
        erwartet = [m for m, _ in hub_b.anfragen]
        ausgaenge = [s.get("outcome") for s in saetze]
        bericht.pruefe("K9", len(saetze) == len(erwartet),
                       f"je Anfrage genau ein Datensatz: {len(erwartet)} "
                       f"Anfragen {erwartet!r}, {len(saetze)} Datensaetze "
                       f"{ausgaenge!r}")
        bericht.pruefe("K9",
                       ausgaenge == ["denied", "denied", "denied", "ok", "denied"],
                       f"und die FOLGE der Ausgaenge ist die der Anfragen: "
                       f"{ausgaenge!r}")

        ok_saetze = [s for s in saetze if s.get("outcome") == "ok"]
        bericht.pruefe("K9", len(ok_saetze) == 1,
                       f"genau eine Freigabe im Audit: {len(ok_saetze)}")
        if ok_saetze:
            satz = ok_saetze[0]
            bericht.pruefe("K9", satz.get("turn_id") == freigabe.get("turn_id"),
                           f"die Freigabe traegt die turn_id im Klartext: "
                           f"{satz.get('turn_id')!r} vs "
                           f"{freigabe.get('turn_id')!r}")
            bericht.pruefe("K9", satz.get("initiator") == "foreground",
                           f"und sie ist als Nutzerhandlung vermerkt: "
                           f"{satz.get('initiator')!r}")

            # DER UMFANG. Akzeptanz 4 nennt ihn ausdruecklich neben der
            # turn_id. Gesucht wird er dort, wo ein Mensch ihn spaeter lesen
            # wuerde: in den Feldern des Datensatzes, im Klartext.
            umfang = freigabe.get("umfang") or {}
            roh = json.dumps(satz, ensure_ascii=False, sort_keys=True)
            gefunden = [f"{art}={zahl}" for art, zahl in sorted(umfang.items())
                        if _umfang_lesbar(roh, art, zahl)]
            bericht.pruefe(
                "K9", len(gefunden) == len(umfang) and bool(umfang),
                f"der Umfang der Freigabe steht LESBAR im Datensatz "
                f"(Akzeptanz 4: \"mit Umfang und turn_id\"); gefunden "
                f"{gefunden!r} von {sorted(umfang.items())!r}; Datensatz "
                f"{roh}")

            # Positivkontrolle der Suche: derselbe Umfang, mit demselben
            # `Audit` in ein Zusatzfeld geschrieben, wird gefunden. Ohne sie
            # waere "nicht gefunden" nicht von "die Suche greift nicht" zu
            # unterscheiden.
            probe = audit_modul.Audit.oeffnen(arbeit / "k9" / "audit")
            probe.schreiben(
                ts=1_700_000_000.0, action_id="context.declassify",
                outcome="ok", turn_id="t59-kontrolle", tool_use_id="-",
                params_hash="sha256:0", mark_id="t59-kontrolle",
                initiator="foreground", prompt_shown="probe",
                umfang=dict(umfang))
            kontrollzeile = [z for z in (arbeit / "k9" / "audit" /
                                         audit_modul.DATEI).read_text(
                                             encoding="utf-8").splitlines()
                             if z.strip()][-1]
            kontrolle = [f"{art}={zahl}" for art, zahl in sorted(umfang.items())
                         if _umfang_lesbar(kontrollzeile, art, zahl)]
            bericht.pruefe(
                "K9", len(kontrolle) == len(umfang) and bool(umfang),
                f"Positivkontrolle: derselbe Umfang in einem Zusatzfeld "
                f"desselben Audits wird gefunden -- die Suche greift: "
                f"{kontrolle!r}")

        # Die Aeusserung kam aus einem Mikrofon; sie gehoert nirgends im
        # Klartext hin. Positivkontrolle daneben: der Kanarienvogel steht sehr
        # wohl in den Antwortbytes (K4), die Suche kann ihn also finden.
        text = hub_b.audit_text()
        bericht.pruefe("K9", KANARI not in text,
                       "der Kanarienvogel steht NICHT im Klartext in der "
                       "Audit-Kette")
        bericht.pruefe("K9", MIT_BEZUG not in text,
                       "und die Aeusserung des Nutzers ebenso wenig")
        bericht.pruefe("K9",
                       all(str(s.get("params_hash", "")).startswith("sha256:")
                           for s in saetze),
                       "jeder Datensatz traegt den Abdruck der Aeusserung "
                       "statt ihres Textes")
        bericht.pruefe("K9",
                       all(bool(s.get("prev_hash", "")) or s.get("seq") == 1
                           for s in saetze),
                       "die Datensaetze haengen in der Kette (prev_hash)")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K9", f"Auditmessung fehlgeschlagen: {exc!r}")


def _umfang_lesbar(roh: str, art: str, zahl: int) -> bool:
    """Steht `art` mit seiner Zahl LESBAR in diesem Datensatz?

    Absichtlich grosszuegig: jede Schreibweise zaehlt, die ein Mensch beim
    Lesen als Umfang erkennen wuerde -- `"ocr": 1`, `('ocr', 1)`, `ocr=1`.
    Was NICHT zaehlt, ist ein Abdruck, aus dem sich die Zahl nicht lesen
    laesst.
    """
    muster = re.compile(
        r"(?<![\w-])%s\W{0,4}%d(?![\d])" % (re.escape(art), zahl))
    return bool(muster.search(roh))


# -- K10 --------------------------------------------------------------------

def pruefe_k10(router_modul: ModuleType, pruefling: Path,
               bericht: Bericht) -> None:
    """Durchgang 1 bekommt opake Referenzen und `app_id` aus einer
    geschlossenen Aufzaehlung -- keine Fenstertitel (Design 5.1).

    Die Zusage stand bis zum 16.08. in `declassify.referenzen()` und hatte
    dort keinen Aufrufer; sie ist an die Stelle gewandert, die sie einhaelt.
    Gemessen wird deshalb dort -- und dass sie einen Aufrufer hat, gleich mit.
    """
    try:
        erlaubt = router_modul.app_ids_installiert()
        bericht.pruefe("K10", APP_ID_KONTROLLE in erlaubt,
                       f"Vorbedingung: die Kontrollanwendung ist in der "
                       f"Aufzaehlung ({len(erlaubt)} Eintraege)")
        bericht.pruefe("K10", APP_ID_ERFUNDEN not in erlaubt,
                       "und die erfundene nicht")

        router = router_modul.Router(quellen=None, mind=None)
        fenster = [
            {"titel": KANARI_TITEL, "app_id": APP_ID_ERFUNDEN},
            {"titel": f"Konsole -- {KANARI_TITEL}", "app_id": APP_ID_KONTROLLE},
        ]
        offen = router._referenzen_bilden(fenster)
        roh = json.dumps(offen, ensure_ascii=False)

        # Positivkontrolle ZUERST: der Titel steckt wirklich in der Eingabe,
        # und die Suche findet ihn dort. Sonst waere "nicht gefunden" nicht
        # von "nie dagewesen" zu unterscheiden.
        bericht.pruefe("K10",
                       KANARI_TITEL in json.dumps(fenster, ensure_ascii=False),
                       "Positivkontrolle: der Titel steht in der Eingabe")
        bericht.pruefe("K10", KANARI_TITEL not in roh,
                       f"kein Fenstertitel in dem, was Durchgang 1 bekommt: "
                       f"{roh}")
        bericht.pruefe("K10", "titel" not in roh,
                       f"auch kein Feld, das einen tragen koennte: {roh}")
        bericht.pruefe("K10",
                       all(set(e) == {"ref", "app_id"} for e in offen),
                       f"jede Referenz traegt genau `ref` und `app_id`: {roh}")
        bericht.pruefe("K10",
                       all(re.fullmatch(r"w_\d+", str(e.get("ref")))
                           for e in offen),
                       f"die Referenzen sind opak (`w_N`): {roh}")
        bericht.pruefe("K10", offen and offen[0].get("app_id") == "unbekannt",
                       f"eine nicht installierte app_id wird `unbekannt`: "
                       f"{offen[:1]!r}")
        bericht.pruefe("K10",
                       len(offen) > 1 and offen[1].get("app_id") == APP_ID_KONTROLLE,
                       f"Positivkontrolle: eine installierte app_id kommt "
                       f"durch: {offen[1:2]!r}")

        # Der Titel bleibt in der Tabelle des Hubs -- er ist nicht verloren,
        # nur nicht im Prompt.
        aufgeloest = router.aufloesen("w_1") or {}
        bericht.pruefe("K10", aufgeloest.get("titel") == KANARI_TITEL,
                       f"der Titel bleibt in der Referenztabelle: "
                       f"{aufgeloest!r}")

        # Der ZULAUF: hat die Zusage einen Aufrufer, oder ist sie eine zweite
        # Attrappe wie `declassify.referenzen()` es war?
        quelle = (pruefling / "daimon" / "mind" / "router.py").read_text(
            encoding="utf-8", errors="replace")
        rufe = [z for z in quelle.splitlines()
                if "_referenzen_bilden(" in z and not z.lstrip().startswith("#")
                and not z.lstrip().startswith("def ")]
        bericht.pruefe("K10", len(rufe) >= 1,
                       f"die Referenzbildung hat einen Aufrufer im Betrieb: "
                       f"{rufe!r}")
        # Und die alte Fassung ist wirklich weg -- zwei Fassungen einer Regel
        # sind eine Regel und eine Attrappe.
        gate_quelle = (pruefling / "daimon" / "hub" / "declassify.py").read_text(
            encoding="utf-8", errors="replace")
        code = "\n".join(z for z in gate_quelle.splitlines()
                         if not z.lstrip().startswith("#"))
        bericht.pruefe("K10", "def referenzen(" not in code,
                       "es gibt keine zweite Fassung der Referenzbildung im "
                       "Gate")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K10", f"Referenzmessung fehlgeschlagen: {exc!r}")


# -- Rahmen -----------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pruefling", type=Path)
    args = parser.parse_args(argv)
    pruefling = args.pruefling.resolve()
    treiber = Path(__file__).resolve().parent / "t59_hub.py"
    bericht = Bericht()

    print(f"Pruefling: {pruefling}")
    print(f"Treiber:   {treiber}")
    print(f"Mutanten-Zuordnung: {json.dumps(MUTANTEN_GRENZEN, ensure_ascii=False)}")

    # `tempfile` ohne eigenes TMPDIR: der Pfad eines AF_UNIX-Sockets ist auf
    # 108 Byte begrenzt, und ein Laufverzeichnis tief im Arbeitsbaum sprengt
    # das. Gemessen: `OSError: AF_UNIX path too long`.
    with tempfile.TemporaryDirectory(prefix="t59-") as tmp:
        arbeit = Path(tmp)
        hub_a = Werkbank(pruefling, arbeit, "a")
        hub_b = Werkbank(pruefling, arbeit, "b")

        # Der Pruefstand arbeitet in DERSELBEN Umgebung wie Hub B: sonst
        # laese er den Kontextspeicher und die Anwendungsliste des Nutzers
        # statt die des Laufs. Das muss VOR dem ersten Produktimport stehen --
        # `router._ANWENDUNGEN` wird beim Import aus `HOME` gebildet.
        os.environ.update(hub_b.umgebung())
        anwendungen = Path(os.environ["HOME"]) / ".local/share/applications"
        anwendungen.mkdir(parents=True, exist_ok=True)
        (anwendungen / f"{APP_ID_KONTROLLE}.desktop").write_text(
            DESKTOP_KONTROLLE, encoding="utf-8")

        try:
            declassify = lade_modul(pruefling, "daimon.hub.declassify")
            marks = lade_modul(pruefling, "daimon.hub.marks")
            context = lade_modul(pruefling, "daimon.eyes.context")
            taint = lade_modul(pruefling, "daimon.common.taint")
            protocol = lade_modul(pruefling, "daimon.common.protocol")
            audit_modul = lade_modul(pruefling, "daimon.hub.audit")
            router_modul = lade_modul(pruefling, "daimon.mind.router")
            answer_modul = lade_modul(pruefling, "daimon.mind.answer")
        except Exception as exc:  # noqa: BLE001
            for kriterium in KRITERIEN:
                bericht.fehler(kriterium, f"Pruefling nicht ladbar: {exc!r}")
            return bericht.bilanz()

        # Die Messungen ohne Hub zuerst: sie brauchen keinen Prozess und
        # sagen bei einem kaputten Pruefling frueh, woran es liegt.
        pruefe_k1_modul(declassify, context, arbeit, bericht)
        pruefe_k5(declassify, marks, context, arbeit, bericht)
        pruefe_k6(declassify, marks, context, pruefling, arbeit, bericht)
        pruefe_k7(declassify, context, arbeit, bericht)
        pruefe_k8_markierung(declassify, marks, context, taint, protocol,
                             arbeit, bericht)
        pruefe_k8_router(router_modul, answer_modul, bericht)
        pruefe_k10(router_modul, pruefling, bericht)

        # Der Kanarienvogel geht mit der ECHTEN Vorrichtung in die
        # Quarantaene, BEVOR der Hub startet -- er laedt sie bei jeder
        # Anfrage neu.
        try:
            gelegt = hub_b.kanari_ablegen(context.Kontextspeicher, KANARI)
            bericht.pruefe("K4", gelegt,
                           "Vorbedingung: der Kanarienvogel liegt im "
                           "Quarantaene-Kontextspeicher von Hub B")
        except Exception as exc:  # noqa: BLE001
            bericht.fehler("K4", f"Kanarienvogel nicht ablegbar: {exc!r}")

        try:
            meine_unit = lade_modul(pruefling, "daimon.common.ipc")._unit(
                os.getpid())
            print(f"Unit des Pruefstands: {meine_unit!r}")
            hub_a.starten(treiber)                  # unberuehrte Allowlist
            hub_b.starten(treiber, [meine_unit])    # Allowlist = diese Unit
            for name, hub in (("A", hub_a), ("B", hub_b)):
                if not warte_auf(hub.bereit):
                    for kriterium in ("K1", "K2", "K3", "K4", "K9"):
                        bericht.fehler(
                            kriterium,
                            f"Hub {name} wurde nicht bereit; Ausgabe: "
                            f"{hub.dienst.ausgabe()[-1500:] if hub.dienst else ''}")
                    return bericht.bilanz()

            pruefe_k1_allowlist(hub_a, hub_b, bericht)
            pruefe_k1_naht(hub_b, bericht)
            pruefe_k2(declassify, marks, context, hub_b, arbeit, bericht)
            pruefe_k3(declassify, hub_b, bericht)
            freigabe = pruefe_k4(hub_b, bericht)
            pruefe_k9(hub_b, audit_modul, freigabe, arbeit, bericht)

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
