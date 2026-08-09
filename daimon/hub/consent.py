"""T-4.11 — Rueckfragen mit Nonce: nicht wiederverwendbar, nicht verwechselbar.

Wer hier was macht (Design 2.2)
----------------------------------------------------------------------------
**Der Hub haelt nur den kanonischen Zustand.** Angezeigt und beantwortet wird
im Auth-Agenten -- der ist der einzige Prozess, der eine Freigabe
entgegennehmen darf, weil er keinen Modelltext rendert. Dieses Modul kennt
deshalb weder DBus noch Fenster: es kennt offene Rueckfragen, Fristen und
die Regel, wann eine Antwort zaehlt.

Drei Ausgaenge, nicht zwei
----------------------------------------------------------------------------
* `granted`  -- der Mensch hat zugestimmt,
* `declined` -- der Mensch hat abgelehnt,
* `cancelled` -- die Frist lief ab, die Benachrichtigung wurde weggewischt,
  oder der Hub startete neu.

`cancelled` ist ausdruecklich **kein Nein**. Ein weggewischtes Fenster heisst
"keine Antwort", und wer daraus ein Nein macht, protokolliert eine
Entscheidung, die niemand getroffen hat. Umgekehrt darf es erst recht kein Ja
werden.

Zwei Bedingungen fuer jede Antwort
----------------------------------------------------------------------------
Die Nonce muss passen **und** der Absender. Die Nonce allein reicht nicht:
sie steht in der Benachrichtigung, und wer die sieht, koennte sie
zurueckschicken. Der Absender allein reicht auch nicht: sonst koennte der
Auth-Agent eine beliebige, laengst abgelaufene Rueckfrage bestaetigen.

Persistiert, weil ein Neustart mitten in einer Rueckfrage sonst eine
verwaiste Genehmigung hinterliesse
----------------------------------------------------------------------------
Der offene Zustand liegt auf der Platte. Beim Laden wird jede noch offene
Rueckfrage zu `cancelled` -- nicht wiederhergestellt. Eine Rueckfrage, deren
Fenster der Neustart weggeraeumt hat, kann niemand mehr beantworten; sie
offen zu lassen hiesse, auf eine Antwort zu warten, die nie kommt.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATEI = "consent-pending.json"
VORGABE_FRIST_S = 120.0

OFFEN = "pending"
ZUSTIMMUNG = "granted"
ABLEHNUNG = "declined"
ABBRUCH = "cancelled"
ZUSTAENDE = (OFFEN, ZUSTIMMUNG, ABLEHNUNG, ABBRUCH)


class ConsentFehler(ValueError):
    """Die Antwort zaehlt nicht. Nennt den Grund."""


def action_hash(action_id: str, params_hash: str) -> str:
    """Woran die Freigabe haengt. Nicht an der Rueckfrage, sondern an der Tat."""
    roh = f"{action_id}\n{params_hash}".encode("utf-8")
    return "sha256:" + hashlib.sha256(roh).hexdigest()


@dataclass
class Rueckfrage:
    id: str
    nonce: str
    action_id: str
    params_hash: str
    action_hash: str
    prompt_shown: str
    absender: str
    frist: float
    zustand: str = OFFEN
    beantwortet: float | None = None


@dataclass
class Consent:
    """Der kanonische Zustand. Kein DBus, keine Darstellung."""

    pfad: Path
    offen: dict = field(default_factory=dict)
    _freigaben: dict = field(default_factory=dict)

    @classmethod
    def laden(cls, verzeichnis: Path | str) -> "Consent":
        verzeichnis = Path(verzeichnis)
        verzeichnis.mkdir(parents=True, exist_ok=True)
        os.chmod(verzeichnis, 0o700)
        selbst = cls(pfad=verzeichnis / DATEI)
        if selbst.pfad.is_file():
            try:
                roh = json.loads(selbst.pfad.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                roh = []
            # Nichts wird wiederhergestellt. Siehe Modulkopf: eine Rueckfrage
            # ohne Fenster ist keine Rueckfrage mehr.
            selbst.verwaiste = [e.get("id") for e in roh if
                                e.get("zustand") == OFFEN]
            selbst._speichern()
        return selbst

    def _speichern(self) -> None:
        vorlaeufig = self.pfad.with_suffix(".json.neu")
        vorlaeufig.write_text(
            json.dumps([asdict(r) for r in self.offen.values()],
                       ensure_ascii=False), encoding="utf-8")
        os.chmod(vorlaeufig, 0o600)
        vorlaeufig.replace(self.pfad)

    # -- Stellen ------------------------------------------------------------

    def stellen(self, *, action_id: str, params_hash: str, prompt_shown: str,
                absender: str, jetzt: float, frist_s: float = VORGABE_FRIST_S
                ) -> Rueckfrage:
        """Eine neue Rueckfrage. Mehrere gleichzeitig sind unterscheidbar."""
        rueckfrage = Rueckfrage(
            id=secrets.token_urlsafe(12), nonce=secrets.token_urlsafe(32),
            action_id=action_id, params_hash=params_hash,
            action_hash=action_hash(action_id, params_hash),
            prompt_shown=prompt_shown, absender=absender,
            frist=float(jetzt) + float(frist_s))
        self.offen[rueckfrage.id] = rueckfrage
        self._speichern()
        return rueckfrage

    # -- Beantworten --------------------------------------------------------

    def antwort(self, *, rueckfrage_id: str, nonce: str, absender: str,
                zustand: str, jetzt: float) -> Rueckfrage:
        if zustand not in (ZUSTIMMUNG, ABLEHNUNG, ABBRUCH):
            raise ConsentFehler(f"unbekannter Zustand {zustand!r}")
        rueckfrage = self.offen.get(rueckfrage_id)
        if rueckfrage is None:
            raise ConsentFehler("unbekannte oder bereits beantwortete Rueckfrage")
        # Beide Bedingungen, und beide mit konstanter Laufzeit verglichen:
        # ein Vergleich, der beim ersten falschen Zeichen abbricht, verraet
        # die Nonce zeichenweise.
        if not secrets.compare_digest(rueckfrage.nonce, str(nonce)):
            raise ConsentFehler("Nonce passt nicht")
        if not secrets.compare_digest(rueckfrage.absender, str(absender)):
            raise ConsentFehler(
                f"Absender {absender!r} ist nicht der, an den gefragt wurde")
        if float(jetzt) > rueckfrage.frist:
            # Abgelaufen ist `cancelled`, egal was der Absender behauptet --
            # auch ein "granted", das nach der Frist eintrifft.
            zustand = ABBRUCH

        rueckfrage.zustand = zustand
        rueckfrage.beantwortet = float(jetzt)
        del self.offen[rueckfrage_id]
        if zustand == ZUSTIMMUNG:
            # EINMALIG und an die Tat gebunden, nicht an die Rueckfrage.
            self._freigaben[rueckfrage.action_hash] = {
                "id": rueckfrage.id, "verbraucht": False,
                "frist": rueckfrage.frist}
        self._speichern()
        return rueckfrage

    def ablaufen_lassen(self, *, jetzt: float) -> list[Rueckfrage]:
        """Was ueber der Frist liegt, wird `cancelled` -- nicht `declined`."""
        faellig = [r for r in self.offen.values() if jetzt > r.frist]
        for r in faellig:
            r.zustand = ABBRUCH
            r.beantwortet = float(jetzt)
            del self.offen[r.id]
        if faellig:
            self._speichern()
        return faellig

    # -- Einloesen ----------------------------------------------------------

    def freigabe_einloesen(self, *, action_id: str, params_hash: str,
                           jetzt: float) -> bool:
        """Genau einmal, und nur fuer genau diese Parameter."""
        schluessel = action_hash(action_id, params_hash)
        eintrag = self._freigaben.get(schluessel)
        if eintrag is None or eintrag["verbraucht"]:
            return False
        if float(jetzt) > eintrag["frist"]:
            return False
        eintrag["verbraucht"] = True
        return True

    def hat_freigabe(self, *, action_id: str, params_hash: str) -> bool:
        eintrag = self._freigaben.get(action_hash(action_id, params_hash))
        return bool(eintrag and not eintrag["verbraucht"])
