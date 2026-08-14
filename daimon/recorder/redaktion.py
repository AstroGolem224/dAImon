"""T-7.2 -- Redaktion VOR dem Schreiben. Was nicht auf die Platte soll,
kommt gar nicht erst hin.

**Die Reihenfolge ist der ganze Task.** Screenpipe redigiert im Hintergrund
und laesst die Rohdaten zuerst auf die Platte; wer dann den Prozess abwuergt
oder die Datei vorher liest, hat den ungefilterten Stand. Hier laeuft die
Pruefung vor jedem `INSERT`, und sie sitzt im Recorder, weil der der einzige
Prozess mit Schreibrecht ist: eine Pruefung in den Augen liesse sich durch
einen zweiten Absender umgehen, diese hier nicht.

**Die Anwendung wird ueber ihre `.desktop`-Kennung erkannt, nicht ueber den
Fenstertitel.** KWin liefert `resource_class`; die wird gegen die
installierten `.desktop`-Dateien nachgeschlagen (`StartupWMClass`, sonst der
Dateiname). Ein Programm, das eine fremde Anwendung im TITEL fuehrt, kann
sich damit weder in die Erfassung hinein- noch aus ihr herausluegen -- der
Weg ueber den Titel aus T-3.12 ist hier ausdruecklich nicht genommen.

**Ein Fenster ohne Kennung wird gesperrt, nicht durchgelassen.** Das ist die
unbequeme Richtung: eine Anwendung, die keine `resource_class` meldet, wird
nicht mitgeschnitten, und das faellt auf. Die andere Richtung faellt erst
auf, wenn etwas auf der Platte steht, das dort nicht hingehoert.

**Vier Gruende fuehren auf `transient`**, und `transient` heisst „nur im
Arbeitsspeicher" -- der Archivspeicher schreibt eine solche Zeile gar nicht:
Denylist, DRM, Privatmodus, abgeschaltete Wahrnehmung.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from daimon.recorder.store import STUFE_REDACTED, STUFE_TRANSIENT

GRUND_DENYLIST = "denylist"
GRUND_DRM = "drm"
GRUND_PRIVAT = "privatmodus"
GRUND_WAHRNEHMUNG_AUS = "wahrnehmung_aus"
GRUND_UNBEKANNT = "kennung_fehlt"

# Der Privatmodus liegt im Laufzeitverzeichnis und nicht im Zustand: er soll
# einen Neustart NICHT ueberleben. Ein Modus, der still weiterlaeuft, waere
# genauso falsch wie einer, der still endet -- aber von beiden ist der
# vergessliche der harmlose: er schneidet wieder mit, sichtbar am Sprite.
PRIVAT_DATEI = "privatmodus"

# Wie lange ein Blick auf den Unit-Zustand gilt. `lampe()` startet einen
# Prozess; das je Meldung zu tun waere teurer als das Schreiben selbst.
WAHRNEHMUNG_CACHE_S = 5.0


@dataclass(frozen=True)
class Urteil:
    stufe: str
    grund: str = ""

    @property
    def schreibt(self) -> bool:
        return self.stufe != STUFE_TRANSIENT


def desktop_kennungen(
        verzeichnisse: Iterable[Path] | None = None) -> dict[str, str]:
    """`resource_class` (klein) -> `.desktop`-Kennung.

    Gelesen wird `StartupWMClass=` und der Dateiname. Beides, weil weder das
    eine noch das andere allein reicht: `StartupWMClass` fehlt oft, und der
    Dateiname trifft nur, wenn die Anwendung ihn auch als Klasse meldet.
    """
    if verzeichnisse is None:
        wurzeln = [Path(os.environ.get(
            "XDG_DATA_HOME", Path.home() / ".local" / "share"))]
        wurzeln += [Path(p) for p in os.environ.get(
            "XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if p]
        verzeichnisse = [w / "applications" for w in wurzeln]

    karte: dict[str, str] = {}
    for verzeichnis in verzeichnisse:
        try:
            eintraege = sorted(Path(verzeichnis).glob("*.desktop"))
        except OSError:
            continue
        for datei in eintraege:
            kennung = datei.stem
            karte.setdefault(kennung.lower(), kennung)
            try:
                text = datei.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for zeile in text.splitlines():
                if zeile.startswith("StartupWMClass="):
                    wmclass = zeile.split("=", 1)[1].strip()
                    if wmclass:
                        karte.setdefault(wmclass.lower(), kennung)
                    break
    return karte


def privat_bis(runtime_dir: Path) -> float:
    """Bis wann der Privatmodus laeuft. 0.0 = laeuft nicht."""
    try:
        return float((runtime_dir / PRIVAT_DATEI).read_text().strip())
    except (OSError, ValueError):
        return 0.0


def privat_setzen(runtime_dir: Path, dauer_s: float, *,
                  uhr: Callable[[], float] = time.time) -> float:
    """Privatmodus fuer `dauer_s` Sekunden. Gibt den Ablauf zurueck.

    Zeitlich begrenzt und nicht als Schalter: ein Privatmodus, den man
    einschaltet und vergisst, ist ein abgeschalteter Mitschnitt mit einem
    beruhigenden Namen.
    """
    ablauf = uhr() + max(0.0, float(dauer_s))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    datei = runtime_dir / PRIVAT_DATEI
    datei.write_text(f"{ablauf}\n")
    os.chmod(datei, 0o600)
    return ablauf


class Redaktion:
    """Der Torwaechter vor `Archiv.schreiben`."""

    def __init__(self, *, denylist: Iterable[str] = (),
                 runtime_dir: Path,
                 kennungen: dict[str, str] | None = None,
                 wahrnehmung_an: Callable[[], bool] | None = None,
                 uhr: Callable[[], float] = time.time) -> None:
        self.denylist = {s.strip().lower() for s in denylist if s.strip()}
        self.runtime_dir = Path(runtime_dir)
        self.kennungen = (desktop_kennungen() if kennungen is None
                          else kennungen)
        self._wahrnehmung_an = wahrnehmung_an
        self._uhr = uhr
        self._wahrnehmung_stand: tuple[float, bool] | None = None

    # -- Kennung -----------------------------------------------------------

    def kennung(self, klasse: str) -> str:
        """`.desktop`-Kennung zu einer Fensterklasse, sonst die rohe Klasse.

        Die rohe Klasse als Rueckfall und nicht ein leerer String: eine
        Anwendung ohne `.desktop`-Datei soll sich trotzdem sperren lassen.
        """
        roh = str(klasse).strip()
        if not roh:
            return ""
        return self.kennungen.get(roh.lower(), roh)

    # -- Wahrnehmung -------------------------------------------------------

    def wahrnehmung_an(self) -> bool:
        if self._wahrnehmung_an is None:
            return True
        jetzt = self._uhr()
        if (self._wahrnehmung_stand is not None
                and jetzt - self._wahrnehmung_stand[0] < WAHRNEHMUNG_CACHE_S):
            return self._wahrnehmung_stand[1]
        wert = bool(self._wahrnehmung_an())
        self._wahrnehmung_stand = (jetzt, wert)
        return wert

    # -- Das Urteil --------------------------------------------------------

    def urteil_ton(self, *, stufe: str = STUFE_REDACTED) -> Urteil:
        """T-7.4: das Urteil fuer ein Transkript. Kein Fenster, keine Klasse.

        Die Anwendungs-Denylist greift hier nicht, und das ist kein Versehen:
        sie sperrt FENSTER, und ein gesprochener Satz hat keines. Wer sie
        trotzdem anlegte -- „der Passwortmanager war im Fokus, also nichts
        aufzeichnen" --, koppelte den Ton an den Bildschirm und liesse ihn
        genau dann durch, wenn kein Fenster vorn ist.

        Was greift: der Privatmodus und die abgeschaltete Wahrnehmung. Die
        Pause aus T-7.3 braucht hier nichts, denn sie stoppt den Dienst --
        und ein gestoppter Recorder nimmt keine Meldung mehr an.
        """
        if self._uhr() < privat_bis(self.runtime_dir):
            return Urteil(STUFE_TRANSIENT, GRUND_PRIVAT)
        if not self.wahrnehmung_an():
            return Urteil(STUFE_TRANSIENT, GRUND_WAHRNEHMUNG_AUS)
        return Urteil(stufe)

    def urteil(self, klasse: str, *, drm: bool = False,
               stufe: str = STUFE_REDACTED) -> Urteil:
        """Reihenfolge wie in Design 4.4: Denylist zuerst, dann DRM.

        Die Modi stehen danach, weil sie den ganzen Betrieb betreffen und
        nicht ein einzelnes Fenster -- und weil ein gesperrtes Fenster auch
        dann gesperrt bleibt, wenn jemand den Privatmodus abschaltet.
        """
        k = self.kennung(klasse)
        if not k:
            return Urteil(STUFE_TRANSIENT, GRUND_UNBEKANNT)
        if k.lower() in self.denylist:
            return Urteil(STUFE_TRANSIENT, GRUND_DENYLIST)
        if drm:
            return Urteil(STUFE_TRANSIENT, GRUND_DRM)
        if self._uhr() < privat_bis(self.runtime_dir):
            return Urteil(STUFE_TRANSIENT, GRUND_PRIVAT)
        if not self.wahrnehmung_an():
            return Urteil(STUFE_TRANSIENT, GRUND_WAHRNEHMUNG_AUS)
        return Urteil(stufe)
