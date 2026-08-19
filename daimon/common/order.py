"""T-4.5 — das Auftragsformat: Ziel, Frist, Einmaligkeit. **Ohne Signatur.**

Warum hier kein HMAC steht
----------------------------------------------------------------------------
Design 1.3 und 6.2 haben ihn gestrichen, und das ist keine Sparmassnahme.
Ein Broker koennte einen Auftrag nur mit einem Schluessel pruefen, den auch
der Hub hat -- also mit einem Schluessel, der auf derselben Maschine unter
derselben uid liegt. Fuer den Angreifer, den das Bedrohungsmodell
ausdruecklich NICHT abwehrt (Codeausfuehrung unter derselben uid), ist dieser
Schluessel per `ptrace` lesbar. Eine Signatur haette also genau die Angreifer
abgewehrt, die es hier nicht gibt, und dafuer den Anschein erweckt, der
Auftrag sei fremdsicher.

Was stattdessen traegt -- und was NICHT
----------------------------------------------------------------------------
Bis zum 19.08. stand hier, die **Herkunft ueber den Socket** trete an die
Stelle der Signatur: wer am Broker-Socket haenge, werde geprueft, ein Auftrag
komme also vom Hub oder gar nicht an. Zwei Dinge daran waren falsch.

Erstens fand die Pruefung nicht statt (BEFUND T-4.5 K6): der Mantel las den
Rumpf direkt nach `accept()`. Das ist repariert, `dienst.annehmen` fragt
jetzt.

Zweitens -- und das bleibt -- ist sie kein Ersatz fuer eine Signatur. Sie ist
ein WEGWEISER, keine Authentifizierung; DESIGN.md fuehrt "`SO_PEERCRED`
verhindert Faelschung" ausdruecklich in der Tabelle der Irrtuemer. Gegen den
Angreifer, den das Bedrohungsmodell nicht abwehrt, hilft sie so wenig wie der
gestrichene HMAC. Der Auftrag hat damit KEINEN Herkunftsnachweis, der einem
same-uid-Angreifer standhaelt, und das ist eine bewusste Entscheidung und
kein Versehen. Ein Modulkopf, der etwas anderes verspricht, erweckt genau den
Anschein, vor dem der Absatz darueber warnt.

Die **Einmaligkeit** haengt am Ticket, das beim Hub eingeloest wird -- nicht
an einem Feld, das der Auftrag ueber sich selbst behauptet.

Die vier Schranken, die dieses Format traegt
----------------------------------------------------------------------------
1. `audience` bindet an genau EINEN Broker. Ein DBus-Auftrag ist bei
   `daimon-fs` nicht einreichbar -- auch dann nicht, wenn er sonst gueltig
   waere.
2. `schema` verhindert abweichende Lesarten. Ein unbekanntes Schema ist ein
   Fehler, kein "dann eben die alte Lesart".
3. `deadline_monotonic` ist MONOTON. Eine Zeitumstellung verlaengert nichts,
   und ein Auftrag, der ueber Nacht liegen bleibt, wird nicht dadurch wieder
   gueltig, dass die Uhr zurueckspringt.
4. Die Serialisierung ist festgelegt. Wer dieselben Felder anders schreibt,
   bekommt eine Abweisung -- sonst waere `params_hash` an eine von mehreren
   Schreibweisen gebunden und damit an keine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

SCHEMA = "daimon.order.v1"

# Genau die Broker aus Design 6.4. Ein fuenfter Wert ist ein Fehler und kein
# neuer Broker: eine Zielgruppe, die es nicht gibt, waere ein Auftrag, den
# niemand annimmt -- und der irgendwo liegen bleibt.
AUDIENCES = ("dbus", "fs", "exec", "input")

FELDER = ("audience", "schema", "action_id", "params", "params_hash",
          "ticket", "deadline_monotonic", "turn_id")


class AuftragsFehler(ValueError):
    """Der Auftrag ist unbrauchbar. Nennt das Feld."""


@dataclass(frozen=True)
class Auftrag:
    audience: str
    action_id: str
    params: dict
    params_hash: str
    ticket: str
    deadline_monotonic: float
    turn_id: str
    schema: str = SCHEMA

    def als_dict(self) -> dict:
        return {f: asdict(self)[f] for f in FELDER}


def kanonisch(auftrag: Auftrag | dict) -> bytes:
    """Die EINE Schreibweise.

    Sortierte Schluessel, keine Leerzeichen, UTF-8. Zwei Auftraege mit
    denselben Werten ergeben dieselben Bytes -- sonst haenge `params_hash` an
    der Laune des Serialisierers.
    """
    daten = auftrag.als_dict() if isinstance(auftrag, Auftrag) else dict(auftrag)
    fehlend = [f for f in FELDER if f not in daten]
    if fehlend:
        raise AuftragsFehler(f"Felder fehlen: {', '.join(fehlend)}")
    fremd = [f for f in daten if f not in FELDER]
    if fremd:
        # Ein zusaetzliches Feld ist keine Erweiterung, sondern eine zweite
        # Lesart. Genau davor schuetzt `schema`, und hier steht die Grenze.
        raise AuftragsFehler(f"unbekannte Felder: {', '.join(sorted(fremd))}")
    return json.dumps({f: daten[f] for f in FELDER}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def params_hash(params: dict | None) -> str:
    """Dieselbe Rechnung wie in der Policy -- absichtlich dieselbe.

    Zwei Kanonisierungen waeren zwei Wahrheiten, und der Auftrag traegt genau
    den Hash, ueber den entschieden wurde.
    """
    from daimon.hub.policy import params_hash as policy_hash
    return policy_hash(params)


def pruefe(roh: bytes | dict, *, audience: str, jetzt: float,
           ticket_gueltig: Any = None) -> Auftrag:
    """Ein Auftrag, oder ein `AuftragsFehler` mit Grund.

    `roh` sind die Bytes von der Leitung (dann wird die Serialisierung
    mitgeprueft) oder eine bereits gelesene Abbildung (dann nicht -- der
    Aufrufer hat sie dann selbst gebaut).

    `ticket_gueltig(ticket) -> bool` gehoert dem HUB. Der Broker fragt, er
    entscheidet nicht: sonst waere Einmaligkeit eine Zusage, die jeder Broker
    fuer sich neu erfindet.
    """
    if isinstance(roh, (bytes, bytearray)):
        try:
            daten = json.loads(roh.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AuftragsFehler(f"kein lesbares JSON: {exc}") from exc
        if not isinstance(daten, dict):
            raise AuftragsFehler("erwartet wird ein Objekt")
        if kanonisch(daten) != bytes(roh):
            # Dieselben Werte, andere Schreibweise. Abgewiesen, weil sonst
            # `params_hash` an eine von mehreren Schreibweisen gebunden waere.
            raise AuftragsFehler(
                "abweichende Serialisierung: der Auftrag ist nicht kanonisch")
    else:
        daten = dict(roh)
        kanonisch(daten)  # prueft Vollstaendigkeit und Fremdfelder

    if daten["schema"] != SCHEMA:
        raise AuftragsFehler(
            f"unbekanntes Schema {daten['schema']!r}; erwartet {SCHEMA!r}")
    if daten["audience"] not in AUDIENCES:
        raise AuftragsFehler(f"unbekannte audience {daten['audience']!r}")
    if daten["audience"] != audience:
        raise AuftragsFehler(
            f"Auftrag ist fuer {daten['audience']!r}, hier ist {audience!r}")

    erwartet = params_hash(daten["params"])
    if daten["params_hash"] != erwartet:
        raise AuftragsFehler("params_hash passt nicht zu den Parametern")

    try:
        frist = float(daten["deadline_monotonic"])
    except (TypeError, ValueError) as exc:
        raise AuftragsFehler("deadline_monotonic ist keine Zahl") from exc
    if frist <= float(jetzt):
        raise AuftragsFehler("Frist abgelaufen")

    if not str(daten["ticket"]).strip():
        raise AuftragsFehler("ticket fehlt")
    if ticket_gueltig is not None and not ticket_gueltig(daten["ticket"]):
        raise AuftragsFehler("Ticket ist eingeloest oder unbekannt")

    return Auftrag(**{f: daten[f] for f in FELDER})


def fingerabdruck(auftrag: Auftrag) -> str:
    """Fuer das Audit: ein kurzer Bezug auf genau diesen Auftrag."""
    return "sha256:" + hashlib.sha256(kanonisch(auftrag)).hexdigest()
