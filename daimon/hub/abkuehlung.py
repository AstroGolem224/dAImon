"""T-3.9 — Abkuehlung je Anlass. 20 s ungefragt, 10 s Reaktion, 3 s Rueckfrage.

Ein Pet, das jede Regung kommentiert, wird abgeschaltet. Die Abkuehlung ist
deshalb keine Feinheit, sondern die Zusage "stoert nicht" in Code -- dieselbe,
die T-1.10 ueber fuenf Tage messen soll.

Warum persistiert
----------------------------------------------------------------------------
Ohne Persistenz waere jeder Neustart des Dienstes eine Einladung, sofort wieder
zu reden. Genau das faellt bei einem Dienst auf, der sich nach Leerlauf beendet
oder nach einem Fehler neu startet: die Abkuehlung waere dann gerade dort weg,
wo sie am meisten gebraucht wird.

Warum Wanduhr und nicht `time.monotonic`
----------------------------------------------------------------------------
Eine monotone Zahl gilt nur innerhalb einer Systemlaufzeit. Ueber einen Neustart
hinweg abgelegt ist sie **bedeutungslos** -- nach dem Boot faengt die Uhr wieder
klein an, und eine gespeicherte 900 000 liegt dann Tage in der Zukunft. Ein
persistierter Zustand braucht deshalb die Wanduhr.

Der Preis der Wanduhr ist der Sprung: eine NTP-Korrektur oder eine Zeitzonen-
umstellung kann eine Frist aufheben oder eine erfinden. Deshalb die Klammer in
`_rest_s()`: liegt ein Ablauf weiter in der Zukunft als die laengste Frist
ueberhaupt, ist die Uhr gesprungen und nicht die Frist echt -- dann wird
freigegeben statt gesperrt. Fail-safe ist hier **reden duerfen**: der Schaden
einer verpassten Abkuehlung ist ein Satz zu viel, der Schaden einer ewigen
Abkuehlung ist ein stummes Pet, das aussieht wie ein abgestuerztes.

ponytail: eine JSON-Datei, ein Schlüssel je Kanal. Obergrenze: sobald die
Abkuehlung je *Anlass* statt je *Kanal* gelten soll (Design §10 deutet das an),
wird der Schluessel zusammengesetzt -- die Datei traegt das ohne Formatwechsel.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from daimon.common.atomar import schreibe_atomar
from daimon.hub.sprechtext import ABKUEHLUNG_S, abkuehlung_s

_FORMAT_VERSION = 1


class Abkuehlung:
    """Wer wann wieder reden darf. Persistent, atomar, prozessuebergreifend."""

    def __init__(self, pfad: Path, *, cfg: Any = None,
                 jetzt: Callable[[], float] = time.time,
                 log: Any = None) -> None:
        self._pfad = Path(pfad)
        self._cfg = cfg
        self._jetzt = jetzt
        self._log = log
        self._lock = threading.Lock()
        self._bis: dict[str, float] = {}
        self._laden()

    # -- Persistenz --------------------------------------------------------

    def _laden(self) -> None:
        """Eine kaputte Datei ist ein leerer Bestand plus Meldung, nie ein
        halber. Anders als beim Ticketbuch ist das hier **kein** harter Fehler:
        eine verlorene Abkuehlung kostet einen Satz zu viel, ein Hub, der
        deswegen nicht startet, kostet alles."""
        try:
            daten = json.loads(self._pfad.read_text(encoding="utf-8"))
            if daten.get("v") != _FORMAT_VERSION:
                raise ValueError("fremdes format")
            bis = daten["bis"]
            if not isinstance(bis, dict):
                raise ValueError("bis ist kein Objekt")
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if self._log is not None:
                self._log.warn("Abkuehlung nicht lesbar -- leer gestartet",
                               DAIMON_GRUND=str(exc)[:120])
            return
        self._bis = {str(k): float(v) for k, v in bis.items()
                     if isinstance(v, (int, float))}

    def _schreiben(self) -> None:
        schreibe_atomar(self._pfad, json.dumps(
            {"v": _FORMAT_VERSION, "bis": self._bis}, sort_keys=True,
        ).encode("utf-8"))

    # -- Abfrage und Vermerk -----------------------------------------------

    def _frist_s(self, kanal: str) -> float:
        return abkuehlung_s(kanal, self._cfg)

    def _rest_s(self, kanal: str, jetzt: float) -> float:
        ablauf = self._bis.get(kanal)
        if ablauf is None:
            return 0.0
        rest = ablauf - jetzt
        if rest <= 0.0:
            return 0.0
        # Die Uhrsprung-Klammer, siehe Modulkopf. Grosszuegig gerechnet: die
        # laengste konfigurierte Frist, nicht die des Kanals -- sonst wuerde
        # eine Umkonfiguration nach unten alte Eintraege fuer echt halten.
        obergrenze = max([self._frist_s(k) for k in ABKUEHLUNG_S] + [0.0])
        if rest > obergrenze:
            if self._log is not None:
                self._log.warn("Abkuehlung liegt zu weit vorn -- Uhrsprung, "
                               "freigegeben", DAIMON_KANAL=kanal,
                               DAIMON_REST_S=round(rest, 3))
            return 0.0
        return rest

    def darf(self, kanal: str) -> tuple[bool, float]:
        """`(darf_reden, restsekunden)`. Kein Nebeneffekt -- der Vermerk ist ein
        eigener Aufruf, weil zwischen Erlaubnis und Aeusserung noch der
        Validator und die Synthese liegen. Wer hier gleich vermerkt, sperrt sich
        auch dann, wenn der Satz nie gesprochen wurde."""
        with self._lock:
            rest = self._rest_s(kanal, self._jetzt())
            return (rest <= 0.0, round(rest, 3))

    def vermerke(self, kanal: str) -> float:
        """Gesprochen. Ab jetzt gilt die Frist. Rueckgabe ist der Ablauf."""
        with self._lock:
            ablauf = self._jetzt() + self._frist_s(kanal)
            self._bis[kanal] = ablauf
            self._schreiben()
            return ablauf

    def zustand(self) -> dict:
        with self._lock:
            jetzt = self._jetzt()
            return {
                "v": 1,
                "rest_s": {k: round(self._rest_s(k, jetzt), 3)
                           for k in sorted(set(self._bis) | set(ABKUEHLUNG_S))},
                "fristen_s": {k: self._frist_s(k) for k in ABKUEHLUNG_S},
                "pfad": str(self._pfad),
            }
