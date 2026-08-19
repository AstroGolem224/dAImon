"""T-4.4 — der Entscheider.

Die Reihenfolge, und warum sie so herum steht
----------------------------------------------------------------------------
`deny -> ask -> allow`, erster Treffer, **Spezifitaet irrelevant** (Design
6.5). Eine Regel mit mehr Bedingungen gewinnt NICHT dadurch, dass sie enger
ist -- genau daran kippen Policy-Sprachen: wer Spezifitaet gewinnen laesst,
hebelt ein Verbot durch eine engere Erlaubnis aus. Hier werden alle Ebenen
ausgewertet und die staerkste Aussage genommen; `deny` ist damit eine
**Vereinigung** ueber alle Ebenen und von keiner Ebene zuruecknehmbar.

Was der Absender NICHT bestimmt
----------------------------------------------------------------------------
`initiator` und `params_hash` stehen in der Anfrage und werden ignoriert.
Der `initiator` wird aus der eingeloesten Rundenmarke abgeleitet, der
`params_hash` hier selbst gerechnet. Design 1357: "Ein Feld, das der Absender
setzt, sagt nichts." Beide Felder existieren trotzdem -- weil ein Test sie
faelschen koennen muss, und weil ein weggelassenes Feld nicht beweist, dass
es ignoriert wird.

Die Direktbefehl-Ausnahme gehoert dem Hub
----------------------------------------------------------------------------
`direct: true` im Katalog hilft nur zusammen mit `quelle == "parser"` -- also
dann, wenn der deterministische Hub-Parser die Aktion aus der Aeusserung
selbst erkannt hat. Kommt dieselbe Aktion aus einer Modellausgabe, wird aus
`allow` ein `ask` (Design 263). Waere es anders, koennte eine uebernommene
Modellausgabe alles ausloesen, was im Katalog als direkt markiert ist, und
die Vorschau waere eine Empfehlung statt einer Schranke.

Drei Fehlerzustaende, nicht einer
----------------------------------------------------------------------------
`unknown_action` (deny), `unparseable_argument` (ask) und
`argument_out_of_range` (deny) sind verschieden. "Kenne ich nicht" ist eine
Aussage ueber den Katalog, "verstehe ich nicht" eine ueber diese Anfrage, und
"ausserhalb der Schranke" eine ueber den Wert. Wer sie zusammenlegt, kann
spaeter nicht unterscheiden, ob das Modell etwas Verbotenes wollte oder nur
Unsinn geschickt hat.

Keine Uhr im Modul
----------------------------------------------------------------------------
`jetzt` wird hereingereicht. Ein Entscheider, der selbst auf die Uhr sieht,
ist nicht pruefbar -- Gestenfenster und TTL waeren dann Wartezeiten im Test.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Mitgeliefert im Checkout -- derselbe Weg wie bei den Personas
# (daimon/mind/persona.py): alle systemd-Units tragen absolute Pfade in dieses
# Repo.
_REPO = Path(__file__).resolve().parents[2]
MITGELIEFERTER_KATALOG = _REPO / "config" / "actions" / "core.yaml"
MITGELIEFERTE_REGELN = _REPO / "config" / "policy.yaml"

# Die vier Gueltigkeiten aus der Akzeptanzliste. `ttl` traegt zusaetzlich eine
# Zahl (`ttl:60`), steht hier aber als Art, nicht als Wert.
GUELTIGKEITEN = ("once", "session", "ttl", "persistent")

VERDIKTE = ("allow", "ask", "deny")
# Staerke, nicht Reihenfolge: verglichen wird, welche Aussage die schaerfere
# ist -- nicht, welche zuerst kam.
_STAERKE = {"allow": 0, "ask": 1, "deny": 2}

# Design 2.5: erteilt UND innerhalb des Fensters. Der Wert steht am Katalog,
# die Vorgabe hier -- ein Katalogeintrag ohne Zahl bekommt zwei Sekunden.
VORGABE_GESTENFENSTER_S = 2.0


class PolicyFehler(ValueError):
    """Regeln oder Katalog sind unbrauchbar. Nennt die Stelle."""


@dataclass(frozen=True)
class Anfrage:
    action_id: str
    params: dict
    session_id: str
    request_id: str
    # "parser" (Hub-Parser aus der Aeusserung), "modell" (Modellausgabe),
    # "scheduler" (Zeitsteuerung).
    quelle: str = "modell"
    # Die eingeloeste Rundenmarke: None oder eine Abbildung mit
    # `gueltig_bis` (monotone Sekunden). Eine Zeichenkette waere ein Name
    # ohne Frist -- und eine Marke ohne Frist ist keine.
    marke: dict | None = None
    # Absichtlich vorhanden und absichtlich ignoriert. Siehe Modulkopf.
    initiator: str | None = None
    params_hash: str | None = None
    jetzt: float = 0.0


@dataclass(frozen=True)
class Entscheidung:
    verdikt: str
    grund: str
    initiator: str
    params_hash: str
    # Welche Regel gewonnen hat -- oder `katalog:<initiator>`, wenn keine
    # Regel griff. Nie None: im Audit stuende sonst "verweigert" und sonst
    # nichts, und niemand koennte nachvollziehen, welche Zeile das war.
    regel: Any = None


def kanonisieren(params: dict | None) -> str:
    """Die eine Schreibweise, aus der der Hash entsteht.

    Sortierte Schluessel, keine Leerzeichen, Ganzzahlwerte in Gleitkomma
    NICHT umgeschrieben -- aber `0.50` und `0.5` sind dieselbe Zahl und
    ergeben denselben Text. Ohne diese Zusage haette dieselbe Zustimmung je
    nach Serialisierung einen anderen Schluessel im Cache und waere wertlos.
    """
    return json.dumps(params or {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def params_hash(params: dict | None) -> str:
    roh = kanonisieren(params).encode("utf-8")
    return "sha256:" + hashlib.sha256(roh).hexdigest()


def _initiator(anfrage: Anfrage) -> str:
    """Aus der MARKE, nicht aus dem Request.

    Eine Zeitsteuerung ist `scheduled`, auch wenn sie eine Marke mitschickte:
    sonst waere der Unterschied zwischen "ein Mensch hat gerade gedrueckt" und
    "ein Zeitplan feuert" eine Frage der Reihenfolge im Code.
    """
    if anfrage.quelle == "scheduler":
        return "scheduled"
    marke = anfrage.marke
    if not isinstance(marke, dict):
        return "background"
    frist = marke.get("gueltig_bis", math.inf)
    try:
        gueltig = float(frist) > float(anfrage.jetzt)
    except (TypeError, ValueError):
        gueltig = False
    return "foreground" if gueltig else "background"


def _params_pruefen(eintrag: dict, params: dict) -> tuple[str, str] | None:
    """`None` heisst in Ordnung, sonst `(verdikt, grund)`."""
    schema = eintrag.get("params") or {}
    for name, regel in schema.items():
        if name not in params:
            if regel.get("required"):
                return "ask", "missing_argument"
            continue
        wert = params[name]
        art = regel.get("type")
        if art in ("float", "int"):
            # `bool` ist in Python ein `int` und hier keiner: `True` als
            # Lautstaerke waere 1.0 und damit eine stille Umdeutung.
            if isinstance(wert, bool) or not isinstance(wert, (int, float)):
                return "ask", "unparseable_argument"
            schranke = regel.get("value_between")
            if schranke and not (float(schranke[0]) <= float(wert) <= float(schranke[1])):
                return "deny", "argument_out_of_range"
        elif art == "string":
            if not isinstance(wert, str):
                return "ask", "unparseable_argument"
            muster = regel.get("pattern")
            if muster:
                import re
                if not re.match(muster, wert):
                    return "deny", "argument_out_of_range"
    return None


@dataclass
class Policy:
    katalog: dict
    ebenen: list[list[dict]]
    _zustimmung: dict = field(default_factory=dict)
    _sicherung: str | None = None
    _geste_bis: float = -math.inf

    GUELTIGKEITEN = GUELTIGKEITEN

    @classmethod
    def aus_dateien(cls, katalog: dict, ebenen: list[list[dict]]) -> "Policy":
        if not isinstance(katalog, dict):
            raise PolicyFehler("Der Katalog muss eine Abbildung id -> Eintrag sein")
        for i, ebene in enumerate(ebenen):
            for regel in ebene:
                v = regel.get("verdikt")
                if v not in VERDIKTE:
                    raise PolicyFehler(
                        f"Ebene {i}: Verdikt {v!r} ist keines von "
                        f"{', '.join(VERDIKTE)}")
        return cls(katalog=katalog, ebenen=[list(e) for e in ebenen])

    # -- Zustaende, die von aussen gesetzt werden ---------------------------

    def sicherung_werfen(self, grund: str) -> None:
        """Circuit Breaker. Schlaegt jede Regel, jede Zustimmung, jeden Modus."""
        self._sicherung = grund or "circuit_breaker"

    def sicherung_loesen(self) -> None:
        self._sicherung = None

    def geste_gesehen(self, jetzt: float, fenster_s: float = VORGABE_GESTENFENSTER_S) -> None:
        self._geste_bis = float(jetzt) + float(fenster_s)

    def zustimmung_merken(self, *, session_id: str, action_id: str,
                          params_hash: str, gueltigkeit: str,
                          jetzt: float = 0.0) -> None:
        art, _, wert = gueltigkeit.partition(":")
        if art not in GUELTIGKEITEN:
            raise PolicyFehler(
                f"Gueltigkeit {gueltigkeit!r} ist keine von "
                f"{', '.join(GUELTIGKEITEN)}")
        bis = math.inf
        if art == "ttl":
            try:
                bis = float(jetzt) + float(wert)
            except ValueError as exc:
                raise PolicyFehler(f"ttl ohne Zahl: {gueltigkeit!r}") from exc
        self._zustimmung[(session_id, action_id, params_hash)] = {
            "art": art, "bis": bis, "verbraucht": False}

    # -- Der Entscheider ----------------------------------------------------

    def entscheide(self, anfrage: Anfrage) -> Entscheidung:
        hash_ = params_hash(anfrage.params)
        initiator = _initiator(anfrage)

        def ergebnis(verdikt: str, grund: str, regel: Any) -> Entscheidung:
            return Entscheidung(verdikt=verdikt, grund=grund,
                                initiator=initiator, params_hash=hash_,
                                regel=regel)

        # 1. Die Sicherung zuerst. Was danach kommt, kann sie nicht aufheben.
        if self._sicherung:
            return ergebnis("deny", "circuit_breaker",
                            {"sicherung": self._sicherung})

        eintrag = self.katalog.get(anfrage.action_id)
        if eintrag is None:
            return ergebnis("deny", "unknown_action", "katalog:unbekannt")

        schlecht = _params_pruefen(eintrag, anfrage.params or {})
        if schlecht:
            return ergebnis(schlecht[0], schlecht[1], "katalog:params")

        # 2. Gestenfenster: erteilt UND offen. Ein geschlossenes Fenster ist
        #    ein `deny`, kein `ask` -- sonst waere die Frage selbst die
        #    Verlaengerung des Fensters.
        fenster = eintrag.get("gestenfenster_s")
        if fenster is not None and float(anfrage.jetzt) > self._geste_bis:
            return ergebnis("deny", "gesture_window_closed", "katalog:geste")

        # 3. Die Vorgabe des Katalogs fuer diesen Initiator.
        verdikt = eintrag.get(initiator, "deny")
        if verdikt not in VERDIKTE:
            raise PolicyFehler(
                f"{anfrage.action_id}: Vorgabe fuer {initiator} ist "
                f"{verdikt!r}")
        grund = f"katalog:{initiator}"
        regel: Any = f"katalog:{initiator}"

        # 4. Alle Ebenen, staerkste Aussage gewinnt. KEINE Spezifitaet.
        for nummer, ebene in enumerate(self.ebenen):
            for eintrag_regel in ebene:
                if not self._passt(eintrag_regel, anfrage, initiator):
                    continue
                v = eintrag_regel["verdikt"]
                if _STAERKE[v] > _STAERKE[verdikt]:
                    verdikt, regel = v, {"ebene": nummer, **eintrag_regel}
                    grund = eintrag_regel.get("grund") or f"regel:{v}"

        # 5. Die Direktbefehl-Ausnahme. Sie kann nur MILDERN, und nur fuer den
        #    Parser -- eine Modellausgabe hebt `allow` auf `ask` an.
        direkt = bool(eintrag.get("direct")) and anfrage.quelle == "parser"
        if verdikt == "allow" and not direkt:
            verdikt, grund = "ask", "vorschau_pflicht"
            regel = "hub:keine_direktausnahme"

        # 6. Eine erteilte Zustimmung hebt `ask` auf `allow`. Ein `deny`
        #    hebt sie NICHT auf: verboten bleibt verboten, auch mit Zettel.
        if verdikt == "ask":
            if self._zustimmung_gilt(anfrage, hash_):
                verdikt, grund = "allow", "zustimmung"
                regel = "cache:zustimmung"

        return ergebnis(verdikt, grund, regel)

    # -- Innereien ----------------------------------------------------------

    def _passt(self, regel: dict, anfrage: Anfrage, initiator: str) -> bool:
        if regel.get("action_id") not in (None, anfrage.action_id):
            return False
        # Strukturierte Praedikate, keine Zeichenketten-Globs: ein Glob ist
        # eine Sprache, und eine Sprache hat Sonderfaelle.
        felder = {"initiator": initiator, "quelle": anfrage.quelle,
                  "session_id": anfrage.session_id,
                  "action_id": anfrage.action_id}
        for schluessel, erwartet in (regel.get("when") or {}).items():
            if schluessel not in felder:
                raise PolicyFehler(
                    f"`when` kennt {schluessel!r} nicht; erlaubt sind "
                    + ", ".join(sorted(felder)))
            if felder[schluessel] != erwartet:
                return False
        return True

    # -- Laden ---------------------------------------------------------------

    @classmethod
    def laden(cls, *, katalog_pfad: Any = None, regel_pfad: Any = None) -> "Policy":
        """Katalog und Regeln von der Platte.

        **Nur `status: approved` kommt in den Katalog.** Ein Kandidat ist ein
        Vorschlag; waere er hier drin, entschiede die Policy ueber etwas, das
        niemand freigegeben hat -- und die Handpruefung aus T-4.2 waere eine
        Formalitaet.
        """
        import yaml  # PyYAML: das Katalogformat ist im Plan als YAML gesetzt

        katalog_pfad = Path(katalog_pfad or MITGELIEFERTER_KATALOG)
        regel_pfad = Path(regel_pfad or MITGELIEFERTE_REGELN)

        with open(katalog_pfad, encoding="utf-8") as fh:
            roh = yaml.safe_load(fh) or {}
        katalog: dict = {}
        for eintrag in roh.get("actions") or []:
            if eintrag.get("status") != "approved":
                continue
            kennung = eintrag.get("id")
            if not kennung:
                raise PolicyFehler(f"{katalog_pfad}: Eintrag ohne `id`")
            if not (eintrag.get("rationale") or "").strip():
                # Dieselbe Zusage wie in T-4.2, hier noch einmal beim Laden:
                # ein freigegebener Eintrag ohne Begruendung ist eine
                # Entscheidung, die niemand aufgeschrieben hat.
                raise PolicyFehler(
                    f"{katalog_pfad}: {kennung} ist approved, hat aber keine "
                    f"`rationale`")
            katalog[kennung] = eintrag

        with open(regel_pfad, encoding="utf-8") as fh:
            regeln_roh = yaml.safe_load(fh) or {}
        ebenen = [list(e.get("regeln") or [])
                  for e in (regeln_roh.get("ebenen") or [])]
        return cls.aus_dateien(katalog, ebenen)

    def _zustimmung_gilt(self, anfrage: Anfrage, hash_: str) -> bool:
        schluessel = (anfrage.session_id, anfrage.action_id, hash_)
        eintrag = self._zustimmung.get(schluessel)
        if eintrag is None or eintrag["verbraucht"]:
            return False
        if float(anfrage.jetzt) > eintrag["bis"]:
            return False
        if eintrag["art"] == "once":
            eintrag["verbraucht"] = True
        return True
