"""T-5.12 -- der Kill-Switch der Augen.

Dieselbe Lehre wie beim Ohren-Schalter (T-3.15), und sie ist teuer bezahlt:
**das Ergebnis haengt am STROM, nicht am Rueckgabewert.** `systemctl --user
stop` liefert 0, sobald der Prozess weg ist. Das ist nicht die Zusage. Die
Zusage ist: danach sieht nichts mehr. Ein Dienst, der beim Beenden seinen
PipeWire-Strom nicht schliesst, hinterlaesst ihn beim Server -- und `rc=0`
haette das bestaetigt.

Aus demselben Grund ist eine NICHT GEMESSENE Stromzahl (`None`) kein Erfolg.
Wer ein fehlendes `pw-dump` als „null Stroeme" liest, macht aus einem
Werkzeugfehler eine Sicherheitsaussage.

Drei Dinge kommen gegenueber den Ohren dazu:

**Der Kontextspeicher wird geleert, und zwar GLOB-FREI geprueft.** Ein
`ls *.json` uebersieht eine Datei mit einem Punkt am Anfang, und genau die
bliebe dann liegen. Gezaehlt wird ueber `os.scandir` -- jeder Eintrag, ohne
Muster.

**Die Portal-Sitzung wird geschlossen, nicht nur die Kette angehalten.** Eine
Kette auf `NULL` beendet die Frames; die SITZUNG bleibt offen, und mit ihr
die Erlaubnis. Wer nur die Kette anhaelt, hat das Sehen abgeschaltet und den
Ausweis behalten.

**Der Strom allein beweist nichts.** Die Kette baut sich je Frame ab (T-5.3);
zwischen zwei Blicken laeuft kein Strom, und ein Zaehler von 0 kam am 13.08.
auch dann heraus, waehrend der Dienst munter weiterlas. Deshalb wird ZUSAETZLICH
gefragt, ob die Unit noch aktiv ist -- das ist die erste Frage des Pruefstands,
und sie wurde vorher nicht gestellt.

**Die Tray-Lampe spiegelt den echten Unit-Zustand.** Nicht das, was dieser
Schalter zuletzt getan hat: eine Lampe, die den letzten BEFEHL zeigt, leuchtet
gruen, waehrend der Dienst nach einem Absturz wieder hochgekommen ist.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from daimon.common.config import state_dir

EYES_UNIT = "daimon-eyes.service"
# Dieselbe Menge wie beim Ohren-Schalter und wie WAHRNEHMUNG_ZIELE im Hub:
# der schlimmste Missbrauch soll „Wahrnehmung geht aus" sein und nicht „der
# Auth-Agent geht aus".
ERLAUBTE_UNITS = ("daimon-ears.service", EYES_UNIT)

# media.class eines Bildschirmstroms. Ein `Video/Source` ist das GERAET und
# steht immer da; nur ein `Stream/Output/Video` heisst, dass jemand mitliest.
# Der Portal-Knoten erscheint als Ausgang, weil er zu uns hin sendet.
VIDEO_KLASSEN = ("Stream/Output/Video", "Stream/Input/Video")

# NUR der eigene Strom zaehlt. Gemessen am 12.08.: die Arbeitsflaeche haelt
# dauerhaft zwei Videostroeme (`kwin_wayland` als Ausgang, `plasmashell` als
# Eingang), die mit dAImon nichts zu tun haben. Ein systemweiter Zaehler
# stuende nach dem Abschalten fuer immer auf "laeuft weiter" -- und ein
# Pruefer, der immer rot ist, ist so wertlos wie einer, der immer gruen ist.
# Unser Strom traegt den Namen aus `capture.KLIENT_NAME`.
from daimon.eyes.capture import KLIENT_NAME


def _pw_dump_text(timeout_s: float = 5.0) -> str | None:
    try:
        lauf = subprocess.run(["pw-dump"], capture_output=True, text=True,
                              timeout=timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    return lauf.stdout if lauf.returncode == 0 else None


def videostroeme(*, dump_text: str | None = ..., klient: str = KLIENT_NAME
                 ) -> int | None:
    """Wie viele EIGENE Bildschirmstroeme laufen. `None` = nicht messbar.

    `None` und `0` duerfen nie verwechselt werden: das eine heisst „niemand
    sieht zu", das andere „wir wissen es nicht".

    Gezaehlt wird nur, was `klient` heisst. Die Begruendung steht oben bei
    `KLIENT_NAME`: die Arbeitsflaeche selbst haelt zwei Videostroeme, die
    nie verschwinden.
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
        in VIDEO_KLASSEN
        and klient in str((k.get("info") or {}).get("props", {})
                          .get("node.name", "")))


def kontextdateien(verzeichnis: Path | None = None) -> int:
    """Wie viele Dateien liegen noch im Kontextverzeichnis. GLOB-FREI.

    `os.scandir` und kein `glob("*.json")`: ein Muster uebersieht eine Datei
    mit einem Punkt am Anfang, und genau die bliebe dann liegen. Ein
    Verzeichnis, das es nicht gibt, hat null Dateien -- das ist hier kein
    Messfehler, sondern der Zielzustand.
    """
    ziel = Path(verzeichnis or (state_dir() / "context"))
    try:
        with os.scandir(ziel) as eintraege:
            return sum(1 for e in eintraege if e.is_file())
    except FileNotFoundError:
        return 0
    except OSError:
        return -1                       # nicht lesbar ist NICHT leer


def _ist_aktiv(unit: str, lauf: Callable[..., Any]) -> bool:
    e = lauf(["systemctl", "--user", "is-active", unit],
             capture_output=True, text=True, timeout=10.0)
    return (e.stdout or "").strip() == "active"


def lampe(unit: str = EYES_UNIT,
          lauf: Callable[..., Any] = subprocess.run) -> str:
    """Was die Tray-Lampe zeigen soll: der ECHTE Unit-Zustand.

    Nicht das, was dieser Schalter zuletzt getan hat. Eine Lampe, die den
    letzten Befehl zeigt, leuchtet gruen, waehrend der Dienst nach einem
    Absturz laengst wieder hochgekommen ist.
    """
    try:
        return "an" if _ist_aktiv(unit, lauf) else "aus"
    except Exception:
        # Unbekannt ist nicht „aus". Eine Lampe, die bei einem Werkzeugfehler
        # Entwarnung gibt, ist schlimmer als gar keine.
        return "unbekannt"


def stoppe(unit: str = EYES_UNIT, *, timeout_s: float = 10.0,
           kontext: Path | None = None,
           lauf: Callable[..., Any] = subprocess.run,
           stroeme: Callable[[], int | None] = videostroeme,
           speicher: Any = None, sitzung: Any = None) -> dict:
    """Die Augen abschalten und BELEGEN, dass sie aus sind.

    `lauf`, `stroeme` und `speicher` sind injizierbar, damit der Schalter ohne
    systemd, ohne PipeWire und ohne Portal pruefbar ist -- und damit der Fall
    „rc=0, Strom laeuft weiter" ueberhaupt herstellbar ist.
    """
    if unit not in ERLAUBTE_UNITS:
        raise ValueError(
            f"{unit!r} steht nicht in der Allowlist. Erlaubt: "
            + ", ".join(ERLAUBTE_UNITS))

    begonnen = time.monotonic()
    war_aktiv = _ist_aktiv(unit, lauf)
    vorher = stroeme()

    e = lauf(["systemctl", "--user", "stop", unit],
             capture_output=True, text=True, timeout=timeout_s)
    rc = int(getattr(e, "returncode", 1))

    # Die Portal-Sitzung wird geschlossen, wenn dieser Schalter IM Dienst
    # laeuft und sie kennt. Laeuft er daneben, erledigt das der Dienst beim
    # SIGTERM -- und ob es geschehen ist, entscheidet ohnehin die Strommessung
    # weiter unten und nicht diese Zeile. Eine Kette auf NULL beendet nur die
    # Frames; die SITZUNG bliebe offen, und mit ihr die Erlaubnis.
    sitzung_zu = None
    if sitzung is not None:
        try:
            sitzung.widerrufen()
            sitzung_zu = True
        except Exception:
            sitzung_zu = False

    # Der Speicher wird geleert, AUCH wenn der Stopp scheitert. Ein Dienst,
    # der sich nicht beenden laesst, ist genau der Fall, in dem der schon
    # gesammelte Bildschirmtext nicht liegen bleiben soll.
    geleert = None
    if speicher is not None:
        try:
            geleert = int(speicher.leeren())
        except Exception:
            geleert = -1

    nachher = stroeme()
    dateien = kontextdateien(kontext)
    noch_aktiv = _ist_aktiv(unit, lauf)
    dauer_ms = round((time.monotonic() - begonnen) * 1000, 3)

    if rc != 0:
        ok, meldung = False, (getattr(e, "stderr", "") or "").strip()[:200]
    elif noch_aktiv:
        # Am 13.08. am laufenden Dienst gemessen: die Strommessung ALLEIN
        # reicht nicht. Die Kette baut sich je Frame ab (T-5.3), zwischen zwei
        # Blicken gibt es also gar keinen Strom -- ein Zaehler von 0 kam
        # deshalb auch heraus, waehrend der Dienst munter weiterlas. Die
        # erste Frage des Pruefstands lautet "Unit inaktiv", und die wurde
        # vorher nicht gestellt.
        ok, meldung = False, "Unit ist nach dem Stopp weiter aktiv"
    elif nachher is None:
        ok, meldung = False, "Videostroeme nicht messbar (pw-dump fehlt?)"
    elif nachher > 0:
        ok, meldung = False, f"{nachher} Videostrom/-stroeme laufen weiter"
    elif dateien != 0:
        ok, meldung = False, (
            f"{dateien} Datei(en) im Kontextverzeichnis" if dateien > 0
            else "Kontextverzeichnis nicht lesbar")
    else:
        ok, meldung = True, ""

    return {"v": 1, "ok": ok, "unit": unit, "rc": rc, "war_aktiv": war_aktiv,
            "videostroeme_vorher": vorher, "videostroeme_nachher": nachher,
            "kontextdateien": dateien, "geleert": geleert,
            "noch_aktiv": noch_aktiv,
            "sitzung_geschlossen": sitzung_zu,
            "lampe": lampe(unit, lauf), "dauer_ms": dauer_ms,
            "meldung": meldung}


def main(argv: list[str] | None = None) -> int:
    """`python -m daimon.eyes.killswitch` -- der Weg, den der Hotkey nimmt."""
    from daimon.eyes.context import Kontextspeicher

    speicher = Kontextspeicher()
    speicher.laden()
    bericht = stoppe(speicher=speicher)
    print(json.dumps(bericht, ensure_ascii=False, sort_keys=True))
    return 0 if bericht["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
