#!/usr/bin/env python3
"""Pruefstand fuer T-4.4 — die Policy-Engine.

Geprueft wird die AKZEPTANZLISTE von T-4.4 (Implementierungsplan, Z. 1213 ff.)
und Design 2.5 / 263, Kriterium fuer Kriterium, ohne `&&`-Verkettung. Jedes
Kriterium rechnet einzeln ab; ein rotes Kriterium verhindert nicht die Messung
der uebrigen.

  K1  Alle Tests aus T-4.3.t gruen
  K2  Der Hub kanonisiert die Parameter selbst und rechnet `params_hash` selbst
  K3  `initiator` wird aus der eingeloesten Rundenmarke abgeleitet
  K4  Strukturierte `when:`-Praedikate, keine String-Globs
  K5  Zustimmungs-Cache mit Schluessel (session_id, action_id, params_hash)
  K6  Vier Gueltigkeiten: once, session, ttl:*, persistent
  K7  Gestenfenster: erteilt UND nur innerhalb 2 s (Design 2.5)
  K8  Direktbefehl-Ausnahme ist Hub-Eigentum (Design 263)

Warum hier mehr steht als `pytest tests/test_policy.py`
----------------------------------------------------------------------------
Der Verifikationsabsatz verlangt den Testlauf und **zusaetzlich** einen Test
mit manipuliertem `params_hash`. Ein Verifizierer, der nur die bestehende
Testdatei aufruft, prueft, was der Builder selbst geschrieben hat. Deshalb
misst dieser Pruefstand jedes Kriterium mit eigenen Anfragen gegen einen
eigenen Katalog -- und die vier Kriterien, an denen eine Policy still zur
Attrappe wird (K2, K3, K7, K8), zusaetzlich AN DER NAHT: nicht an
`Policy.entscheide`, sondern an `Hub.aktion_anfrage`, dem Weg, den eine
Anfrage im Betrieb nimmt.

Das ist die Frage, an der dieses Repo sechsmal gescheitert ist: wer ruft das
auf, im Betrieb, heute? Fuer die Policy ist die Antwort
`daimon/hub/daemon.py::aktion_anfrage` -> `Koordinator.ausfuehren` ->
`Policy.entscheide`. Der Pruefstand baut genau diese Kette mit den ECHTEN
Teilen (Markenbuch, Policy, Consent, Auftragsbuch, Schlange, Audit) und
schickt Anfragen hinein, wie ein Absender sie schickt.

Jede Manipulation wird gewogen
----------------------------------------------------------------------------
`waegen()` haelt den Wert VOR und NACH dem Eingriff nebeneinander und faellt
laut, wenn er sich nicht bewegt hat. Am 17.08. hat eine Positivkontrolle des
Builders nichts veraendert -- das `replace` traf nicht, die Kopie blieb
byteweise identisch, und der Pruefer meldete brav `ok`. "Nichts gefunden"
muss von "nicht gemessen" unterscheidbar sein.

Die Uhr wird EINGESPEIST, nicht abgewartet
----------------------------------------------------------------------------
`daimon/hub/policy.py` hat absichtlich keine eigene Uhr: `Anfrage.jetzt`
kommt herein (Modulkopf: "Ein Entscheider, der selbst auf die Uhr sieht, ist
nicht pruefbar"). Der Pruefstand nutzt genau das und misst das Gestenfenster
mit gesetzten Zeitpunkten statt mit `time.sleep(2.1)`. Begruendung: ein
Verifizierer, der gegen `time.time()` misst, misst einen ZEITPUNKT und keine
Bedingung -- das sind die vier Messfehler aus
docs/REVIEWER-UEBERGABE-17.08.md 4. Es wird an keiner Stelle gewartet;
Laufzeit des Gestenfensterteils: Millisekunden. Damit die eingespeiste Uhr
nicht selbst zur Attrappe wird, gibt es zu jeder Fenstermessung eine
Positivkontrolle: dieselbe Anfrage, nur `jetzt` verschoben, muss ein ANDERES
Verdikt ergeben.

Waechter statt Vorratspruefung
----------------------------------------------------------------------------
Das Gestenfenster hat heute keinen Zulauf: kein Eintrag in
`config/actions/core.yaml` traegt `gestenfenster_s`, und `geste_gesehen()`
ruft im Betrieb niemand. Das ist kein Fehler (CLAUDE.md, Regel 6: kein
Vorratscode fuer einen Zulauf, den es nicht gibt) -- aber es ist eine Zusage,
die im Betrieb nicht gilt. Also steht hier ein WAECHTER: sobald der Katalog
eine Aktion mit Gestenfenster bekommt, MUSS es im Betrieb einen Aufrufer von
`geste_gesehen()` geben. Solange nicht, meldet der Waechter den Zustand
sichtbar, statt ihn gruen zu schweigen.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8")

# Anhang D kennt T-4.4.v nicht. Die Mutanten sind deshalb hier gesetzt, jeder
# an genau ein Akzeptanzkriterium gebunden. Die Zuordnung wird bei jedem Lauf
# ausgegeben, damit sie nicht in einem Kommentar verrottet.
MUTANTEN_GRENZEN = {
    "schranke-nur-unverstanden": "K1",
    "hash-aus-dem-request": "K2",
    "initiator-aus-dem-request": "K3",
    "when-als-glob": "K4",
    "cache-ohne-hash": "K5",
    "ttl-ohne-frist": "K6",
    "geste-immer-offen": "K7",
    "direkt-fuer-jede-quelle": "K8",
    "naht-quelle-vom-absender": "K8",
}

# Der Katalog, gegen den dieser Pruefstand entscheidet. Bewusst EIGENER
# Katalog und nicht der aus tests/test_policy.py: sonst pruefte der
# Verifizierer die Annahmen des Builders mit dessen eigenen Daten.
KATALOG = {
    "media.playpause": {
        "direct": True, "destructive": False,
        "foreground": "allow", "background": "deny", "scheduled": "deny",
    },
    # Nicht direkt: die Vorschau ist Pflicht, auch fuer den Parser.
    "window.to_next_desktop": {
        "direct": False, "destructive": False,
        "foreground": "ask", "background": "deny", "scheduled": "deny",
    },
    "window.to_previous_desktop": {
        "direct": False, "destructive": False,
        "foreground": "ask", "background": "deny", "scheduled": "deny",
    },
    "audio.volume.set": {
        "direct": True, "destructive": False,
        "foreground": "allow", "background": "deny", "scheduled": "deny",
        "params": {"value": {"type": "float", "value_between": [0.0, 1.0],
                             "required": True}},
    },
    # Design 2.5: erteilt UND nur im Fenster benutzbar.
    "clipboard.read": {
        "direct": False, "destructive": False,
        "foreground": "ask", "background": "deny", "scheduled": "deny",
        "gestenfenster_s": 2.0,
    },
}

# Eine Ebene, die nichts verschaerft -- die Katalogvorgabe soll sichtbar
# bleiben. Wo eine Ebene gebraucht wird, steht sie am Ort der Messung.
EBENE_LEER: list[dict] = []

MARKE_GUELTIG = {"id": "m1", "gueltig_bis": 1_000_000.0}
MARKE_ABGELAUFEN = {"id": "m1", "gueltig_bis": 50.0}

# Die vier `when`-Schluessel, die die Engine kennen darf (policy.py::_passt).
WHEN_SCHLUESSEL = {"initiator", "quelle", "session_id", "action_id"}
GLOB_ZEICHEN = set("*?[]")

# Design 2.5: die drei Faehigkeiten mit Gestenfenster.
GESTEN_AKTIONEN = ("clipboard.read", "input.type", "screen.declassify",
                   "declassify")


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
        print("\nBilanz T-4.4:")
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

    Der Fehler vom 17.08.: ein Eingriff, der nicht traf, und ein Pruefer, der
    `ok` meldete. Wer hier nichts veraendert, bekommt keinen gruenen Lauf,
    sondern einen Abbruch -- der folgende Befund waere nicht gemessen,
    sondern erfunden.
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


def policy_mit(modul: ModuleType, ebenen=None, katalog=None):
    return modul.Policy.aus_dateien(
        KATALOG if katalog is None else katalog,
        [EBENE_LEER] if ebenen is None else ebenen)


def anfrage(modul: ModuleType, action_id: str, **felder):
    grund: dict[str, Any] = {
        "params": {}, "session_id": "s1", "request_id": "r1",
        "quelle": "parser", "marke": MARKE_GUELTIG, "jetzt": 100.0}
    grund.update(felder)
    return modul.Anfrage(action_id=action_id, **grund)


# -- K1: alle Tests aus T-4.3.t gruen ---------------------------------------

def pytest_lauf(pruefling: Path, ziel: str, extra: Sequence[str] = ()
                ) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pruefling)
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", ziel, "-q", "-p", "no:cacheprovider",
         "--rootdir", str(pruefling), *extra],
        cwd=pruefling, env=env, text=True, capture_output=True, timeout=600)


def pruefe_k1(pruefling: Path, arbeit: Path, bericht: Bericht) -> None:
    ziel = pruefling / "tests" / "test_policy.py"
    if not ziel.is_file():
        bericht.fehler("K1", f"{ziel} fehlt -- die Tests aus T-4.3.t sind "
                             f"nicht messbar")
        return
    try:
        # Positivkontrolle ZUERST: kann dieser Apparat ueberhaupt rot werden?
        # Ohne sie waere ein gruener Lauf nicht von einem stummen zu
        # unterscheiden -- der Fall, der am 17.08. viermal eingetreten ist.
        rot = arbeit / "k1" / "tests"
        rot.mkdir(parents=True)
        (rot / "test_kontrolle_rot.py").write_text(
            "def test_der_apparat_kann_rot():\n    assert False\n",
            encoding="utf-8")
        kontrolle = pytest_lauf(pruefling, str(rot / "test_kontrolle_rot.py"))
        bericht.pruefe("K1", kontrolle.returncode != 0,
                       f"Positivkontrolle: ein absichtlich roter Test macht "
                       f"den pytest-Apparat rot (Exit {kontrolle.returncode})")

        gesammelt = pytest_lauf(pruefling, str(ziel), ["--collect-only"])
        # `-q --collect-only` fasst je Datei zusammen: "test_policy.py: 33".
        # Faellt das weg, wird die lange Form gezaehlt.
        treffer = re.search(r"test_policy\.py:\s*(\d+)", gesammelt.stdout)
        anzahl = (int(treffer.group(1)) if treffer
                  else len(re.findall(r"test_policy\.py::", gesammelt.stdout)))
        bericht.pruefe("K1", anzahl >= 20,
                       f"tests/test_policy.py traegt mindestens 20 Tests, "
                       f"gesammelt {anzahl} (ein gruener Lauf ohne Tests ist "
                       f"kein gruener Lauf)")

        lauf = pytest_lauf(pruefling, str(ziel))
        bericht.pruefe("K1", lauf.returncode == 0,
                       f"pytest tests/test_policy.py ist gruen (Exit "
                       f"{lauf.returncode}): {lauf.stdout[-1200:]}")
        # T-4.3.t verlangt ausdruecklich: kein Test wirft einen Importfehler.
        bericht.pruefe("K1", "ImportError" not in lauf.stdout
                       and "errors during collection" not in lauf.stdout,
                       f"kein Sammel- oder Importfehler (T-4.3.t-Vertrag): "
                       f"{lauf.stdout[-600:]}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K1", f"Testlauf fehlgeschlagen: {exc!r}")


# -- K2: der Hub rechnet den Hash selbst ------------------------------------

def pruefe_k2(modul: ModuleType, bericht: Bericht) -> None:
    """Die zentrale Pruefrage: ein manipulierter `params_hash` im Request."""
    try:
        pol = policy_mit(modul)
        params = {"value": 0.5, "geraet": "master"}

        echt = pol.entscheide(anfrage(modul, "audio.volume.set", params=params))
        geflunkert = "sha256:" + "de" * 32
        gefaelscht = pol.entscheide(anfrage(
            modul, "audio.volume.set", params=params,
            params_hash=geflunkert))

        # Gewogen: der mitgeschickte Wert MUSS sich vom gerechneten
        # unterscheiden, sonst misst der folgende Vergleich nichts.
        waegen(geflunkert, echt.params_hash,
               "manipulierter params_hash im Request")
        bericht.pruefe("K2", gefaelscht.params_hash == echt.params_hash,
                       f"der Hub ignoriert den mitgeschickten params_hash: "
                       f"{gefaelscht.params_hash!r} == {echt.params_hash!r}")
        bericht.pruefe("K2", gefaelscht.params_hash != geflunkert,
                       f"der mitgeschickte Wert taucht nicht in der "
                       f"Entscheidung auf: {gefaelscht.params_hash!r}")
        bericht.pruefe("K2", gefaelscht.verdikt == echt.verdikt,
                       f"das Verdikt haengt nicht am mitgeschickten Hash: "
                       f"{gefaelscht.verdikt!r} vs {echt.verdikt!r}")

        # Selbst gerechnet, nicht aus dem Pruefling geholt: sonst pruefte der
        # Pruefstand eine Formel gegen sich selbst.
        bericht.pruefe("K2", echt.params_hash == eigener_hash(params),
                       f"der Hash ist der sha256 der kanonischen Form: "
                       f"{echt.params_hash!r} vs {eigener_hash(params)!r}")

        # Kanonisierung: der Hub bringt die Parameter in EINE Schreibweise.
        andere_reihenfolge = {"geraet": "master", "value": 0.5}
        gleich = pol.entscheide(anfrage(modul, "audio.volume.set",
                                        params=andere_reihenfolge))
        bericht.pruefe("K2", gleich.params_hash == echt.params_hash,
                       f"Schluesselreihenfolge aendert den Hash nicht: "
                       f"{gleich.params_hash!r}")
        gleich2 = pol.entscheide(anfrage(
            modul, "audio.volume.set", params={"value": 0.50, "geraet": "master"}))
        bericht.pruefe("K2", gleich2.params_hash == echt.params_hash,
                       f"0.50 und 0.5 sind dieselbe Zahl und ergeben denselben "
                       f"Hash: {gleich2.params_hash!r}")

        # Positivkontrolle der Hashmessung: ANDERE Parameter, anderer Hash.
        anders = pol.entscheide(anfrage(modul, "audio.volume.set",
                                        params={"value": 0.6}))
        bericht.pruefe("K2", anders.params_hash != echt.params_hash,
                       f"Positivkontrolle: andere Parameter ergeben einen "
                       f"anderen Hash: {anders.params_hash!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K2", f"Hashmessung fehlgeschlagen: {exc!r}")


# -- K3: initiator aus der Rundenmarke --------------------------------------

def pruefe_k3(modul: ModuleType, bericht: Bericht) -> None:
    try:
        pol = policy_mit(modul)

        ohne = pol.entscheide(anfrage(modul, "media.playpause", marke=None,
                                      initiator="foreground"))
        waegen("foreground", ohne.initiator,
               "initiator='foreground' im Request ohne Rundenmarke")
        bericht.pruefe("K3", ohne.initiator == "background",
                       f"ohne Marke ist der initiator 'background', nicht der "
                       f"behauptete: {ohne.initiator!r}")
        bericht.pruefe("K3", ohne.verdikt == "deny",
                       f"und das Verdikt folgt dem abgeleiteten initiator: "
                       f"{ohne.verdikt!r} / {ohne.grund!r}")

        mit = pol.entscheide(anfrage(modul, "media.playpause",
                                     initiator="background"))
        bericht.pruefe("K3", mit.initiator == "foreground",
                       f"Positivkontrolle: MIT gueltiger Marke ist der "
                       f"initiator 'foreground', auch wenn der Request "
                       f"'background' behauptet: {mit.initiator!r}")
        bericht.pruefe("K3", mit.verdikt == "allow",
                       f"Positivkontrolle: die Aktion wird erlaubt -- die "
                       f"Engine ist nicht bloss stumm: {mit.verdikt!r}")

        abgelaufen = pol.entscheide(anfrage(modul, "media.playpause",
                                            marke=MARKE_ABGELAUFEN))
        waegen(mit.initiator, abgelaufen.initiator,
               "Marke abgelaufen (gueltig_bis < jetzt)")
        bericht.pruefe("K3", abgelaufen.initiator == "background",
                       f"eine abgelaufene Marke traegt nicht: "
                       f"{abgelaufen.initiator!r}")

        geplant = pol.entscheide(anfrage(modul, "media.playpause",
                                         quelle="scheduler"))
        bericht.pruefe("K3", geplant.initiator == "scheduled",
                       f"eine Zeitsteuerung ist 'scheduled', auch mit Marke: "
                       f"{geplant.initiator!r}")
        bericht.pruefe("K3", geplant.verdikt == "deny",
                       f"und wird abgelehnt: {geplant.verdikt!r}")

        # Kaputte Marke: eine Zeichenkette ist ein Name ohne Frist.
        kaputt = pol.entscheide(anfrage(modul, "media.playpause",
                                        marke="m1"))
        bericht.pruefe("K3", kaputt.initiator == "background",
                       f"eine Marke ohne Frist traegt nicht: "
                       f"{kaputt.initiator!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K3", f"Initiatormessung fehlgeschlagen: {exc!r}")


# -- K4: strukturierte when-Praedikate --------------------------------------

def pruefe_k4(modul: ModuleType, pruefling: Path, bericht: Bericht) -> None:
    try:
        # Positivkontrolle: ein LITERALES Praedikat greift.
        literal = policy_mit(modul, [[
            {"when": {"action_id": "media.playpause"}, "verdikt": "deny",
             "grund": "literal"}]])
        e = literal.entscheide(anfrage(modul, "media.playpause"))
        bericht.pruefe("K4", e.verdikt == "deny",
                       f"Positivkontrolle: ein literales `when` greift: "
                       f"{e.verdikt!r} / {e.grund!r}")

        # Ein Glob ist kein Praedikat: er darf NICHTS treffen.
        glob = policy_mit(modul, [[
            {"when": {"action_id": "media.*"}, "verdikt": "deny",
             "grund": "glob"}]])
        g = glob.entscheide(anfrage(modul, "media.playpause"))
        waegen(e.verdikt, g.verdikt,
               "dieselbe Regel mit Glob statt Literal")
        bericht.pruefe("K4", g.verdikt != "deny",
                       f"`media.*` wird NICHT als Muster ausgewertet: "
                       f"{g.verdikt!r} / {g.grund!r}")

        # Ein unbekannter Schluessel ist ein Fehler, kein stilles Ignorieren.
        # Wer ihn durchgehen laesst, hat eine Regel, die nie greift, und
        # niemand erfaehrt es.
        unbekannt = policy_mit(modul, [[
            {"when": {"app_id": "firefox"}, "verdikt": "deny"}]])
        try:
            unbekannt.entscheide(anfrage(modul, "media.playpause"))
            bericht.fehler("K4", "ein unbekannter `when`-Schluessel wurde "
                                 "stillschweigend ignoriert")
        except modul.PolicyFehler as fehler:
            bericht.pruefe("K4", "app_id" in str(fehler),
                           f"unbekannter `when`-Schluessel wird benannt "
                           f"abgewiesen: {fehler}")

        # Ein unbekanntes Verdikt kommt gar nicht erst in die Engine.
        try:
            policy_mit(modul, [[{"action_id": "media.playpause",
                                 "verdikt": "vielleicht"}]])
            bericht.fehler("K4", "Verdikt 'vielleicht' wurde angenommen")
        except modul.PolicyFehler as fehler:
            bericht.pruefe("K4", "vielleicht" in str(fehler),
                           f"unbekanntes Verdikt wird benannt abgewiesen: "
                           f"{fehler}")

        # Und die MITGELIEFERTE Regeldatei haelt sich daran. Das ist der
        # Zulauf: was die Engine kann, nuetzt nichts, wenn die Datei, die im
        # Betrieb geladen wird, Globs enthaelt.
        regeln = pruefling / "config" / "policy.yaml"
        if not regeln.is_file():
            bericht.fehler("K4", f"{regeln} fehlt -- die Regeldatei aus der "
                                 f"Dateiliste von T-4.4")
            return
        import yaml  # PyYAML: dasselbe Format wie im Pruefling
        roh = yaml.safe_load(regeln.read_text(encoding="utf-8")) or {}
        ebenen = roh.get("ebenen") or []
        alle = [r for e in ebenen for r in (e.get("regeln") or [])]
        bericht.pruefe("K4", len(ebenen) >= 1,
                       f"config/policy.yaml traegt mindestens eine Ebene, "
                       f"traegt {len(ebenen)}")
        bericht.pruefe("K4", len(alle) >= 1,
                       f"Positivkontrolle: die Datei traegt ueberhaupt Regeln "
                       f"({len(alle)}) -- eine leere Datei erfuellt jede "
                       f"Formpruefung")
        fremd = sorted({s for r in alle
                        for s in (r.get("when") or {}) if s not in WHEN_SCHLUESSEL})
        bericht.pruefe("K4", not fremd,
                       f"jede `when`-Bedingung nutzt nur "
                       f"{sorted(WHEN_SCHLUESSEL)}, fremd: {fremd}")
        mit_glob = [f"{r.get('grund') or r}: {w}={v}" for r in alle
                    for w, v in (r.get("when") or {}).items()
                    if isinstance(v, str) and (set(v) & GLOB_ZEICHEN)]
        bericht.pruefe("K4", not mit_glob,
                       f"keine Regel benutzt Glob-Zeichen in einem Wert: "
                       f"{mit_glob}")
        verdikte = sorted({str(r.get("verdikt")) for r in alle})
        bericht.pruefe("K4", set(verdikte) <= set(modul.VERDIKTE),
                       f"jede Regel traegt ein bekanntes Verdikt: {verdikte}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K4", f"Praedikatmessung fehlgeschlagen: {exc!r}")


# -- K5: der Cache-Schluessel -----------------------------------------------

def zustimme(pol, e, *, session_id="s1", action_id="window.to_next_desktop",
             gueltigkeit="session", jetzt=100.0) -> None:
    pol.zustimmung_merken(session_id=session_id, action_id=action_id,
                          params_hash=e.params_hash, gueltigkeit=gueltigkeit,
                          jetzt=jetzt)


def pruefe_k5(modul: ModuleType, bericht: Bericht) -> None:
    """Alle DREI Teile des Schluessels einzeln bewegt."""
    try:
        pol = policy_mit(modul)
        offen = pol.entscheide(anfrage(modul, "window.to_next_desktop"))
        bericht.pruefe("K5", offen.verdikt == "ask",
                       f"Ausgangslage: ohne Zustimmung wird gefragt: "
                       f"{offen.verdikt!r}")
        zustimme(pol, offen)
        mit = pol.entscheide(anfrage(modul, "window.to_next_desktop"))
        waegen(offen.verdikt, mit.verdikt, "Zustimmung fuer das Tripel gemerkt")
        bericht.pruefe("K5", mit.verdikt == "allow",
                       f"Positivkontrolle: das passende Tripel hebt `ask` auf "
                       f"`allow`: {mit.verdikt!r} / {mit.grund!r}")

        # (a) andere Sitzung
        fremd = pol.entscheide(anfrage(modul, "window.to_next_desktop",
                                       session_id="s2"))
        bericht.pruefe("K5", fremd.verdikt == "ask",
                       f"eine andere session_id traegt die Zustimmung nicht: "
                       f"{fremd.verdikt!r}")

        # (b) andere Aktion
        andere = pol.entscheide(anfrage(modul, "window.to_previous_desktop"))
        bericht.pruefe("K5", andere.verdikt == "ask",
                       f"eine andere action_id traegt die Zustimmung nicht: "
                       f"{andere.verdikt!r}")

        # (c) andere Parameter -- derselbe Schluessel bis auf den Hash.
        pol2 = policy_mit(modul)
        leise = pol2.entscheide(anfrage(modul, "audio.volume.set",
                                        params={"value": 0.2}, quelle="modell"))
        bericht.pruefe("K5", leise.verdikt == "ask",
                       f"Ausgangslage Parameterfall: {leise.verdikt!r}")
        zustimme(pol2, leise, action_id="audio.volume.set")
        wieder = pol2.entscheide(anfrage(modul, "audio.volume.set",
                                         params={"value": 0.2}, quelle="modell"))
        bericht.pruefe("K5", wieder.verdikt == "allow",
                       f"Positivkontrolle Parameterfall: dieselben Parameter "
                       f"tragen: {wieder.verdikt!r}")
        laut = pol2.entscheide(anfrage(modul, "audio.volume.set",
                                       params={"value": 0.9}, quelle="modell"))
        waegen(wieder.params_hash, laut.params_hash, "Parameter 0.2 -> 0.9")
        bericht.pruefe("K5", laut.verdikt == "ask",
                       f"andere Parameter tragen die Zustimmung NICHT -- "
                       f"sonst waere die Zustimmung zu 'leise' eine zu "
                       f"'laut': {laut.verdikt!r}")

        # Ein `deny` hebt keine Zustimmung auf: verboten bleibt verboten.
        pol3 = policy_mit(modul, [[
            {"action_id": "window.to_next_desktop", "verdikt": "deny",
             "grund": "vom Nutzer verboten"}]])
        e3 = pol3.entscheide(anfrage(modul, "window.to_next_desktop"))
        zustimme(pol3, e3)
        trotzdem = pol3.entscheide(anfrage(modul, "window.to_next_desktop"))
        bericht.pruefe("K5", trotzdem.verdikt == "deny",
                       f"eine Zustimmung hebt kein `deny` auf: "
                       f"{trotzdem.verdikt!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K5", f"Cachemessung fehlgeschlagen: {exc!r}")


# -- K6: die vier Gueltigkeiten ---------------------------------------------

def pruefe_k6(modul: ModuleType, bericht: Bericht) -> None:
    try:
        bericht.pruefe("K6", tuple(modul.GUELTIGKEITEN) ==
                       ("once", "session", "ttl", "persistent"),
                       f"vier Gueltigkeiten, genau diese: "
                       f"{tuple(modul.GUELTIGKEITEN)!r}")

        # once -- einmal, dann verbraucht.
        pol = policy_mit(modul)
        e = pol.entscheide(anfrage(modul, "window.to_next_desktop"))
        zustimme(pol, e, gueltigkeit="once")
        erst = pol.entscheide(anfrage(modul, "window.to_next_desktop"))
        zweit = pol.entscheide(anfrage(modul, "window.to_next_desktop"))
        waegen(erst.verdikt, zweit.verdikt, "zweite Nutzung einer `once`-Zustimmung")
        bericht.pruefe("K6", erst.verdikt == "allow",
                       f"`once` traegt einmal: {erst.verdikt!r}")
        bericht.pruefe("K6", zweit.verdikt == "ask",
                       f"`once` traegt kein zweites Mal: {zweit.verdikt!r}")

        # session -- so oft wie noetig, in dieser Sitzung.
        pol = policy_mit(modul)
        e = pol.entscheide(anfrage(modul, "window.to_next_desktop"))
        zustimme(pol, e, gueltigkeit="session")
        drei = [pol.entscheide(anfrage(modul, "window.to_next_desktop")).verdikt
                for _ in range(3)]
        bericht.pruefe("K6", drei == ["allow"] * 3,
                       f"`session` traegt wiederholt: {drei}")

        # ttl:60 -- gegen die EINGESPEISTE Uhr, nicht gegen time.time().
        pol = policy_mit(modul)
        e = pol.entscheide(anfrage(modul, "window.to_next_desktop", jetzt=100.0))
        zustimme(pol, e, gueltigkeit="ttl:60", jetzt=100.0)
        drin = pol.entscheide(anfrage(modul, "window.to_next_desktop", jetzt=159.0))
        drauss = pol.entscheide(anfrage(modul, "window.to_next_desktop", jetzt=161.0))
        waegen(drin.verdikt, drauss.verdikt,
               "Uhr von 159 auf 161 gestellt (ttl:60 ab 100)")
        bericht.pruefe("K6", drin.verdikt == "allow",
                       f"`ttl:60` traegt vor Ablauf: {drin.verdikt!r}")
        bericht.pruefe("K6", drauss.verdikt == "ask",
                       f"`ttl:60` traegt nach Ablauf nicht: {drauss.verdikt!r}")

        # persistent -- ueber jede Frist hinweg.
        pol = policy_mit(modul)
        e = pol.entscheide(anfrage(modul, "window.to_next_desktop", jetzt=100.0))
        zustimme(pol, e, gueltigkeit="persistent", jetzt=100.0)
        # Die Marke muss zu diesem Zeitpunkt noch gelten -- sonst maesse der
        # Fall den abgelaufenen initiator und nicht die Gueltigkeit.
        spaet = pol.entscheide(anfrage(modul, "window.to_next_desktop",
                                       jetzt=1e12,
                                       marke={"id": "m1", "gueltig_bis": 2e12}))
        bericht.pruefe("K6", spaet.verdikt == "allow",
                       f"`persistent` traegt auch in ferner Zukunft: "
                       f"{spaet.verdikt!r}")

        # Eine fuenfte Gueltigkeit gibt es nicht.
        for kaputt in ("ewig", "ttl", "ttl:bald"):
            try:
                pol.zustimmung_merken(session_id="s1",
                                      action_id="window.to_next_desktop",
                                      params_hash=e.params_hash,
                                      gueltigkeit=kaputt, jetzt=100.0)
                bericht.fehler("K6", f"Gueltigkeit {kaputt!r} wurde angenommen")
            except modul.PolicyFehler as fehler:
                bericht.pruefe("K6", True,
                               f"Gueltigkeit {kaputt!r} wird abgewiesen: "
                               f"{str(fehler)[:120]}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K6", f"Gueltigkeitsmessung fehlgeschlagen: {exc!r}")


# -- K7: das Gestenfenster, zweiseitig --------------------------------------

def pruefe_k7(modul: ModuleType, pruefling: Path, bericht: Bericht) -> None:
    """Zwei unabhaengige Bedingungen: erteilt UND das Fenster offen."""
    try:
        # (1) Erteilt, Fenster zu -> deny. Das ist die Haelfte, die ein
        #     Verifizierer uebersieht, der nur "erteilt" prueft.
        pol = policy_mit(modul)
        zu = pol.entscheide(anfrage(modul, "clipboard.read", jetzt=100.0))
        bericht.pruefe("K7", zu.verdikt == "deny",
                       f"ohne Geste wird verweigert: {zu.verdikt!r}")
        bericht.pruefe("K7", zu.grund == "gesture_window_closed",
                       f"und zwar mit eigenem Grund (nicht 'ask' -- sonst "
                       f"waere die Frage die Verlaengerung des Fensters): "
                       f"{zu.grund!r}")
        # Erteilt, aber Fenster weiterhin zu.
        pol.zustimmung_merken(session_id="s1", action_id="clipboard.read",
                              params_hash=zu.params_hash,
                              gueltigkeit="persistent", jetzt=100.0)
        erteilt_zu = pol.entscheide(anfrage(modul, "clipboard.read", jetzt=100.0))
        bericht.pruefe("K7", erteilt_zu.verdikt == "deny",
                       f"ERTEILT und Fenster zu bleibt `deny` -- die Erteilung "
                       f"allein reicht nicht: {erteilt_zu.verdikt!r} / "
                       f"{erteilt_zu.grund!r}")

        # (2) Fenster offen, nicht erteilt -> `ask`, nicht `allow`.
        pol2 = policy_mit(modul)
        pol2.geste_gesehen(100.0)
        offen_unerteilt = pol2.entscheide(anfrage(modul, "clipboard.read",
                                                  jetzt=100.5))
        bericht.pruefe("K7", offen_unerteilt.verdikt == "ask",
                       f"Fenster offen, nicht erteilt: es wird gefragt, nicht "
                       f"ausgefuehrt: {offen_unerteilt.verdikt!r}")

        # (3) Beides -> allow. Die Positivkontrolle: ohne sie waere jedes
        #     `deny` oben auch mit einer toten Engine erklaerbar.
        pol2.zustimmung_merken(session_id="s1", action_id="clipboard.read",
                               params_hash=offen_unerteilt.params_hash,
                               gueltigkeit="persistent", jetzt=100.0)
        beides = pol2.entscheide(anfrage(modul, "clipboard.read", jetzt=100.5))
        waegen(offen_unerteilt.verdikt, beides.verdikt,
               "Zustimmung bei offenem Fenster erteilt")
        bericht.pruefe("K7", beides.verdikt == "allow",
                       f"erteilt UND Fenster offen: {beides.verdikt!r}")

        # (4) Die Breite: 2 s, gemessen an der eingespeisten Uhr. Kein
        #     `sleep` -- policy.py hat keine eigene Uhr, `jetzt` kommt herein.
        bericht.pruefe("K7", float(modul.VORGABE_GESTENFENSTER_S) == 2.0,
                       f"das Fenster ist 2 s breit (Design 2.5): "
                       f"{modul.VORGABE_GESTENFENSTER_S!r}")
        rand = pol2.entscheide(anfrage(modul, "clipboard.read", jetzt=102.0))
        drueber = pol2.entscheide(anfrage(modul, "clipboard.read", jetzt=102.001))
        waegen(rand.verdikt, drueber.verdikt,
               "Uhr von 102.000 auf 102.001 gestellt (Geste bei 100.0)")
        bericht.pruefe("K7", rand.verdikt == "allow",
                       f"bei genau +2,000 s ist das Fenster noch offen: "
                       f"{rand.verdikt!r}")
        bericht.pruefe("K7", drueber.verdikt == "deny",
                       f"bei +2,001 s ist es zu -- die Aktion ist keine "
                       f"Reaktion mehr: {drueber.verdikt!r} / {drueber.grund!r}")

        # (5) Eine Aktion OHNE Gestenfenster wird davon nicht beruehrt --
        #     sonst waere jedes `deny` oben auch ohne die Fensterlogik da.
        ohne_fenster = pol.entscheide(anfrage(modul, "media.playpause",
                                              jetzt=100.0))
        bericht.pruefe("K7", ohne_fenster.verdikt == "allow",
                       f"Positivkontrolle: eine Aktion ohne `gestenfenster_s` "
                       f"laeuft bei geschlossenem Fenster weiter: "
                       f"{ohne_fenster.verdikt!r}")

        pruefe_k7_waechter(pruefling, bericht)
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K7", f"Gestenfenstermessung fehlgeschlagen: {exc!r}")


def gesten_aufrufer(wurzel: Path) -> list[str]:
    """Wer ruft `geste_gesehen()` im Betrieb -- ausserhalb der Definition?"""
    treffer = []
    for datei in sorted(wurzel.rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue
        for nummer, zeile in enumerate(
                datei.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "geste_gesehen(" not in zeile:
                continue
            if zeile.lstrip().startswith(("def ", "#", "*")):
                continue
            treffer.append(f"{datei}:{nummer}: {zeile.strip()[:90]}")
    return treffer


def pruefe_k7_waechter(pruefling: Path, bericht: Bericht) -> None:
    """Der Zulauf-Waechter (CLAUDE.md, Regel 6).

    Heute gibt es keinen Zulauf: der Katalog kennt keine Aktion mit
    Gestenfenster, und `geste_gesehen()` ruft niemand. Das ist zulaessig --
    Vorratscode fuer einen Zulauf, den es nicht gibt, waere derselbe Fehler
    von der anderen Seite. Unzulaessig waere: der Katalog bekommt so eine
    Aktion, und niemand oeffnet je das Fenster. Dann steht die Zusage im
    Code, gilt aber nie.
    """
    try:
        katalog_pfad = pruefling / "config" / "actions" / "core.yaml"
        if not katalog_pfad.is_file():
            bericht.fehler("K7", f"{katalog_pfad} fehlt")
            return
        import yaml
        roh = yaml.safe_load(katalog_pfad.read_text(encoding="utf-8")) or {}
        eintraege = roh.get("actions") or []
        bericht.pruefe("K7", len(eintraege) >= 1,
                       f"Positivkontrolle: der Katalog traegt ueberhaupt "
                       f"Aktionen ({len(eintraege)}) -- ein leerer Katalog "
                       f"erfuellt jede Waechterbedingung")
        mit_fenster = [str(e.get("id")) for e in eintraege
                       if e.get("gestenfenster_s") is not None
                       or str(e.get("id")) in GESTEN_AKTIONEN]

        aufrufer = gesten_aufrufer(pruefling / "daimon")
        # Positivkontrolle des Suchers: er MUSS einen Aufruf sehen koennen.
        # Ohne sie waere "kein Aufrufer gefunden" nicht von "nicht gesucht"
        # zu unterscheiden -- vier Falschbefunde am 17.08. kamen genau daher.
        with tempfile.TemporaryDirectory(prefix="t44-waechter-") as tmp:
            probe = Path(tmp) / "daimon"
            probe.mkdir()
            (probe / "attrappe.py").write_text(
                "def x(pol):\n    pol.geste_gesehen(1.0)\n", encoding="utf-8")
            gesehen = gesten_aufrufer(probe)
        bericht.pruefe("K7", len(gesehen) == 1,
                       f"Positivkontrolle: der Sucher findet einen echten "
                       f"Aufruf von geste_gesehen(): {gesehen!r}")

        print(f"Waechter Gestenfenster: Katalogeintraege {mit_fenster!r}, "
              f"Aufrufer {aufrufer!r}")
        bericht.pruefe(
            "K7", not mit_fenster or bool(aufrufer),
            f"Zulauf-Waechter: der Katalog fuehrt {mit_fenster!r} mit "
            f"Gestenfenster, aber im Betrieb ruft niemand geste_gesehen() -- "
            f"die Zusage aus Design 2.5 steht dann im Code und gilt nie")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K7", f"Waechter Gestenfenster fehlgeschlagen: {exc!r}")


# -- K8: die Direktbefehl-Ausnahme ------------------------------------------

def pruefe_k8(modul: ModuleType, bericht: Bericht) -> None:
    try:
        pol = policy_mit(modul)

        parser = pol.entscheide(anfrage(modul, "media.playpause",
                                        quelle="parser"))
        bericht.pruefe("K8", parser.verdikt == "allow",
                       f"Positivkontrolle: `direct: true` UND Hub-Parser "
                       f"ergibt `allow`: {parser.verdikt!r}")

        aus_modell = pol.entscheide(anfrage(modul, "media.playpause",
                                            quelle="modell"))
        waegen(parser.verdikt, aus_modell.verdikt,
               "dieselbe Aktion, quelle 'parser' -> 'modell'")
        bericht.pruefe("K8", aus_modell.verdikt == "ask",
                       f"dieselbe Aktion aus einer MODELLAUSGABE geht durch "
                       f"die Vorschau, egal was der Katalog sagt: "
                       f"{aus_modell.verdikt!r}")
        bericht.pruefe("K8", aus_modell.grund == "vorschau_pflicht",
                       f"und der Grund benennt die Vorschaupflicht: "
                       f"{aus_modell.grund!r}")

        # Das Katalogflag allein reicht nicht -- und der Parser allein auch
        # nicht. BEIDE Haelften einzeln bewegt.
        nicht_direkt = pol.entscheide(anfrage(modul, "window.to_next_desktop",
                                              quelle="parser"))
        bericht.pruefe("K8", nicht_direkt.verdikt == "ask",
                       f"`direct: false` geht auch fuer den Parser durch die "
                       f"Vorschau: {nicht_direkt.verdikt!r}")

        # Eine erfundene Quelle ist kein Parser.
        for erfunden in ("Parser", "parser ", "hub-parser", ""):
            e = pol.entscheide(anfrage(modul, "media.playpause",
                                       quelle=erfunden))
            bericht.pruefe("K8", e.verdikt == "ask",
                           f"quelle={erfunden!r} ist nicht 'parser': "
                           f"{e.verdikt!r}")

        # Die Ausnahme kann nur MILDERN: sie hebt kein `deny` auf.
        streng = policy_mit(modul, [[
            {"action_id": "media.playpause", "verdikt": "deny",
             "grund": "vom Nutzer verboten"}]])
        e = streng.entscheide(anfrage(modul, "media.playpause", quelle="parser"))
        bericht.pruefe("K8", e.verdikt == "deny",
                       f"die Direktausnahme hebt kein `deny` auf: "
                       f"{e.verdikt!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K8", f"Direktbefehlsmessung fehlgeschlagen: {exc!r}")


# -- Die NAHT: Hub.aktion_anfrage -------------------------------------------

class _Log:
    def info(self, *a, **kw): pass

    def warn(self, *a, **kw): pass

    def error(self, *a, **kw): pass


class Naht:
    """Der Weg, den eine Anfrage im BETRIEB nimmt -- ohne Sockets.

    Echt sind: Markenbuch, Policy (aus den Dateien des Prueflings), Consent,
    Auftragsbuch, Aktionsschlange, Audit, Koordinator und
    `Hub.aktion_anfrage` selbst. Gestellt sind nur die zwei Enden, die mit
    der Policy nichts zu tun haben: die Sprachausgabe (`tts_anfrage`) und
    der Broker (er ist nicht da, also meldet er `broker_weg` -- das Verdikt
    steht davor fest).

    `RUECKFRAGE_FRIST_S` wird auf 0,2 s gedreht: ein `ask` wartet sonst zwei
    Minuten auf einen Klick, den in diesem Lauf niemand macht. Gemessen wird
    das VERDIKT, nicht die Wartezeit.
    """

    def __init__(self, daemon_modul: ModuleType, audit_modul: ModuleType,
                 marks_modul: ModuleType, wurzel: Path, *, mit_marke: bool = True):
        self.D = daemon_modul
        self.audit_modul = audit_modul
        self.wurzel = wurzel
        (wurzel / "state").mkdir(parents=True, exist_ok=True)
        (wurzel / "rt").mkdir(parents=True, exist_ok=True)

        # Kein Pruefstand schreibt ins Journal dieser Maschine (tests/conftest).
        audit_modul._ins_journal = lambda text: None

        daemon_modul.RUECKFRAGE_FRIST_S = 0.2

        h = daemon_modul.Hub.__new__(daemon_modul.Hub)
        h.log = _Log()
        h.freigaben = marks_modul.FreigabeBuch()
        h.consent = None
        h.runtime_dir = wurzel / "rt"
        h._aktion = None
        h._audit = None
        h.gesprochen = []
        h.tts_anfrage = lambda a: (h.gesprochen.append(a.get("text", ""))
                                   or {"v": 1, "ok": True})

        class Cfg:
            state_dir = wurzel / "state"
        h.cfg = Cfg()

        # Das ECHTE Markenbuch. Die Marke entsteht wie im Betrieb: aus
        # quelle="auth", mit einer turn_id, die der Hub erzeugt.
        self.marken = marks_modul.MarkenBuch()
        self.turn_id = ""
        if mit_marke:
            self.turn_id = "t44" + "0" * 29
            self.marken.ausgeben(quelle="auth", turn_id=self.turn_id)
        h.marken = self.marken
        self.hub = h

    def frage(self, **felder) -> dict:
        grund = {"v": 1, "art": "ausfuehren", "action_id": "media.playpause",
                 "params": {}, "session_id": "s1", "tool_use_id": "tu1"}
        grund.update(felder)
        return self.hub.aktion_anfrage(grund)

    def audit_saetze(self) -> list[dict]:
        datei = (self.wurzel / "state" / "audit" / self.audit_modul.DATEI)
        if not datei.is_file():
            return []
        return [json.loads(z) for z in
                datei.read_text(encoding="utf-8").splitlines() if z.strip()]


def pruefe_naht(pruefling: Path, arbeit: Path, bericht: Bericht) -> None:
    """K2, K3 und K8 noch einmal -- an der Stelle, an der sie im Betrieb gelten.

    Ein Test je Stueck findet nie, dass niemand das Stueck aufruft. Deshalb
    laeuft hier `Hub.aktion_anfrage`, der Endpunkt hinter `aktion.sock`.
    """
    try:
        D = importlib.import_module("daimon.hub.daemon")
        audit_modul = importlib.import_module("daimon.hub.audit")
        marks_modul = importlib.import_module("daimon.hub.marks")
    except Exception as exc:  # noqa: BLE001
        for k in ("K2", "K3", "K8"):
            bericht.fehler(k, f"Naht nicht ladbar: {exc!r}")
        return

    # --- K2 an der Naht: manipulierter params_hash im Request --------------
    try:
        naht = Naht(D, audit_modul, marks_modul, arbeit / "naht-k2")
        params = {"value": 0.4}
        geflunkert = "sha256:" + "ab" * 32
        antwort = naht.frage(action_id="audio.volume.set", params=params,
                             params_hash=geflunkert, quelle="modell")
        bericht.pruefe("K2", antwort.get("ok") is True,
                       f"Positivkontrolle: der Aktionsendpunkt antwortet "
                       f"ueberhaupt: {antwort!r}")
        saetze = naht.audit_saetze()
        bericht.pruefe("K2", len(saetze) >= 1,
                       f"Positivkontrolle: die Anfrage hinterlaesst einen "
                       f"Auditsatz ({len(saetze)}) -- ohne ihn waere unten "
                       f"nichts zu lesen")
        if saetze:
            geschrieben = saetze[-1].get("params_hash")
            waegen(geflunkert, geschrieben,
                   "manipulierter params_hash an aktion.sock")
            bericht.pruefe("K2", geschrieben == eigener_hash(params),
                           f"der Hub schreibt seinen SELBST gerechneten Hash "
                           f"ins Audit, nicht den mitgeschickten: "
                           f"{geschrieben!r} vs {eigener_hash(params)!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K2", f"Naht (params_hash) fehlgeschlagen: {exc!r}")

    # --- K3 an der Naht: initiator im Request ohne Rundenmarke -------------
    try:
        ohne = Naht(D, audit_modul, marks_modul, arbeit / "naht-k3-ohne",
                    mit_marke=False)
        a = ohne.frage(initiator="foreground", quelle="modell")
        bericht.pruefe("K3", a.get("verdikt") == "deny",
                       f"ohne Rundenmarke im Buch hilft `initiator: "
                       f"foreground` im Request nicht: {a!r}")
        saetze = ohne.audit_saetze()
        geschrieben = saetze[-1].get("initiator") if saetze else None
        bericht.pruefe("K3", geschrieben not in (None, "foreground"),
                       f"und im Audit steht der abgeleitete initiator: "
                       f"{geschrieben!r}")

        # Positivkontrolle: MIT Marke im Buch kommt derselbe Weg weiter.
        mit = Naht(D, audit_modul, marks_modul, arbeit / "naht-k3-mit")
        b = mit.frage(initiator="background", quelle="modell")
        waegen(a.get("verdikt"), b.get("verdikt"),
               "dieselbe Anfrage mit gueltiger Rundenmarke im Buch")
        bericht.pruefe("K3", b.get("verdikt") == "ask",
                       f"Positivkontrolle: mit Marke im Buch entsteht ein "
                       f"`ask`, obwohl der Request 'background' behauptet: "
                       f"{b!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K3", f"Naht (initiator) fehlgeschlagen: {exc!r}")

    # --- K8 an der Naht: die Quelle ist Hub-Eigentum -----------------------
    try:
        naht = Naht(D, audit_modul, marks_modul, arbeit / "naht-k8")

        # Positivkontrolle ZUERST: der Weg lebt. Eine Anfrage OHNE Behauptung
        # bekommt ein echtes Verdikt (`ask`), kein pauschales `deny`.
        ehrlich = naht.frage(action_id="media.playpause", quelle="modell")
        bericht.pruefe("K8", ehrlich.get("verdikt") == "ask",
                       f"Positivkontrolle: eine Modellaktion erreicht die "
                       f"Policy und bekommt `ask`: {ehrlich!r}")
        bericht.pruefe("K8", ehrlich.get("direkt") is False,
                       f"Positivkontrolle: sie ist nicht direkt: {ehrlich!r}")

        # Und jetzt die Behauptung. `quelle == "parser"` ist die Bedingung,
        # die die Vorschau abschaltet (Design 263). Ein deterministischer
        # Hub-Parser, der sie setzen duerfte, existiert in diesem Baum nicht
        # -- also darf keine Anfrage vom Socket sie fuer sich reklamieren.
        behauptet = naht.frage(action_id="media.playpause", quelle="parser")
        print(f"Naht K8: ehrlich={ehrlich!r} behauptet={behauptet!r}")
        bericht.pruefe(
            "K8", behauptet.get("direkt") is not True,
            f"ein Absender kann sich `quelle: parser` NICHT selbst geben -- "
            f"die Direktbefehl-Ausnahme ist Hub-Eigentum (Design 263, "
            f"Akzeptanzpunkt 8): {behauptet!r}")
        bericht.pruefe(
            "K8", behauptet.get("verdikt") == ehrlich.get("verdikt"),
            f"das Verdikt haengt nicht am Feld `quelle` des Absenders: "
            f"behauptet={behauptet.get('verdikt')!r} vs "
            f"ehrlich={ehrlich.get('verdikt')!r}")

        # Der Waechter dazu: gibt es ueberhaupt einen deterministischen
        # Hub-Parser? Solange nicht, ist `quelle == "parser"` im Betrieb
        # unerreichbar -- und jede Anfrage, die sie doch erreicht, kommt vom
        # Absender.
        parser = parser_im_betrieb(pruefling)
        print(f"Waechter Hub-Parser: {parser!r}")
        bericht.pruefe(
            "K8", True,
            f"Waechterlage notiert: Produzenten von quelle='parser' im "
            f"Produktivbaum: {parser!r}")
    except Exception as exc:  # noqa: BLE001
        bericht.fehler("K8", f"Naht (Direktbefehl) fehlgeschlagen: {exc!r}")


def parser_im_betrieb(pruefling: Path) -> list[str]:
    """Wo im Produktivbaum wird `quelle="parser"` erzeugt (nicht verglichen)?"""
    treffer = []
    for datei in sorted((pruefling / "daimon").rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue
        for nummer, zeile in enumerate(
                datei.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if '"parser"' not in zeile and "'parser'" not in zeile:
                continue
            nackt = zeile.strip()
            if nackt.startswith("#") or "==" in nackt:
                continue
            treffer.append(f"{datei.relative_to(pruefling)}:{nummer}: {nackt[:80]}")
    return treffer


# -- Rahmen -----------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pruefling", type=Path)
    args = parser.parse_args(argv)
    pruefling = args.pruefling.resolve()
    bericht = Bericht()

    print(f"Pruefling: {pruefling}")
    print(f"Mutanten-Zuordnung: {json.dumps(MUTANTEN_GRENZEN, ensure_ascii=False)}")

    with tempfile.TemporaryDirectory(prefix="t44-") as tmp:
        arbeit = Path(tmp)

        pruefe_k1(pruefling, arbeit, bericht)

        try:
            modul = lade(pruefling, "daimon.hub.policy")
        except Exception as exc:  # noqa: BLE001
            for kriterium in KRITERIEN:
                bericht.fehler(kriterium, f"daimon.hub.policy nicht ladbar: {exc!r}")
            return bericht.bilanz()

        pruefe_k2(modul, bericht)
        pruefe_k3(modul, bericht)
        pruefe_k4(modul, pruefling, bericht)
        pruefe_k5(modul, bericht)
        pruefe_k6(modul, bericht)
        pruefe_k7(modul, pruefling, bericht)
        pruefe_k8(modul, bericht)
        pruefe_naht(pruefling, arbeit, bericht)

    return bericht.bilanz()


if __name__ == "__main__":
    raise SystemExit(main())
