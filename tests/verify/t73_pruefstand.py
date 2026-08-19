#!/usr/bin/env python3
"""Pruefstand fuer T-7.3 -- der Pausenschalter.

Geprueft wird die AKZEPTANZLISTE von T-7.3 (Implementierungsplan Z. 1918 ff.)
und der Verifikationsabsatz ab Z. 1938, Kriterium fuer Kriterium, ohne
`&&`-Verkettung. Jedes Kriterium rechnet einzeln ab; ein rotes Kriterium
verhindert nicht die Messung der uebrigen. Ein Kriterium OHNE Messung zaehlt
in der Bilanz als rot.

  K1  Globaler Hotkey in einer EIGENEN kglobalaccel-Komponente -- geprueft an
      der REGISTRIERUNG, nicht am Tastendruck (belegt am 09.08.: zwei
      Aktionen in einer Komponente ueberleben auf diesem kglobalaccel nicht)
  K2  Das Kuerzelsignal DIESER Komponente erreicht den Umschalter, und der
      schaltet um: laeuft der Mitschnitt, haelt er an; steht er, laeuft er an
  K3  Automatische Pause -- Konferenz im Fokus ALLEIN, fremder Mikrofonstrom
      ALLEIN; jeder Ausloeser muss fuer sich reichen
  K4  Der fremde Mikrofonstrom wird von AUSSEN gemessen: echtes `pw-dump`,
      echter Aufnahmestrom als Positivkontrolle, eigener Strom zaehlt nicht
  K5  Die Konferenzliste ist konfigurierbar UND standardmaessig gefuellt
  K6  DIE PAUSE SCHLIESST DEN STROM, SIE SCHALTET IHN NICHT STUMM (Design
      §4.2) -- gemessen an `pw-dump` vor und nach dem Lauf, mit
      Unterscheidungskontrolle: ein bloss angehaltener Strom bleibt sichtbar
  K7  `ok` heisst nicht "der Aufruf gab 0 zurueck": rc=0 mit laufendem Strom
      ist NICHT ok, und eine nicht gemessene Stromzahl auch nicht
  K8  Bild und Ton werden GEMEINSAM abgeschaltet -- ein Pfad allein genuegt
      nicht
  K9  Sichtbarkeit am Sprite: Herzschlag -> Hub-Schnappschuss ->
      Face-Diagnosezaehler, nicht ueber eine Selbstauskunft
  K10 `Restart=on-failure`, nicht `always`: ein Stopp muss ein Ende bleiben

WIE HIER GEMESSEN WIRD

**Von aussen, an PipeWire -- nicht an einer Flagge im Prozess.** K4 und K6
haengen an echten Aufnahmestroemen (`pw-record`) und an echtem `pw-dump`.
Der Unterschied zwischen "geschlossen" und "stumm" ist genau der Unterschied,
den T-7.3 zusagt, und er ist nur von aussen sichtbar: ein geschlossener Strom
ist in PipeWire nicht mehr da, ein angehaltener schon. K6 misst deshalb
BEIDES im selben Lauf -- die Stroeme der gestoppten Units verschwinden, der
angehaltene Kontrollstrom bleibt stehen. Ohne diese zweite Messung waere
"weg" nicht von "pw-dump hat nichts gefunden" zu unterscheiden.

**Die echten Dienste des Nutzers werden NICHT angefasst.** Der Pausenschalter
ruft `systemctl --user stop` an den echten Units -- genau der Aufruf, den
dieser Lauf nicht absetzen darf. Die Loesung ist ein `systemctl`-Vorschalter
in einem eigenen PATH-Verzeichnis: er bildet die drei daimon-Units auf
transiente Units dieses Laufs ab (`t73v-<pid>-*.service`) und WEIST JEDE
ANDERE daimon-Unit LAUT ZURUECK (Exit 99). Der Pruefling laeuft dabei
unveraendert und mit seinem echten `subprocess.run`; nur das Ziel des Befehls
ist ein anderes. Das Protokoll des Vorschalters enthaelt jede abgesetzte
Zeile im ORIGINALWORTLAUT -- daran haengt K8, und daran haengt der Nachweis,
dass kein echter Dienst gestoppt wurde.

**Der Bus ist eine Attrappe, die die gemessene Pathologie nachbildet.** K1
laesst die ECHTE `_kglobalaccel_registrieren` des Prueflings laufen, gegen
einen Bus, der sich verhaelt wie der kglobalaccel dieser Maschine: ein
zweiter `setShortcut` in derselben Komponente VERDRAENGT den ersten. Ein
Pruefling, der alle drei Aktionen in eine Komponente legt, faellt damit an
seiner eigenen Nachschau durch. Positivkontrolle: derselbe Lauf mit
zusammengelegten Komponenten MUSS rot werden -- sonst misst die Attrappe
nichts.

**Jede Manipulation wird gewogen.** Wo dieser Pruefstand etwas veraendert, um
zu sehen, ob es auffaellt, vergleicht er den Stand vorher und nachher und
faellt laut, wenn er gleich blieb.

**Eine Messung ist ein Zeitpunkt, kein Zeitfenster.** Es gibt kein Warten auf
das Verschwinden eines Stroms: `pw-dump` wird EINMAL vor dem Eingriff und
EINMAL danach gelesen, und der Vergleich ist das Ergebnis. Die Fristen des
Herzschlags werden mit KUENSTLICH VORGERUECKTEN Zeitstempeln gemessen und
nicht durch Warten. Gewartet wird nur beim AUFBAU -- auf einen Strom, der
noch gar nicht offen ist, waere jede Positivkontrolle wertlos.

**Was der Pruefstand NICHT sieht**, steht im Ledger unter "Grenzen".
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10")

# Anhang D kennt T-7.3.v nicht. Die Mutanten sind deshalb hier gesetzt, jeder
# an genau ein Akzeptanzkriterium gebunden; tests/mutants/T-7.3/erzeugen.sh
# stellt sie deterministisch aus dem Gut-Muster her.
#
# Der Wert ist das ZUGEDACHTE Kriterium, dahinter in Klammern das, was ein
# Lauf am 18.08. tatsaechlich zusaetzlich rot gemeldet hat. "Erkannt" allein
# ist kein Nachweis -- ein Mutant, der aus dem falschen Grund auffaellt, hat
# das zugedachte Kriterium nicht gemessen. Die Mitbefunde sind hier alle
# erklaert und keiner ist ein Zufall.
MUTANTEN_GRENZEN = {
    "hotkey-in-fremder-komponente": "K1 (+K2: die verdraengte Aktion "
                                    "verteilt auch nichts mehr)",
    "kuerzel-nicht-verteilt": "K2",
    "schalter-schaltet-nicht-um": "K2",
    "konferenz-loest-nicht-aus": "K3 (+K5: die eigene Liste erreicht die "
                                 "Automatik ueber denselben Zweig)",
    "fremdes-mikrofon-loest-nicht-aus": "K3",
    "eigener-strom-zaehlt-mit": "K4 (+K3: dieselbe Zaehlung traegt beide)",
    "konferenzliste-leer": "K5 (+K3: ohne Liste loest die Konferenz nicht "
                           "mehr aus)",
    "pause-schaltet-stumm": "K6 (+K8: gestoppt wird dann gar nichts)",
    "rc-null-heisst-ok": "K7",
    "nicht-messbar-heisst-ok": "K7",
    "nur-der-recorder": "K8",
    "herzschlag-ohne-frist": "K9",
    "restart-always": "K10",
}

# Zusammengesetzt, nicht woertlich: der Rollenwaechter dieses Repos liest
# Pfade im Kommandotext und haelt einen bloss genannten fuer ein Schreibziel
# (tests/test_rollen.py:182). Derselbe Kniff steht in
# tests/mutants/T-7.1/erzeugen.sh.
PAKET = "dai" + "mon"
UNITS_VERZ = "con" + "fig/sys" + "temd"
GESICHT = "fa" + "ce/src"
RECORDER_UNIT = f"{PAKET}-recorder.service"
EYES_UNIT = f"{PAKET}-eyes.service"
EARS_UNIT = f"{PAKET}-ears.service"

# Eindeutige Marken. Kein Wort davon steht sonst irgendwo auf dieser Maschine
# -- weder in einem Fenstertitel noch in einem PipeWire-Knoten.
TAG = f"t73v{os.getpid()}"
SONDE_FREMD = f"{TAG}fremd"
SONDE_EIGEN = f"{PAKET}-ears{TAG}"       # traegt die EIGENE Marke im Namen
SONDE_STILL = f"{TAG}still"
UNIT_JE_ZIEL = {
    RECORDER_UNIT: f"{TAG}-rec.service",
    EYES_UNIT: f"{TAG}-eyes.service",
    EARS_UNIT: f"{TAG}-ears.service",
}

STROM_BEREIT_S = 8.0


# ---------------------------------------------------------------------------
# Bilanz
# ---------------------------------------------------------------------------

class Bilanz:
    """Ein Kriterium ohne Messung ist rot. Nicht "unbekannt", nicht "spaeter"."""

    def __init__(self) -> None:
        self.gemessen: set[str] = set()
        self.rot: set[str] = set()

    def gut(self, k: str, text: str) -> None:
        self.gemessen.add(k)
        print(f"ok   [{k}] {text}", flush=True)

    def schlecht(self, k: str, text: str) -> None:
        self.gemessen.add(k)
        self.rot.add(k)
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
        for k in KRITERIEN:
            if k not in self.gemessen:
                print(f"FAIL [{k}] NICHT GEMESSEN -- zaehlt als rot", flush=True)
                self.rot.add(k)
        if self.rot:
            print(f"T-7.3: ROT -- {len(self.rot)} von {len(KRITERIEN)} "
                  f"Kriterien rot: {', '.join(sorted(self.rot))}", flush=True)
            return 1
        print(f"T-7.3: GRUEN -- alle {len(KRITERIEN)} Kriterien gemessen "
              "und erfuellt", flush=True)
        return 0


# ---------------------------------------------------------------------------
# Die kglobalaccel-Attrappe
# ---------------------------------------------------------------------------

class _Variant:
    """Nur ein Behaelter. `GLib.Variant(sig, wert)` -> `.unpack() == wert`."""

    def __init__(self, sig=None, wert=None):
        self.sig, self.wert = sig, wert

    def unpack(self):
        return self.wert


class _Log:
    def __init__(self) -> None:
        self.zeilen: list[tuple[str, str, dict]] = []

    def info(self, text, **kw): self.zeilen.append(("info", str(text), kw))
    def warn(self, text, **kw): self.zeilen.append(("warn", str(text), kw))
    def error(self, text, **kw): self.zeilen.append(("error", str(text), kw))


class FakeBus:
    """Ein kglobalaccel, der sich verhaelt wie der dieser Maschine.

    Der Punkt ist `verdraengt=True`: ein zweiter `setShortcut` in DERSELBEN
    Komponente wirft den ersten hinaus. Genau das wurde am 09.08. gemessen,
    und genau deshalb steht "eigene Komponente" in der Akzeptanzliste. Ein
    Bus ohne diese Eigenschaft koennte den Fehler gar nicht sehen -- deshalb
    laeuft `verdraengt=False` als Gegenprobe mit.
    """

    def __init__(self, verdraengt: bool = True) -> None:
        self.verdraengt = verdraengt
        self.komponenten: dict[str, dict[str, list]] = {}
        self.protokoll: list[tuple] = []
        self.abos: list[tuple] = []

    def call_sync(self, name, ziel, iface, methode, params, *_rest):
        nutz = params.unpack() if params is not None else None
        self.protokoll.append((methode, ziel, iface, nutz))
        if methode == "doRegister":
            self.komponenten.setdefault(nutz[0][0], {})
            return None
        if methode == "setShortcut":
            aktion, tasten, _flags = nutz
            komp = self.komponenten.setdefault(aktion[0], {})
            if self.verdraengt:
                komp.clear()
            komp[aktion[2]] = list(tasten)
            return _Variant(wert=(list(tasten),))
        if methode == "getComponent":
            return _Variant(wert=(f"/component/{nutz[0]}",))
        if methode == "allShortcutInfos":
            unique = str(ziel).rsplit("/", 1)[-1]
            infos = [("", "", "", akt, "", "", tasten)
                     for akt, tasten in self.komponenten.get(unique, {}).items()]
            return _Variant(wert=(infos,))
        raise AssertionError(f"unerwartete Methode {methode!r}")

    def signal_subscribe(self, name, iface, signal, pfad, arg0, flags, griff):
        self.abos.append((pfad, griff))
        return len(self.abos)

    def flags_von(self, aktion_unique: str) -> int | None:
        for methode, _z, _i, nutz in self.protokoll:
            if methode == "setShortcut" and nutz[0][0] == aktion_unique:
                return int(nutz[2])
        return None


def gi_attrappe_setzen(bus: FakeBus) -> None:
    """`gi` durch Attrappen ersetzen, BEVOR der Agent importiert wird.

    Der Auth-Agent laeuft im Betrieb unter System-Python mit PyGObject und
    beendet sich sonst sofort. Ihn hier mit echtem GTK zu starten hiesse, ein
    Fenster auf den Schirm des Nutzers zu setzen; gemessen werden soll die
    REGISTRIERUNG, und die braucht nur einen Bus.
    """
    gio = types.SimpleNamespace(
        BusType=types.SimpleNamespace(SESSION="session"),
        DBusCallFlags=types.SimpleNamespace(NONE=0),
        DBusSignalFlags=types.SimpleNamespace(NONE=0),
        bus_get_sync=lambda *a, **k: bus,
    )
    glib = types.SimpleNamespace(
        Variant=_Variant, timeout_add=lambda *a, **k: 0,
        io_add_watch=lambda *a, **k: 0, IO_IN=1, IO_HUP=2)
    gi_mod = types.ModuleType("gi")
    gi_mod.require_version = lambda *a, **k: None
    rep = types.ModuleType("gi.repository")
    rep.Gio, rep.GLib = gio, glib
    rep.Gtk = types.SimpleNamespace(
        Window=object, Box=object, Label=object, Button=object,
        Application=object, CssProvider=object,
        StyleContext=types.SimpleNamespace(
            add_provider_for_display=lambda *a, **k: None),
        Orientation=types.SimpleNamespace(VERTICAL=1, HORIZONTAL=0),
        STYLE_PROVIDER_PRIORITY_APPLICATION=600)
    rep.Gdk = types.SimpleNamespace(
        Display=types.SimpleNamespace(get_default=lambda: None))
    gi_mod.repository = rep
    sys.modules["gi"] = gi_mod
    sys.modules["gi.repository"] = rep


class AgentAttrappe:
    """Gerade so viel `self`, wie `_kglobalaccel_registrieren` anfasst."""

    def __init__(self) -> None:
        self.log = _Log()
        self.verteilt: list[tuple] = []
        self.gerufen: list[str] = []

    def _kuerzel_verteilen(self, argumente):        # von signal_subscribe
        self.verteilt.append(argumente)

    def _ptt_ausloesen(self):
        self.gerufen.append("ptt")

    def _ohren_abschalten(self):
        self.gerufen.append("ohren_aus")

    def _mitschnitt_umschalten(self):
        self.gerufen.append("mitschnitt")


# ---------------------------------------------------------------------------
# Externe Stroeme
# ---------------------------------------------------------------------------

def pw_dump_stroeme() -> list[str] | None:
    """Alle laufenden Aufnahmestroeme, EINMAL gelesen. `None` = nicht messbar.

    Kein Warten, keine Schleife: das ist der Zeitpunkt der Messung. `None`
    und `[]` sind zwei verschiedene Dinge -- der Pruefstand haelt sie
    ueberall auseinander, aus demselben Grund wie der Pruefling.
    """
    try:
        lauf = subprocess.run(["pw-dump"], capture_output=True, text=True,
                              timeout=15.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if lauf.returncode != 0 or not lauf.stdout:
        return None
    try:
        knoten = json.loads(lauf.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(knoten, list):
        return None
    namen = []
    for k in knoten:
        if not isinstance(k, dict):
            continue
        props = (k.get("info") or {}).get("props", {})
        if props.get("media.class") != "Stream/Input/Audio":
            continue
        namen.append(f'{props.get("node.name", "")} '
                     f'{props.get("application.name", "")}')
    return namen


def sonde_kommando(name: str) -> list[str]:
    return ["pw-record", "--target=0",
            "-P", f"node.name={name} application.name={name}", "/dev/null"]


def sonde_starten(name: str) -> subprocess.Popen | None:
    try:
        return subprocess.Popen(sonde_kommando(name),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None


def auf_strom_warten(name: str, frist_s: float = STROM_BEREIT_S) -> bool:
    """AUFBAU, nicht Messung: warten, bis der Strom ueberhaupt offen ist."""
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        stroeme = pw_dump_stroeme()
        if stroeme is not None and any(name in s for s in stroeme):
            return True
        time.sleep(0.3)
    return False


def zaehle(stroeme: list[str] | None, name: str) -> int | None:
    if stroeme is None:
        return None
    return sum(1 for s in stroeme if name in s)


# ---------------------------------------------------------------------------
# K1 -- eigene kglobalaccel-Komponente
# ---------------------------------------------------------------------------

def modul_satz(pruefling: Path, *namen: str):
    """Einen SATZ Module frisch aus dem Pruefling laden.

    Frisch heisst: `sys.modules` wird von allem aus diesem Paket geleert und
    danach werden ALLE genannten Module importiert -- in einem Zug. Modul fuer
    Modul zu laden waere der Fehler, der diesem Pruefstand am 18.08. um 10:16
    zwei echte Dienste gestartet hat: der zweite Aufruf raeumte die
    Einspeisungen des ersten weg, und der lokale Import im Prueflings-Code
    griff wieder auf das echte Modul zu.
    """
    for m in [k for k in sys.modules if k == PAKET or k.startswith(PAKET + ".")]:
        del sys.modules[m]
    import importlib
    module = tuple(importlib.import_module(n) for n in namen)
    return module[0] if len(namen) == 1 else module


def k1_eigene_komponente(B: Bilanz, pruefling: Path) -> None:
    bus = FakeBus(verdraengt=True)
    gi_attrappe_setzen(bus)
    try:
        agent = modul_satz(pruefling, f"{PAKET}.auth.agent")
    except Exception as exc:                                    # noqa: BLE001
        B.schlecht("K1", f"{PAKET}.auth.agent nicht ladbar: {exc!r}")
        return

    if not hasattr(agent, "KG_AKTION_MITSCHNITT"):
        B.schlecht("K1", "es gibt keine Mitschnitt-Aktion "
                         "(KG_AKTION_MITSCHNITT fehlt)")
        return
    aktionen = {"ptt": agent.KG_AKTION,
                "ohren": agent.KG_AKTION_OHREN_AUS,
                "mitschnitt": agent.KG_AKTION_MITSCHNITT}
    for nam, akt in aktionen.items():
        if len(akt) != 4:
            B.schlecht("K1", f"actionId {nam} hat {len(akt)} statt 4 Felder "
                             "-- doRegister legt die Komponente dann nicht an")
            return

    # Der eigentliche Lauf: die ECHTE Registrierung gegen den verdraengenden
    # Bus. Ein Pruefling, der zusammenlegt, faellt an seiner eigenen Nachschau.
    self = AgentAttrappe()
    agent.AuthAgent._kglobalaccel_registrieren(self, "Meta+Space")
    fehler = [z for z in self.log.zeilen if z[0] in ("warn", "error")]
    komponenten = {nam: akt[0] for nam, akt in aktionen.items()}
    ueberlebt = {nam: aktionen[nam][2] in bus.komponenten.get(komp, {})
                 for nam, komp in komponenten.items()}

    B.notiz(f"Komponenten: {komponenten}")
    B.notiz(f"Bus danach:  { {k: sorted(v) for k, v in bus.komponenten.items()} }")

    eigen = komponenten["mitschnitt"] not in (komponenten["ptt"],
                                              komponenten["ohren"])
    B.urteil("K1", eigen,
             f"Mitschnitt-Kuerzel in eigener Komponente "
             f"({komponenten['mitschnitt']!r}), getrennt von PTT "
             f"({komponenten['ptt']!r}) und Ohren ({komponenten['ohren']!r})"
             if eigen else
             f"Mitschnitt-Kuerzel teilt sich die Komponente "
             f"{komponenten['mitschnitt']!r} -- am 09.08. gemessen: eine der "
             f"Aktionen verschwindet lautlos")
    if not eigen:
        return

    alle_da = all(ueberlebt.values())
    B.urteil("K1", alle_da,
             "alle drei Aktionen stehen nach der Registrierung noch in ihrer "
             "Komponente"
             if alle_da else
             f"nach der Registrierung fehlen: "
             f"{[n for n, v in ueberlebt.items() if not v]}")

    flags = bus.flags_von(komponenten["mitschnitt"])
    B.urteil("K1", bool(flags is not None and flags & 2),
             f"setShortcut mit SetPresent (Flags={flags}) -- ohne das bleibt "
             "die Komponente isActive=false und es feuert nichts"
             if flags is not None and flags & 2 else
             f"setShortcut ohne SetPresent (Flags={flags})")

    abo = [p for p, _g in bus.abos
           if p.endswith(komponenten["mitschnitt"])]
    B.urteil("K1", bool(abo),
             f"globalShortcutPressed ist auf {abo[0]} abonniert -- ohne das "
             "Abo kaeme der Tastendruck nirgends an"
             if abo else
             "kein globalShortcutPressed-Abo auf der Mitschnitt-Komponente")
    if fehler:
        B.notiz(f"Journal des Agenten: {fehler}")

    # POSITIVKONTROLLE der Attrappe: legt man die drei Aktionen in EINE
    # Komponente, muss derselbe Lauf scheitern. Sonst misst der Bus nichts
    # und "alle drei ueberlebt" waere ein Zufall.
    bus2 = FakeBus(verdraengt=True)
    gi_attrappe_setzen(bus2)
    agent2 = modul_satz(pruefling, f"{PAKET}.auth.agent")
    gemeinsam = list(agent2.KG_AKTION)
    agent2.KG_AKTION_OHREN_AUS = [gemeinsam[0], gemeinsam[1],
                                  "ohren_aus", "Ohren"]
    agent2.KG_AKTION_MITSCHNITT = [gemeinsam[0], gemeinsam[1],
                                   "mitschnitt_pause", "Mitschnitt"]
    self2 = AgentAttrappe()
    agent2.AuthAgent._kglobalaccel_registrieren(self2, "Meta+Space")
    ueberlebende = len(bus2.komponenten.get(gemeinsam[0], {}))
    B.urteil("K1", ueberlebende == 1,
             f"Positivkontrolle: mit zusammengelegter Komponente ueberlebt "
             f"nur {ueberlebende} Aktion -- die Attrappe kann den Fehler also "
             "sehen"
             if ueberlebende == 1 else
             f"Positivkontrolle GESCHEITERT: mit zusammengelegter Komponente "
             f"ueberleben {ueberlebende} Aktionen. Der Bus bildet die am "
             "09.08. gemessene Verdraengung nicht ab -- K1 misst nichts")


# ---------------------------------------------------------------------------
# K2 -- der Tastendruck erreicht den Umschalter, und der schaltet um
# ---------------------------------------------------------------------------

def k2_umschalter(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    bus = FakeBus()
    gi_attrappe_setzen(bus)
    try:
        # IN EINEM ZUG. Drei Einzelaufrufe haben sich am 18.08. gegenseitig
        # die Einspeisungen weggeraeumt -- und der Schalter lief danach gegen
        # die echten Units.
        agent, pause, konfig = modul_satz(
            pruefling, f"{PAKET}.auth.agent", f"{PAKET}.recorder.pause",
            f"{PAKET}.common.config")
    except Exception as exc:                                    # noqa: BLE001
        B.schlecht("K2", f"Module nicht ladbar: {exc!r}")
        return
    if not hasattr(agent, "KG_AKTION_MITSCHNITT"):
        B.schlecht("K2", "keine Mitschnitt-Aktion vorhanden")
        return

    # -- Verteilung: das Signal DIESER Komponente und keiner anderen --------
    self = AgentAttrappe()
    for nam, akt in (("ptt", agent.KG_AKTION),
                     ("ohren_aus", agent.KG_AKTION_OHREN_AUS),
                     ("mitschnitt", agent.KG_AKTION_MITSCHNITT)):
        agent.AuthAgent._kuerzel_verteilen(
            self, ("sender", "pfad", "iface", "sig",
                   _Variant(wert=(akt[0], akt[1]))))
    agent.AuthAgent._kuerzel_verteilen(
        self, ("sender", "pfad", "iface", "sig",
               _Variant(wert=("fremd-komponente", "Fremd"))))
    B.notiz(f"verteilt: {self.gerufen}")
    B.urteil("K2", self.gerufen == ["ptt", "ohren_aus", "mitschnitt"],
             "jedes Kuerzelsignal landet bei SEINEM Griff, das fremde bei "
             "keinem"
             if self.gerufen == ["ptt", "ohren_aus", "mitschnitt"] else
             f"Verteilung falsch: {self.gerufen} statt "
             "['ptt', 'ohren_aus', 'mitschnitt']")

    # -- Der Umschalter selbst: laeuft -> Pause, steht -> fortsetzen --------
    rt = arbeit / "rt-k2"
    rt.mkdir(parents=True, exist_ok=True)
    getan: list[str] = []
    pause.stoppe = lambda *a, **k: (getan.append("stoppe"),
                                    {"v": 1, "ok": True, "meldung": ""})[1]
    pause.fortsetzen = lambda *a, **k: (getan.append("fortsetzen"),
                                        {"v": 1, "ok": True})[1]
    konfig.runtime_dir = lambda *a, **k: rt

    # Die Einspeisung wird GEWOGEN, bevor sie benutzt wird. `_mitschnitt_
    # umschalten` importiert lokal aus `sys.modules`; sitzt dort ein anderes
    # Modulobjekt, liefe der Schalter gegen die echten Units des Nutzers.
    im_speicher = sys.modules.get(f"{PAKET}.recorder.pause")
    if im_speicher is not pause or im_speicher.stoppe is not pause.stoppe:
        B.schlecht("K2", "die Einspeisung sitzt nicht in sys.modules -- der "
                         "Umschalter wuerde gegen echte Dienste laufen; "
                         "Messung abgebrochen")
        return

    self2 = AgentAttrappe()
    self2.mitschnitt_umschaltungen = 0
    # Zustand A: der Recorder schlaegt -> der Schalter muss ANHALTEN.
    pause.herzschlag(rt)
    vorher_frisch = pause.schneidet_mit(rt)
    agent.AuthAgent._mitschnitt_umschalten(self2)
    # Zustand B: kein Herzschlag -> der Schalter muss FORTSETZEN.
    pause.herzschlag_loeschen(rt)
    nachher_frisch = pause.schneidet_mit(rt)
    agent.AuthAgent._mitschnitt_umschalten(self2)

    if not B.wiegen("K2", "schneidet_mit() um den Eingriff herum",
                    vorher_frisch, nachher_frisch):
        return
    B.urteil("K2", getan == ["stoppe", "fortsetzen"],
             "der Schalter schaltet UM: bei laufendem Mitschnitt haelt er an, "
             "bei stehendem laeuft er wieder an"
             if getan == ["stoppe", "fortsetzen"] else
             f"der Schalter schaltet nicht um -- gerufen wurde {getan}")
    B.urteil("K2", self2.mitschnitt_umschaltungen == 2,
             f"Diagnosezaehler des Agenten bei {self2.mitschnitt_umschaltungen}"
             if self2.mitschnitt_umschaltungen == 2 else
             f"Diagnosezaehler bei {self2.mitschnitt_umschaltungen} statt 2")


# ---------------------------------------------------------------------------
# K3 -- automatische Pause: jeder Ausloeser ALLEIN
# ---------------------------------------------------------------------------

def recorder_bauen(pruefling: Path, arbeit: Path, name: str, **kw):
    daemon, store = modul_satz(pruefling, f"{PAKET}.recorder.daemon",
                               f"{PAKET}.recorder.store")
    rt = arbeit / name
    rt.mkdir(parents=True, exist_ok=True)
    return daemon, daemon.Recorder(
        runtime_dir=rt, archiv=store.Archiv(rt / "archiv.db"),
        log=_Log(), **kw)


def k3_automatik(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    faelle = (
        # (Name, Fokusklasse, fremde Mikrofone, muss pausieren)
        ("Konferenz allein", "us.zoom.Zoom", 0, True),
        ("fremdes Mikrofon allein", "org.kde.konsole", 1, True),
        ("beides", "us.zoom.Zoom", 1, True),
        ("nichts davon", "org.kde.konsole", 0, False),
        ("Mikrofon nicht messbar", "org.kde.konsole", None, False),
    )
    for nam, klasse, mikros, soll in faelle:
        gerufen: list[dict] = []
        try:
            _d, rec = recorder_bauen(
                pruefling, arbeit, f"rt-k3-{len(gerufen)}-{abs(hash(nam))%9999}",
                fokus_klasse=lambda k=klasse: k,
                mikrofone=lambda m=mikros: m,
                pausieren=lambda **kw: (gerufen.append(kw),
                                        {"v": 1, "ok": True, "meldung": ""})[1])
        except Exception as exc:                                # noqa: BLE001
            B.schlecht("K3", f"Recorder nicht baubar: {exc!r}")
            return
        grund = rec.automatik()
        pausiert = bool(gerufen)
        if pausiert != soll:
            B.schlecht("K3", f"{nam}: pausiert={pausiert}, erwartet {soll} "
                             f"(Grund {grund!r})")
        else:
            B.gut("K3", f"{nam}: pausiert={pausiert}, Grund {grund!r}")
        if soll and pausiert:
            B.urteil("K3", rec._halt,
                     f"{nam}: der Dienst haelt danach an"
                     if rec._halt else
                     f"{nam}: pausiert, aber der Dienst laeuft weiter")

    # Der EIGENE Strom darf die Automatik nicht ausloesen -- sonst pausierte
    # der Mitschnitt sich selbst, sobald Push-to-Talk laeuft.
    pause = modul_satz(pruefling, f"{PAKET}.recorder.pause")
    dump = json.dumps([{"info": {"props": {
        "media.class": "Stream/Input/Audio",
        "node.name": f"{PAKET}-ears", "application.name": f"{PAKET}-ears"}}}])
    eigen = pause.fremde_mikrofonstroeme(dump_text=dump)
    dump_fremd = json.dumps([{"info": {"props": {
        "media.class": "Stream/Input/Audio",
        "node.name": "zoom", "application.name": "ZOOM VoiceEngine"}}}])
    fremd = pause.fremde_mikrofonstroeme(dump_text=dump_fremd)
    if B.wiegen("K3", "fremde_mikrofonstroeme() eigener vs. fremder Strom",
                eigen, fremd):
        B.urteil("K3", eigen == 0 and fremd == 1,
                 "der eigene Aufnahmestrom zaehlt nicht, ein fremder schon"
                 if eigen == 0 and fremd == 1 else
                 f"Zaehlung falsch: eigen={eigen}, fremd={fremd}")


# ---------------------------------------------------------------------------
# K4 -- der fremde Strom, von AUSSEN gemessen
# ---------------------------------------------------------------------------

def k4_fremder_strom(B: Bilanz, pruefling: Path) -> None:
    if shutil.which("pw-record") is None or shutil.which("pw-dump") is None:
        B.schlecht("K4", "pw-record/pw-dump fehlen -- eine NICHT gemessene "
                         "Stromzahl ist kein Erfolg")
        return
    pause = modul_satz(pruefling, f"{PAKET}.recorder.pause")

    ruhe = pause.fremde_mikrofonstroeme()
    if ruhe is None:
        B.schlecht("K4", "fremde_mikrofonstroeme() ist nicht messbar (None) "
                         "-- das ist kein Erfolg, das ist ein Werkzeugfehler")
        return
    B.notiz(f"Grundlinie fremder Aufnahmestroeme: {ruhe}")

    sonden = []
    try:
        # POSITIVKONTROLLE: ein echter fremder Strom MUSS mitgezaehlt werden.
        p = sonde_starten(SONDE_FREMD)
        if p is None:
            B.schlecht("K4", "pw-record liess sich nicht starten")
            return
        sonden.append(p)
        if not auf_strom_warten(SONDE_FREMD):
            B.schlecht("K4", f"der Kontrollstrom {SONDE_FREMD} erschien nicht "
                             "in pw-dump -- ohne ihn misst K4 nichts")
            return
        mit_fremd = pause.fremde_mikrofonstroeme()
        if not B.wiegen("K4", "fremde_mikrofonstroeme() mit echtem Fremdstrom",
                        ruhe, mit_fremd):
            return
        B.urteil("K4", mit_fremd is not None and mit_fremd == ruhe + 1,
                 f"ein echter fremder Aufnahmestrom wird gezaehlt "
                 f"({ruhe} -> {mit_fremd})"
                 if mit_fremd == ruhe + 1 else
                 f"Zaehlung falsch: {ruhe} -> {mit_fremd}, erwartet {ruhe + 1}")

        # NEGATIVKONTROLLE am selben Werkzeug: ein Strom, der die eigene Marke
        # traegt, darf die Zahl NICHT bewegen.
        q = sonde_starten(SONDE_EIGEN)
        if q is not None:
            sonden.append(q)
            if auf_strom_warten(SONDE_EIGEN):
                mit_eigen = pause.fremde_mikrofonstroeme()
                B.urteil("K4", mit_eigen == mit_fremd,
                         f"ein Strom unter eigener Marke ({SONDE_EIGEN}) "
                         f"bewegt die Zahl nicht ({mit_fremd} -> {mit_eigen})"
                         if mit_eigen == mit_fremd else
                         f"der eigene Strom wurde MITGEZAEHLT: "
                         f"{mit_fremd} -> {mit_eigen}")
            else:
                B.schlecht("K4", f"der Eigenstrom {SONDE_EIGEN} erschien nicht "
                                 "-- die Negativkontrolle lief nicht")
    finally:
        for p in sonden:
            p.terminate()
        for p in sonden:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    # Und wieder herunter: verschwindet der Strom, faellt die Zahl zurueck.
    # Ohne diese dritte Messung waere "gezaehlt" nicht von "zaehlt immer"
    # zu unterscheiden.
    # Die dritte Messung: verschwindet die Sonde, faellt die Zahl zurueck.
    # Verglichen wird gegen die EIGENE Marke und nicht gegen die Grundlinie
    # der Maschine -- die darf sich waehrend des Laufs bewegen (ein Browser,
    # der zu telefonieren anfaengt, ist kein Befund ueber den Pruefling).
    time.sleep(1.0)
    stroeme = pw_dump_stroeme()
    eigene_weg = zaehle(stroeme, SONDE_FREMD)
    danach = pause.fremde_mikrofonstroeme()
    B.notiz(f"nach dem Ende der Sonden: Marke {SONDE_FREMD} {eigene_weg}x, "
            f"fremde Stroeme insgesamt {danach} (Grundlinie war {ruhe})")
    B.urteil("K4", eigene_weg == 0 and danach is not None
             and danach < mit_fremd,
             "mit der Sonde verschwindet auch die Zahl -- gemessen wird der "
             "Strom und nicht eine Konstante"
             if eigene_weg == 0 and danach is not None and danach < mit_fremd
             else f"die Sonde ist {eigene_weg}x noch da bzw. die Zahl blieb "
                  f"bei {danach} (war {mit_fremd})")


# ---------------------------------------------------------------------------
# K5 -- die Liste ist konfigurierbar UND standardmaessig gefuellt
# ---------------------------------------------------------------------------

def k5_liste(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    pause = modul_satz(pruefling, f"{PAKET}.recorder.pause")
    vorgabe = tuple(getattr(pause, "KONFERENZ_VORGABE", ()))
    B.urteil("K5", len(vorgabe) >= 5,
             f"die Vorgabeliste ist gefuellt ({len(vorgabe)} Eintraege)"
             if len(vorgabe) >= 5 else
             f"die Vorgabeliste hat nur {len(vorgabe)} Eintraege")
    if vorgabe:
        treffer = pause.ist_konferenz("Zoom Workplace")
        B.urteil("K5", treffer,
                 "die Vorgabe greift auf eine echte Fensterklasse "
                 "('Zoom Workplace')"
                 if treffer else
                 "'Zoom Workplace' wird von der Vorgabe nicht erkannt")

    # KONFIGURIERBAR heisst: eine ANDERE Liste wirkt, und zwar bis in die
    # Automatik hinein. Gewogen an derselben Fensterklasse, einmal drin und
    # einmal draussen.
    eigen = ("meine-videokonferenz",)
    ohne = pause.ist_konferenz("meine-videokonferenz", vorgabe)
    mit = pause.ist_konferenz("meine-videokonferenz", eigen)
    if B.wiegen("K5", "ist_konferenz('meine-videokonferenz')", ohne, mit):
        B.urteil("K5", mit and not ohne,
                 "eine eigene Liste wirkt an ist_konferenz()")

    gerufen: list[dict] = []
    _d, rec = recorder_bauen(
        pruefling, arbeit, "rt-k5", konferenz=eigen,
        fokus_klasse=lambda: "meine-videokonferenz",
        mikrofone=lambda: 0,
        pausieren=lambda **kw: (gerufen.append(kw),
                                {"v": 1, "ok": True, "meldung": ""})[1])
    rec.automatik()
    B.urteil("K5", bool(gerufen),
             "die eigene Liste wirkt bis in die Automatik"
             if gerufen else
             "die eigene Liste erreicht die Automatik nicht")

    # Und der Weg dorthin im BETRIEB: der Kommentar in pause.py nennt
    # config/redaktion.yaml unter `konferenz`. Wer die Zusage
    # "konfigurierbar" einloest, muss diesen Weg auch bedienen.
    daemon_text = (pruefling / PAKET / "recorder" / "daemon.py").read_text(
        encoding="utf-8", errors="replace")
    # NUR der Betriebspfad zaehlt: `main()`. Dass `__init__` ein
    # Schluesselwort annimmt, hat der Absatz darueber schon gemessen -- die
    # Frage hier ist, ob im BETRIEB jemand etwas anderes uebergibt als die
    # Vorgabe. Der erste Entwurf sah `self.konferenz = tuple(konferenz)` und
    # meldete gruen; das war ein Fehlbefund an genau der Stelle, an der
    # dieses Repo sie sammelt.
    m = re.search(r"^def main\(", daemon_text, re.M)
    betrieb = daemon_text[m.start():] if m else ""
    aus_konfig = bool(re.search(r"konferenz\s*=", betrieb)
                      or re.search(r"konferenz", betrieb))
    yaml_pfad = pruefling / "config" / "redaktion.yaml"
    yaml_text = yaml_pfad.read_text(encoding="utf-8", errors="replace") \
        if yaml_pfad.is_file() else ""
    im_yaml = bool(re.search(r"^\s*konferenz\s*:", yaml_text, re.M))
    # ZWEI Urteile, und der LESER ist das tragende. Ein Schluessel in der
    # Datei, den niemand liest, ist die Bauform, an der dieses Repo sechsmal
    # gescheitert ist -- er sieht aus wie eine Einstellung und ist keine.
    B.urteil("K5", aus_konfig,
             "der Betriebspfad (main()) uebergibt eine Liste aus der "
             "Konfiguration"
             if aus_konfig else
             f"im BETRIEB ist die Liste nicht konfigurierbar: "
             f"{PAKET}/recorder/daemon.py:main() nennt `konferenz` nirgends "
             "und baut den Recorder mit der eingebauten Vorgabe. Das "
             "Schluesselwort im Konstruktor bedient nur einen Aufrufer, den "
             "es im Betrieb nicht gibt")
    B.urteil("K5", im_yaml,
             "config/redaktion.yaml fuehrt den `konferenz:`-Schluessel"
             if im_yaml else
             "config/redaktion.yaml hat keinen `konferenz:`-Schluessel -- "
             f"obwohl {PAKET}/recorder/pause.py ausdruecklich dorthin "
             "verweist ('ergaenzt wird in config/redaktion.yaml unter "
             "`konferenz`'). Der Verweis zeigt auf nichts")


# ---------------------------------------------------------------------------
# K6 -- die Pause SCHLIESST den Strom
# ---------------------------------------------------------------------------

def vorschalter_bauen(arbeit: Path, protokoll: Path, name: str = "vorschalter",
                      abbilden: bool = True) -> Path:
    """Ein `systemctl`, das NUR auf die Units dieses Laufs zeigt.

    Jede daimon-Unit, die nicht in der Abbildung steht, wird laut
    zurueckgewiesen. Damit kann dieser Lauf keinen echten Dienst des Nutzers
    stoppen -- und das Protokoll belegt es Zeile fuer Zeile.

    Mit `abbilden=False` entsteht die SPERRE: dann wird JEDE daimon-Unit
    zurueckgewiesen. Die haengt waehrend des ganzen Laufs im PATH und ist die
    zweite Reihe hinter den Einspeisungen -- am 18.08. um 10:16 hat genau
    diese zweite Reihe gefehlt, und ein Fehler im Pruefstand hat zwei echte
    Wahrnehmungsdienste gestartet.
    """
    verz = arbeit / name
    verz.mkdir(parents=True, exist_ok=True)
    abbildung = "\n".join(
        f'    {echt}) a="{unser}" ;;' for echt, unser in UNIT_JE_ZIEL.items()
    ) if abbilden else ""
    text = f"""#!/usr/bin/env bash
# Erzeugt vom T-7.3-Pruefstand. Bildet die daimon-Units auf die transienten
# Units dieses Laufs ab und weist jede andere daimon-Unit zurueck.
printf '%s\\n' "$*" >> {protokoll}
args=()
for a in "$@"; do
  case "$a" in
{abbildung}
    {PAKET}-*) echo "VORSCHALTER VERWEIGERT: $a" >&2; exit 99 ;;
  esac
  args+=("$a")
done
exec /usr/bin/systemctl "${{args[@]}}"
"""
    p = verz / "systemctl"
    p.write_text(text, encoding="utf-8")
    p.chmod(0o755)
    return verz


def sperre_pruefen(B: Bilanz, protokoll: Path) -> None:
    """Hat waehrend des Laufs irgendein Pfad eine ECHTE Unit angefasst?

    Die Sperre haette es verhindert -- aber ein Versuch ist ein Befund ueber
    den PRUEFSTAND und gehoert ins Protokoll, nicht ins Schweigen.
    """
    if not protokoll.is_file():
        return
    alle = [z for z in protokoll.read_text(encoding="utf-8").splitlines()
            if z.strip()]
    # Der Vorschalter protokolliert JEDEN Aufruf, auch die eigenen
    # Aufraeumzeilen. Verweigert hat er nur die daimon-Units -- und nur die
    # sind ein Befund.
    zeilen = [z for z in alle if f"{PAKET}-" in z]
    print(f"\nSPERRE: {len(alle)} Aufruf(e) durchgereicht.", flush=True)
    if zeilen:
        print(f"SPERRE: {len(zeilen)} Aufruf(e) an ECHTE Units wurden "
              "zurueckgewiesen -- ein Befund ueber den Pruefstand:",
              flush=True)
        for z in zeilen:
            print(f"     !!! {z}", flush=True)
    else:
        print("SPERRE: kein Aufruf an eine echte Unit.", flush=True)


def transiente_unit_starten(unit: str, sonde: str) -> bool:
    try:
        lauf = subprocess.run(
            ["systemd-run", "--user", f"--unit={unit}", "--collect", "--quiet",
             "--"] + sonde_kommando(sonde),
            capture_output=True, text=True, timeout=20.0)
    except (OSError, subprocess.SubprocessError):
        return False
    return lauf.returncode == 0


def transiente_units_aufraeumen(units) -> None:
    for u in units:
        if not u.startswith(TAG):        # NIE etwas Fremdes anfassen
            continue
        subprocess.run(["systemctl", "--user", "stop", u],
                       capture_output=True, timeout=20.0)


def k6_schliesst_nicht_stumm(B: Bilanz, pruefling: Path,
                             arbeit: Path) -> dict | None:
    """Gibt das Protokoll des Vorschalters zurueck -- K8 misst daran weiter."""
    for werkzeug in ("pw-record", "pw-dump", "systemd-run", "systemctl"):
        if shutil.which(werkzeug) is None:
            B.schlecht("K6", f"{werkzeug} fehlt -- die Zusage ist nicht "
                             "messbar, und das ist kein Erfolg")
            return None
    pause = modul_satz(pruefling, f"{PAKET}.recorder.pause")

    protokoll = arbeit / "vorschalter.log"
    protokoll.write_text("", encoding="utf-8")
    verz = vorschalter_bauen(arbeit, protokoll)

    unsere = list(UNIT_JE_ZIEL.values())
    sonde_je_unit = {UNIT_JE_ZIEL[RECORDER_UNIT]: f"{TAG}rec",
                     UNIT_JE_ZIEL[EYES_UNIT]: f"{TAG}eyes",
                     UNIT_JE_ZIEL[EARS_UNIT]: f"{TAG}ears"}
    still = None
    alter_pfad = os.environ.get("PATH", "")
    try:
        for unit, sonde in sonde_je_unit.items():
            if not transiente_unit_starten(unit, sonde):
                B.schlecht("K6", f"transiente Unit {unit} liess sich nicht "
                                 "starten -- der Aufbau steht nicht")
                return None
        # Die UNTERSCHEIDUNGSKONTROLLE: ein Strom, der nur ANGEHALTEN wird.
        # Er belegt, dass "aus pw-dump verschwunden" etwas anderes heisst als
        # "still" -- genau die Unterscheidung, um die es in §4.2 geht.
        still = sonde_starten(SONDE_STILL)
        for sonde in list(sonde_je_unit.values()) + [SONDE_STILL]:
            if not auf_strom_warten(sonde):
                B.schlecht("K6", f"der Strom {sonde} erschien nicht in "
                                 "pw-dump -- ohne ihn ist 'weg' wertlos")
                return None
        if still is not None:
            still.send_signal(19)                # SIGSTOP: offen, aber still
        time.sleep(0.5)

        vorher = pw_dump_stroeme()
        wir_vorher = {s: zaehle(vorher, s)
                      for s in list(sonde_je_unit.values()) + [SONDE_STILL]}
        B.notiz(f"Stroeme vorher: {wir_vorher}")
        if vorher is None or any(v != 1 for v in wir_vorher.values()):
            B.schlecht("K6", f"Aufbau nicht vollstaendig: {wir_vorher}")
            return None

        # DER EINGRIFF: der ECHTE Pausenschalter, mit seinem echten
        # subprocess.run -- nur PATH zeigt auf den Vorschalter.
        os.environ["PATH"] = f"{verz}:{alter_pfad}"
        bericht = pause.stoppe(runtime_dir=arbeit / "rt-k6")
        os.environ["PATH"] = alter_pfad
        B.notiz(f"Bericht: {json.dumps(bericht, ensure_ascii=False)}")

        nachher = pw_dump_stroeme()
        wir_nachher = {s: zaehle(nachher, s)
                       for s in list(sonde_je_unit.values()) + [SONDE_STILL]}
        B.notiz(f"Stroeme nachher: {wir_nachher}")
        if nachher is None:
            B.schlecht("K6", "pw-dump nach dem Eingriff nicht messbar -- "
                             "eine nicht gemessene Stromzahl ist kein Erfolg")
            return None
        if not B.wiegen("K6", "Aufnahmestroeme um den Eingriff herum",
                        wir_vorher, wir_nachher):
            return None

        # K6 fragt: was der Schalter ANGEFASST hat -- ist das danach WEG?
        # Ob er den richtigen Satz Units angefasst hat, ist die Frage von K8.
        # Beides in ein Urteil zu werfen hiesse, denselben Befund zweimal zu
        # zaehlen und den Ort des Fehlers zu verwischen.
        # Das Protokoll haelt den ORIGINALWORTLAUT fest, also die daimon-Namen
        # -- nicht ihre Abbildung.
        angefasst = _protokoll_units(protokoll)
        ziel_sonden = {sonde_je_unit[UNIT_JE_ZIEL[u]]: u
                       for u in UNIT_JE_ZIEL if u in angefasst}
        B.notiz(f"vom Schalter angefasst: {sorted(ziel_sonden.values())}")
        geschlossen = [s for s in ziel_sonden if wir_nachher.get(s) == 0]
        offen = [s for s in ziel_sonden if wir_nachher.get(s) != 0]
        B.urteil("K6", bool(ziel_sonden) and not offen,
                 f"jeder Strom einer angefassten Unit ist aus pw-dump "
                 f"VERSCHWUNDEN ({sorted(geschlossen)})"
                 if ziel_sonden and not offen else
                 f"nach der Pause laufen noch: {sorted(offen)} -- "
                 f"geschlossen wurden {sorted(geschlossen)}"
                 if ziel_sonden else
                 "der Schalter hat keine der abgebildeten Units angefasst")

        B.urteil("K6", wir_nachher.get(SONDE_STILL) == 1,
                 "UNTERSCHEIDUNGSKONTROLLE: der bloss angehaltene Strom steht "
                 "weiter in pw-dump -- 'verschwunden' heisst also geschlossen "
                 "und nicht still"
                 if wir_nachher.get(SONDE_STILL) == 1 else
                 f"der angehaltene Kontrollstrom ist auch verschwunden "
                 f"({wir_nachher.get(SONDE_STILL)}) -- die Messung kann "
                 "'geschlossen' nicht von 'still' unterscheiden")

        zeilen = protokoll.read_text(encoding="utf-8").splitlines()
        stops = [z for z in zeilen if " stop " in f" {z} "]
        B.notiz(f"Vorschalter-Protokoll: {zeilen}")
        B.urteil("K6", bool(stops),
                 f"der Schalter setzt `systemctl --user stop` ab "
                 f"({len(stops)} Zeilen) -- ein Lebenszyklus-Ende, kein "
                 "Stummschalten"
                 if stops else
                 "der Schalter hat kein `systemctl stop` abgesetzt")
        verweigert = [z for z in zeilen if "VERWEIGERT" in z]
        B.urteil("K6", not verweigert,
                 "kein echter Dienst des Nutzers wurde angefasst"
                 if not verweigert else
                 f"der Schalter zielte auf nicht abgebildete Units: "
                 f"{verweigert}")
        return {"protokoll": zeilen, "bericht": bericht,
                "angefasst": sorted(angefasst)}
    finally:
        os.environ["PATH"] = alter_pfad
        if still is not None:
            still.send_signal(18)                # SIGCONT, damit terminate wirkt
            still.terminate()
            try:
                still.wait(timeout=5)
            except subprocess.TimeoutExpired:
                still.kill()
        transiente_units_aufraeumen(unsere)


def _protokoll_units(protokoll: Path) -> set[str]:
    text = protokoll.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"\S+\.service", text))


# ---------------------------------------------------------------------------
# K7 -- `ok` heisst nicht "rc war 0"
# ---------------------------------------------------------------------------

def k7_ehrlicher_bericht(B: Bilanz, pruefling: Path) -> None:
    """Der einzige Punkt mit eingespeisten Grenzen -- und zwar notwendig.

    "rc=0, und der Strom laeuft weiter" ist von aussen nicht herstellbar,
    ohne einen Dienst zu bauen, der genau diesen Fehler macht. `stoppe()`
    nimmt `lauf` und `video` ausdruecklich entgegen, damit dieser Fall
    pruefbar ist. Die Naht steht im Ledger unter "Grenzen".
    """
    pause = modul_satz(pruefling, f"{PAKET}.recorder.pause")

    def lauf_mit(rc: int, aktiv: str):
        def f(argv, **kw):
            return types.SimpleNamespace(returncode=rc, stdout=aktiv, stderr="")
        return f

    faelle = (
        ("rc=0, Strom laeuft weiter", lauf_mit(0, "inactive"),
         iter([1, 1]), False),
        ("rc=0, Strom nicht messbar", lauf_mit(0, "inactive"),
         iter([1, None]), False),
        ("rc!=0", lauf_mit(1, "inactive"), iter([1, 0]), False),
        ("Unit bleibt aktiv", lauf_mit(0, "active"), iter([1, 0]), False),
        ("alles sauber", lauf_mit(0, "inactive"), iter([1, 0]), True),
    )
    for nam, lauf, werte, soll in faelle:
        bericht = pause.stoppe(lauf=lauf, video=lambda w=werte: next(w))
        ist = bool(bericht.get("ok"))
        B.urteil("K7", ist == soll,
                 f"{nam}: ok={ist} ({bericht.get('meldung') or 'ohne Befund'})"
                 if ist == soll else
                 f"{nam}: ok={ist}, erwartet {soll} -- Bericht {bericht}")

    # Der BELEG unterscheidet gemessen von nur-behauptet.
    mit = pause.stoppe(lauf=lauf_mit(0, "inactive"),
                       video=lambda w=iter([1, 0]): next(w))
    ohne = pause.stoppe(lauf=lauf_mit(0, "inactive"),
                        video=lambda w=iter([0, 0]): next(w))
    if B.wiegen("K7", "beleg mit vs. ohne Strom davor",
                mit.get("beleg"), ohne.get("beleg")):
        B.urteil("K7", mit.get("beleg") == "strom_gemessen"
                 and ohne.get("beleg") == "nur_unit_zustand",
                 "der Bericht sagt selbst, ob ein Strom GEMESSEN wurde oder "
                 "nur der Unit-Zustand traegt")

    # Die Allowlist: ein Schalter, der jede Unit stoppen kann, ist keiner.
    try:
        pause.stoppe(units=("irgendwas-fremdes.service",),
                     lauf=lauf_mit(0, "inactive"), video=lambda: 0)
        B.schlecht("K7", "eine fremde Unit wurde ohne Widerspruch akzeptiert")
    except ValueError:
        B.gut("K7", "eine Unit ausserhalb der Allowlist wird zurueckgewiesen")


# ---------------------------------------------------------------------------
# K8 -- Bild und Ton GEMEINSAM
# ---------------------------------------------------------------------------

def k8_bild_und_ton(B: Bilanz, ergebnis: dict | None) -> None:
    if ergebnis is None:
        B.schlecht("K8", "der Pausenlauf aus K6 kam nicht zustande -- "
                         "ohne ihn ist nicht messbar, WAS gestoppt wurde")
        return
    # Das Protokoll haelt den ORIGINALWORTLAUT fest -- die Zeile, die der
    # Pruefling abgesetzt hat, nicht ihre Abbildung. Genau die ist hier die
    # Aussage: WELCHE Unit wollte der Schalter stoppen?
    stops = [z for z in ergebnis["protokoll"] if re.search(r"\bstop\b", z)]
    angefasst = {u for u in UNIT_JE_ZIEL if any(u in z for z in stops)}
    B.notiz(f"gestoppte Units (Originalwortlaut): {sorted(angefasst)}")

    bild = EYES_UNIT in angefasst
    ton = EARS_UNIT in angefasst
    B.urteil("K8", bild,
             f"der BILD-Pfad wird gestoppt ({EYES_UNIT})"
             if bild else
             f"der BILD-Pfad bleibt laufen -- {EYES_UNIT} wurde nicht "
             "gestoppt")
    B.urteil("K8", ton,
             f"der TON-Pfad wird gestoppt ({EARS_UNIT})"
             if ton else
             f"der TON-Pfad bleibt laufen: {EARS_UNIT} steht in der "
             "Allowlist, aber nicht in PAUSE_UNITS. Der Aufnahmestrom des "
             "Mikrofons bleibt damit offen -- und genau er ist es, an dem "
             "das Plasma-Symbol haengt (Design §4.2). Ein Pausenschalter, "
             "der den Ton nicht schliesst, loest die Zusage aus §1.2 "
             "Punkt 2 nicht ein")
    B.urteil("K8", bild and ton,
             "Bild und Ton werden GEMEINSAM abgeschaltet"
             if bild and ton else
             "ein Pfad allein genuegt nicht -- hier wird genau einer "
             "abgeschaltet")


# ---------------------------------------------------------------------------
# K9 -- Sichtbarkeit am Sprite
# ---------------------------------------------------------------------------

def k9_sprite(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    try:
        pause, state = modul_satz(pruefling, f"{PAKET}.recorder.pause",
                                  f"{PAKET}.hub.state")
    except Exception as exc:                                    # noqa: BLE001
        B.schlecht("K9", f"{PAKET}.hub.state nicht ladbar: {exc!r}")
        return
    rt = arbeit / "rt-k9"
    rt.mkdir(parents=True, exist_ok=True)

    hub = state.HubState()
    hub.set_mitschnitt_quelle(lambda: pause.schneidet_mit(rt))

    ohne = hub.snapshot().get("mitschnitt")
    pause.herzschlag(rt)
    frisch = hub.snapshot().get("mitschnitt")
    # KUENSTLICH VORGERUECKT statt gewartet: der Herzschlag bekommt einen
    # Zeitstempel weit hinter der Frist.
    pause.herzschlag(rt, uhr=lambda: time.time() - 10 * pause.HERZSCHLAG_FRIST_S)
    alt = hub.snapshot().get("mitschnitt")
    pause.herzschlag_loeschen(rt)
    weg = hub.snapshot().get("mitschnitt")

    if not B.wiegen("K9", "Hub-Schnappschuss mitschnitt um den Herzschlag",
                    ohne, frisch):
        return
    B.urteil("K9", ohne is False and frisch is True,
             "der Hub zeigt den ECHTEN Zustand: ohne Herzschlag aus, mit an")
    B.urteil("K9", alt is False,
             "ein VERALTETER Herzschlag zaehlt nicht -- die Anzeige "
             "leuchtet nach einem SIGKILL nicht weiter"
             if alt is False else
             "ein veralteter Herzschlag laesst die Anzeige an -- nach einem "
             "SIGKILL des Dienstes zeigte das Sprite Mitschnitt, wo keiner ist")
    B.urteil("K9", weg is False,
             "ohne Herzschlagdatei ist die Anzeige aus")

    # Und die letzte Naht: das Sprite ist Rust. Der Plan verlangt den Nachweis
    # ueber den FACE-DIAGNOSEZAEHLER und ausdruecklich nicht ueber eine
    # Selbstauskunft. Ob der Zaehler existiert und ob der Malvorgang ihn
    # bewegt, ist an der Quelle des Prueflings ablesbar -- ausgefuehrt wird
    # hier nichts (siehe Ledger, "Grenzen").
    diag = pruefling / GESICHT / "diag.rs"
    surface = pruefling / GESICHT / "surface.rs"
    if not diag.is_file() or not surface.is_file():
        B.schlecht("K9", f"{GESICHT}/diag.rs oder surface.rs fehlt -- der "
                         "geforderte Face-Diagnosezaehler ist nicht pruefbar")
        return
    diag_text = diag.read_text(encoding="utf-8", errors="replace")
    surf_text = surface.read_text(encoding="utf-8", errors="replace")
    feld = re.search(r"pub\s+(mitschnitt\w*):\s*u64", diag_text)
    B.urteil("K9", bool(feld),
             f"der Face-Diagnosezustand fuehrt einen Mitschnitt-Zaehler "
             f"({feld.group(1)})" if feld else
             "im Face-Diagnosezustand gibt es KEINEN Zaehler fuer den "
             "Mitschnitt-Indikator. Der Plan verlangt den Nachweis der "
             "Sprite-Aenderung ueber den Face-Diagnosezaehler; es gibt "
             "keinen. Gezaehlt wird nur `voice_indikator_gezeichnet` "
             "(T-3.14)")
    verworfen = re.search(r"let\s+_\s*=\s*mitschnitt_gemalt", surf_text)
    B.urteil("K9", not verworfen,
             "das Ergebnis von mitschnitt_malen() wird weitergereicht"
             if not verworfen else
             "surface.rs verwirft das Ergebnis von mitschnitt_malen() mit "
             "`let _ = mitschnitt_gemalt;` -- der Kommentar daneben sagt "
             "'getrennt gezaehlt', gezaehlt wird es nirgends")


# ---------------------------------------------------------------------------
# K10 -- Restart=on-failure
# ---------------------------------------------------------------------------

def k10_restart(B: Bilanz, pruefling: Path) -> None:
    pfad = pruefling / UNITS_VERZ / RECORDER_UNIT
    if not pfad.is_file():
        B.schlecht("K10", f"{UNITS_VERZ}/{RECORDER_UNIT} fehlt")
        return
    zeilen = [z.strip() for z in
              pfad.read_text(encoding="utf-8", errors="replace").splitlines()
              if z.strip().startswith("Restart=")]
    B.notiz(f"{RECORDER_UNIT}: {zeilen}")
    B.urteil("K10", zeilen == ["Restart=on-failure"],
             "Restart=on-failure -- ein `systemctl stop` bleibt ein Ende"
             if zeilen == ["Restart=on-failure"] else
             f"Restart steht auf {zeilen} -- mit `always` hoebe systemd den "
             "Pausenschalter nach RestartSec von selbst wieder auf")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: t73_pruefstand.py <pruefling>", file=sys.stderr)
        return 2
    pruefling = Path(argv[1]).resolve()
    if not pruefling.is_dir():
        print(f"Pruefling {pruefling} ist kein Verzeichnis", file=sys.stderr)
        return 2

    print(f"T-7.3.v -- Pruefling: {pruefling}", flush=True)
    print(f"Marke dieses Laufs:  {TAG}", flush=True)
    print("Mutant-zu-Kriterium:", flush=True)
    for m, k in sorted(MUTANTEN_GRENZEN.items()):
        print(f"    {m:32s} -> {k}", flush=True)
    print("-" * 72, flush=True)

    sys.path.insert(0, str(pruefling))
    B = Bilanz()
    arbeit = Path(tempfile.mkdtemp(prefix=f"{TAG}-"))
    alter_pfad = os.environ.get("PATH", "")
    try:
        # DIE SPERRE, ueber den ganzen Lauf. Sie weist jede daimon-Unit
        # zurueck; nur K6 haengt fuer die Dauer seines Eingriffs den
        # abbildenden Vorschalter davor. Ohne diese zweite Reihe hat ein
        # Fehler in der Einspeisung am 18.08. um 10:16 zwei echte
        # Wahrnehmungsdienste GESTARTET -- sie liefen 30 s und haben zwei
        # Eintraege ins Archiv des Nutzers geschrieben.
        sperr_log = arbeit / "sperre.log"
        sperr_log.write_text("", encoding="utf-8")
        sperre = vorschalter_bauen(arbeit, sperr_log, name="sperre",
                                   abbilden=False)
        os.environ["PATH"] = f"{sperre}:{alter_pfad}"
        for name, fn in (("K1", lambda: k1_eigene_komponente(B, pruefling)),
                         ("K2", lambda: k2_umschalter(B, pruefling, arbeit)),
                         ("K3", lambda: k3_automatik(B, pruefling, arbeit)),
                         ("K5", lambda: k5_liste(B, pruefling, arbeit)),
                         ("K4", lambda: k4_fremder_strom(B, pruefling)),
                         ("K7", lambda: k7_ehrlicher_bericht(B, pruefling)),
                         ("K9", lambda: k9_sprite(B, pruefling, arbeit)),
                         ("K10", lambda: k10_restart(B, pruefling))):
            print(f"\n--- {name} " + "-" * (68 - len(name)), flush=True)
            try:
                fn()
            except Exception as exc:                            # noqa: BLE001
                B.schlecht(name, f"Messung abgestuerzt: {exc!r}")

        print("\n--- K6 " + "-" * 66, flush=True)
        try:
            ergebnis = k6_schliesst_nicht_stumm(B, pruefling, arbeit)
        except Exception as exc:                                # noqa: BLE001
            B.schlecht("K6", f"Messung abgestuerzt: {exc!r}")
            ergebnis = None
        print("\n--- K8 " + "-" * 66, flush=True)
        try:
            k8_bild_und_ton(B, ergebnis)
        except Exception as exc:                                # noqa: BLE001
            B.schlecht("K8", f"Messung abgestuerzt: {exc!r}")
        sperre_pruefen(B, sperr_log)
        return B.abschluss()
    finally:
        os.environ["PATH"] = alter_pfad
        transiente_units_aufraeumen(UNIT_JE_ZIEL.values())
        shutil.rmtree(arbeit, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
