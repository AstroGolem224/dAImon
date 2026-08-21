"""T-4.13 — der Input-Broker: das gefaehrlichste Werkzeug, so kurz wie moeglich.

One-shot heisst one-shot
----------------------------------------------------------------------------
Der Broker bekommt EINE begrenzte, unveraenderliche Ereignisfolge, fuehrt sie
aus, schliesst die Portal-Session und **beendet sich**. Es gibt keinen
Leerlauf, in dem er auf die naechste Folge wartet: ein dauerhaft lebender
Prozess, der Tastenanschlaege synthetisieren kann, ist genau die Faehigkeit,
die dieses Projekt nicht dauerhaft haben will. `RuntimeMaxSec=` in der Unit
ist das Zwangsende, falls dieser Code es nicht selbst schafft.

Die Reihenfolge der Absagen ist selbst eine Zusage
----------------------------------------------------------------------------
Der Sperrbildschirm steht VOR allem anderen. Er ist ein harter Breaker: bei
aktivem `org.freedesktop.ScreenSaver` wird abgelehnt, unabhaengig von der
Allowlist, von der Folge und davon, ob eine Freigabe vorliegt. Danach erst
kommt die App-Allowlist, danach erst die Folge. Wer die Reihenfolge dreht,
hat einen Breaker, den eine gueltige Folge aushebeln kann.

**App-Allowlist statt Passwortfeld-Erkennung.** Die Behauptung aus v1.0 ist
zurueckgezogen: ob der Fokus in einem Passwortfeld steht, ist auf Wayland
nicht ermittelbar, und was nicht ermittelbar ist, wird hier auch nicht
behauptet. Stattdessen wird in genau die Anwendungen getippt, die auf der
Liste stehen -- und in keine andere.

Warum `ydotool` standardmaessig aus ist
----------------------------------------------------------------------------
Zwei Gruende, und der zweite ist der schwerere:

1. **Es positioniert auf dieser Maschine nicht.** Spike T-1.3, gemessen:
   `ydotool mousemove -a` landet bei `(0,0)`, Exit-Code 0, keine
   Fehlermeldung. Nur relative Bewegung geht, und die laeuft durch die
   Zeigerbeschleunigung -- ein `(30,30)`-Schritt kommt als `(53,53)` an.
   Fuer alles mit Positionierung ist libei deshalb nicht die bevorzugte,
   sondern die EINZIGE brauchbare Option.
2. **Es verlangt einen dauerhaften privilegierten `ydotoold`.** Das
   widerspricht der One-shot-Zusage direkt: dann laeuft der gefaehrliche Teil
   dauerhaft, nur eben in einem anderen Prozess.

Deshalb: der Regelweg ist libei ueber das Portal `RemoteDesktop`. Der
`ydotool`-Rueckfall ist **abgeschaltet** und muss ausdruecklich eingeschaltet
werden. Ist er an, gilt zweierlei:

* Der Broker startet `ydotoold` als **eigenes Kind** -- nicht als transiente
  Unit daneben. Stirbt der Broker, etwa am Zwangsende der Unit, stirbt der
  privilegierte Dienst mit ihm. Eine Nachbar-Unit ueberlebt ihn.
* Laeuft schon irgendwo auf dieser Maschine ein `ydotoold`, wird die Aktion
  **abgelehnt**. Ein systemweiter Daemon ist die Faehigkeit, die dieser
  Broker gerade nicht dauerhaft haben will; ihn stillschweigend
  mitzubenutzen waere die bequemste Art, die Zusage zu verlieren.

Die Folge ist unveraenderlich
----------------------------------------------------------------------------
Sie wird beim Annehmen geprueft und danach nicht mehr angefasst: feste
Laenge, feste Ereignisarten, Werte in Schranken. Ein Broker, der die Folge
noch umbauen darf, hat eine zweite Stelle, an der etwas hineingeraet.

Das Audit sieht die Zeichen nie
----------------------------------------------------------------------------
Protokolliert werden **Laenge und Klassenlabel** der Folge -- nicht ihr
Inhalt. Ein Audit-Log, das Tastaturanschlaege im Klartext mitschreibt, ist
ein Keylogger mit Kettenhash.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Was ueberhaupt synthetisiert werden darf. KEINE absolute Positionierung:
# `ydotool mousemove -a` landet auf dieser Maschine gemessen bei (0,0), und
# eine Art, die nur ueber einen Weg funktioniert, waere eine Zusage, die vom
# Weg abhaengt.
ARTEN = ("key", "type", "move_rel", "button")

# Harte Obergrenzen. Sie sind nicht die Policy -- die entscheidet, OB
# ueberhaupt. Das hier ist die Grenze, ab der eine Folge kein Einzelvorgang
# mehr ist, sondern eine Fernsteuerung.
MAX_EREIGNISSE = 32
MAX_TEXT_ZEICHEN = 200
MAX_BEWEGUNG_PX = 500

# Die drei Namen, an denen die Zusagen dieses Brokers haengen.
PORTAL_SCHNITTSTELLE = "org.freedesktop.portal.RemoteDesktop"
SCREENSAVER_DIENST = "org.freedesktop.ScreenSaver"
YDOTOOLD = "ydotoold"


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


def klassenlabel(folge: tuple[Ereignis, ...]) -> str:
    """Was fuer Ereignisse das waren -- nie, welche.

    Nur die ARTEN, sortiert und ohne Wiederholung. `type+key` sagt "es wurde
    getippt und eine Taste gedrueckt"; es sagt nicht, welche.
    """
    return "+".join(sorted({e.art for e in folge})) or "leer"


def fremder_ydotoold(eigene: tuple = ()) -> int | None:
    """Laeuft auf dieser Maschine schon ein `ydotoold`? Dann seine PID.

    Ueber `/proc`, nicht ueber `pgrep`: der Broker soll das auch dann sehen,
    wenn seine Sandbox kein Werkzeug im Pfad hat. `ProcSubset=pid` laesst die
    fremden Prozessverzeichnisse stehen, `ProtectProc=invisible` verbirgt die
    fremder Nutzer -- ein `ydotoold` unter einer anderen uid ist von hier aus
    nicht sichtbar, und genau das steht im Ledger unter Grenzen.
    """
    ausgenommen = set(eigene) | {os.getpid()}
    try:
        eintraege = list(Path("/proc").iterdir())
    except OSError:
        return None
    for eintrag in eintraege:
        if not eintrag.name.isdigit():
            continue
        pid = int(eintrag.name)
        if pid in ausgenommen:
            continue
        try:
            roh = (eintrag / "cmdline").read_bytes()
        except OSError:
            continue
        if not roh:
            continue
        erstes = roh.split(b"\0")[0].decode("utf-8", "replace")
        if erstes and os.path.basename(erstes) == YDOTOOLD:
            return pid
    return None


@dataclass
class InputBroker:
    """Einmal benutzbar. Danach ist `verbraucht` wahr und bleibt es."""

    ydotool_erlaubt: bool = False
    lauf: Callable[..., Any] = subprocess.run
    starten: Callable[..., Any] = subprocess.Popen
    verbraucht: bool = False
    # Genau die Anwendungen, in die getippt werden darf. Leer heisst: keine.
    allowlist: frozenset = field(default_factory=frozenset)
    # Der Regelweg. Eine Fabrik, die eine Sitzung mit `senden` und
    # `schliessen` liefert -- libei ueber das Portal `RemoteDesktop`.
    portal_sitzung: Callable[[], Any] | None = None
    # Der harte Breaker. KEIN Vorgabewert `False`: ohne Leser wird nichts
    # synthetisiert. Ein Broker, der bei fehlendem Leser "nicht gesperrt"
    # annimmt, tippt in den Sperrbildschirm.
    screensaver_aktiv: Callable[[], bool] | None = None
    # Die Kollisionspruefung.
    fremder_dienst: Callable[..., Any] = fremder_ydotoold
    # Bekommt Laenge und Klassenlabel. Nie einen Wert.
    audit: Callable[..., Any] | None = None

    # -- Absagen und Protokoll -----------------------------------------------

    def _protokollieren(self, *, grund: str, ok: bool, weg: str, app: str,
                        folge: tuple) -> None:
        if self.audit is None:
            return
        # Laenge und Klassenlabel. Der Wert eines Ereignisses erreicht diese
        # Zeile nicht, und er soll es auch nicht koennen.
        self.audit(aktion="input.folge", app=app, laenge=len(folge),
                   klasse=klassenlabel(folge), weg=weg, ok=bool(ok),
                   grund=grund)

    def _absage(self, grund: str, meldung: str, app: str,
                folge: tuple = ()) -> dict:
        self._protokollieren(grund=grund, ok=False, weg="keiner", app=app,
                             folge=folge)
        return {"ok": False, "grund": grund, "weg": "keiner",
                "meldung": meldung}

    # -- Der eine Lauf --------------------------------------------------------

    def ausfuehren(self, roh: list[dict], *, app: str = "") -> dict:
        if self.verbraucht:
            # Kein zweiter Lauf, auch nicht mit gueltigem Auftrag: die Unit
            # ist one-shot, und ein Broker, der zweimal kann, ist es nicht.
            return {"ok": False, "grund": "verbraucht", "weg": "keiner",
                    "meldung": "dieser Broker hat seine eine Folge schon gehabt"}
        self.verbraucht = True

        # 1. Der harte Breaker, VOR allem anderen. Nicht nach der Allowlist
        #    und nicht nach der Freigabe: bei aktivem Sperrbildschirm wird
        #    abgelehnt, unabhaengig von allem anderen.
        if self.screensaver_aktiv is None:
            return self._absage(
                "kein_breaker",
                f"ohne Leser fuer {SCREENSAVER_DIENST} wird nichts "
                f"synthetisiert", app)
        try:
            gesperrt = bool(self.screensaver_aktiv())
        except Exception as fehler:
            # Kein Bus, keine Antwort -> zu. Bei einem Dienst, der Tasten
            # synthetisieren kann, ist Nichtstun der harmlose Fehlerfall.
            return self._absage("breaker_unlesbar",
                                f"{SCREENSAVER_DIENST}: {str(fehler)[:120]}",
                                app)
        if gesperrt:
            return self._absage(
                "sperrbildschirm",
                f"{SCREENSAVER_DIENST} meldet einen aktiven Sperrbildschirm",
                app)

        # 2. Die App-Allowlist. Keine Passwortfeld-Erkennung -- siehe Modulkopf.
        if app not in self.allowlist:
            return self._absage(
                "app_nicht_gelistet",
                f"{app!r} steht nicht auf der Allowlist "
                f"({len(self.allowlist)} Eintrag/Eintraege)", app)

        # 3. Die Folge, einmal geprueft, danach unveraenderlich.
        try:
            folge = folge_pruefen(roh)
        except InputFehler as fehler:
            return self._absage("folge", str(fehler), app)

        # 4. Der Regelweg: libei ueber das Portal `RemoteDesktop`.
        if self.portal_sitzung is not None:
            return self._ueber_portal(folge, app)

        if not self.ydotool_erlaubt:
            # Kein stiller Rueckfall. Ohne Portal wird nichts synthetisiert.
            return self._absage(
                "kein_portal",
                f"libei ueber {PORTAL_SCHNITTSTELLE} nicht verfuegbar und "
                f"ydotool ist abgeschaltet (Spike T-1.3: positioniert nicht, "
                f"verlangt dauerhaften privilegierten Dienst)", app, folge)

        # 5. Kollisionspruefung, BEVOR ein eigener Daemon startet: ein
        #    systemweit laufender `ydotoold` wird erkannt und die Aktion
        #    abgelehnt.
        fremd = self.fremder_dienst()
        if fremd is not None:
            return self._absage(
                "fremder_ydotoold",
                f"auf dieser Maschine laeuft bereits ein {YDOTOOLD} "
                f"(PID {fremd}) -- ein dauerhafter privilegierter "
                f"Eingabedienst, den dieser Broker nicht mitbenutzt", app,
                folge)

        return self._ueber_ydotool(folge, app)

    # -- Der Regelweg -----------------------------------------------------------

    def _ueber_portal(self, folge: tuple, app: str) -> dict:
        """libei ueber `RemoteDesktop`. Die Sitzung wird IMMER geschlossen."""
        sitzung = self.portal_sitzung()
        try:
            ergebnis = sitzung.senden(folge)
        finally:
            # Auch wenn das Senden wirft. Eine offene RemoteDesktop-Sitzung
            # ist eine stehende Erlaubnis, Eingaben zu synthetisieren; sie
            # ueberlebt hier keinen Fehlerfall.
            sitzung.schliessen()
        ok = bool(ergebnis)
        self._protokollieren(grund="" if ok else "portal", ok=ok, weg="libei",
                             app=app, folge=folge)
        return {"ok": ok, "grund": "" if ok else "portal", "weg": "libei",
                "ereignisse": len(folge)}

    # -- Der Rueckfall ------------------------------------------------------

    def _ueber_ydotool(self, folge: tuple, app: str) -> dict:
        """Nur mit ausdruecklicher Erlaubnis -- und im eigenen Prozessbaum."""
        kind = self.starten([YDOTOOLD], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
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
                    self._protokollieren(grund="ydotool", ok=False,
                                         weg="ydotool", app=app, folge=folge)
                    return {"ok": False, "grund": "ydotool", "weg": "ydotool",
                            "meldung": (getattr(lauf, "stderr", "") or "")[:160]}
            self._protokollieren(grund="", ok=True, weg="ydotool", app=app,
                                 folge=folge)
            return {"ok": True, "grund": "", "weg": "ydotool",
                    "ereignisse": len(folge),
                    "hinweis": "relative Bewegung, ungenau (Zeigerbeschleunigung)"}
        finally:
            # Der privilegierte Dienst bleibt NICHT stehen -- und er ist ein
            # Kind dieses Prozesses, kein Nachbar. Faellt der Broker vorher
            # weg, faellt er mit.
            try:
                kind.terminate()
                kind.wait(timeout=5)
            except Exception:
                pass
