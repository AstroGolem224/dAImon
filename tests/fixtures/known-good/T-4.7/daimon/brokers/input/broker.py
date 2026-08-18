"""T-4.13 — der Input-Broker: das gefaehrlichste Werkzeug, so kurz wie moeglich.

One-shot heisst one-shot
----------------------------------------------------------------------------
Der Broker bekommt EINE begrenzte, unveraenderliche Ereignisfolge, fuehrt sie
aus, schliesst die Portal-Session und **beendet sich**. Es gibt keinen
Leerlauf, in dem er auf die naechste Folge wartet: ein dauerhaft lebender
Prozess, der Tastenanschlaege synthetisieren kann, ist genau die Faehigkeit,
die dieses Projekt nicht dauerhaft haben will. `RuntimeMaxSec=` in der Unit
ist das Zwangsende, falls dieser Code es nicht selbst schafft.

Warum `ydotool` standardmaessig aus ist
----------------------------------------------------------------------------
Zwei Gruende, und der zweite ist der schwerere:

1. **Es positioniert auf dieser Maschine nicht.** Spike T-1.3, gemessen:
   `ydotool mousemove -a` landet bei `(0,0)`, Exit-Code 0, keine
   Fehlermeldung. Nur relative Bewegung geht, und die laeuft durch die
   Zeigerbeschleunigung -- 996 px nominell wurden 3984 px real.
2. **Es verlangt einen dauerhaften privilegierten `ydotoold`.** Das
   widerspricht der One-shot-Zusage direkt: dann laeuft der gefaehrliche Teil
   dauerhaft, nur eben in einem anderen Prozess.

Deshalb: der Regelweg ist libei ueber das Portal `RemoteDesktop`. Der
`ydotool`-Rueckfall ist **abgeschaltet** und muss ausdruecklich eingeschaltet
werden; ist er an, startet und beendet der Broker `ydotoold` selbst, statt
ihn liegen zu lassen.

Die Folge ist unveraenderlich
----------------------------------------------------------------------------
Sie wird beim Annehmen geprueft und danach nicht mehr angefasst: feste
Laenge, feste Ereignisarten, Werte in Schranken. Ein Broker, der die Folge
noch umbauen darf, hat eine zweite Stelle, an der etwas hineingeraet.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any, Callable

# Was ueberhaupt synthetisiert werden darf.
ARTEN = ("key", "type", "move_rel", "button")

# Harte Obergrenzen. Sie sind nicht die Policy -- die entscheidet, OB
# ueberhaupt. Das hier ist die Grenze, ab der eine Folge kein Einzelvorgang
# mehr ist, sondern eine Fernsteuerung.
MAX_EREIGNISSE = 32
MAX_TEXT_ZEICHEN = 200
MAX_BEWEGUNG_PX = 500


class InputFehler(RuntimeError):
    """Die Folge wird nicht ausgefuehrt. Nennt den Grund."""


@dataclass(frozen=True)
class Ereignis:
    art: str
    wert: Any


def folge_pruefen(roh: list[dict]) -> tuple[Ereignis, ...]:
    """Einmal pruefen, dann unveraenderlich."""
    if not isinstance(roh, list) or not roh:
        raise InputFehler("leere Ereignisfolge")
    if len(roh) > MAX_EREIGNISSE:
        raise InputFehler(
            f"{len(roh)} Ereignisse; hoechstens {MAX_EREIGNISSE} -- laenger "
            f"ist keine Einzelhandlung mehr, sondern eine Fernsteuerung")
    geprueft = []
    for nummer, e in enumerate(roh):
        art = (e or {}).get("art")
        if art not in ARTEN:
            raise InputFehler(f"Ereignis {nummer}: unbekannte Art {art!r}")
        wert = e.get("wert")
        if art == "type":
            if not isinstance(wert, str) or not wert:
                raise InputFehler(f"Ereignis {nummer}: `type` braucht Text")
            if len(wert) > MAX_TEXT_ZEICHEN:
                raise InputFehler(
                    f"Ereignis {nummer}: {len(wert)} Zeichen, hoechstens "
                    f"{MAX_TEXT_ZEICHEN}")
        elif art == "move_rel":
            if not (isinstance(wert, (list, tuple)) and len(wert) == 2):
                raise InputFehler(f"Ereignis {nummer}: `move_rel` braucht [dx, dy]")
            for achse in wert:
                if isinstance(achse, bool) or not isinstance(achse, (int, float)):
                    raise InputFehler(f"Ereignis {nummer}: Bewegung ist keine Zahl")
                if abs(achse) > MAX_BEWEGUNG_PX:
                    raise InputFehler(
                        f"Ereignis {nummer}: {achse} px ueberschreitet "
                        f"{MAX_BEWEGUNG_PX}")
        elif art in ("key", "button"):
            if not isinstance(wert, str) or not wert.strip():
                raise InputFehler(f"Ereignis {nummer}: {art} braucht einen Namen")
        geprueft.append(Ereignis(art=art, wert=wert))
    return tuple(geprueft)


@dataclass
class InputBroker:
    """Einmal benutzbar. Danach ist `verbraucht` wahr und bleibt es."""

    ydotool_erlaubt: bool = False
    lauf: Callable[..., Any] = subprocess.run
    portal: Callable[..., Any] | None = None
    verbraucht: bool = False

    def ausfuehren(self, roh: list[dict]) -> dict:
        if self.verbraucht:
            # Kein zweiter Lauf, auch nicht mit gueltigem Auftrag: die Unit
            # ist one-shot, und ein Broker, der zweimal kann, ist es nicht.
            return {"ok": False, "grund": "verbraucht",
                    "meldung": "dieser Broker hat seine eine Folge schon gehabt"}
        try:
            folge = folge_pruefen(roh)
        except InputFehler as fehler:
            self.verbraucht = True
            return {"ok": False, "grund": "folge", "meldung": str(fehler)}

        self.verbraucht = True
        if self.portal is not None:
            ergebnis = self.portal(folge)
            return {"ok": bool(ergebnis), "grund": "" if ergebnis else "portal",
                    "weg": "libei", "ereignisse": len(folge)}

        if not self.ydotool_erlaubt:
            # Kein stiller Rueckfall. Ohne Portal wird nichts synthetisiert.
            return {"ok": False, "grund": "kein_portal",
                    "weg": "keiner",
                    "meldung": "libei/RemoteDesktop nicht verfuegbar und "
                               "ydotool ist abgeschaltet (Spike T-1.3: "
                               "positioniert nicht, verlangt dauerhaften "
                               "privilegierten Dienst)"}

        return self._ueber_ydotool(folge)

    def _ueber_ydotool(self, folge: tuple[Ereignis, ...]) -> dict:
        """Nur mit ausdruecklicher Erlaubnis -- und mit Aufraeumen."""
        gestartet = self.lauf(["systemd-run", "--user", "--collect", "--quiet",
                               "--unit=daimon-ydotoold", "ydotoold"],
                              capture_output=True, text=True, timeout=10)
        if int(getattr(gestartet, "returncode", 1)) != 0:
            return {"ok": False, "grund": "ydotoold",
                    "meldung": (getattr(gestartet, "stderr", "") or "")[:160]}
        try:
            for e in folge:
                if e.art == "type":
                    argv = ["ydotool", "type", "--", e.wert]
                elif e.art == "key":
                    argv = ["ydotool", "key", "--", e.wert]
                elif e.art == "button":
                    argv = ["ydotool", "click", "--", e.wert]
                else:
                    # ABSOLUTE Positionierung gibt es hier nicht: sie landet
                    # gemessen bei (0,0). Nur relativ, und das steht so im
                    # Ergebnis, damit niemand Genauigkeit annimmt.
                    argv = ["ydotool", "mousemove", "--",
                            str(int(e.wert[0])), str(int(e.wert[1]))]
                lauf = self.lauf(argv, capture_output=True, text=True, timeout=10)
                if int(getattr(lauf, "returncode", 1)) != 0:
                    return {"ok": False, "grund": "ydotool", "weg": "ydotool",
                            "meldung": (getattr(lauf, "stderr", "") or "")[:160]}
            return {"ok": True, "grund": "", "weg": "ydotool",
                    "ereignisse": len(folge),
                    "hinweis": "relative Bewegung, ungenau (Zeigerbeschleunigung)"}
        finally:
            # Der privilegierte Dienst bleibt NICHT stehen.
            self.lauf(["systemctl", "--user", "stop", "daimon-ydotoold"],
                      capture_output=True, text=True, timeout=10)
