"""T-4.7 — der DBus-Broker: eine feste Operation je genehmigter Aktion.

Warum es kein `invokeShortcut(name)` gibt
----------------------------------------------------------------------------
Ein generisches `invokeShortcut` waere ein Broker mit genau einer Operation,
deren Argument der ganze Kontrollraum ist -- jeder Kurzbefehl, den KDE kennt,
inklusive der, die niemand freigegeben hat. Der Katalog waere dann eine
Empfehlung an den Aufrufer statt einer Schranke im Ausfuehrenden.

Hier entsteht deshalb **je `approved`-Aktion eine Operation** mit festem
Ziel, festem Interface, fester Methode und festen Argumenten. Was nicht im
Katalog steht, hat keine Operation -- und ein Aufruf ohne Operation ist kein
"unbekannter Parameter", sondern kein Aufruf.

Zwei Schichten, absichtlich
----------------------------------------------------------------------------
1. **Hier**: nur gebaute Operationen, Argumente aus dem Katalog geprueft.
2. **Davor**: `xdg-dbus-proxy --filter` (config/dbus-filter.conf), der
   `org.kde.kwin.Scripting` gar nicht erst durchlaesst.

Die zweite Schicht ist nicht die Begruendung fuer eine schwache erste. Sie
faengt den Fall ab, in dem dieser Code einen Fehler hat -- und umgekehrt.
`org.kde.kwin.Scripting.loadScript` waere beliebiges JavaScript im
Compositor; es ist an beiden Stellen ausgeschlossen.

Was dieser Broker NICHT prueft
----------------------------------------------------------------------------
Die Policy. Die hat der Hub entschieden, bevor der Auftrag entstand. Der
Broker prueft den AUFTRAG (Zielgruppe, Frist, Ticket, Hash) und die
Zugehoerigkeit der Aktion zum Katalog -- eine zweite Policy hier waere eine
zweite Wahrheit, und die beiden liefen auseinander.
"""

from __future__ import annotations

import subprocess
import re
from dataclasses import dataclass
from typing import Any, Callable

from daimon.common.order import Auftrag, AuftragsFehler, pruefe

AUDIENCE = "dbus"

# Was der Broker ueberhaupt ansprechen darf -- Dienstnamen, nicht Methoden.
# Ein Dienst mehr ist eine Angriffsflaeche mehr; diese Liste waechst nur mit
# einer eigenen Entscheidung.
ERLAUBTE_DIENSTE = ("org.kde.kglobalaccel",)

# Ausdruecklich nie, an beiden Schichten. Steht hier als Name, damit ein Test
# ihn nennen kann, ohne ihn zu erfinden.
VERBOTEN = ("org.kde.kwin.Scripting", "org.kde.KWin.Scripting")


class BrokerFehler(RuntimeError):
    """Der Auftrag wird nicht ausgefuehrt. Nennt den Grund."""


@dataclass(frozen=True)
class Operation:
    """Ein fester Aufruf. Nichts daran ist zur Laufzeit waehlbar."""

    dienst: str
    pfad: str
    schnittstelle: str
    methode: str
    argumente: tuple[str, ...]

    def argv(self) -> list[str]:
        return ["gdbus", "call", "--session", "--dest", self.dienst,
                "--object-path", self.pfad, "--method",
                f"{self.schnittstelle}.{self.methode}", *self.argumente]


def _kglobalaccel_operation(eintrag: dict) -> Operation | None:
    """Aus einem Katalogeintrag mit `kglobalaccel: [komponente, aktion]`.

    Die vierteilige actionId wird HIER gebaut, aus Katalogwerten. Kaeme sie
    aus dem Auftrag, waere der Katalog wieder nur eine Empfehlung.
    """
    paar = eintrag.get("kglobalaccel")
    if not (isinstance(paar, (list, tuple)) and len(paar) == 2):
        return None
    komponente, aktion = str(paar[0]), str(paar[1])
    if any(z in komponente + aktion for z in ("'", '"', "\n", "\\")):
        # Ein Name, der die Argumentzeile aufbrechen koennte, wird nicht
        # maskiert, sondern abgelehnt: Maskierung ist eine Zusage, die man
        # vergisst.
        raise BrokerFehler(f"unbrauchbarer Aktionsname: {komponente}/{aktion}")
    # Der Aufruf liegt am KOMPONENTENOBJEKT, nicht an /kglobalaccel. Am
    # 10.08. live gemessen: `org.kde.KGlobalAccel.invokeShortcut(as)` gibt es
    # nicht -- der Bus antwortet `UnknownMethod`. Was es gibt, ist
    # `org.kde.kglobalaccel.Component.invokeShortcut(s)` unter
    # `/component/<komponente>`.
    #
    # KDE ersetzt in diesem Pfad alles, was in einem DBus-Objektpfad nicht
    # erlaubt ist, durch `_` -- `org.kde.spectacle.desktop` wird zu
    # `org_kde_spectacle_desktop`. Dieselbe Ersetzung hier, statt den Namen
    # ungeprueft einzusetzen: ein Pfad mit einem Punkt darin waere kein
    # Objektpfad, und der Aufruf schluege mit einer Meldung fehl, die nach
    # einem fehlenden Kurzbefehl aussieht.
    pfad = "/component/" + re.sub(r"[^A-Za-z0-9_]", "_", komponente)
    return Operation(dienst="org.kde.kglobalaccel", pfad=pfad,
                     schnittstelle="org.kde.kglobalaccel.Component",
                     methode="invokeShortcut", argumente=(aktion,))


@dataclass
class DBusBroker:
    """Haelt die Operationstabelle. Gebaut aus dem Katalog, einmal."""

    operationen: dict
    lauf: Callable[..., Any] = subprocess.run

    @classmethod
    def aus_katalog(cls, katalog: dict, **kw) -> "DBusBroker":
        tabelle: dict[str, Operation] = {}
        for kennung, eintrag in katalog.items():
            if eintrag.get("status") != "approved":
                continue
            op = _kglobalaccel_operation(eintrag)
            if op is None:
                # Katalogeintraege ohne kglobalaccel-Ursprung gehoeren einem
                # anderen Broker (`audio.volume.set`, `kde.window.raise`).
                # Hier keine Operation zu bauen ist die richtige Antwort --
                # eine erfundene waere eine Faehigkeit ohne Grundlage.
                continue
            if op.dienst not in ERLAUBTE_DIENSTE:
                raise BrokerFehler(
                    f"{kennung}: Dienst {op.dienst!r} steht nicht in der "
                    f"Allowlist")
            tabelle[kennung] = op
        return cls(operationen=tabelle, **kw)

    def ausfuehren(self, roh: bytes, *, jetzt: float,
                   ticket_einloesen: Callable[[str], Any]) -> dict:
        """Der einzige Weg von einem Auftrag zu einer Wirkung.

        `ticket_einloesen` gehoert dem HUB und wird **unmittelbar vor** dem
        Aufruf gerufen -- nicht davor beim Pruefen und nicht danach. Was
        dazwischen liegt, ist der Aufruf selbst.
        """
        try:
            auftrag: Auftrag = pruefe(roh, audience=AUDIENCE, jetzt=jetzt)
        except AuftragsFehler as fehler:
            return {"ok": False, "grund": "auftrag", "meldung": str(fehler)}

        operation = self.operationen.get(auftrag.action_id)
        if operation is None:
            # Der Kern des Tasks: nicht genehmigt heisst nicht "abgewiesener
            # Parameter", sondern "gibt es hier nicht".
            return {"ok": False, "grund": "keine_operation",
                    "meldung": f"{auftrag.action_id} hat hier keine Operation"}

        if any(v in operation.schnittstelle for v in VERBOTEN):
            return {"ok": False, "grund": "verboten",
                    "meldung": f"{operation.schnittstelle} ist ausgeschlossen"}

        try:
            ticket_einloesen(auftrag.ticket)
        except Exception as fehler:  # AuftragsFehler des Hubs
            return {"ok": False, "grund": "ticket", "meldung": str(fehler)}

        e = self.lauf(operation.argv(), capture_output=True, text=True,
                      timeout=10)
        rc = int(getattr(e, "returncode", 1))
        return {"ok": rc == 0, "grund": "" if rc == 0 else "dbus",
                "action_id": auftrag.action_id, "rc": rc,
                "meldung": (getattr(e, "stderr", "") or "").strip()[:200]}
