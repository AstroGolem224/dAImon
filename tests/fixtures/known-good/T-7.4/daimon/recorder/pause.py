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
EARS_UNIT = "daimon-ears.service"

# Was dieser Schalter anfassen darf -- und mehr nicht. Dieselbe Bauart wie
# die Allowlist im Ohren-Kill-Switch: ein Schalter, der jede Unit stoppen
# kann, ist kein Schalter, sondern ein Werkzeug.
ERLAUBTE_UNITS = (RECORDER_UNIT, EYES_UNIT, EARS_UNIT)

# Was die Pause tatsaechlich stoppt. Reihenfolge: erst der Schreiber, dann
# die Quelle -- sonst laeuft der Recorder noch, waehrend die Augen sterben,
# und schreibt die letzte Meldung.
PAUSE_UNITS = (RECORDER_UNIT, EYES_UNIT, EARS_UNIT)

# Der Herzschlag: der Recorder frischt ihn je Runde auf, der Hub liest ihn.
# Eine blosse „ist da"-Datei bliebe nach einem SIGKILL liegen und zeigte
# Mitschnitt an, wo keiner ist -- das Alter macht sie ehrlich.
HERZSCHLAG_DATEI = "mitschnitt"
HERZSCHLAG_FRIST_S = 5.0

# Derselbe Mechanismus fuer die Augen, und zwar aus einem gemessenen Grund.
# Bis zum 16.08. fragte der Recorder `bildschirmstroeme()`, ob wahrgenommen
# wird -- und die zaehlt JEDEN `Stream/Output/Video`, auch fremde. Fuer den
# Kill-Switch ist das Mitzaehlen richtig (bleibt nach dem Abschalten eine
# fremde Sitzung stehen, will man das sehen). Fuer die Frage „sehen UNSERE
# Augen gerade?" ist es falsch: eine laufende Bildschirmfreigabe in einer
# Konferenz beantwortet sie mit „ja", auch wenn der Augendienst gestoppt ist.
#
# Der Augendienst schreibt deshalb sein eigenes Lebenszeichen, und der
# Recorder liest es. Beide haben `%t/daimon` als `RuntimeDirectory`.
WAHRNEHMUNG_DATEI = "wahrnehmung"

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


def bildschirmstroeme(*, dump_text: str | None = ...) -> int | None:
    """Laufende ScreenCast-Sitzungen. `None` = nicht messbar.

    Eine Weiterleitung und kein zweites Verfahren: die Messung gehoert dem
    Augen-Kill-Switch (T-5.12), sie steht dort und ist dort begruendet.
    Bis zum 14.08. zaehlte sie den Klientnamen `daimon-eyes` -- den es nicht
    gibt -- und war deshalb immer 0; diese Funktion war die Umgehung davon.
    Nachdem die Quelle berichtigt ist, waere ein eigenes Verfahren hier eine
    zweite Wahrheit.
    """
    return videostroeme(dump_text=dump_text)


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


def herzschlag(runtime_dir: Path, *, uhr: Callable[[], float] = time.time,
               datei: str = HERZSCHLAG_DATEI) -> None:
    """„Ich arbeite", einmal je Runde. Der Recorder fuer den Mitschnitt, der
    Augendienst fuer die Wahrnehmung."""
    (Path(runtime_dir) / datei).write_text(f"{uhr()}\n")


def herzschlag_loeschen(runtime_dir: Path, *,
                        datei: str = HERZSCHLAG_DATEI) -> None:
    try:
        (Path(runtime_dir) / datei).unlink()
    except OSError:
        pass


def _frisch(runtime_dir: Path, datei: str, uhr: Callable[[], float],
            frist_s: float) -> bool:
    try:
        stand = float((Path(runtime_dir) / datei).read_text().strip())
    except (OSError, ValueError):
        return False
    return 0.0 <= uhr() - stand < frist_s


def schneidet_mit(runtime_dir: Path, *,
                  uhr: Callable[[], float] = time.time,
                  frist_s: float = HERZSCHLAG_FRIST_S) -> bool:
    """Was das Sprite zeigen soll: der ECHTE Zustand, nicht der letzte Befehl.

    Ein Herzschlag, der aelter ist als die Frist, zaehlt nicht -- sonst
    leuchtete die Anzeige weiter, nachdem der Dienst mit SIGKILL gestorben
    ist.
    """
    return _frisch(runtime_dir, HERZSCHLAG_DATEI, uhr, frist_s)


def augen_sehen(runtime_dir: Path, *,
                uhr: Callable[[], float] = time.time,
                frist_s: float = HERZSCHLAG_FRIST_S) -> bool:
    """Sehen die Augen gerade? Gemessen an IHREM Lebenszeichen.

    Kein Herzschlag heisst hier AUS, und das ist die richtige Richtung --
    anders als bei einer Messung, die der Prozess technisch nicht ausfuehren
    kann (`systemctl --user` im Sandkasten des Recorders, Befund vom 14.08.).
    Diese hier gelingt immer: es ist eine Datei im eigenen
    Laufzeitverzeichnis. Dass niemand hineinschreibt, ist deshalb eine
    Aussage und kein Werkzeugfehler -- niemand sieht hin.

    Nach einem SIGKILL des Augendienstes verfaellt sie von selbst; nach dem
    Kill-Switch ist sie binnen `frist_s` alt, und `beenden()` raeumt sie
    ohnehin sofort weg.
    """
    return _frisch(runtime_dir, WAHRNEHMUNG_DATEI, uhr, frist_s)


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
