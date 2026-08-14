"""T-7.3 -- der Pausenschalter. Anhalten, das man belegen kann.

**`ok` heisst nicht „der Aufruf gab 0 zurueck".** `systemctl stop` meldet
Erfolg, sobald der Prozess weg ist -- das ist nicht die Zusage. Die Zusage
ist: danach nimmt nichts mehr auf. Gemessen wird deshalb an `pw-dump`, und
eine NICHT messbare Stromzahl (`pw-dump` fehlt) ist ebenfalls kein Erfolg:
`None` und `0` sind hier zwei verschiedene Dinge.

**Die Pause schliesst den Strom, sie schaltet ihn nicht stumm** (Design
§4.2). Deshalb `systemctl stop` und keine Flagge im Prozess: ein stummer,
offener Strom ist ein Mikrofonsymbol in Plasma, das luegt.

**Beide Pfade gemeinsam.** Solange es keinen Tonmitschnitt gibt (T-7.4),
sind das der Archivdienst und die Augen -- der Bildstrom gehoert `eyes`, und
nur den Recorder zu stoppen liesse den Bildschirm weiter erfassen. Wer T-7.4
baut, ergaenzt die Ohren-Unit in `PAUSE_UNITS` und den Tonstrom in der
Messung; die Allowlist deckelt, was dieser Schalter ueberhaupt anfassen darf.

**Die automatische Pause loest aus, sie setzt nicht fort.** Eine Pause, die
sich selbst beendet, sobald die Konferenz weg ist, waere ein Mitschnitt, der
wieder anlaeuft, ohne dass jemand es gesagt hat. Fortsetzen ist ein
Tastendruck.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from daimon.ears.killswitch import AUFNAHME_KLASSE, _pw_dump_text
from daimon.eyes.killswitch import videostroeme

RECORDER_UNIT = "daimon-recorder.service"
EYES_UNIT = "daimon-eyes.service"

# Was dieser Schalter anfassen darf -- und mehr nicht. Dieselbe Bauart wie
# die Allowlist im Ohren-Kill-Switch: ein Schalter, der jede Unit stoppen
# kann, ist kein Schalter, sondern ein Werkzeug.
ERLAUBTE_UNITS = (RECORDER_UNIT, EYES_UNIT, "daimon-ears.service")

# Was die Pause tatsaechlich stoppt. Reihenfolge: erst der Schreiber, dann
# die Quelle -- sonst laeuft der Recorder noch, waehrend die Augen sterben,
# und schreibt die letzte Meldung.
PAUSE_UNITS = (RECORDER_UNIT, EYES_UNIT)

# Der Herzschlag: der Recorder frischt ihn je Runde auf, der Hub liest ihn.
# Eine blosse „ist da"-Datei bliebe nach einem SIGKILL liegen und zeigte
# Mitschnitt an, wo keiner ist -- das Alter macht sie ehrlich.
HERZSCHLAG_DATEI = "mitschnitt"
HERZSCHLAG_FRIST_S = 5.0

# Konferenzanwendungen, bei denen automatisch pausiert wird. `.desktop`-
# Kennungen und Fensterklassen, wie in der Denylist: hier steht die Vorgabe,
# ergaenzt wird in config/redaktion.yaml unter `konferenz`.
KONFERENZ_VORGABE = (
    "zoom", "us.zoom.Zoom",
    "teams", "com.microsoft.Teams", "teams-for-linux",
    "skype", "com.skype.Client",
    "discord", "com.discordapp.Discord",
    "jitsi", "org.jitsi.jitsi-meet",
    "webex", "Cisco Webex Meetings",
    "signal", "org.signal.Signal",
)


VIDEO_KLASSE = "Stream/Output/Video"


def bildschirmstroeme(*, dump_text: str | None = ...) -> int | None:
    """Laufende ScreenCast-Sitzungen. `None` = nicht messbar.

    GEMESSEN AM 14.08., und der Befund hat diese Funktion erst noetig
    gemacht: `daimon.eyes.killswitch.videostroeme` zaehlt Knoten mit dem
    Klientnamen `daimon-eyes` -- und davon gibt es KEINEN. Der Augendienst
    liest ueber den PipeWire-Dateideskriptor des Portals; in `pw-dump`
    erscheint nur der Knoten, den **kwin_wayland** dafuer erzeugt. Die Zahl
    war damit immer 0, auch bei laufender Erfassung.

    Was hier gezaehlt wird, ist deshalb `Stream/Output/Video` -- er entsteht
    mit der Portal-Sitzung und verschwindet mit ihr (nachgemessen: Augen an
    -> 1, Augen aus -> 0). Das ist die Groesse, die "jemand nimmt den
    Bildschirm auf" tatsaechlich abbildet.

    Sie zaehlt auch FREMDE Bildschirmaufnahmen mit. Fuer den Zweck hier ist
    das die richtige Richtung: bleibt nach der Pause eine Sitzung stehen,
    will man das sehen und nicht wegfiltern.
    """
    text = _pw_dump_text() if dump_text is ... else dump_text
    if not text:
        return None
    try:
        knoten = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(knoten, list):
        return None
    return sum(
        1 for k in knoten
        if isinstance(k, dict)
        and (k.get("info") or {}).get("props", {}).get("media.class")
        == VIDEO_KLASSE
    )


def _ist_aktiv(unit: str, lauf: Callable[..., Any]) -> bool:
    e = lauf(["systemctl", "--user", "is-active", unit],
             capture_output=True, text=True, timeout=10.0)
    return (e.stdout or "").strip() == "active"


def fremde_mikrofonstroeme(*, dump_text: str | None = ...,
                           eigene: Iterable[str] = ("daimon-ears", "dAImon")
                           ) -> int | None:
    """Aufnahmestroeme, die NICHT uns gehoeren. `None` = nicht messbar.

    Der Zweck ist die automatische Pause: hoert eine fremde Anwendung mit,
    sitzt der Nutzer sehr wahrscheinlich in einem Gespraech -- und dann
    schneiden wir es nicht mit. Die eigenen Stroeme zaehlen nicht, sonst
    pausierte der Mitschnitt sich selbst, sobald Push-to-Talk laeuft.
    """
    text = _pw_dump_text() if dump_text is ... else dump_text
    if not text:
        return None
    try:
        knoten = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(knoten, list):
        return None
    marken = tuple(e.lower() for e in eigene)
    treffer = 0
    for k in knoten:
        if not isinstance(k, dict):
            continue
        props = (k.get("info") or {}).get("props", {})
        if props.get("media.class") != AUFNAHME_KLASSE:
            continue
        wer = " ".join(str(props.get(f, "")) for f in
                       ("application.name", "node.name", "application.process.binary"))
        if any(m in wer.lower() for m in marken):
            continue
        treffer += 1
    return treffer


def ist_konferenz(klasse: str, liste: Iterable[str] = KONFERENZ_VORGABE) -> bool:
    """Fensterklasse gegen die Konferenzliste. Teiltreffer, absichtlich.

    `zoom` soll auch `Zoom Workplace` fangen. Die Richtung des Irrtums ist
    hier die harmlose: ein Fehltreffer pausiert den Mitschnitt, er startet
    keinen.
    """
    k = str(klasse).strip().lower()
    if not k:
        return False
    return any(e.strip().lower() in k or k in e.strip().lower()
               for e in liste if e.strip())


def herzschlag(runtime_dir: Path, *, uhr: Callable[[], float] = time.time
               ) -> None:
    """Der Recorder sagt „ich schneide mit", einmal je Runde."""
    datei = Path(runtime_dir) / HERZSCHLAG_DATEI
    datei.write_text(f"{uhr()}\n")


def herzschlag_loeschen(runtime_dir: Path) -> None:
    try:
        (Path(runtime_dir) / HERZSCHLAG_DATEI).unlink()
    except OSError:
        pass


def schneidet_mit(runtime_dir: Path, *,
                  uhr: Callable[[], float] = time.time,
                  frist_s: float = HERZSCHLAG_FRIST_S) -> bool:
    """Was das Sprite zeigen soll: der ECHTE Zustand, nicht der letzte Befehl.

    Ein Herzschlag, der aelter ist als die Frist, zaehlt nicht -- sonst
    leuchtete die Anzeige weiter, nachdem der Dienst mit SIGKILL gestorben
    ist.
    """
    try:
        stand = float((Path(runtime_dir) / HERZSCHLAG_DATEI).read_text().strip())
    except (OSError, ValueError):
        return False
    return 0.0 <= uhr() - stand < frist_s


def stoppe(units: Iterable[str] = PAUSE_UNITS, *,
           runtime_dir: Path | None = None,
           lauf: Callable[..., Any] = subprocess.run,
           video: Callable[[], int | None] = bildschirmstroeme,
           timeout_s: float = 10.0) -> dict:
    """Pausieren und BELEGEN, dass nichts mehr aufnimmt.

    `lauf` und `video` sind einspeisbar, damit der Schalter ohne systemd und
    ohne PipeWire pruefbar ist -- und damit der Fall „rc=0, Strom laeuft
    weiter" ueberhaupt herstellbar ist.
    """
    ziele = list(units)
    for unit in ziele:
        if unit not in ERLAUBTE_UNITS:
            raise ValueError(
                f"{unit!r} steht nicht in der Allowlist. Erlaubt: "
                + ", ".join(ERLAUBTE_UNITS))

    t0 = time.monotonic()
    vorher = video()
    rc = 0
    for unit in ziele:
        e = lauf(["systemctl", "--user", "stop", unit],
                 capture_output=True, text=True, timeout=timeout_s)
        rc = rc or int(e.returncode)

    nachher = video()
    noch_aktiv = [u for u in ziele if _ist_aktiv(u, lauf)]
    if runtime_dir is not None:
        herzschlag_loeschen(runtime_dir)

    # Drei Wege zu `ok=False`, und der mittlere ist der wichtige: ein
    # Rueckgabewert 0 mit laufendem Strom.
    meldung = ""
    if rc != 0:
        meldung = f"systemctl stop rc={rc}"
    elif noch_aktiv:
        meldung = "noch aktiv: " + ", ".join(noch_aktiv)
    elif nachher is None:
        meldung = "Bildschirmstroeme nicht messbar (pw-dump?) -- kein Nachweis"
    elif nachher > 0:
        meldung = f"{nachher} Bildschirmstrom/-stroeme laufen weiter"

    # Die Positivkontrolle gehoert IN den Bericht. War vorher kein Strom da,
    # sagt "nachher keiner" nichts -- dann traegt allein, dass die Units weg
    # sind. Am 14.08. live passiert: `ok: true`, `vorher: 0`, und der Beleg
    # war leer, ohne dass es jemandem aufgefallen waere.
    beleg = ("strom_gemessen" if vorher and nachher == 0
             else "nur_unit_zustand")

    return {"v": 1, "ok": not meldung, "units": ziele, "rc": rc,
            "bildschirmstroeme_vorher": vorher,
            "bildschirmstroeme_nachher": nachher, "beleg": beleg,
            "noch_aktiv": noch_aktiv, "meldung": meldung,
            "dauer_ms": round((time.monotonic() - t0) * 1000, 3)}


def fortsetzen(units: Iterable[str] = PAUSE_UNITS, *,
               lauf: Callable[..., Any] = subprocess.run,
               timeout_s: float = 30.0) -> dict:
    """Wieder anlaufen lassen. Rueckwaerts: erst die Quelle, dann der
    Schreiber -- ein Recorder ohne Augen haette nichts zu tun."""
    ziele = list(units)
    for unit in ziele:
        if unit not in ERLAUBTE_UNITS:
            raise ValueError(f"{unit!r} steht nicht in der Allowlist")
    rc = 0
    for unit in reversed(ziele):
        e = lauf(["systemctl", "--user", "start", unit],
                 capture_output=True, text=True, timeout=timeout_s)
        rc = rc or int(e.returncode)
    aktiv = [u for u in ziele if _ist_aktiv(u, lauf)]
    return {"v": 1, "ok": rc == 0 and len(aktiv) == len(ziele), "rc": rc,
            "aktiv": aktiv, "units": ziele}
