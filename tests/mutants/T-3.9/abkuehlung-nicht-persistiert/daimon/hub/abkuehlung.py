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

Beide Uhren, und die Boot-ID entscheidet
----------------------------------------------------------------------------
Hier standen erst Wanduhr, dann monotone Zeit, und beide Begruendungen halten:

* **Monoton** muss es sein, weil eine NTP-Korrektur oder eine Zeitzonen-
  umstellung sonst eine Frist aufhebt oder eine erfindet. Eine Abkuehlung, die
  sich durch Uhrstellen abschalten laesst, ist keine.
* **Wanduhr** muss es sein, weil eine monotone Zahl nur innerhalb einer
  Systemlaufzeit gilt. Ueber einen Neustart abgelegt ist sie bedeutungslos --
  nach dem Boot faengt die Uhr klein an, und eine gespeicherte 900 000 liegt
  dann Tage in der Zukunft.

Der Widerspruch fiel beim Gegenlesen am 03.08. auf: der Builder hatte Wanduhr
gewaehlt, der Verifizierer verlangte monoton, und **keiner von beiden hatte
unrecht**. Aufgeloest wird er nicht durch eine Wahl, sondern durch das Feld, das
fehlte: die **Boot-ID** (`/proc/sys/kernel/random/boot_id`). Sie steht mit in
der Datei.

  * Gleiche Boot-ID -> es ist dieselbe Systemlaufzeit -> **monotone** Zeit
    entscheidet. Uhrstellen bleibt wirkungslos.
  * Andere Boot-ID -> es war ein Neustart -> **Wanduhr** entscheidet, denn die
    monotone Zahl von vorher bedeutet nichts mehr.

Der Preis der Wanduhr bleibt der Sprung, deshalb bleibt die Klammer in
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

_FORMAT_VERSION = 2
BOOT_ID_PFAD = "/proc/sys/kernel/random/boot_id"


def boot_id() -> str:
    """Kennung dieser Systemlaufzeit, oder "" wenn nicht lesbar.

    Leer heisst: unbekannt, und dann gilt die Wanduhr. Das ist die vorsichtige
    Richtung -- eine monotone Zahl ohne Gewissheit ueber die Laufzeit zu
    verwenden hiesse, sie ueber einen Neustart hinweg zu glauben.
    """
    try:
        with open(BOOT_ID_PFAD, "r", encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return ""


class Abkuehlung:
    """Wer wann wieder reden darf. Persistent, atomar, prozessuebergreifend.

    Zwei Uhren: `jetzt` ist monoton und entscheidet innerhalb einer
    Systemlaufzeit, `wanduhr` ueberbrueckt den Neustart. Welche gilt, sagt die
    Boot-ID in der Datei -- siehe Modulkopf.
    """

    def __init__(self, pfad: Path, *, cfg: Any = None,
                 jetzt: Callable[[], float] = time.monotonic,
                 wanduhr: Callable[[], float] = time.time,
                 boot: Callable[[], str] = boot_id,
                 log: Any = None) -> None:
        self._pfad = Path(pfad)
        self._cfg = cfg
        self._jetzt = jetzt
        self._wanduhr = wanduhr
        self._boot = boot()
        self._log = log
        self._lock = threading.Lock()
        # Zwei Buecher, dieselben Schluessel: `_mono` gilt bei gleicher
        # Boot-ID, `_wand` sonst. Geschrieben werden immer beide -- welche
        # Uhr beim naechsten Lesen zaehlt, weiss man beim Schreiben nicht.
        self._mono: dict[str, float] = {}
        self._wand: dict[str, float] = {}
        self._gleicher_boot = False
        # MUTANT: der Bestand wird nicht geladen -- die Abkuehlung
        # lebt nur im Speicher und ueberlebt keinen Neustart
        # (Kriterium 8).

    # -- Persistenz --------------------------------------------------------

    def _laden(self) -> None:
        """Eine kaputte Datei ist ein leerer Bestand plus Meldung, nie ein
        halber. Anders als beim Ticketbuch ist das hier **kein** harter Fehler:
        eine verlorene Abkuehlung kostet einen Satz zu viel, ein Hub, der
        deswegen nicht startet, kostet alles."""
        try:
            daten = json.loads(self._pfad.read_text(encoding="utf-8"))
            if daten.get("v") != _FORMAT_VERSION:
                raise ValueError(f"fremdes Format {daten.get('v')!r}")
            mono, wand = daten["bis_mono"], daten["bis_wand"]
            if not isinstance(mono, dict) or not isinstance(wand, dict):
                raise ValueError("bis_mono/bis_wand sind keine Objekte")
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if self._log is not None:
                self._log.warn("Abkuehlung nicht lesbar -- leer gestartet",
                               DAIMON_GRUND=str(exc)[:120])
            return
        zahlen = lambda d: {str(k): float(v) for k, v in d.items()
                            if isinstance(v, (int, float))}
        self._mono, self._wand = zahlen(mono), zahlen(wand)
        self._gleicher_boot = bool(self._boot) and daten.get("boot") == self._boot
        if self._log is not None and not self._gleicher_boot and self._wand:
            self._log.info("Abkuehlung aus einer anderen Systemlaufzeit -- "
                           "die Wanduhr entscheidet",
                           DAIMON_ACTION="abkuehlung_boot")

    def _schreiben(self) -> None:
        schreibe_atomar(self._pfad, json.dumps(
            {"v": _FORMAT_VERSION, "boot": self._boot,
             "bis_mono": self._mono, "bis_wand": self._wand},
            sort_keys=True,
        ).encode("utf-8"))

    # -- Abfrage und Vermerk -----------------------------------------------

    def _frist_s(self, kanal: str) -> float:
        return abkuehlung_s(kanal, self._cfg)

    def _rest_s(self, kanal: str, jetzt: float | None = None) -> float:
        """Restsekunden. Welche Uhr gilt, sagt die Boot-ID -- Modulkopf."""
        if self._gleicher_boot:
            ablauf, uhr = self._mono.get(kanal), self._jetzt()
        else:
            ablauf, uhr = self._wand.get(kanal), self._wanduhr()
        if ablauf is None:
            return 0.0
        rest = ablauf - uhr
        if rest <= 0.0:
            return 0.0
        # Die Sprung-Klammer, siehe Modulkopf. Sie greift vor allem im
        # Wanduhr-Fall (Neustart plus NTP-Korrektur), schadet im monotonen Fall
        # aber nicht. Grosszuegig gerechnet: die laengste konfigurierte Frist,
        # nicht die des Kanals -- sonst hielte eine Umkonfiguration nach unten
        # alte Eintraege fuer echt.
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
            rest = self._rest_s(kanal)
            return (rest <= 0.0, round(rest, 3))

    def vermerke(self, kanal: str) -> float:
        """Gesprochen. Ab jetzt gilt die Frist. Rueckgabe ist der Ablauf."""
        with self._lock:
            frist = self._frist_s(kanal)
            ablauf = self._jetzt() + frist
            self._mono[kanal] = ablauf
            self._wand[kanal] = self._wanduhr() + frist
            # Ab jetzt ist es DIESE Laufzeit, auch wenn der Bestand aus einer
            # aelteren kam -- sonst entscheidet nach dem ersten eigenen
            # Vermerk weiter die Wanduhr.
            self._gleicher_boot = bool(self._boot)
            # MUTANT: nichts geht auf die Platte.
            return ablauf

    def zustand(self) -> dict:
        with self._lock:
            return {
                "v": 2,
                "rest_s": {k: round(self._rest_s(k), 3)
                           for k in sorted(set(self._mono) | set(self._wand)
                                           | set(ABKUEHLUNG_S))},
                "fristen_s": {k: self._frist_s(k) for k in ABKUEHLUNG_S},
                "uhr": "monoton" if self._gleicher_boot else "wanduhr",
                "pfad": str(self._pfad),
            }
