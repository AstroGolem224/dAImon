"""Gut-Muster fuer T-0.8. Bewusst schlicht und unabhaengig von der echten
Implementierung geschrieben -- ein Gut-Muster, das aus dem Produktivcode
kopiert waere, wuerde nur beweisen, dass der Code der Code ist.

Vier getrennte Zustandsautomaten. Getrennt heisst hier: getrennte Speicher,
getrennte Schluesselraeume, keine gemeinsame Tabelle, in der Typen
verwechselbar waeren.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


class MarkenFehler(Exception):
    """Jede Ablehnung. Der Aufrufer soll nicht unterscheiden muessen, ob eine
    Marke abgelaufen, verbraucht oder nie da war -- das waere ein
    Auskunftskanal ohne Nutzen."""


class _Stumm:
    def _nichts(self, *args, **kwargs):
        return None

    info = warning = error = debug = _nichts


@dataclass(frozen=True)
class Rundenmarke:
    turn_id: str
    ablauf_ts: float


@dataclass(frozen=True)
class Aktionsfreigabe:
    action_hash: str
    ablauf_ts: float


class MarkenBuch:
    def __init__(self, *, frist_s: float = 120.0, jetzt=time.monotonic,
                 log=None) -> None:
        self._frist_s = float(frist_s)
        self._jetzt = jetzt
        self._log = log or _Stumm()
        self._sperre = threading.Lock()
        self._offen: dict[str, Rundenmarke] = {}
        self._verbraucht: set[str] = set()

    def ausgeben(self, *, quelle: str, turn_id: str) -> Rundenmarke:
        if quelle != "auth":
            self._log.warning("Rundenmarke abgelehnt", DAIMON_MARKE="runde",
                              DAIMON_AKTION="abgelehnt", DAIMON_QUELLE=quelle)
            raise MarkenFehler("Rundenmarke entsteht nur aus intent_mark des Auth-Agenten")
        with self._sperre:
            # Eine turn_id, die schon verbraucht ist, bekommt keine zweite
            # Marke. Sonst steuerte ein Feld aus dem Aufruf, ob eine
            # abgeschlossene Runde wieder gilt -- und "einmal einloesbar"
            # haenge an der Annahme, dass ein anderer Prozess turn_ids nie
            # wiederholt.
            if False:  # MUTANT: verbrauchte turn_id wird wieder ausgegeben
                self._log.warning("Rundenmarke abgelehnt", DAIMON_MARKE="runde",
                                  DAIMON_AKTION="abgelehnt", DAIMON_TURN=turn_id)
                raise MarkenFehler("turn_id bereits verbraucht")
        marke = Rundenmarke(turn_id=turn_id,
                            ablauf_ts=self._jetzt() + self._frist_s)
        with self._sperre:
            self._offen[turn_id] = marke
        self._log.info("Rundenmarke ausgegeben", DAIMON_MARKE="runde",
                       DAIMON_AKTION="ausgegeben", DAIMON_TURN=turn_id)
        return marke

    def einloesen(self, turn_id: str) -> Rundenmarke:
        with self._sperre:
            marke = self._offen.pop(turn_id, None)
        if marke is None:
            self._log.warning("Rundenmarke abgelehnt", DAIMON_MARKE="runde",
                              DAIMON_AKTION="abgelehnt", DAIMON_TURN=turn_id)
            raise MarkenFehler("keine offene Rundenmarke fuer diese turn_id")
        if self._jetzt() >= marke.ablauf_ts:
            self._log.warning("Rundenmarke abgelaufen", DAIMON_MARKE="runde",
                              DAIMON_AKTION="abgelehnt", DAIMON_TURN=turn_id)
            raise MarkenFehler("Rundenmarke abgelaufen")
        with self._sperre:
            self._verbraucht.add(turn_id)
        self._log.info("Rundenmarke eingeloest", DAIMON_MARKE="runde",
                       DAIMON_AKTION="eingeloest", DAIMON_TURN=turn_id)
        return marke

    def initiator(self, turn_id: str) -> str:
        with self._sperre:
            marke = self._offen.get(turn_id)
        if marke is None or self._jetzt() >= marke.ablauf_ts:
            return "background"
        return "user"


class FreigabeBuch:
    def __init__(self, *, frist_s: float = 60.0, jetzt=time.monotonic,
                 log=None) -> None:
        self._frist_s = float(frist_s)
        self._jetzt = jetzt
        self._log = log or _Stumm()
        self._sperre = threading.Lock()
        self._nonces: dict[str, str] = {}
        self._freigaben: dict[str, Aktionsfreigabe] = {}

    def nonce_ausgeben(self, *, action_hash: str) -> str:
        nonce = secrets.token_hex(16)
        with self._sperre:
            self._nonces[nonce] = action_hash
        self._log.info("Nonce ausgegeben", DAIMON_MARKE="freigabe",
                       DAIMON_AKTION="ausgegeben")
        return nonce

    def bestaetigen(self, *, nonce: str, action_hash: str) -> Aktionsfreigabe:
        with self._sperre:
            # Die Nonce wird IMMER entnommen, auch wenn der Hash nicht passt.
            # Sonst waere die Bindung an den Hash durch Wiederholen zu umgehen.
            erwartet = self._nonces.pop(nonce, None)
        if erwartet is None or erwartet != action_hash:
            self._log.warning("Freigabe abgelehnt", DAIMON_MARKE="freigabe",
                              DAIMON_AKTION="abgelehnt")
            raise MarkenFehler("Nonce unbekannt oder gehoert zu einer anderen Aktion")
        freigabe = Aktionsfreigabe(action_hash=action_hash,
                                   ablauf_ts=self._jetzt() + self._frist_s)
        with self._sperre:
            self._freigaben[action_hash] = freigabe
        self._log.info("Freigabe erteilt", DAIMON_MARKE="freigabe",
                       DAIMON_AKTION="bestaetigt")
        return freigabe

    def einloesen(self, *, action_hash: str) -> Aktionsfreigabe:
        with self._sperre:
            freigabe = self._freigaben.pop(action_hash, None)
        if freigabe is None:
            self._log.warning("Freigabe abgelehnt", DAIMON_MARKE="freigabe",
                              DAIMON_AKTION="abgelehnt")
            raise MarkenFehler("keine Freigabe fuer diese Aktion")
        if self._jetzt() >= freigabe.ablauf_ts:
            self._log.warning("Freigabe abgelaufen", DAIMON_MARKE="freigabe",
                              DAIMON_AKTION="abgelehnt")
            raise MarkenFehler("Freigabe abgelaufen")
        self._log.info("Freigabe eingeloest", DAIMON_MARKE="freigabe",
                       DAIMON_AKTION="eingeloest")
        return freigabe


_QUELLEN = frozenset({"wake_word", "rundenmarke"})


class KontingentBuch:
    def __init__(self, *, frist_s: float = 60.0, jetzt=time.monotonic,
                 log=None) -> None:
        self._frist_s = float(frist_s)
        self._jetzt = jetzt
        self._log = log or _Stumm()
        self._sperre = threading.Lock()
        self._offen: dict[str, float] = {}

    def ausgeben(self, *, quelle: str) -> str:
        if quelle not in _QUELLEN:
            self._log.warning("Kontingent abgelehnt", DAIMON_MARKE="kontingent",
                              DAIMON_AKTION="abgelehnt", DAIMON_QUELLE=quelle)
            raise MarkenFehler("Kontingent entsteht nur aus Wake-Word oder Rundenmarke")
        kid = secrets.token_hex(16)
        with self._sperre:
            self._offen[kid] = self._jetzt() + self._frist_s
        self._log.info("Kontingent ausgegeben", DAIMON_MARKE="kontingent",
                       DAIMON_AKTION="ausgegeben")
        return kid

    def einloesen_fuer_egress(self, kontingent_id: str) -> None:
        with self._sperre:
            ablauf = self._offen.pop(kontingent_id, None)
        if ablauf is None:
            self._log.warning("Kontingent abgelehnt", DAIMON_MARKE="kontingent",
                              DAIMON_AKTION="abgelehnt")
            raise MarkenFehler("kein offenes Kontingent")
        if self._jetzt() >= ablauf:
            self._log.warning("Kontingent abgelaufen", DAIMON_MARKE="kontingent",
                              DAIMON_AKTION="abgelehnt")
            raise MarkenFehler("Kontingent abgelaufen")
        self._log.info("Kontingent eingeloest", DAIMON_MARKE="kontingent",
                       DAIMON_AKTION="eingeloest")

    # Diese beiden sind absichtlich konstant. Design 2.4: sonst genuegte ein
    # Video, das den Namen sagt und "was steht auf meinem Bildschirm?" fragt,
    # um den Bildschirm in die Cloud zu schicken. Eine Zusage, die man
    # aufrufen und testen kann, ist mehr wert als ein Kommentar.
    def erlaubt_aktion(self, kontingent_id: str) -> bool:
        return False

    def erlaubt_deklassifizierung(self, kontingent_id: str) -> bool:
        return False
