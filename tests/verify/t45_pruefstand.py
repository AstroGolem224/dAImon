#!/usr/bin/env python3
"""Pruefstand fuer T-4.5 — den Ausfuehrungsauftrag.

Geprueft wird die AKZEPTANZLISTE von T-4.5 (Implementierungsplan, Z. 1229 ff.)
gegen Design 1.3 und 6.2, Kriterium fuer Kriterium, ohne `&&`-Verkettung.
Jedes Kriterium rechnet einzeln ab; ein rotes Kriterium verhindert nicht die
Messung der uebrigen.

  K1  Keine Signatur (Design 1.3 und 6.2 haben den HMAC gestrichen)
  K2  Die acht Felder; `params_hash` bindet die Parameter
  K3  `audience` bindet an genau EINEN Broker
  K4  Festgelegte kanonische Serialisierung; `schema` verhindert Lesarten
  K5  Monotone Frist -- eine Zeitumstellung verlaengert nichts
  K6  Herkunft ueber den Socket (Peer-Pruefung), nicht ueber Kryptografie
  K7  Ticketeinloesung beim Hub, unmittelbar vor der Ausfuehrung

Reiner Prueflogik-Test, ohne Ausfuehrung
----------------------------------------------------------------------------
Der Verifikationsabsatz sagt das ausdruecklich, und dieser Pruefstand haelt
sich daran: Es wird nie ein `gdbus` gerufen, nie eine Datei geschrieben, nie
ein Kurzbefehl ausgeloest. Wo eine Reihenfolge gemessen wird ("Ticket VOR der
Tat"), steht an der Stelle der Tat ein Spitzel, der nur mitschreibt, dass er
gerufen wurde. Ausfuehrungs-Kanarienvoegel liegen bei den Broker-Tasks
(T-4.7 ff.), nicht hier.

Die umgekehrte Mutante
----------------------------------------------------------------------------
K1 ist der ungewoehnliche Teil. Eine Mutante, die eine HMAC-Pruefung
EINFUEHRT, fuegt Sicherheitsmaschinerie hinzu -- und muss trotzdem als
Verstoss gegen Design 6.2 gemeldet werden. Der Grund steht dort und ist kein
Geiz:

  Ein Broker kann nicht mit einem Schluessel pruefen, den nur der Hub hat.
  Verteilt man ihn, kann jeder Broker faelschen. Und fuer den Angreifer, den
  1.3 ausdruecklich NICHT abwehrt (Codeausfuehrung unter derselben uid), ist
  er per `ptrace` ohnehin lesbar.

Eine HMAC-Pruefung waere also Scheinsicherheit, und die ist teurer als keine:
Sie erzeugt Vertrauen, das nicht gedeckt ist -- und verdeckt, dass die
Herkunft AUSSCHLIESSLICH an der Leitung haengt (K6). Genau deshalb misst K1
zweigleisig: einmal am Verhalten (ein Auftrag mit den acht Feldern wird
angenommen, ein neuntes Feld abgewiesen) und einmal am Bauteil (kein
Authentifizierungs-Primitiv im Auftragsweg). Die zweite Messung faengt die
Mutante, die den HMAC nur OPTIONAL prueft -- am Verhalten faellt die nicht
auf.

Jede Manipulation wird gewogen
----------------------------------------------------------------------------
`waegen()` bricht ab, wenn ein Eingriff nichts veraendert hat. Ein Verifizierer
darf nicht "abgewiesen" melden, wenn er in Wahrheit nichts angefasst hat.
Und zu jeder Verweigerung steht eine Positivkontrolle: derselbe Auftrag ohne
den Eingriff MUSS angenommen werden.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import socket
import sys
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7")

# Anhang D kennt T-4.5.v nicht. Die Mutanten sind deshalb hier gesetzt, jeder
# an genau ein Akzeptanzkriterium gebunden. Die Zuordnung wird bei jedem Lauf
# ausgegeben, damit sie nicht in einem Kommentar verrottet.
MUTANTEN_GRENZEN = {
    "hmac-pflicht": "K1",
    "hmac-optional": "K1",
    "feld-faellt-weg": "K2",
    "params-hash-egal": "K2",
    "audience-egal": "K3",
    "schema-egal": "K4",
    "serialisierung-egal": "K4",
    "frist-nach-wanduhr": "K5",
    "broker-frist-nach-wanduhr": "K5",
    "peer-egal": "K6",
    "ticket-wieder-einloesbar": "K7",
    "ticket-nach-der-tat": "K7",
}

# Die acht Felder aus Design 6.2 und aus der Akzeptanzliste. Hier
# ABGESCHRIEBEN, nicht aus dem Pruefling geholt -- sonst pruefte der
# Verifizierer die Liste gegen sich selbst.
FELDER_SOLL = ("audience", "schema", "action_id", "params", "params_hash",
               "ticket", "deadline_monotonic", "turn_id")

# Die vier Broker aus Design 6.4.
AUDIENCES_SOLL = ("dbus", "fs", "exec", "input")

# Was im Auftragsweg NICHTS zu suchen hat. Namen von Bauteilen, nicht von
# Prosa: `hashlib.sha256` ist erlaubt (params_hash, Fingerabdruck), `hmac`
# und `compare_digest` sind es nicht -- sie sind Authentifizierung, und
# genau die ist gestrichen.
KRYPTO_VERBOTEN = ("hmac", "compare_digest", "pbkdf2_hmac", "blake2b_key",
                   "nacl", "cryptography", "Fernet", "ed25519", "signxml")
# Feldnamen, die eine Signatur waeren.
SIGNATURFELDER = ("sig", "signature", "signatur", "hmac", "mac", "auth_tag",
                  "token", "key_id")

# Der Auftragsweg: die Dateien, in denen ein Auftrag entsteht, reist und
# geprueft wird. Ein HMAC an irgendeiner dieser Stellen ist der Verstoss.
AUFTRAGSWEG = (
    "daimon/common/order.py",
    "daimon/hub/order.py",
    "daimon/hub/coordinator.py",
    "daimon/brokers/dienst.py",
    "daimon/brokers/dbus/broker.py",
    "daimon/brokers/dbus/daemon.py",
    "daimon/brokers/fs/daemon.py",
    "daimon/brokers/exec/daemon.py",
    "daimon/brokers/input/daemon.py",
)

# Die drei Broker mit `verarbeite`-Abschluss im Dienstmantel, und das Modul,
# dessen Aufruf die TAT ist. Die Reihenfolge "Ticket, dann Tat" wird hier
# gemessen (K7), ohne die Tat je auszufuehren.
MANTEL_BROKER = {
    "daimon/brokers/fs/daemon.py": "fs",
    "daimon/brokers/exec/daemon.py": "broker",
    "daimon/brokers/input/daemon.py": "broker",
}


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
        print("\nBilanz T-4.5:")
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
    """Unabhaengig nachgerechnet, nicht aus dem Pruefling geholt.

    Damit ist die kanonische Form GEPINNT: sortierte Schluessel, keine
    Leerzeichen, kein ASCII-Escaping. Wer sie aendert, macht diesen
    Verifizierer rot -- und genau das soll er.
    """
    text = json.dumps(params or {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def eigene_bytes(daten: dict) -> bytes:
    """Die kanonische Zeile, unabhaengig gebaut. Dieselbe Regel wie oben."""
    return json.dumps({f: daten[f] for f in sorted(daten)}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def gut(**abweichung) -> dict:
    """Ein Auftrag, der ALLE acht Felder korrekt traegt."""
    params = {"component": "kwin", "shortcut": "Window Maximize"}
    auftrag = {
        "v": None,  # wird gleich entfernt; nur als Platzhalter der Reihenfolge
        "audience": "dbus",
        "schema": "daimon.order.v1",
        "action_id": "kde.shortcut.invoke",
        "params": params,
        "params_hash": eigener_hash(params),
        "ticket": "t-" + "a" * 40,
        "deadline_monotonic": 1000.0,
        "turn_id": "t_88",
    }
    del auftrag["v"]
    auftrag.update(abweichung)
    if "params" in abweichung and "params_hash" not in abweichung:
        auftrag["params_hash"] = eigener_hash(abweichung["params"])
    return auftrag


def wirft(fehlerklasse, funktion, *args, **kw) -> str:
    """Gibt die Fehlermeldung zurueck, oder "" wenn nichts geworfen wurde."""
    try:
        funktion(*args, **kw)
    except fehlerklasse as fehler:
        return str(fehler) or "(ohne Meldung)"
    except Exception as fehler:  # ein anderer Fehler ist auch kein Durchlass
        return f"[{type(fehler).__name__}] {fehler}"
    return ""


# -- AST-Leser --------------------------------------------------------------

def baum(pfad: Path) -> ast.AST | None:
    try:
        return ast.parse(pfad.read_text(encoding="utf-8"), filename=str(pfad))
    except (OSError, SyntaxError):
        return None


def krypto_namen(pfad: Path) -> list[str]:
    """Authentifizierungs-Primitive im Bauteil, nicht in der Prosa.

    Gelesen wird der SYNTAXBAUM: Importe, Namen und Attribute. Ein Kommentar,
    der "HMAC" erklaert (und in order.py steht genau so einer), ist kein
    Befund -- ein `import hmac` ist einer.
    """
    b = baum(pfad)
    if b is None:
        return []
    treffer: set[str] = set()
    for knoten in ast.walk(b):
        namen: list[str] = []
        if isinstance(knoten, ast.Import):
            namen = [a.name for a in knoten.names]
        elif isinstance(knoten, ast.ImportFrom):
            namen = [knoten.module or ""] + [a.name for a in knoten.names]
        elif isinstance(knoten, ast.Attribute):
            namen = [knoten.attr]
        elif isinstance(knoten, ast.Name):
            namen = [knoten.id]
        for name in namen:
            for verboten in KRYPTO_VERBOTEN:
                if verboten.lower() in name.lower():
                    treffer.add(f"{name} ({pfad.name})")
    return sorted(treffer)


def aufrufe_mit_argument(b: ast.AST, funktionsname: str, argument: str
                         ) -> list[bool]:
    """Fuer jeden Aufruf von `funktionsname`: traegt er `argument=`?"""
    ergebnis = []
    for knoten in ast.walk(b):
        if not isinstance(knoten, ast.Call):
            continue
        ziel = knoten.func
        name = (ziel.attr if isinstance(ziel, ast.Attribute)
                else getattr(ziel, "id", ""))
        if name != funktionsname:
            continue
        ergebnis.append(any(kw.arg == argument for kw in knoten.keywords))
    return ergebnis


def horch_aufrufe(b: ast.AST) -> dict[str, bool]:
    """`_horche_einfach(<SOCKET>, ...)` -> hat der Aufruf `erlaubte_units`?

    Der erste Positionsparameter ist der Socketname; er steht als Konstante
    da (`AKTION_SOCKET`), also wird der Name des Namens gelesen.
    """
    gefunden: dict[str, bool] = {}
    for knoten in ast.walk(b):
        if not isinstance(knoten, ast.Call):
            continue
        ziel = knoten.func
        name = (ziel.attr if isinstance(ziel, ast.Attribute)
                else getattr(ziel, "id", ""))
        args = list(knoten.args)
        if name == "Thread":
            # Der Hub startet `_horche_einfach` als Thread: das Ziel steht in
            # `target=`, die Argumente in `args=`/`kwargs=`.
            ziel_kw = next((k for k in knoten.keywords if k.arg == "target"),
                           None)
            zielname = ""
            if ziel_kw is not None:
                z = ziel_kw.value
                zielname = (z.attr if isinstance(z, ast.Attribute)
                            else getattr(z, "id", ""))
            if zielname != "_horche_einfach":
                continue
            args_kw = next((k for k in knoten.keywords if k.arg == "args"),
                           None)
            socketname = ""
            if args_kw is not None and isinstance(args_kw.value, ast.Tuple) \
                    and args_kw.value.elts:
                erstes = args_kw.value.elts[0]
                socketname = getattr(erstes, "id", "")
            kwargs_kw = next((k for k in knoten.keywords if k.arg == "kwargs"),
                             None)
            hat = False
            if kwargs_kw is not None and isinstance(kwargs_kw.value, ast.Dict):
                hat = any(isinstance(s, ast.Constant)
                          and s.value == "erlaubte_units"
                          for s in kwargs_kw.value.keys)
            if socketname:
                gefunden[socketname] = hat
        elif name == "_horche_einfach" and args:
            socketname = getattr(args[0], "id", "")
            if socketname:
                gefunden[socketname] = any(k.arg == "erlaubte_units"
                                           for k in knoten.keywords)
    return gefunden


def verarbeite_reihenfolge(pfad: Path, tatmodul: str
                           ) -> tuple[int | None, int | None]:
    """Zeile der Ticketeinloesung und Zeile der ersten TAT in `verarbeite`."""
    b = baum(pfad)
    if b is None:
        return None, None
    for knoten in ast.walk(b):
        if not (isinstance(knoten, ast.FunctionDef)
                and knoten.name == "verarbeite"):
            continue
        ticket = tat = None
        for unter in ast.walk(knoten):
            if not isinstance(unter, ast.Call):
                continue
            ziel = unter.func
            if isinstance(ziel, ast.Attribute):
                wurzel = getattr(ziel.value, "id", "")
                if ziel.attr == "ticket_beim_hub_einloesen":
                    ticket = (unter.lineno if ticket is None
                              else min(ticket, unter.lineno))
                elif wurzel == tatmodul:
                    tat = (unter.lineno if tat is None
                           else min(tat, unter.lineno))
        return ticket, tat
    return None, None


def auftragsbuch_orte(wurzel: Path) -> list[str]:
    """Wo wird `Auftragsbuch(...)` gebaut? Ueber den GANZEN Produktivbaum."""
    orte = []
    for pfad in sorted(wurzel.rglob("*.py")):
        b = baum(pfad)
        if b is None:
            continue
        for knoten in ast.walk(b):
            if isinstance(knoten, ast.Call) and \
                    getattr(knoten.func, "id", "") == "Auftragsbuch":
                orte.append(f"{pfad.relative_to(wurzel.parent)}:{knoten.lineno}")
    return orte


# -- K1: keine Signatur -----------------------------------------------------

def pruefe_k1(pruefling: Path, order: ModuleType, arbeit: Path,
              bericht: Bericht) -> None:
    # 1. Positivkontrolle des Lesers ZUERST. Ohne sie waere "kein HMAC
    #    gefunden" nicht von "der Leser sieht nichts" zu unterscheiden --
    #    genau der Falschbefund, der am 17.08. viermal passiert ist.
    koeder = arbeit / "koeder_hmac.py"
    koeder.write_text(
        "import hmac\n"
        "def pruefe(a, b, schluessel):\n"
        "    return hmac.compare_digest(hmac.new(schluessel, a).digest(), b)\n",
        encoding="utf-8")
    gefunden = krypto_namen(koeder)
    bericht.pruefe("K1", len(gefunden) >= 2,
                   f"Positivkontrolle: der AST-Leser findet ein eingelegtes "
                   f"`import hmac` samt `compare_digest` -> {gefunden}")

    # Zweite Positivkontrolle: er darf sich nicht an PROSA stossen. In
    # order.py steht ein Absatz ueber den gestrichenen HMAC; ein Leser, der
    # daran anschlaegt, waere gegen jeden ehrlichen Baum rot.
    prosa = arbeit / "koeder_prosa.py"
    prosa.write_text(
        '"""Kein HMAC. Design 6.2 hat die Signatur gestrichen."""\n'
        "# hmac waere hier Zeremonie\n"
        "WERT = 1\n", encoding="utf-8")
    bericht.pruefe("K1", krypto_namen(prosa) == [],
                   "Positivkontrolle: derselbe Leser schlaegt NICHT an einem "
                   "Kommentar an, der den gestrichenen HMAC erklaert")

    # 2. Der Auftragsweg selbst.
    for rel in AUFTRAGSWEG:
        pfad = pruefling / rel
        if not pfad.is_file():
            bericht.fehler("K1", f"{rel} fehlt -- der Auftragsweg ist nicht "
                                 f"vollstaendig messbar")
            continue
        treffer = krypto_namen(pfad)
        bericht.pruefe("K1", not treffer,
                       f"{rel} enthaelt kein Authentifizierungs-Primitiv "
                       f"(Design 6.2: Signatur gestrichen); gefunden: "
                       f"{treffer}")

    # 3. Kein Signaturfeld im Format.
    felder = tuple(getattr(order, "FELDER", ()))
    schlecht = [f for f in felder if f.lower() in SIGNATURFELDER]
    bericht.pruefe("K1", not schlecht,
                   f"kein Feld des Auftrags ist eine Signatur; verdaechtig: "
                   f"{schlecht}")

    # 4. Am Verhalten: der Auftrag mit genau den acht Feldern wird
    #    ANGENOMMEN. Eine Mutante, die eine Signatur VERLANGT, faellt hier.
    fehler = wirft(Exception, order.pruefe, gut(), audience="dbus", jetzt=0.0)
    bericht.pruefe("K1", fehler == "",
                   f"ein Auftrag mit genau den acht Feldern aus Design 6.2 "
                   f"wird angenommen -- ohne Signatur: {fehler or 'ok'}")

    # 5. Und ein neuntes Feld wird abgewiesen. Damit kann eine Signatur auch
    #    nicht als "optionale Erweiterung" hineinwachsen.
    mit_sig = gut()
    mit_sig["sig"] = "hmac-sha256:dead" + "be" * 20
    abgewiesen = wirft(Exception, order.pruefe, mit_sig,
                       audience="dbus", jetzt=0.0)
    waegen(fehler or "angenommen", abgewiesen or "angenommen",
           "neuntes Feld `sig` in den Auftrag gelegt")
    bericht.pruefe("K1", abgewiesen != "",
                   f"ein Auftrag mit zusaetzlichem `sig`-Feld wird abgewiesen "
                   f"(sonst waechst die Signatur als Erweiterung hinein): "
                   f"{abgewiesen or 'ANGENOMMEN'}")


# -- K2: die acht Felder, und `params_hash` bindet die Parameter ------------

def pruefe_k2(order: ModuleType, bericht: Bericht) -> None:
    felder = tuple(getattr(order, "FELDER", ()))
    bericht.pruefe("K2", sorted(felder) == sorted(FELDER_SOLL),
                   f"der Auftrag traegt genau die acht Felder aus Design 6.2: "
                   f"{sorted(felder)} vs {sorted(FELDER_SOLL)}")

    # Positivkontrolle vor jeder Verweigerung.
    ohne_eingriff = wirft(Exception, order.pruefe, gut(),
                          audience="dbus", jetzt=0.0)
    bericht.pruefe("K2", ohne_eingriff == "",
                   f"Positivkontrolle: der unversehrte Auftrag wird "
                   f"angenommen ({ohne_eingriff or 'ok'})")

    # Jedes einzelne Feld fehlt einmal.
    for feld in FELDER_SOLL:
        stumpf = gut()
        del stumpf[feld]
        meldung = wirft(Exception, order.pruefe, stumpf,
                        audience="dbus", jetzt=0.0)
        bericht.pruefe("K2", meldung != "",
                       f"ein Auftrag ohne `{feld}` wird abgewiesen: "
                       f"{meldung or 'ANGENOMMEN'}")

    # Manipulierte Parameter: der Wert wird gewogen, nicht behauptet.
    original = gut()
    manipuliert = dict(original)
    manipuliert["params"] = {"component": "kwin", "shortcut": "Window Close"}
    waegen(original["params"], manipuliert["params"],
           "Parameter veraendert, `params_hash` unveraendert gelassen")
    waegen(eigener_hash(original["params"]),
           eigener_hash(manipuliert["params"]),
           "sha256 ueber die kanonische Parameterform vorher/nachher")
    meldung = wirft(Exception, order.pruefe, manipuliert,
                    audience="dbus", jetzt=0.0)
    bericht.pruefe("K2", meldung != "",
                   f"manipulierte Parameter bei unveraendertem `params_hash` "
                   f"werden abgewiesen: {meldung or 'ANGENOMMEN'}")

    # Die Gegenrichtung: nur der Hash wird gedreht.
    gedreht = gut()
    alt = gedreht["params_hash"]
    gedreht["params_hash"] = "sha256:" + "de" * 32
    waegen(alt, gedreht["params_hash"], "`params_hash` im Auftrag ersetzt")
    meldung = wirft(Exception, order.pruefe, gedreht,
                    audience="dbus", jetzt=0.0)
    bericht.pruefe("K2", meldung != "",
                   f"ein `params_hash`, der nicht zu den Parametern passt, "
                   f"wird abgewiesen: {meldung or 'ANGENOMMEN'}")

    # Der Pruefling rechnet dasselbe wie der unabhaengig nachgerechnete Wert.
    for params in ({}, {"a": 1}, {"b": 0.50, "a": "ä"},
                   {"a": "ä", "b": 0.5}):
        bericht.pruefe("K2", order.params_hash(params) == eigener_hash(params),
                       f"`params_hash({params!r})` = "
                       f"{order.params_hash(params)} stimmt mit dem "
                       f"unabhaengig gerechneten sha256 ueberein")


# -- K3: `audience` bindet an genau einen Broker -----------------------------

def pruefe_k3(pruefling: Path, order: ModuleType, bericht: Bericht) -> None:
    audiences = tuple(getattr(order, "AUDIENCES", ()))
    bericht.pruefe("K3", sorted(audiences) == sorted(AUDIENCES_SOLL),
                   f"genau vier Zielgruppen (Design 6.4): {sorted(audiences)}")

    # Positivkontrolle: derselbe Auftrag beim RICHTIGEN Broker.
    beim_richtigen = wirft(Exception, order.pruefe, gut(audience="dbus"),
                           audience="dbus", jetzt=0.0)
    bericht.pruefe("K3", beim_richtigen == "",
                   f"Positivkontrolle: ein DBus-Auftrag wird bei `daimon-dbus` "
                   f"angenommen ({beim_richtigen or 'ok'})")

    # Der Kern des Akzeptanzpunkts, wortwoertlich.
    beim_falschen = wirft(Exception, order.pruefe, gut(audience="dbus"),
                          audience="fs", jetzt=0.0)
    waegen(beim_richtigen or "angenommen", beim_falschen or "angenommen",
           "derselbe Auftrag bei `daimon-fs` statt `daimon-dbus` eingereicht")
    bericht.pruefe("K3", beim_falschen != "",
                   f"ein DBus-Auftrag ist bei `daimon-fs` NICHT einreichbar: "
                   f"{beim_falschen or 'ANGENOMMEN'}")

    # Jede Kreuzung einzeln -- vier Broker, zwoelf falsche Paarungen.
    for ziel in AUDIENCES_SOLL:
        for wo in AUDIENCES_SOLL:
            meldung = wirft(Exception, order.pruefe, gut(audience=ziel),
                            audience=wo, jetzt=0.0)
            if ziel == wo:
                bericht.pruefe("K3", meldung == "",
                               f"{ziel} -> {wo}: angenommen "
                               f"({meldung or 'ok'})")
            else:
                bericht.pruefe("K3", meldung != "",
                               f"{ziel} -> {wo}: abgewiesen "
                               f"({meldung or 'ANGENOMMEN'})")

    # Eine Zielgruppe, die es nicht gibt, ist ein Fehler und kein Broker.
    for erfunden in ("daimon-fs", "DBUS", "", "alle"):
        meldung = wirft(Exception, order.pruefe, gut(audience=erfunden),
                        audience=erfunden, jetzt=0.0)
        bericht.pruefe("K3", meldung != "",
                       f"unbekannte audience {erfunden!r} wird abgewiesen: "
                       f"{meldung or 'ANGENOMMEN'}")

    # Der WEG gehoert dem Hub, nicht dem Auftrag: der Auftrag traegt keinen
    # Socketpfad, und der Hub kennt fuer jede Zielgruppe genau einen.
    quelle = (pruefling / "daimon/hub/daemon.py").read_text(encoding="utf-8")
    b = ast.parse(quelle)
    sockets: dict = {}
    for knoten in ast.walk(b):
        if isinstance(knoten, ast.Assign) and \
                any(getattr(z, "id", "") == "BROKER_SOCKETS"
                    for z in knoten.targets) and \
                isinstance(knoten.value, ast.Dict):
            sockets = {k.value: v.value for k, v in
                       zip(knoten.value.keys, knoten.value.values)
                       if isinstance(k, ast.Constant)
                       and isinstance(v, ast.Constant)}
    bericht.pruefe("K3", sorted(sockets) == sorted(AUDIENCES_SOLL),
                   f"der Hub kennt fuer jede Zielgruppe genau einen Socket "
                   f"(BROKER_SOCKETS): {sorted(sockets)}")
    bericht.pruefe("K3", len(set(sockets.values())) == len(sockets),
                   f"keine zwei Zielgruppen teilen sich einen Socket: "
                   f"{sockets}")


# -- K4: kanonische Serialisierung und `schema` ------------------------------

def pruefe_k4(order: ModuleType, bericht: Bericht) -> None:
    daten = gut()
    kanon = order.kanonisch(daten)

    # Positivkontrolle: die kanonische Zeile geht durch.
    ohne_eingriff = wirft(Exception, order.pruefe, kanon,
                          audience="dbus", jetzt=0.0)
    bericht.pruefe("K4", ohne_eingriff == "",
                   f"Positivkontrolle: die kanonische Zeile wird angenommen "
                   f"({ohne_eingriff or 'ok'})")
    bericht.pruefe("K4", kanon == eigene_bytes(daten),
                   f"die kanonische Form ist die unabhaengig nachgerechnete "
                   f"(sortiert, ohne Leerzeichen, ohne ASCII-Escaping)")

    # Dieselben Werte, andere Einfuegereihenfolge -> dieselben Bytes.
    rueckwaerts = {k: daten[k] for k in reversed(list(daten))}
    bericht.pruefe("K4", order.kanonisch(rueckwaerts) == kanon,
                   "dieselben Werte in anderer Einfuegereihenfolge ergeben "
                   "dieselben Bytes")

    # Drei Abweichungen, jede einzeln, jede gewogen.
    abweichungen = {
        "Schluessel unsortiert geschrieben":
            json.dumps({k: daten[k] for k in reversed(list(daten))},
                       sort_keys=False, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8"),
        "Leerzeichen nach Doppelpunkt und Komma":
            json.dumps(daten, sort_keys=True, separators=(", ", ": "),
                       ensure_ascii=False).encode("utf-8"),
        "Einrueckung":
            json.dumps(daten, sort_keys=True, indent=2,
                       ensure_ascii=False).encode("utf-8"),
    }
    # Und eine mit Umlaut, damit auch `ensure_ascii` gemessen ist.
    mit_umlaut = gut(params={"text": "Grüße"})
    abweichungen["ASCII-Escaping (\\u00fc statt ü)"] = json.dumps(
        mit_umlaut, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")

    for was, rohbytes in abweichungen.items():
        vergleich = (kanon if "ASCII" not in was
                     else order.kanonisch(mit_umlaut))
        waegen(vergleich, rohbytes, f"Serialisierung: {was}")
        # Gleiche Werte, andere Bytes -- das ist die Voraussetzung dafuer,
        # dass hier ueberhaupt etwas zu messen ist.
        bericht.pruefe("K4",
                       json.loads(rohbytes.decode()) ==
                       json.loads(vergleich.decode()),
                       f"{was}: dieselben Werte, nur anders geschrieben")
        meldung = wirft(Exception, order.pruefe, rohbytes,
                        audience="dbus", jetzt=0.0)
        bericht.pruefe("K4", meldung != "",
                       f"abweichende Serialisierung wird abgewiesen ({was}): "
                       f"{meldung or 'ANGENOMMEN'}")

    # `schema`: die bekannte Fassung geht, jede andere nicht.
    bericht.pruefe("K4", getattr(order, "SCHEMA", None) == "daimon.order.v1",
                   f"das Schema heisst `daimon.order.v1` "
                   f"(ist: {getattr(order, 'SCHEMA', None)!r})")
    for falsch in ("daimon.order.v2", "daimon.order.v0", 3, "", None,
                   "DAIMON.ORDER.V1"):
        meldung = wirft(Exception, order.pruefe, gut(schema=falsch),
                        audience="dbus", jetzt=0.0)
        bericht.pruefe("K4", meldung != "",
                       f"unbekanntes schema {falsch!r} wird abgewiesen: "
                       f"{meldung or 'ANGENOMMEN'}")


# -- K5: monotone Frist ------------------------------------------------------

def pruefe_k5(pruefling: Path, order: ModuleType, hub_order: ModuleType,
              bericht: Bericht) -> None:
    buch = hub_order.Auftragsbuch()
    frist = float(hub_order.VORGABE_FRIST_S)

    # 1. Der Unterschied wird GEMESSEN, nicht behauptet. Die monotone Uhr ist
    #    die Betriebszeit (Groessenordnung 10^5 s), die Wanduhr die Unix-Zeit
    #    (10^9 s). Wer die falsche liest, liegt um Jahrzehnte daneben.
    vor_m, vor_t = time.monotonic(), time.time()
    auftrag = buch.ausstellen(audience="dbus", action_id="kde.shortcut.invoke",
                              params={"a": 1}, turn_id="t_1")
    abstand_monoton = abs(auftrag.deadline_monotonic - (vor_m + frist))
    abstand_wanduhr = abs(auftrag.deadline_monotonic - (vor_t + frist))
    print(f"Frist {auftrag.deadline_monotonic:.3f} | monoton+Frist "
          f"{vor_m + frist:.3f} (Abstand {abstand_monoton:.3f}) | "
          f"wanduhr+Frist {vor_t + frist:.3f} (Abstand {abstand_wanduhr:.3f})")
    bericht.pruefe("K5", abstand_monoton < 1.0,
                   f"`deadline_monotonic` liegt an der MONOTONEN Uhr "
                   f"(Abstand {abstand_monoton:.3f} s)")
    bericht.pruefe("K5", abstand_wanduhr > 1e6,
                   f"`deadline_monotonic` liegt NICHT an der Wanduhr "
                   f"(Abstand {abstand_wanduhr:.0f} s)")

    # 2. Eine Zeitumstellung verlaengert nichts -- und die Gegenprobe zeigt,
    #    dass die Messung ueberhaupt etwas sehen KANN.
    #    Beide Uhren werden einzeln verstellt; nur die monotone darf wirken.
    ticket = auftrag.ticket
    echt_time, echt_monotonic = time.time, time.monotonic
    try:
        time.time = lambda: echt_time() + 1_000_000.0
        nach_wanduhr = buch.gueltig(ticket)
    finally:
        time.time = echt_time
    try:
        time.monotonic = lambda: echt_monotonic() + frist + 1.0
        nach_monoton = buch.gueltig(ticket)
    finally:
        time.monotonic = echt_monotonic
    waegen(nach_wanduhr, nach_monoton,
           f"Wanduhr um +1e6 s vorgestellt vs. monotone Uhr um "
           f"+{frist + 1:.0f} s vorgestellt")
    bericht.pruefe("K5", nach_wanduhr is True,
                   f"eine Zeitumstellung von +1e6 s laesst das Ticket "
                   f"unveraendert gueltig (gueltig={nach_wanduhr})")
    bericht.pruefe("K5", nach_monoton is False,
                   f"eine um die Frist vorgestellte MONOTONE Uhr laesst es "
                   f"verfallen (gueltig={nach_monoton})")
    bericht.pruefe("K5", buch.gueltig(ticket) is True,
                   "nach beiden Eingriffen ist das Ticket wieder gueltig -- "
                   "die Uhren sind sauber zurueckgestellt")

    # 3. Abgelaufene Frist an der Pruefung, mit Positivkontrolle.
    lebt = wirft(Exception, order.pruefe, gut(deadline_monotonic=1000.0),
                 audience="dbus", jetzt=999.0)
    tot = wirft(Exception, order.pruefe, gut(deadline_monotonic=1000.0),
                audience="dbus", jetzt=1001.0)
    waegen(lebt or "angenommen", tot or "angenommen",
           "Pruefzeitpunkt von 999.0 auf 1001.0 gestellt (Frist 1000.0)")
    bericht.pruefe("K5", lebt == "",
                   f"Positivkontrolle: vor der Frist wird angenommen "
                   f"({lebt or 'ok'})")
    bericht.pruefe("K5", tot != "",
                   f"abgelaufene monotone Frist wird abgewiesen: "
                   f"{tot or 'ANGENOMMEN'}")
    # Der Rand gehoert benannt: genau AUF der Frist ist abgelaufen.
    rand = wirft(Exception, order.pruefe, gut(deadline_monotonic=1000.0),
                 audience="dbus", jetzt=1000.0)
    bericht.pruefe("K5", rand != "",
                   f"genau auf der Frist gilt sie als abgelaufen: "
                   f"{rand or 'ANGENOMMEN'}")

    # 4. Und die Broker lesen dieselbe Uhr. Positivkontrolle des Lesers:
    #    ein eingelegtes `time.time()` muss er finden.
    koeder = ast.parse("def verarbeite(roh):\n"
                       "    return pruefe(roh, audience='x', "
                       "jetzt=time.time())\n")
    bericht.pruefe("K5", aufrufe_mit_argument(koeder, "pruefe", "jetzt")
                   == [True],
                   "Positivkontrolle: der Leser findet den `jetzt=`-Parameter "
                   "eines eingelegten `pruefe`-Aufrufs")
    for rel in ("daimon/brokers/fs/daemon.py", "daimon/brokers/exec/daemon.py",
                "daimon/brokers/input/daemon.py",
                "daimon/brokers/dbus/daemon.py"):
        pfad = pruefling / rel
        text = pfad.read_text(encoding="utf-8") if pfad.is_file() else ""
        b = ast.parse(text) if text else None
        if b is None:
            bericht.fehler("K5", f"{rel} nicht lesbar")
            continue
        uhren = set()
        for knoten in ast.walk(b):
            if isinstance(knoten, ast.Call):
                for kw in knoten.keywords:
                    if kw.arg != "jetzt":
                        continue
                    quelle = kw.value
                    name = (f"{getattr(quelle.func.value, 'id', '?')}."
                            f"{quelle.func.attr}"
                            if isinstance(quelle, ast.Call)
                            and isinstance(quelle.func, ast.Attribute)
                            else ast.dump(quelle)[:60])
                    uhren.add(name)
        bericht.pruefe("K5", uhren and all("monotonic" in u for u in uhren),
                       f"{rel} reicht die MONOTONE Uhr in die Auftragspruefung "
                       f"({sorted(uhren) or 'kein jetzt= gefunden'})")


# -- K6: Herkunft ueber den Socket -------------------------------------------

class Probe:
    """Eine Verbindung an einen echten Broker-Socket. Ohne Ausfuehrung.

    Der Broker-Rumpf ist ein Spitzel: er fuehrt nichts aus, er schreibt auf,
    dass er gerufen wurde. Damit misst diese Probe genau eine Sache -- ob die
    Leitung geprueft wird, bevor der Auftrag ueberhaupt jemanden erreicht.
    """

    def __init__(self, dienst: ModuleType, verzeichnis: Path, name: str,
                 **kw) -> None:
        self.erreicht: list[bytes] = []
        self.pfad = verzeichnis / f"{name}.sock"
        self.dienst = dienst
        self.kw = kw
        self.faden: threading.Thread | None = None

    def _rumpf(self, roh: bytes) -> dict:
        self.erreicht.append(roh)
        return {"ok": False, "grund": "spitzel"}

    def __enter__(self) -> "Probe":
        self.faden = threading.Thread(
            target=self.dienst.lauf,
            args=(self.pfad, self._rumpf),
            kwargs=dict(self.kw), daemon=True)
        self.faden.start()
        for _ in range(200):
            if self.pfad.exists():
                break
            time.sleep(0.01)
        return self

    def __exit__(self, *_):
        self.pfad.unlink(missing_ok=True)
        return False

    def klopfen(self, nutzlast: bytes = b'{"leer":true}\n') -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(5.0)
            # Zwischen `bind()` und `listen()` gibt es die Datei schon und
            # den Horcher noch nicht -- das ist ein Rennen des PRUEFSTANDS,
            # kein Befund. Also kurz nachfassen.
            for versuch in range(200):
                try:
                    c.connect(str(self.pfad))
                    break
                except ConnectionRefusedError:
                    if versuch == 199:
                        raise
                    time.sleep(0.01)
            try:
                c.sendall(nutzlast)
            except OSError:
                return b""
            try:
                return c.recv(4096)
            except OSError:
                return b""


def pruefe_k6(pruefling: Path, arbeit: Path, bericht: Bericht) -> None:
    ipc = importlib.import_module("daimon.common.ipc")
    dienst = importlib.import_module("daimon.brokers.dienst")

    # Wer sind wir? Ohne diese Antwort waere die Positivkontrolle unten
    # geraten statt gemessen.
    eigene_unit = ipc._unit(__import__("os").getpid())
    print(f"Eigene Unit dieses Pruefstands: {eigene_unit!r}")

    echt_peer = ipc.peer_of
    gerufen: list[int] = []

    def spitzel(conn, produzent):
        gerufen.append(1)
        return echt_peer(conn, produzent)

    # 1. FINDET die Peer-Pruefung ueberhaupt statt?
    try:
        ipc.peer_of = spitzel
        with Probe(dienst, arbeit, "spur") as probe:
            antwort = probe.klopfen()
        stattgefunden = bool(gerufen)
    finally:
        ipc.peer_of = echt_peer
    print(f"Peer-Pruefung gerufen: {len(gerufen)}x, Rumpf erreicht: "
          f"{len(probe.erreicht)}x, Antwort {antwort[:80]!r}")
    bericht.pruefe("K6", stattgefunden,
                   f"der Broker-Socket prueft die Gegenstelle, bevor er eine "
                   f"Zeile liest (`ipc.peer_of` gerufen: {len(gerufen)}x). "
                   f"Der Auftrag traegt keine Signatur (Design 6.2) -- diese "
                   f"Pruefung IST der Herkunftsnachweis")

    if not stattgefunden:
        # Kein Zahn, kein Biss. Die beiden folgenden Messungen waeren hier
        # nicht "gruen", sondern nicht durchfuehrbar -- und das wird gesagt,
        # nicht verschwiegen.
        bericht.fehler("K6", "Zaehne der Peer-Pruefung NICHT gemessen: es "
                             "findet keine statt (siehe oben)")
        bericht.fehler("K6", "Positivkontrolle der Probe NICHT gemessen: "
                             "ohne Peer-Pruefung gibt es nichts zu passieren")
    else:
        # 2. Hat sie Zaehne? Eine abgelehnte Gegenstelle darf den Rumpf nicht
        #    erreichen.
        def verweigern(conn, produzent):
            raise ipc.PeerError("Unit 'fremd.service' nicht zugelassen")

        def durchlassen(conn, produzent):
            echt = echt_peer(conn, produzent)
            return ipc.Peer(pid=echt.pid, uid=echt.uid, gid=echt.gid,
                            unit="daimon-hub.service", produzent=produzent)

        try:
            ipc.peer_of = verweigern
            with Probe(dienst, arbeit, "zahn") as probe_zu:
                probe_zu.klopfen()
            zu = len(probe_zu.erreicht)
            ipc.peer_of = durchlassen
            with Probe(dienst, arbeit, "offen") as probe_auf:
                probe_auf.klopfen()
            auf = len(probe_auf.erreicht)
        finally:
            ipc.peer_of = echt_peer
        waegen(zu, auf, "Gegenstelle einmal als fremde, einmal als "
                        "`daimon-hub.service` aufgeloest")
        bericht.pruefe("K6", zu == 0,
                       f"eine fremde Gegenstelle erreicht den Broker-Rumpf "
                       f"NICHT ({zu} Auftraege durchgekommen)")
        bericht.pruefe("K6", auf == 1,
                       f"Positivkontrolle: `daimon-hub.service` kommt durch "
                       f"({auf} Auftraege) -- die Probe kann also ankommen")

    # 3. Die andere Richtung derselben Leitung: der Broker loest sein Ticket
    #    beim Hub ein, ueber `aktion.sock`. Auch dort ist der Auftragsweg nur
    #    so herkunftssicher wie der Socket.
    b = baum(pruefling / "daimon/hub/daemon.py")
    sockets = horch_aufrufe(b) if b is not None else {}
    print(f"Unit-Allowlisten der Hub-Sockets: {sockets}")
    bericht.pruefe("K6", sockets.get("KONTEXT_SOCKET") is True,
                   f"Positivkontrolle des Lesers: er sieht die Allowlist von "
                   f"`kontext.sock` ({sockets.get('KONTEXT_SOCKET')})")
    bericht.pruefe("K6", sockets.get("AKTION_SOCKET") is True,
                   f"`aktion.sock` hat eine Unit-Allowlist "
                   f"({sockets.get('AKTION_SOCKET')}) -- ueber diesen Socket "
                   f"laufen Aktionsbitte UND Ticketeinloesung (T-4.5, "
                   f"Akzeptanzpunkte 6 und 7)")

    # 4. Und der Socket ist 0600. Nicht der Herkunftsnachweis, aber die
    #    Vorbedingung dafuer, dass er ueberhaupt etwas bedeutet.
    with Probe(dienst, arbeit, "modus") as probe:
        modus = probe.pfad.stat().st_mode & 0o777
    bericht.pruefe("K6", modus == 0o600,
                   f"der Broker-Socket wird mit 0600 angelegt (ist "
                   f"{modus:04o})")


# -- K7: Ticketeinloesung beim Hub -------------------------------------------

def pruefe_k7(pruefling: Path, order: ModuleType, hub_order: ModuleType,
              arbeit: Path, bericht: Bericht) -> None:
    buch = hub_order.Auftragsbuch()

    def neu(**kw):
        return buch.ausstellen(audience="dbus",
                               action_id="kde.shortcut.invoke",
                               params={"a": 1}, turn_id="t_1", **kw)

    # Tickets sind einmalig und nicht ratbar.
    tickets = {neu().ticket for _ in range(100)}
    bericht.pruefe("K7", len(tickets) == 100,
                   f"100 Auftraege ergeben 100 verschiedene Tickets "
                   f"({len(tickets)})")
    bericht.pruefe("K7", all(len(t) >= 32 for t in tickets),
                   "jedes Ticket ist mindestens 32 Zeichen lang")

    # Einloesen: einmal ja, zweimal nein. Positivkontrolle inbegriffen.
    a = neu()
    erst = wirft(Exception, buch.einloesen, a.ticket)
    zweit = wirft(Exception, buch.einloesen, a.ticket)
    waegen(erst or "eingeloest", zweit or "eingeloest",
           "dasselbe Ticket ein zweites Mal eingereicht")
    bericht.pruefe("K7", erst == "",
                   f"Positivkontrolle: das Ticket loest beim ersten Mal ein "
                   f"({erst or 'ok'})")
    bericht.pruefe("K7", zweit != "",
                   f"ein wiederholtes Ticket wird abgewiesen: "
                   f"{zweit or 'ERNEUT EINGELOEST'}")

    # Unbekannt, verfallen, abgelaufen -- jedes einzeln.
    bericht.pruefe("K7", wirft(Exception, buch.einloesen, "gibt-es-nicht")
                   != "", "ein unbekanntes Ticket wird abgewiesen")
    b_ = neu()
    buch.verfallen_lassen(b_.ticket)
    bericht.pruefe("K7", wirft(Exception, buch.einloesen, b_.ticket) != "",
                   "ein nach Ablehnung verfallenes Ticket wird abgewiesen")
    c = neu(jetzt=0.0, frist_s=10.0)
    abgelaufen = wirft(Exception, buch.einloesen, c.ticket, jetzt=11.0)
    bericht.pruefe("K7", "abgelaufen" in abgelaufen.lower(),
                   f"ein abgelaufenes Ticket wird als abgelaufen abgewiesen, "
                   f"nicht als eingeloest: {abgelaufen!r}")
    nochmal = wirft(Exception, buch.einloesen, c.ticket, jetzt=11.0)
    bericht.pruefe("K7", "eingeloest" not in nochmal.lower(),
                   f"verfallen ist nicht eingeloest -- der zweite Versuch "
                   f"meldet nicht 'bereits eingeloest': {nochmal!r}")

    # `gueltig()` fragt, ohne zu verbrauchen. Sonst waere die Pruefung des
    # Brokers selbst die Einloesung, und die gehoert dem Hub.
    d = neu()
    bericht.pruefe("K7", buch.gueltig(d.ticket) and buch.gueltig(d.ticket),
                   "`gueltig()` sagt zweimal hintereinander ja")
    bericht.pruefe("K7", wirft(Exception, buch.einloesen, d.ticket) == "",
                   "und danach loest dasselbe Ticket noch ein -- `gueltig()` "
                   "verbraucht nichts")

    # Der Broker FRAGT, er entscheidet nicht: `pruefe` nimmt das Urteil des
    # Hubs entgegen und faellt es nicht selbst.
    e = neu()
    daten = gut(ticket=e.ticket, deadline_monotonic=e.deadline_monotonic)
    mit_ja = wirft(Exception, order.pruefe, daten, audience="dbus",
                   jetzt=e.deadline_monotonic - 1.0,
                   ticket_gueltig=lambda t: True)
    mit_nein = wirft(Exception, order.pruefe, daten, audience="dbus",
                     jetzt=e.deadline_monotonic - 1.0,
                     ticket_gueltig=lambda t: False)
    waegen(mit_ja or "angenommen", mit_nein or "angenommen",
           "Urteil des Hubs ueber das Ticket von ja auf nein gedreht")
    bericht.pruefe("K7", mit_ja == "",
                   f"Positivkontrolle: mit gueltigem Ticket angenommen "
                   f"({mit_ja or 'ok'})")
    bericht.pruefe("K7", mit_nein != "",
                   f"ein vom Hub abgelehntes Ticket wird abgewiesen: "
                   f"{mit_nein or 'ANGENOMMEN'}")
    bericht.pruefe("K7", buch.gueltig(e.ticket),
                   "`pruefe()` selbst verbraucht das Ticket nicht -- die "
                   "Einloesung gehoert dem Hub")

    # Das BUCH liegt im Hub. Ein Broker mit eigenem Buch waere "hoechstens
    # einmal" je Broker und damit gar nicht.
    orte = auftragsbuch_orte(pruefling / "daimon")
    print(f"`Auftragsbuch(...)` gebaut in: {orte}")
    bericht.pruefe("K7", orte,
                   f"Positivkontrolle des Suchers: er findet ueberhaupt eine "
                   f"Stelle, die ein Auftragsbuch baut ({orte})")
    fremd = [o for o in orte if not o.startswith("daimon/hub/")]
    bericht.pruefe("K7", not fremd,
                   f"kein Broker haelt ein eigenes Auftragsbuch: {fremd}")

    # Und der Weg dorthin: der Broker fragt `aktion.sock`, und der Hub
    # beantwortet dort `ticket_einloesen`.
    dienst = importlib.import_module("daimon.brokers.dienst")
    bericht.pruefe("K7", getattr(dienst, "HUB_SOCKET", "") == "aktion.sock",
                   f"der Broker loest ueber `aktion.sock` ein (ist: "
                   f"{getattr(dienst, 'HUB_SOCKET', None)!r})")
    hub_quelle = (pruefling / "daimon/hub/daemon.py").read_text(
        encoding="utf-8")
    bericht.pruefe("K7", '"ticket_einloesen"' in hub_quelle,
                   "der Hub beantwortet `art: ticket_einloesen` an diesem "
                   "Endpunkt")

    # Reihenfolge: Ticket VOR der Tat. Erst am Verhalten (DBus, dessen
    # Ausfuehrung injizierbar ist -- es wird nichts ausgefuehrt, nur
    # mitgeschrieben), dann strukturell fuer die drei Mantel-Broker.
    dbus = importlib.import_module("daimon.brokers.dbus.broker")
    operation = dbus.Operation(dienst="org.kde.kglobalaccel",
                               pfad="/component/kwin",
                               schnittstelle="org.kde.kglobalaccel.Component",
                               methode="invokeShortcut",
                               argumente=("Window Maximize",))
    getan: list[list[str]] = []

    def spitzel(argv, **kw):
        getan.append(list(argv))
        class E:
            returncode = 0
            stderr = ""
        return E()

    broker = dbus.DBusBroker(operationen={"kde.shortcut.invoke": operation},
                             lauf=spitzel)
    roh = order.kanonisch(gut(deadline_monotonic=1000.0))

    def nein(_ticket):
        raise RuntimeError("Hub hat abgelehnt")

    antwort_nein = broker.ausfuehren(roh, jetzt=0.0, ticket_einloesen=nein)
    ohne_ticket = len(getan)
    antwort_ja = broker.ausfuehren(roh, jetzt=0.0,
                                   ticket_einloesen=lambda t: None)
    mit_ticket = len(getan)
    waegen(ohne_ticket, mit_ticket,
           "Ticketeinloesung einmal abgelehnt, einmal erteilt")
    bericht.pruefe("K7", ohne_ticket == 0,
                   f"ohne eingeloestes Ticket wird NICHTS getan "
                   f"({ohne_ticket} Aufrufe, Antwort "
                   f"{antwort_nein.get('grund')!r})")
    bericht.pruefe("K7", mit_ticket == 1,
                   f"Positivkontrolle: mit eingeloestem Ticket geht der eine "
                   f"Aufruf hinaus ({mit_ticket}, Antwort "
                   f"{antwort_ja.get('ok')!r})")

    # Positivkontrolle des Reihenfolge-Lesers.
    probe = arbeit / "koeder_reihenfolge.py"
    probe.write_text(
        "def verarbeite(roh):\n"
        "    dienst.ticket_beim_hub_einloesen(p, t)\n"
        "    return fs.lesen(g, 1)\n", encoding="utf-8")
    ticket_zeile, tat_zeile = verarbeite_reihenfolge(probe, "fs")
    bericht.pruefe("K7", ticket_zeile == 2 and tat_zeile == 3,
                   f"Positivkontrolle: der Reihenfolge-Leser findet beide "
                   f"Zeilen im Koeder (Ticket {ticket_zeile}, Tat {tat_zeile})")

    for rel, tatmodul in MANTEL_BROKER.items():
        ticket_zeile, tat_zeile = verarbeite_reihenfolge(pruefling / rel,
                                                         tatmodul)
        if ticket_zeile is None or tat_zeile is None:
            bericht.fehler("K7", f"{rel}: Reihenfolge nicht messbar "
                                 f"(Ticket {ticket_zeile}, Tat {tat_zeile})")
            continue
        bericht.pruefe("K7", ticket_zeile < tat_zeile,
                       f"{rel}: die Ticketeinloesung (Z. {ticket_zeile}) steht "
                       f"VOR der Tat (Z. {tat_zeile})")


# -- Hauptlauf ---------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pruefstand T-4.5")
    ap.add_argument("pruefling", type=Path)
    args = ap.parse_args(argv)
    pruefling = args.pruefling.resolve()

    bericht = Bericht()
    print(f"Pruefling: {pruefling}")
    print("Mutant -> Kriterium: " + ", ".join(
        f"{m}={k}" for m, k in sorted(MUTANTEN_GRENZEN.items())))

    with tempfile.TemporaryDirectory(prefix="t45-") as tmp:
        arbeit = Path(tmp)
        try:
            order = lade(pruefling, "daimon.common.order")
            hub_order = importlib.import_module("daimon.hub.order")
        except Exception as fehler:
            for k in KRITERIEN:
                bericht.fehler(k, f"Auftragsmodule nicht ladbar: {fehler!r}")
            return bericht.bilanz()

        for name, funktion in (
                ("K1", lambda: pruefe_k1(pruefling, order, arbeit, bericht)),
                ("K2", lambda: pruefe_k2(order, bericht)),
                ("K3", lambda: pruefe_k3(pruefling, order, bericht)),
                ("K4", lambda: pruefe_k4(order, bericht)),
                ("K5", lambda: pruefe_k5(pruefling, order, hub_order,
                                         bericht)),
                ("K6", lambda: pruefe_k6(pruefling, arbeit, bericht)),
                ("K7", lambda: pruefe_k7(pruefling, order, hub_order, arbeit,
                                         bericht)),
        ):
            try:
                funktion()
            except Exception as fehler:
                # Ein abgestuerztes Kriterium ist rot, kein stiller Ausfall.
                bericht.fehler(name, f"Messung abgebrochen: {fehler!r}")

    return bericht.bilanz()


if __name__ == "__main__":
    raise SystemExit(main())
