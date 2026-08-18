#!/usr/bin/env python3
"""Pruefstand fuer T-4.7 — den DBus-Broker mit Argumentvalidierung.

Geprueft wird die AKZEPTANZLISTE von T-4.7 (Implementierungsplan, Z. 1260 ff.)
und der Verifikationsabsatz (Z. 1270), Kriterium fuer Kriterium, ohne
`&&`-Verkettung. Jedes Kriterium rechnet einzeln ab; ein rotes Kriterium
verhindert nicht die Messung der uebrigen.

  K1  Eine Operation je `approved`-Aktion, feste Parameter -- kein generisches
      `invokeShortcut`
  K2  Nicht genehmigter Shortcut DERSELBEN Komponente wird abgewiesen (der
      Kern), auch wenn der Proxy die Methode durchlaesst
  K3  `xdg-dbus-proxy --filter` als zweite Schicht, `--log` aktiv, und der Log
      enthaelt die Versuche -- samt Zulauf: wer startet ihn im Betrieb
  K4  `org.kde.kwin.Scripting.loadScript` ist nicht erreichbar: der Versuch
      wird abgewiesen, und es entsteht keine Datei
  K5  Sandbox nach Design 7.5
  K6  Positiv-Kanarienvogel: ein genehmigter Shortcut wird AUSGEFUEHRT und die
      Wirkung gemessen -- ohne ihn waere "alles abgewiesen" auch dann gruen,
      wenn der Broker gar nicht laeuft

Was hier wirklich passiert -- und was ausdruecklich nicht
----------------------------------------------------------------------------
Dieser Pruefstand fasst den laufenden Desktop an. Er muss: der Plan verlangt
einen ausgefuehrten Shortcut mit gepruefter Wirkung. Gewaehlt ist
`desktop.next` (kglobalaccel-Komponente `kwin`, Kurzbefehl "Switch to Next
Desktop"): die Wirkung ist an einer DBus-Eigenschaft ablesbar
(`org.kde.KWin.VirtualDesktopManager.current`), sie schliesst kein Fenster,
beendet keine Sitzung, beruehrt keinen `daimon-*`-Dienst -- und sie ist
exakt wiederherstellbar, weil dieselbe Eigenschaft schreibbar ist. Der
Vorzustand wird vor dem ersten Eingriff gelesen und in einem `finally`
zurueckgeschrieben; stimmt er danach nicht, bricht der Lauf laut ab.

`loadScript` wird NICHT scharf ausprobiert. Belegt wird die Abweisung: der
Versuch geht durch den Proxy, der Pfad zeigt auf eine Datei, die es nicht
gibt, und gemessen wird, WER das Nein gesagt hat (der Filter, nicht KWin) --
plus dass keine Datei entstanden ist. Ein KWin-Skript, das durchkaeme, liesse
sich nicht zurueckdrehen; also gibt es hier keines.

Kein `systemctl`, kein Anklopfen an einem produktiven Socket, kein Start eines
Wahrnehmungsdienstes. Der Proxy, der Katalog und alle Protokolle liegen in
einem `TemporaryDirectory`.

Jede Manipulation wird gewogen
----------------------------------------------------------------------------
`waegen()` bricht ab, wenn ein Eingriff nichts veraendert hat. Und zu jeder
Verweigerung steht eine Positivkontrolle: dieselbe Leitung, derselbe Aufruf,
nur ohne den Grund der Verweigerung -- der MUSS durchkommen. "Nichts
gefunden" muss von "nicht gemessen" unterscheidbar bleiben.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6")

# Anhang D kennt T-4.7.v nicht. Die Mutanten sind deshalb hier gesetzt, jeder
# an genau ein Akzeptanzkriterium gebunden. Die Zuordnung wird bei jedem Lauf
# ausgegeben, damit sie nicht in einem Kommentar verrottet.
MUTANTEN_GRENZEN = {
    "shortcut-aus-auftrag": "K1",
    "operation-generisch": "K1",
    "status-egal": "K2",
    "dienst-aus-katalog": "K4",
    "filter-startet-nicht": "K3",
    "filter-ohne-log": "K3",
    "filter-ohne-filter": "K3",
    "proxy-ohne-zulauf": "K3",
    "kwin-durch-den-filter": "K4",
    "sandbox-privateusers": "K5",
    "sandbox-ohne-caps": "K5",
    "broker-fuehrt-nicht-aus": "K6",
}

# ABGESCHRIEBEN aus Plan und Design, nicht aus dem Pruefling geholt -- sonst
# pruefte der Verifizierer die Zusage gegen sich selbst.
DIENST_SOLL = "org.kde.kglobalaccel"
SCHNITTSTELLE_SOLL = "org.kde.kglobalaccel.Component"
METHODE_SOLL = "invokeShortcut"
VERBOTENE_DIENSTE = ("org.kde.KWin", "org.kde.kwin.Scripting",
                     "org.kde.KWin.Scripting")

# Der Kanarienvogel und seine Umkehr. Beide stehen als `approved` im Katalog
# des Pruefligs; beide sind Kurzbefehle der KOMPONENTE `kwin`.
KANARIE = "desktop.next"
KANARIE_ZURUECK = "desktop.previous"

# Der Kern-Fall: derselbe Dienst, dieselbe Komponente, dieselbe Methode --
# ein Kurzbefehl, der im Katalog NICHT `approved` ist. "Switch to Desktop 1"
# steht so in config/actions/candidates.yaml (Status `candidate`) und in
# core.yaml an keiner Stelle. Seine Wirkung ist mit demselben Instrument
# messbar wie die des Kanarienvogels -- ein Durchschlupf faellt also auf.
KERN_AKTION = "pruef.desktop.eins"
KERN_KOMPONENTE = "kwin"
KERN_KURZBEFEHL = "Switch to Desktop 1"

# Ein Kurzbefehl, den es nicht gibt. Er dient dem Nachweis, dass der Proxy die
# METHODE durchlaesst, ohne den Kurzbefehl anzusehen -- ohne dabei etwas
# auszuloesen.
NICHTS_KURZBEFEHL = "daimon-t47-kein-kurzbefehl"

KWIN_DIENST = "org.kde.KWin"
VD_PFAD = "/VirtualDesktopManager"
VD_SCHNITTSTELLE = "org.kde.KWin.VirtualDesktopManager"

# Design 7.5, Absatz "Direktiven, die brechen". Hier als Namensliste, mit
# Positivkontrolle: die Namen muessen im Design des Pruefligs auch wirklich
# stehen -- sonst misst dieser Verifizierer gegen eine Regel, die es nicht
# mehr gibt.
BRECHENDE_DIREKTIVEN = ("ProtectHome=yes", "PrivateUsers=yes",
                        "MemoryDenyWriteExecute=yes")
# Design 7.5, Zeile `dbus` der Abweichungstabelle.
DBUS_ABWEICHUNG = {"ProtectHome": "read-only", "PrivateDevices": "yes"}


# -- Bericht ----------------------------------------------------------------

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
        print("\nBilanz T-4.7:")
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

def waegen(vorher: Any, nachher: Any, beschreibung: str) -> None:
    """Eine Manipulation, die nachweislich etwas bewegt.

    Wer hier nichts veraendert, bekommt keinen gruenen Lauf, sondern einen
    Abbruch -- der folgende Befund waere sonst nicht gemessen, sondern
    erfunden.
    """
    if vorher == nachher:
        raise RuntimeError(
            f"POSITIVKONTROLLE GESCHEITERT: '{beschreibung}' hat nichts "
            f"veraendert (vorher == nachher == {vorher!r}).")
    print(f"Manipulation '{beschreibung}': {vorher!r} -> {nachher!r}")


def lade(pruefling: Path, name: str) -> ModuleType:
    """Ein Modul AUS DEM PRUEFLING, mit Nachweis, dass es von dort kommt."""
    if str(pruefling) not in sys.path:
        sys.path.insert(0, str(pruefling))
    importlib.invalidate_caches()
    for eintrag in tuple(sys.modules):
        if eintrag == "daimon" or eintrag.startswith("daimon."):
            del sys.modules[eintrag]
    modul = importlib.import_module(name)
    quelle = Path(modul.__file__ or "").resolve()
    try:
        quelle.relative_to(pruefling.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Import entkam dem Pruefling: {quelle}") from exc
    return modul


def eigener_hash(params: dict | None) -> str:
    """Unabhaengig nachgerechnet, nicht aus dem Pruefling geholt."""
    text = json.dumps(params or {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def auftrag_bytes(action_id: str, *, params: dict | None = None,
                  frist: float, ticket: str = "t-" + "a" * 40) -> bytes:
    """Die kanonische Auftragszeile, unabhaengig gebaut (Design 6.2)."""
    daten = {
        "audience": "dbus",
        "schema": "daimon.order.v1",
        "action_id": action_id,
        "params": params or {},
        "params_hash": eigener_hash(params),
        "ticket": ticket,
        "deadline_monotonic": frist,
        "turn_id": "t47",
    }
    return json.dumps(daten, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def baum(pfad: Path) -> ast.AST | None:
    try:
        return ast.parse(pfad.read_text(encoding="utf-8"), filename=str(pfad))
    except (OSError, SyntaxError):
        return None


def dateibaum(wurzeln: list[Path]) -> dict[str, str]:
    """Ein Abzug: Pfad -> sha256. Die Grundlage von "keine Datei entstand"."""
    abzug: dict[str, str] = {}
    for wurzel in wurzeln:
        if not wurzel.exists():
            continue
        for pfad in sorted(wurzel.rglob("*")):
            if pfad.is_file():
                try:
                    abzug[str(pfad)] = hashlib.sha256(
                        pfad.read_bytes()).hexdigest()[:16]
                except OSError:
                    abzug[str(pfad)] = "unlesbar"
    return abzug


# -- Der echte Sitzungsbus, als Messinstrument ------------------------------

def echter_bus() -> str:
    adresse = os.environ.get("DAIMON_T47_ECHTER_BUS", "")
    if adresse:
        return adresse
    laufzeit = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return f"unix:path={laufzeit}/bus"


def gdbus(adresse: str, dienst: str, pfad: str, methode: str, *args: str,
          timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Ein Aufruf ueber eine BENANNTE Leitung.

    Nie `--session`: welcher Bus gemeint ist, steht hier im Klartext. Der
    Pruefstand misst am echten Bus und laesst den Broker durch den Proxy
    sprechen -- die beiden duerfen nie durcheinandergeraten.
    """
    return subprocess.run(
        ["gdbus", "call", "--address", adresse, "--dest", dienst,
         "--object-path", pfad, "--method", methode, *args],
        capture_output=True, text=True, timeout=timeout)


def desktop_jetzt(adresse: str) -> str:
    """Die aktuelle Arbeitsflaeche als UUID. Das Messinstrument fuer K2/K6."""
    e = gdbus(adresse, KWIN_DIENST, VD_PFAD,
              "org.freedesktop.DBus.Properties.Get",
              VD_SCHNITTSTELLE, "current")
    treffer = re.search(r"'([0-9a-f-]{36})'", e.stdout)
    if not treffer:
        raise RuntimeError(
            f"Arbeitsflaeche nicht lesbar (rc={e.returncode}): "
            f"{(e.stdout + e.stderr).strip()[:200]}")
    return treffer.group(1)


def desktop_zaehlen(adresse: str) -> int:
    e = gdbus(adresse, KWIN_DIENST, VD_PFAD,
              "org.freedesktop.DBus.Properties.Get",
              VD_SCHNITTSTELLE, "count")
    treffer = re.search(r"uint32 (\d+)", e.stdout)
    return int(treffer.group(1)) if treffer else 0


def desktop_setzen(adresse: str, uuid: str) -> None:
    """Die Wiederherstellung. Schreibt dieselbe Eigenschaft, die gelesen wird."""
    gdbus(adresse, KWIN_DIENST, VD_PFAD,
          "org.freedesktop.DBus.Properties.Set",
          VD_SCHNITTSTELLE, "current", f"<'{uuid}'>")
    time.sleep(0.4)


# -- Der Proxy --------------------------------------------------------------

def optionen_aus(pfad: Path) -> list[str]:
    """Die Optionsliste einer Filterdatei: Kommentar- und Leerzeilen fort.

    `--args=FD` liest NUL-getrennte Argumente (man 1 xdg-dbus-proxy). Diese
    Umwandlung ist die wohlwollende Lesart der Datei -- gemessen wird danach,
    ob der Proxy mit dieser Liste ueberhaupt startet.
    """
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    return [z.strip() for z in zeilen
            if z.strip() and not z.strip().startswith("#")]


class Proxy:
    """Ein `xdg-dbus-proxy` im Tempverzeichnis, mit Protokoll und Cursor."""

    def __init__(self, optionen: list[str], arbeitsverzeichnis: Path,
                 name: str, bus: str) -> None:
        self.socket = arbeitsverzeichnis / f"{name}.sock"
        self.log = arbeitsverzeichnis / f"{name}.log"
        self.optionen = list(optionen)
        self.bus = bus
        self.prozess: subprocess.Popen | None = None
        self.fehler = ""

    @property
    def adresse(self) -> str:
        return f"unix:path={self.socket}"

    def starten(self, wartezeit: float = 5.0) -> bool:
        argv = ["xdg-dbus-proxy", self.bus, str(self.socket), *self.optionen]
        if shutil.which("stdbuf"):
            # Ohne Zeilenpufferung kaeme das Protokoll erst beim Beenden --
            # und eine Messung, die auf das Ende wartet, ist kein Zeitpunkt.
            argv = ["stdbuf", "-o0", "-e0", *argv]
        mit = open(self.log, "wb")
        self.prozess = subprocess.Popen(argv, stdout=mit, stderr=mit,
                                        start_new_session=True)
        ende = time.monotonic() + wartezeit
        while time.monotonic() < ende:
            if self.socket.exists():
                time.sleep(0.2)
                return True
            if self.prozess.poll() is not None:
                self.fehler = self.log.read_text(
                    encoding="utf-8", errors="replace").strip()
                return False
            time.sleep(0.1)
        self.fehler = "Zeitueberschreitung: kein Socket"
        return False

    def cursor(self) -> int:
        """Ein BEZUGSPUNKT, kein Zeitfenster: die Laenge des Protokolls."""
        try:
            return self.log.stat().st_size
        except OSError:
            return 0

    def seit(self, cursor: int) -> str:
        time.sleep(0.3)  # dem Proxy Zeit lassen, die Zeile zu schreiben
        with open(self.log, "rb") as fh:
            fh.seek(cursor)
            return fh.read().decode("utf-8", errors="replace")

    def beenden(self) -> None:
        if self.prozess and self.prozess.poll() is None:
            self.prozess.terminate()
            try:
                self.prozess.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.prozess.kill()
                self.prozess.wait(timeout=3)
        self.socket.unlink(missing_ok=True)


# -- Kataloge ---------------------------------------------------------------

KERN_EINTRAG = """
  - id: {kennung}
    quelle: "kglobalaccel:{komponente}"
    kglobalaccel: ["{komponente}", "{kurzbefehl}"]
    params: {{}}
    destructive: false
    reversible_by: null
    externally_visible: false
    open_world: false
    never_cacheable: false
    direct: false
    foreground: ask
    background: deny
    scheduled: deny
    status: {status}
"""


def katalog_mit_kern(quelle: Path, ziel: Path, status: str) -> Path:
    """Der Katalog des Pruefligs plus EIN Eintrag mit gesetztem Status.

    Nur `status` unterscheidet die beiden Fassungen. Damit misst K2 genau
    das, was der Plan den Kern nennt -- nicht die Komponente, nicht die
    Methode, nicht den Objektpfad: die Genehmigung.
    """
    text = quelle.read_text(encoding="utf-8")
    text += KERN_EINTRAG.format(kennung=KERN_AKTION, komponente=KERN_KOMPONENTE,
                                kurzbefehl=KERN_KURZBEFEHL, status=status)
    ziel.write_text(text, encoding="utf-8")
    return ziel


# -- Unit-Dateien -----------------------------------------------------------

def unit_lesen(pfad: Path) -> list[tuple[str, str]]:
    """Direktiven einer Unit als Paare, Fortsetzungszeilen zusammengezogen."""
    if not pfad.exists():
        return []
    roh = pfad.read_text(encoding="utf-8")
    roh = re.sub(r"\\\n\s*", " ", roh)
    paare = []
    for zeile in roh.splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or zeile.startswith("["):
            continue
        if "=" not in zeile:
            continue
        schluessel, _, wert = zeile.partition("=")
        paare.append((schluessel.strip(), wert.strip()))
    return paare


def design_basis(design: Path) -> list[tuple[str, str]]:
    """Die Basiszeilen aus Design 7.5 -- AUS DEM DESIGN, nicht abgeschrieben."""
    text = design.read_text(encoding="utf-8")
    stelle = text.find("### 7.5 Sandbox")
    if stelle < 0:
        return []
    block = re.search(r"```ini\n(.*?)```", text[stelle:], re.S)
    if not block:
        return []
    paare = []
    for zeile in block.group(1).splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, _, wert = zeile.partition("=")
        paare.append((schluessel.strip(), wert.strip()))
    return paare


def _nennt_ausfuehrbar(b: ast.AST, text: str) -> bool:
    """Nennt dieser Syntaxbaum `text` an einer Stelle, die etwas TUT?

    Docstrings zaehlen nicht. Der erste Lauf dieses Pruefstands hielt den
    Modulkopf von `brokers/dbus/broker.py` -- "Davor: xdg-dbus-proxy
    --filter ..." -- fuer einen Zulauf und meldete Ruhe, wo keine ist. Genau
    dieser Absatz ist die Zusage, deren Einloesung hier gemessen wird; er
    darf sie nicht selbst belegen.
    """
    docstrings = set()
    for knoten in ast.walk(b):
        if isinstance(knoten, (ast.Module, ast.FunctionDef,
                               ast.AsyncFunctionDef, ast.ClassDef)):
            koerper = getattr(knoten, "body", None) or []
            if koerper and isinstance(koerper[0], ast.Expr) and \
                    isinstance(koerper[0].value, ast.Constant) and \
                    isinstance(koerper[0].value.value, str):
                docstrings.add(id(koerper[0].value))
    for knoten in ast.walk(b):
        if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str) \
                and text in knoten.value and id(knoten) not in docstrings:
            return True
    return False


def proxy_zulauf(pruefling: Path) -> dict:
    """Wer startet den Proxy im Betrieb, und spricht der Broker durch ihn?

    Gelesen werden die Units unter config/systemd/ und der Produktivbaum.
    Nicht "steht es irgendwo geschrieben", sondern: gibt es eine Zeile, die
    ihn AUSFUEHRT, und zeigt der Broker auf seinen Socket.
    """
    befund = {"starter": [], "adresse": "", "verlangt": False, "erwaehnt": []}
    systemd = pruefling / "config" / "systemd"
    for unit in sorted(systemd.glob("*.service")) if systemd.exists() else []:
        for schluessel, wert in unit_lesen(unit):
            if schluessel.startswith("ExecStart") and "xdg-dbus-proxy" in wert:
                befund["starter"].append(unit.name)
    for pfad in sorted((pruefling / "daimon").rglob("*.py")):
        b = baum(pfad)
        if b is None:
            continue
        if _nennt_ausfuehrbar(b, "xdg-dbus-proxy"):
            befund["starter"].append(str(pfad.relative_to(pruefling)))
    broker_unit = systemd / "daimon-dbus.service"
    for schluessel, wert in unit_lesen(broker_unit):
        if schluessel == "Environment" and "DBUS_SESSION_BUS_ADDRESS" in wert:
            befund["adresse"] = wert
        if schluessel in ("Requires", "BindsTo", "Wants") and "proxy" in wert:
            befund["verlangt"] = True
        if schluessel == "ExecStart" and "DBUS_SESSION_BUS_ADDRESS" in wert:
            befund["adresse"] = wert
    return befund


# -- Die Kriterien ----------------------------------------------------------

def k1_operationstabelle(b: Bericht, broker_modul, daemon_modul,
                        katalog: dict, frist: float) -> None:
    """K1 — eine feste Operation je genehmigter Aktion, feste Parameter."""
    genehmigt = {kennung: eintrag for kennung, eintrag in katalog.items()
                 if eintrag.get("status") == "approved"
                 and eintrag.get("kglobalaccel")}
    broker = broker_modul.DBusBroker.aus_katalog(katalog)
    b.pruefe("K1", bool(genehmigt),
             f"der Katalog des Pruefligs traegt genehmigte Aktionen mit "
             f"kglobalaccel-Ursprung ({len(genehmigt)})")
    b.pruefe("K1", set(broker.operationen) == set(genehmigt),
             f"eine Operation je genehmigter Aktion: Tabelle "
             f"{sorted(set(broker.operationen) ^ set(genehmigt)) or 'deckungsgleich'}")

    fehlerhaft = []
    for kennung, eintrag in genehmigt.items():
        operation = broker.operationen.get(kennung)
        if operation is None:
            fehlerhaft.append(f"{kennung}: keine Operation")
            continue
        komponente, aktion = eintrag["kglobalaccel"]
        pfad_soll = "/component/" + re.sub(r"[^A-Za-z0-9_]", "_", str(komponente))
        if (operation.dienst != DIENST_SOLL
                or operation.schnittstelle != SCHNITTSTELLE_SOLL
                or operation.methode != METHODE_SOLL
                or tuple(operation.argumente) != (str(aktion),)
                or operation.pfad != pfad_soll):
            fehlerhaft.append(f"{kennung}: {operation}")
    b.pruefe("K1", not fehlerhaft,
             f"jede Operation traegt festes Ziel, feste Schnittstelle, feste "
             f"Methode und das Argument AUS DEM KATALOG "
             f"({fehlerhaft[:2] or 'alle ' + str(len(genehmigt))})")

    # Feste Parameter: derselbe Auftrag mit anderen `params` ergibt dieselbe
    # Argumentzeile. Gemessen an einem Spitzel, der nichts ausfuehrt.
    aufgezeichnet: list[list[str]] = []

    def spitzel(argv, **kw):
        aufgezeichnet.append(list(argv))
        class E:
            returncode = 0
            stderr = ""
        return E()

    still = broker_modul.DBusBroker(operationen=dict(broker.operationen),
                                    lauf=spitzel)
    schlicht = auftrag_bytes(KANARIE, frist=frist)
    geschmuggelt = auftrag_bytes(
        KANARIE, params={"component": KERN_KOMPONENTE,
                         "shortcut": KERN_KURZBEFEHL,
                         "kglobalaccel": [KERN_KOMPONENTE, KERN_KURZBEFEHL]},
        frist=frist)
    waegen(schlicht, geschmuggelt,
           "derselbe Auftrag, einmal ohne und einmal mit eingeschmuggelten "
           "Kurzbefehl-Parametern")
    e1 = still.ausfuehren(schlicht, jetzt=frist - 60,
                          ticket_einloesen=lambda t: None)
    e2 = still.ausfuehren(geschmuggelt, jetzt=frist - 60,
                          ticket_einloesen=lambda t: None)
    b.pruefe("K1", e1.get("ok") and e2.get("ok"),
             f"beide Auftraege werden angenommen (Positivkontrolle der "
             f"Messung): {e1.get('grund') or 'ok'} / {e2.get('grund') or 'ok'}")
    b.pruefe("K1", len(aufgezeichnet) == 2 and aufgezeichnet[0] == aufgezeichnet[1],
             f"die Argumentzeile haengt am KATALOG, nicht am Auftrag: "
             f"{aufgezeichnet[0][-3:] if aufgezeichnet else '(kein Aufruf)'} vs "
             f"{aufgezeichnet[1][-3:] if len(aufgezeichnet) > 1 else '-'}")
    if aufgezeichnet:
        b.pruefe("K1", all(KERN_KURZBEFEHL not in " ".join(argv)
                           for argv in aufgezeichnet),
                 "kein Wert aus `params` erreicht die Argumentzeile")

    # Und strukturell: `ausfuehren` liest `auftrag.params` gar nicht erst.
    quelle = Path(broker_modul.__file__)
    b_baum = baum(quelle)
    params_gelesen = _liest_params(b_baum, "ausfuehren")
    koeder = ast.parse("def ausfuehren(self, roh):\n"
                       "    return self.lauf(['gdbus', auftrag.params['x']])\n")
    b.pruefe("K1", _liest_params(koeder, "ausfuehren"),
             "Positivkontrolle des Lesers: an einem eingelegten Koeder findet "
             "er den Griff nach `auftrag.params`")
    b.pruefe("K1", not params_gelesen,
             "`ausfuehren` greift nirgends nach `auftrag.params` -- ein "
             "generisches `invokeShortcut(name)` gaebe es damit nicht")


def _liest_params(b: ast.AST | None, funktion: str) -> bool:
    if b is None:
        return False
    for knoten in ast.walk(b):
        if isinstance(knoten, ast.FunctionDef) and knoten.name == funktion:
            for innen in ast.walk(knoten):
                if isinstance(innen, ast.Attribute) and innen.attr == "params":
                    return True
    return False


def k2_kern(b: Bericht, broker_modul, daemon_modul, katalog_pfad: Path,
            arbeit: Path, bus: str, proxy: Proxy | None, frist: float,
            eingriff) -> None:
    """K2 — der Kern: nicht genehmigt heisst hier "gibt es nicht"."""
    kandidat = katalog_mit_kern(katalog_pfad, arbeit / "katalog-candidate.yaml",
                                "candidate")
    genehmigt = katalog_mit_kern(katalog_pfad, arbeit / "katalog-approved.yaml",
                                 "approved")
    waegen(kandidat.read_text(encoding="utf-8"),
           genehmigt.read_text(encoding="utf-8"),
           "derselbe Katalog, einmal mit `status: candidate` und einmal mit "
           "`status: approved` fuer " + KERN_AKTION)

    broker_nein = broker_modul.DBusBroker.aus_katalog(
        daemon_modul.katalog_lesen(kandidat))
    broker_ja = broker_modul.DBusBroker.aus_katalog(
        daemon_modul.katalog_lesen(genehmigt))

    b.pruefe("K2", KERN_AKTION not in broker_nein.operationen,
             f"{KERN_AKTION} (Komponente {KERN_KOMPONENTE}, Kurzbefehl "
             f"{KERN_KURZBEFEHL!r}, Status candidate) hat KEINE Operation")
    b.pruefe("K2", KERN_AKTION in broker_ja.operationen,
             "Positivkontrolle: derselbe Eintrag als `approved` hat eine -- "
             "es scheitert also am Status, nicht am Eintrag")

    roh = auftrag_bytes(KERN_AKTION, frist=frist)
    vorher = desktop_jetzt(bus)
    ergebnis = broker_nein.ausfuehren(roh, jetzt=frist - 60,
                                      ticket_einloesen=lambda t: None)
    time.sleep(0.5)
    nachher = desktop_jetzt(bus)
    b.pruefe("K2", not ergebnis.get("ok") and ergebnis.get("grund") == "keine_operation",
             f"der Auftrag wird abgewiesen, und zwar als 'keine_operation': "
             f"{ergebnis}")
    b.pruefe("K2", vorher == nachher,
             f"und er hat NICHTS bewirkt: Arbeitsflaeche {vorher[:8]} -> "
             f"{nachher[:8]}")

    # Positivkontrolle des Instruments: derselbe Kurzbefehl, derselbe Broker-
    # Code, nur genehmigt -- er wirkt. Sonst waere "nichts bewirkt" auch dann
    # gruen, wenn der Kurzbefehl gar nichts tut.
    gewirkt = eingriff(broker_ja, KERN_AKTION, "Kern-Kurzbefehl als approved")
    b.pruefe("K2", gewirkt,
             f"Positivkontrolle: als `approved` bewirkt derselbe Kurzbefehl "
             f"sehr wohl etwas -- die Messung kann einen Durchschlupf sehen")

    # Und die zweite Schicht sieht das nicht: der Proxy laesst die METHODE
    # durch, ohne den Kurzbefehl anzusehen.
    if proxy is not None:
        cursor = proxy.cursor()
        e = gdbus(proxy.adresse, DIENST_SOLL, f"/component/{KERN_KOMPONENTE}",
                  f"{SCHNITTSTELLE_SOLL}.{METHODE_SOLL}", NICHTS_KURZBEFEHL)
        spur = proxy.seit(cursor)
        b.pruefe("K2", e.returncode == 0,
                 f"der Proxy laesst {METHODE_SOLL} derselben Komponente mit "
                 f"einem BELIEBIGEN Kurzbefehl durch (rc={e.returncode}) -- "
                 f"genau deshalb ist die Katalogpruefung im Broker der Kern")
        b.pruefe("K2", METHODE_SOLL in spur,
                 "und der Versuch steht im Proxy-Log")


def k3_proxy(b: Bericht, pruefling: Path, arbeit: Path, bus: str,
             proxy: Proxy | None, filter_optionen: list[str],
             filter_start_fehler: str) -> None:
    """K3 — die zweite Schicht: startbar, filternd, protokollierend, benutzt."""
    konf = pruefling / "config" / "dbus-filter.conf"
    b.pruefe("K3", konf.exists(), f"{konf.name} existiert")
    b.pruefe("K3", "--filter" in filter_optionen,
             "`--filter` steht in der Filterdatei -- ohne sie ist der Proxy "
             "eine Weiterleitung")
    b.pruefe("K3", "--log" in filter_optionen,
             "`--log` steht in der Filterdatei")
    b.pruefe("K3", proxy is not None,
             f"ein Proxy laesst sich mit genau diesen Optionen STARTEN: "
             f"{filter_start_fehler or 'gestartet'}")

    # Positivkontrolle des Starters: eine bekannt gueltige Minimalliste muss
    # anlaufen. Sonst waere "startet nicht" auch dann gruen, wenn dieser
    # Pruefstand gar keinen Proxy starten kann.
    kontrolle = Proxy(["--filter", "--log"], arbeit, "kontrolle", bus)
    try:
        gestartet = kontrolle.starten()
        b.pruefe("K3", gestartet,
                 f"Positivkontrolle des Starters: eine gueltige Minimalliste "
                 f"laeuft an ({kontrolle.fehler or 'ok'})")
    finally:
        kontrolle.beenden()

    zulauf = proxy_zulauf(pruefling)
    print(f"Proxy-Zulauf: {zulauf}")
    b.pruefe("K3", bool(zulauf["starter"]),
             f"im Betrieb startet ihn jemand: {zulauf['starter'] or 'NIEMAND'}")
    b.pruefe("K3", bool(zulauf["adresse"]),
             f"und der Broker spricht durch ihn -- `gdbus call --session` "
             f"nimmt die Adresse aus der Umgebung: "
             f"{zulauf['adresse'] or 'daimon-dbus.service setzt sie nicht'}")

    # Positivkontrolle des Zulauf-Lesers an einem synthetischen Baum.
    probe = arbeit / "zulaufprobe"
    (probe / "config" / "systemd").mkdir(parents=True, exist_ok=True)
    (probe / "daimon").mkdir(parents=True, exist_ok=True)
    (probe / "config" / "systemd" / "daimon-dbus-proxy.service").write_text(
        "[Service]\nExecStart=/usr/bin/xdg-dbus-proxy --args=3 x y\n",
        encoding="utf-8")
    (probe / "config" / "systemd" / "daimon-dbus.service").write_text(
        "[Service]\nEnvironment=DBUS_SESSION_BUS_ADDRESS=unix:path=%t/p.sock\n"
        "Requires=daimon-dbus-proxy.service\n", encoding="utf-8")
    (probe / "daimon" / "starter.py").write_text(
        '"""Ein Modulkopf, der xdg-dbus-proxy nur ERWAEHNT."""\n'
        'import subprocess\n'
        'def start():\n'
        '    subprocess.run(["xdg-dbus-proxy", "--args=3"])\n', encoding="utf-8")
    kontroll_zulauf = proxy_zulauf(probe)
    b.pruefe("K3", bool(kontroll_zulauf["starter"]) and bool(kontroll_zulauf["adresse"]),
             f"Positivkontrolle des Zulauf-Lesers: an einem Baum MIT Proxy "
             f"findet er beides ({kontroll_zulauf['starter']})")
    # Negativkontrolle: eine blosse Erwaehnung im Modulkopf ist KEIN Zulauf.
    # Genau daran ist der erste Lauf dieses Pruefstands falsch gruen geworden.
    (probe / "daimon" / "nur_prosa.py").write_text(
        '"""Davor laeuft xdg-dbus-proxy --filter, sagt dieser Absatz."""\n'
        'X = 1\n', encoding="utf-8")
    nur_prosa = _nennt_ausfuehrbar(
        baum(probe / "daimon" / "nur_prosa.py"), "xdg-dbus-proxy")
    b.pruefe("K3", not nur_prosa,
             "Negativkontrolle: eine Erwaehnung im Docstring zaehlt NICHT als "
             "Zulauf -- sonst belegte die Zusage sich selbst")


def k4_loadscript(b: Bericht, broker_modul, daemon_modul, pruefling: Path,
                  arbeit: Path, proxy: Proxy | None, bus: str,
                  frist: float) -> None:
    """K4 — `loadScript` ist nicht erreichbar, und es entsteht keine Datei."""
    # 1. Der Broker: kein Katalogeintrag kann eine Operation ausserhalb der
    #    Dienst-Allowlist bauen. Gemessen ueber ALLE Kandidaten, nicht ueber
    #    einen ausgedachten.
    kandidaten = pruefling / "config" / "actions" / "candidates.yaml"
    gebaut, ausserhalb = 0, []
    if kandidaten.exists():
        import yaml
        roh = yaml.safe_load(kandidaten.read_text(encoding="utf-8")) or {}
        eintraege = {}
        for eintrag in (roh.get("actions") or []):
            kennung = str(eintrag.get("id") or "")
            komponente = str(eintrag.get("component") or "")
            aktion = str(eintrag.get("action") or "")
            if not (kennung and komponente and aktion):
                continue
            eintraege[kennung] = {"status": "approved",
                                  "kglobalaccel": [komponente, aktion],
                                  # Felder, die ein Angreifer setzen wuerde:
                                  "dienst": "org.kde.kwin.Scripting",
                                  "schnittstelle": "org.kde.kwin.Scripting",
                                  "methode": "loadScript"}
        try:
            broker = broker_modul.DBusBroker.aus_katalog(eintraege)
        except Exception as fehler:            # eine Abweisung ist auch gut
            broker = None
            b.pruefe("K4", True,
                     f"der Broker weist einen Katalog mit fremdem Dienst ab: "
                     f"{str(fehler)[:80]}")
        if broker is not None:
            gebaut = len(broker.operationen)
            for kennung, operation in broker.operationen.items():
                if operation.dienst != DIENST_SOLL or \
                        operation.schnittstelle != SCHNITTSTELLE_SOLL:
                    ausserhalb.append(f"{kennung}: {operation.dienst}")
    b.pruefe("K4", gebaut > 0,
             f"Positivkontrolle: aus den Kandidaten entstehen ueberhaupt "
             f"Operationen ({gebaut}) -- sonst maesse der Sweep nichts")
    b.pruefe("K4", not ausserhalb,
             f"keine davon zeigt auf einen anderen Dienst oder eine andere "
             f"Schnittstelle, auch wenn der Eintrag es verlangt: "
             f"{ausserhalb[:3] or 'keine'}")

    # 2. Die zweite Schicht: der Versuch geht durch den Proxy und wird
    #    abgewiesen -- und WER ihn abweist, ist an der Antwort ablesbar.
    if proxy is None:
        b.fehler("K4", "ohne startbaren Proxy ist die zweite Schicht nicht "
                       "messbar: der Versuch kann nicht abgewiesen werden, "
                       "weil ihn niemand sieht")
        return

    # Beobachtet wird ein LEERES Verzeichnis und der Ort, an dem KDE geladene
    # Skripte ablegt. Nicht das ganze Arbeitsverzeichnis: dort waechst das
    # Proxy-Protokoll, und dann maesse der Differ sich selbst.
    beobachtung = arbeit / "beobachtet"
    beobachtung.mkdir(exist_ok=True)
    skriptpfad = beobachtung / "gibt-es-nicht" / "daimon-t47.js"
    beobachtet = [Path.home() / ".local" / "share" / "kwin" / "scripts",
                  beobachtung]
    vorher_abzug = dateibaum(beobachtet)

    # Positivkontrolle des Differs, VOR der Messung: eine selbst angelegte
    # Datei muss er sehen. Sonst waere "keine Datei entstanden" auch dann
    # gruen, wenn er blind ist.
    spur = beobachtung / "differ-probe.txt"
    spur.write_text("probe", encoding="utf-8")
    b.pruefe("K4", dateibaum(beobachtet) != vorher_abzug,
             "Positivkontrolle des Datei-Differs: eine angelegte Datei faellt auf")
    spur.unlink()
    vorher_abzug = dateibaum(beobachtet)

    cursor = proxy.cursor()
    e = gdbus(proxy.adresse, KWIN_DIENST, "/Scripting",
              "org.kde.kwin.Scripting.loadScript", str(skriptpfad))
    protokoll = proxy.seit(cursor)
    antwort = (e.stdout + e.stderr).strip()
    print(f"loadScript durch den Proxy: rc={e.returncode} {antwort[:160]}")
    b.pruefe("K4", e.returncode != 0,
             f"`loadScript` durch den Proxy scheitert (rc={e.returncode})")
    b.pruefe("K4", ("ServiceUnknown" in antwort or "AccessDenied" in antwort
                    or "not allowed" in antwort.lower()),
             f"und das Nein kommt vom FILTER (der Dienst ist hinter ihm nicht "
             f"einmal sichtbar), nicht von KWin: {antwort[:120]}")
    b.pruefe("K4", ("*HIDDEN*" in protokoll or "*DENIED*" in protokoll)
             and "loadScript" in protokoll,
             "der Versuch steht mitsamt seiner Abweisung im Proxy-Log")
    b.pruefe("K4", dateibaum(beobachtet) == vorher_abzug,
             "und es ist keine Datei entstanden")
    b.pruefe("K4", not skriptpfad.exists(),
             f"auch der uebergebene Skriptpfad existiert weiterhin nicht "
             f"({skriptpfad.name})")

    # Positivkontrolle derselben Leitung: ein erlaubter Aufruf kommt durch.
    # Sonst waere "abgewiesen" von "der Proxy ist tot" nicht zu unterscheiden.
    cursor = proxy.cursor()
    erlaubt = gdbus(proxy.adresse, DIENST_SOLL, "/kglobalaccel",
                    "org.kde.KGlobalAccel.allMainComponents")
    b.pruefe("K4", erlaubt.returncode == 0,
             f"Positivkontrolle der Leitung: ein erlaubter Aufruf durch "
             f"DENSELBEN Proxy gelingt (rc={erlaubt.returncode})")
    b.pruefe("K4", "allMainComponents" in proxy.seit(cursor),
             "und auch er steht im Log -- das Log fuehrt beide Ausgaenge")


def k5_sandbox(b: Bericht, pruefling: Path) -> None:
    """K5 — Sandbox nach Design 7.5, gelesen aus dem Design des Pruefligs."""
    unit = pruefling / "config" / "systemd" / "daimon-dbus.service"
    design = pruefling / "docs" / "DESIGN.md"
    b.pruefe("K5", unit.exists(), "config/systemd/daimon-dbus.service existiert")
    basis = design_basis(design)
    b.pruefe("K5", len(basis) >= 8,
             f"Design 7.5 liefert die Basiszeilen ({len(basis)} Direktiven)")
    gesetzt = unit_lesen(unit)
    hat = defaultdict(list)
    for schluessel, wert in gesetzt:
        hat[schluessel].append(wert)

    fehlend = []
    for schluessel, wert in basis:
        werte = hat.get(schluessel, [])
        if schluessel == "InaccessiblePaths":
            soll = {t.lstrip("-") for t in wert.split()}
            ist = {t.lstrip("-") for w in werte for t in w.split()}
            if not soll <= ist:
                fehlend.append(f"{schluessel}: {sorted(soll - ist)}")
        elif wert not in werte:
            fehlend.append(f"{schluessel}={wert} (hat: {werte or 'nichts'})")
    b.pruefe("K5", not fehlend,
             f"jede Basiszeile aus Design 7.5 steht in der Unit: "
             f"{fehlend or 'vollstaendig'}")

    for schluessel, wert in DBUS_ABWEICHUNG.items():
        b.pruefe("K5", wert in hat.get(schluessel, []),
                 f"Abweichung der Zeile `dbus` in Design 7.5: "
                 f"{schluessel}={wert} (hat: {hat.get(schluessel) or 'nichts'})")

    designtext = design.read_text(encoding="utf-8") if design.exists() else ""
    for direktive in BRECHENDE_DIREKTIVEN:
        b.pruefe("K5", direktive in designtext,
                 f"Positivkontrolle: Design 7.5 nennt {direktive} weiterhin "
                 f"unter den Direktiven, die brechen")
        schluessel, _, wert = direktive.partition("=")
        b.pruefe("K5", wert not in hat.get(schluessel, []),
                 f"und die Unit setzt {direktive} NICHT "
                 f"(hat: {hat.get(schluessel) or 'nichts'})")

    # Positivkontrolle des Unit-Lesers: an einer Unit ohne die Zeile faellt sie
    # auf, an einer mit ihr nicht.
    kontrolle = [("NoNewPrivileges", "yes")]
    b.pruefe("K5", ("ProtectSystem", "strict") not in kontrolle
             and ("NoNewPrivileges", "yes") in gesetzt,
             "Positivkontrolle des Unit-Lesers: er liest Paare und findet "
             "NoNewPrivileges=yes in der echten Unit")


def k6_kanarienvogel(b: Bericht, gewirkt: bool, wiederhergestellt: bool,
                     spur_im_log: bool, hat_proxy: bool) -> None:
    """K6 — der genehmigte Shortcut wurde ausgefuehrt, die Wirkung gemessen."""
    b.pruefe("K6", gewirkt,
             "ein GENEHMIGTER Shortcut (" + KANARIE + ") wird ueber den "
             "Broker ausgefuehrt, und die Wirkung ist an "
             "VirtualDesktopManager.current messbar")
    b.pruefe("K6", wiederhergestellt,
             "der Vorzustand ist danach wiederhergestellt")
    if hat_proxy:
        b.pruefe("K6", spur_im_log,
                 "und der ausgefuehrte Aufruf steht im Proxy-Log -- der Weg "
                 "des Brokers ist durch die zweite Schicht fuehrbar")


# -- Lauf -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pruefstand T-4.7")
    ap.add_argument("pruefling", type=Path)
    args = ap.parse_args(argv)
    pruefling = args.pruefling.resolve()
    b = Bericht()

    print(f"Pruefling: {pruefling}")
    print("Mutant -> Kriterium: " + ", ".join(
        f"{name}={kriterium}" for name, kriterium in sorted(
            MUTANTEN_GRENZEN.items())))
    mutation = pruefling / "mutation.txt"
    if mutation.exists():
        print("Mutation: " + mutation.read_text(encoding="utf-8").strip()[:200])

    if not shutil.which("xdg-dbus-proxy"):
        b.fehler("K3", "xdg-dbus-proxy fehlt auf dieser Maschine -- die "
                       "zweite Schicht ist nicht messbar")
    bus = echter_bus()

    broker_modul = lade(pruefling, "daimon.brokers.dbus.broker")
    daemon_modul = lade(pruefling, "daimon.brokers.dbus.daemon")
    katalog_pfad = pruefling / "config" / "actions" / "core.yaml"
    katalog = daemon_modul.katalog_lesen(katalog_pfad)
    frist = time.monotonic() + 600.0

    vorzustand = desktop_jetzt(bus)
    anzahl = desktop_zaehlen(bus)
    print(f"Arbeitsflaechen: {anzahl}, aktuell {vorzustand}")
    if anzahl < 2:
        b.fehler("K6", "nur eine Arbeitsflaeche -- der Kanarienvogel waere "
                       "nicht messbar (umgebungs-blockiert)")

    with tempfile.TemporaryDirectory(prefix="t47-") as tmp:
        arbeit = Path(tmp)
        filter_optionen = optionen_aus(pruefling / "config" / "dbus-filter.conf")
        print("Filter-Optionen: " + " ".join(filter_optionen))
        proxy = Proxy(filter_optionen, arbeit, "filter", bus)
        gestartet = proxy.starten() if shutil.which("xdg-dbus-proxy") else False
        start_fehler = proxy.fehler
        if not gestartet:
            print(f"Proxy startet NICHT: {start_fehler[:200]}")
            proxy.beenden()

        aktiver_proxy = proxy if gestartet else None
        kanarien_spur = {"log": False}

        def eingriff(broker, aktion: str, was: str) -> bool:
            """Ein echter Aufruf, gewogen an der Arbeitsflaeche.

            Laeuft ueber den Proxy, wenn es einen gibt -- damit misst
            derselbe Eingriff auch, ob der Weg des Brokers filterbar ist.
            """
            vorher = desktop_jetzt(bus)
            cursor = aktiver_proxy.cursor() if aktiver_proxy else 0
            alt = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
            if aktiver_proxy:
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = aktiver_proxy.adresse
            try:
                ergebnis = broker.ausfuehren(
                    auftrag_bytes(aktion, frist=frist), jetzt=frist - 60,
                    ticket_einloesen=lambda t: None)
            finally:
                if alt is None:
                    os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
                else:
                    os.environ["DBUS_SESSION_BUS_ADDRESS"] = alt
            time.sleep(0.6)
            nachher = desktop_jetzt(bus)
            if aktiver_proxy and METHODE_SOLL in aktiver_proxy.seit(cursor):
                kanarien_spur["log"] = True
            print(f"Eingriff '{was}': {ergebnis.get('ok')} "
                  f"({ergebnis.get('grund') or 'ok'}), Arbeitsflaeche "
                  f"{vorher[:8]} -> {nachher[:8]}")
            return vorher != nachher

        try:
            k1_operationstabelle(b, broker_modul, daemon_modul, katalog, frist)

            broker = broker_modul.DBusBroker.aus_katalog(katalog)
            gewirkt = eingriff(broker, KANARIE, "Kanarienvogel " + KANARIE)
            zurueck = eingriff(broker, KANARIE_ZURUECK,
                               "Umkehr " + KANARIE_ZURUECK)
            wieder = desktop_jetzt(bus) == vorzustand
            k6_kanarienvogel(b, gewirkt, wieder and bool(zurueck),
                             kanarien_spur["log"], aktiver_proxy is not None)

            k2_kern(b, broker_modul, daemon_modul, katalog_pfad, arbeit, bus,
                    aktiver_proxy, frist, eingriff)
            k3_proxy(b, pruefling, arbeit, bus, aktiver_proxy, filter_optionen,
                     start_fehler)
            k4_loadscript(b, broker_modul, daemon_modul, pruefling, arbeit,
                          aktiver_proxy, bus, frist)
            k5_sandbox(b, pruefling)
        finally:
            if aktiver_proxy:
                aktiver_proxy.beenden()
            # Der Vorzustand wird IMMER wiederhergestellt, auch wenn oben
            # etwas geworfen hat -- und wenn das misslingt, laut.
            if desktop_jetzt(bus) != vorzustand:
                desktop_setzen(bus, vorzustand)
            jetzt = desktop_jetzt(bus)
            print(f"Arbeitsflaeche wiederhergestellt: {jetzt == vorzustand} "
                  f"({jetzt})")
            if jetzt != vorzustand:
                raise RuntimeError(
                    f"AUFRAEUMEN GESCHEITERT: Arbeitsflaeche steht auf "
                    f"{jetzt}, war {vorzustand}")

    return b.bilanz()


if __name__ == "__main__":
    raise SystemExit(main())
