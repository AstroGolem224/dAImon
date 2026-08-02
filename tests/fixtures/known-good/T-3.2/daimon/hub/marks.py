"""T-0.8 — Rundenmarke, Aktionsfreigabe, API-Kontingent.

Drei getrennte Zustandsautomaten (der vierte, das Broker-Ticket, liegt in
`tickets.py`). Getrennt ist hier keine Kosmetik: die drei erlauben
verschiedene Dinge, und ein gemeinsamer Speicher, in dem die Typen
verwechselbar waeren, waere genau der Fehler, den das Bedrohungsmodell
(Design 1.3) ausschliesst.

Gemeinsame Regeln fuer alle drei:

  * **Kein Zustand aus dem Request.** Kein Ablaufzeitpunkt, keine Frist und
    kein Hash kommt aus einem Feld, das ein Aufrufer mitschickt. Fristen
    kommen aus dem Konstruktor (Konfiguration), Zeitpunkte aus der
    injizierten Zeitquelle, IDs erzeugt das Buch selbst. Eine oeffentliche
    Methode mit einem `ablauf_ts`-Parameter waere schon der Fehler.
  * **Einmal.** Einloesen verbraucht. Ein zweiter Versuch ist eine
    Ablehnung, auch ein gleichzeitiger aus einem anderen Thread -- der Hub
    ist multithreaded (siehe `state.py`), deshalb haelt jedes Buch ein Lock.
  * **Audit.** Ausgabe, Einloesung und jede Ablehnung erzeugen eine Zeile
    ueber den injizierten Logger. Ein injizierter Logger sieht alles.

Zur Sprachregelung (Design 1.3): Eine Marke belegt, dass der Auth-Agent eine
Nutzerhandlung gemeldet hat -- nicht mehr. Deshalb heisst es hier Marke,
Freigabe, Kontingent und Ticket, und sonst nichts.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


class MarkenFehler(Exception):
    """Oberklasse fuer alle Ablehnungen der vier Buecher."""


def neue_request_id() -> str:
    """Opake Kennung, die der Hub dem Mind mitgibt.

    Sie autorisiert **nichts**: sie ist weder Marke noch Freigabe noch
    Kontingent, und kein Buch kennt sie. Sie dient nur der Korrelation im
    Audit -- welche Antwort gehoert zu welcher Anfrage. Wer sie vorweist,
    bekommt dafuer gar nichts.
    """
    return secrets.token_hex(16)


def _pruefe_kein_request_feld(wert: Any, name: str) -> str:
    """IDs und Hashes kommen als Zeichenkette oder gar nicht."""
    if not isinstance(wert, str) or not wert:
        raise MarkenFehler(f"{name}: nichtleere Zeichenkette erwartet")
    return wert


class _Buch:
    """Gemeinsames Geruest: Lock, Zeitquelle, Audit. KEIN gemeinsamer
    Speicher -- jede Subklasse haelt ihre eigenen Mappen, und die Typen
    bleiben untereinander unverwechselbar."""

    TYP = "buch"

    def __init__(self, *, frist_s: float, jetzt: Callable[[], float],
                 log: Any) -> None:
        if frist_s <= 0:
            raise ValueError("frist_s muss positiv sein")
        self._frist = frist_s
        self._jetzt = jetzt
        self._log = log
        self._lock = threading.Lock()

    def _audit(self, handlung: str, **felder: Any) -> None:
        if self._log is None:
            return
        self._log.info(
            f"{self.TYP}: {handlung}",
            DAIMON_TYP=self.TYP,
            DAIMON_HANDLUNG=handlung,
            **felder,
        )

    def _ablehnung(self, grund: str, **felder: Any) -> MarkenFehler:
        self._audit("ablehnung", DAIMON_GRUND=grund, **felder)
        return MarkenFehler(f"{self.TYP}: {grund}")


# ---------------------------------------------------------------------------
# Rundenmarke
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rundenmarke:
    turn_id: str
    ablauf_ts: float


class MarkenBuch(_Buch):
    """Rundenmarken. Zeitquelle injizierbar, damit Ablauf ohne sleep pruefbar
    ist -- ein Test, der eine Stunde wartet, wird nie gefahren.

    Eine Rundenmarke entsteht ausschliesslich aus einer `intent_mark`-Meldung
    des Auth-Agenten (Design 2.4). Sie ist an `turn_id` und eine Frist
    gebunden und genau einmal einloesbar.
    """

    TYP = "rundenmarke"
    QUELLE_AUTH = "auth"

    def __init__(self, *, frist_s: float = 120.0,
                 jetzt: Callable[[], float] = time.monotonic,
                 log: Any = None) -> None:
        super().__init__(frist_s=frist_s, jetzt=jetzt, log=log)
        self._marken: dict[str, Rundenmarke] = {}
        self._verbraucht: set[str] = set()

    def ausgeben(self, *, quelle: str, turn_id: str) -> Rundenmarke:
        """`quelle` MUSS "auth" sein. Alles andere -> MarkenFehler.

        Das ist die Kernregel: eine Rundenmarke entsteht nur aus einer
        Meldung des Auth-Agenten. `turn_id` benennt die Runde; der
        Ablaufzeitpunkt wird hier aus der eigenen Zeitquelle und Frist
        berechnet, nie aus dem Aufruf uebernommen.

        Eine bereits verbrauchte `turn_id` wird abgelehnt. Sie kommt aus
        dem Aufruf, und ein Request-Feld darf nicht darueber entscheiden,
        ob eine abgeschlossene Runde wieder gilt -- sonst belebt eine
        wiederholte oder wiedereingespielte `intent_mark` die verbrauchte
        Marke wieder.
        """
        with self._lock:
            if quelle != self.QUELLE_AUTH:
                raise self._ablehnung(
                    "ausgabe nur aus quelle 'auth'",
                    DAIMON_QUELLE=str(quelle)[:64],
                )
            turn_id = _pruefe_kein_request_feld(turn_id, "turn_id")
            if turn_id in self._verbraucht:
                raise self._ablehnung(
                    "turn_id bereits verbraucht",
                    DAIMON_TURN_ID=turn_id,
                )
            marke = Rundenmarke(turn_id=turn_id,
                                ablauf_ts=self._jetzt() + self._frist)
            self._marken[turn_id] = marke
            self._audit("ausgabe", DAIMON_TURN_ID=turn_id)
            return marke

    def einloesen(self, turn_id: str) -> Rundenmarke:
        """Einmal. Zweiter Aufruf -> MarkenFehler. Abgelaufen -> MarkenFehler.
        """
        with self._lock:
            marke = self._marken.get(turn_id)
            if marke is None:
                raise self._ablehnung("unbekannte turn_id",
                                      DAIMON_TURN_ID=str(turn_id)[:64])
            if turn_id in self._verbraucht:
                raise self._ablehnung("bereits eingeloest",
                                      DAIMON_TURN_ID=turn_id)
            if self._jetzt() >= marke.ablauf_ts:
                raise self._ablehnung("abgelaufen", DAIMON_TURN_ID=turn_id)
            self._verbraucht.add(turn_id)
            self._audit("einloesung", DAIMON_TURN_ID=turn_id)
            return marke

    def initiator(self, turn_id: str) -> str:
        """"user" bei gueltiger, nicht eingeloester Marke, sonst "background".

        Wirft nicht -- das ist eine Auskunft, keine Einloesung. Eine fehlende
        oder abgelaufene Marke ergibt "background", und eine Auskunft
        veraendert keinen Zustand.
        """
        with self._lock:
            marke = self._marken.get(turn_id)
            if (marke is not None
                    and turn_id not in self._verbraucht
                    and self._jetzt() < marke.ablauf_ts):
                return "user"
            return "background"


# ---------------------------------------------------------------------------
# Aktionsfreigabe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Aktionsfreigabe:
    action_hash: str
    ablauf_ts: float


@dataclass(frozen=True)
class _Nonce:
    action_hash: str
    ablauf_ts: float


class FreigabeBuch(_Buch):
    """Aktionsfreigaben. Keine ambiente Vollmacht (Design 2.4): ein Klick auf
    "Ausfuehren" belegt, dass der Nutzer *diese* Aktion wollte -- deshalb ist
    die Freigabe an den `action_hash` der kanonisierten Aktion gebunden, den
    der Hub selbst berechnet hat.

    Ablauf: der Hub gibt eine Nonce aus, bevor die Vorschau gezeigt wird; der
    Auth-Agent bestaetigt mit Nonce und Hash; die Ausfuehrung loest die
    Freigabe genau einmal ein.
    """

    TYP = "aktionsfreigabe"

    def __init__(self, *, frist_s: float = 60.0,
                 jetzt: Callable[[], float] = time.monotonic,
                 log: Any = None) -> None:
        super().__init__(frist_s=frist_s, jetzt=jetzt, log=log)
        self._nonces: dict[str, _Nonce] = {}
        self._freigaben: dict[str, Aktionsfreigabe] = {}
        self._verbraucht: set[str] = set()

    def nonce_ausgeben(self, *, action_hash: str) -> str:
        """Der Hub gibt die Nonce aus, bevor die Vorschau gezeigt wird."""
        with self._lock:
            action_hash = _pruefe_kein_request_feld(action_hash, "action_hash")
            nonce = secrets.token_hex(16)
            self._nonces[nonce] = _Nonce(action_hash=action_hash,
                                         ablauf_ts=self._jetzt() + self._frist)
            self._audit("nonce_ausgabe", DAIMON_ACTION_HASH=action_hash)
            return nonce

    def bestaetigen(self, *, nonce: str,
                    action_hash: str) -> Aktionsfreigabe:
        """Nonce muss existieren, unverbraucht sein UND zu genau diesem
        action_hash gehoeren. Falscher Hash -> MarkenFehler, und die Nonce
        ist danach verbrannt (ein Fehlversuch darf keinen zweiten erlauben).
        """
        with self._lock:
            eintrag = self._nonces.pop(nonce, None)
            if eintrag is None:
                raise self._ablehnung(
                    "unbekannte oder verbrauchte nonce",
                    DAIMON_ACTION_HASH=str(action_hash)[:64],
                )
            if self._jetzt() >= eintrag.ablauf_ts:
                raise self._ablehnung("nonce abgelaufen",
                                      DAIMON_ACTION_HASH=eintrag.action_hash)
            if eintrag.action_hash != action_hash:
                # Die Nonce ist oben bereits entfernt: verbrannt.
                raise self._ablehnung(
                    "nonce gehoert zu anderem action_hash",
                    DAIMON_ACTION_HASH=str(action_hash)[:64],
                )
            freigabe = Aktionsfreigabe(action_hash=action_hash,
                                       ablauf_ts=self._jetzt() + self._frist)
            self._freigaben[action_hash] = freigabe
            self._verbraucht.discard(action_hash)
            self._audit("ausgabe", DAIMON_ACTION_HASH=action_hash)
            return freigabe

    def einloesen(self, *, action_hash: str) -> Aktionsfreigabe:
        """Einmal, fuer genau diesen Hash."""
        with self._lock:
            freigabe = self._freigaben.get(action_hash)
            if freigabe is None:
                raise self._ablehnung(
                    "keine freigabe fuer diesen action_hash",
                    DAIMON_ACTION_HASH=str(action_hash)[:64],
                )
            if action_hash in self._verbraucht:
                raise self._ablehnung("bereits eingeloest",
                                      DAIMON_ACTION_HASH=action_hash)
            if self._jetzt() >= freigabe.ablauf_ts:
                raise self._ablehnung("abgelaufen",
                                      DAIMON_ACTION_HASH=action_hash)
            self._verbraucht.add(action_hash)
            self._audit("einloesung", DAIMON_ACTION_HASH=action_hash)
            return freigabe


# ---------------------------------------------------------------------------
# API-Kontingent
# ---------------------------------------------------------------------------

class KontingentBuch(_Buch):
    """API-Kontingente. Entstehen aus Wake-Word oder Rundenmarke und erlauben
    genau **einen** Egress-Aufruf.

    Ein Kontingent autorisiert **keine** Aktion und deklassifiziert **nichts**
    (Design 2.4). Sonst genuegte ein Video, das den Namen sagt und "was steht
    auf meinem Bildschirm?" fragt, um den Bildschirm in die Cloud zu
    schicken. `erlaubt_aktion` und `erlaubt_deklassifizierung` sind deshalb
    aufrufbare, konstante Zusagen -- eine Zusage, die man testen kann, ist
    mehr wert als ein Kommentar.
    """

    TYP = "api_kontingent"
    QUELLEN = frozenset({"wake_word", "rundenmarke"})

    def __init__(self, *, frist_s: float = 60.0,
                 jetzt: Callable[[], float] = time.monotonic,
                 log: Any = None) -> None:
        super().__init__(frist_s=frist_s, jetzt=jetzt, log=log)
        self._kontingente: dict[str, float] = {}  # id -> ablauf_ts
        self._verbraucht: set[str] = set()

    def ausgeben(self, *, quelle: str) -> str:
        """`quelle` in {"wake_word", "rundenmarke"}. Gibt eine Kontingent-ID
        zurueck. Alles andere -> MarkenFehler."""
        with self._lock:
            if quelle not in self.QUELLEN:
                raise self._ablehnung(
                    "quelle muss 'wake_word' oder 'rundenmarke' sein",
                    DAIMON_QUELLE=str(quelle)[:64],
                )
            kontingent_id = secrets.token_hex(16)
            self._kontingente[kontingent_id] = self._jetzt() + self._frist
            self._audit("ausgabe", DAIMON_QUELLE=quelle,
                        DAIMON_KONTINGENT=kontingent_id)
            return kontingent_id

    def einloesen_fuer_egress(self, kontingent_id: str) -> None:
        """Genau ein Egress-Aufruf. Zweiter -> MarkenFehler."""
        with self._lock:
            ablauf = self._kontingente.get(kontingent_id)
            if ablauf is None:
                raise self._ablehnung(
                    "unbekannte kontingent_id",
                    DAIMON_KONTINGENT=str(kontingent_id)[:64],
                )
            if kontingent_id in self._verbraucht:
                raise self._ablehnung("bereits eingeloest",
                                      DAIMON_KONTINGENT=kontingent_id)
            if self._jetzt() >= ablauf:
                raise self._ablehnung("abgelaufen",
                                      DAIMON_KONTINGENT=kontingent_id)
            self._verbraucht.add(kontingent_id)
            self._audit("einloesung", DAIMON_KONTINGENT=kontingent_id)

    def erlaubt_aktion(self, kontingent_id: str) -> bool:
        """IMMER False. Existiert als ausdrueckliche, testbare Zusage, nicht
        als Platzhalter -- siehe Moduldokstring."""
        return False

    def erlaubt_deklassifizierung(self, kontingent_id: str) -> bool:
        """IMMER False, aus demselben Grund."""
        return False
