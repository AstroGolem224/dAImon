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
# BERICHTIGT am 14.08. Hier stand: "nur der eigene Strom zaehlt", gefiltert
# auf `capture.KLIENT_NAME` -- mit der Begruendung, die Arbeitsflaeche halte
# dauerhaft zwei Videostroeme. Beides ist falsch, und zusammen ergab es eine
# Zahl, die IMMER 0 war:
#
#   * EINEN Knoten namens `daimon-eyes` gibt es nicht. Der Augendienst liest
#     ueber den PipeWire-Dateideskriptor des PORTALS; in `pw-dump` erscheint
#     nur der Knoten, den kwin_wayland fuer die ScreenCast-Sitzung erzeugt.
#   * Dieser Knoten bleibt auch nicht "fuer immer" stehen. Zweimal gemessen
#     am 14.08., zu zwei Tageszeiten: Augen an -> genau ein
#     `Stream/Output/Video` von kwin_wayland, Augen aus -> NULL Video-Knoten.
#
# "0 Stroeme nach dem Schalter" war damit seit T-5.12 keine Aussage: die Zahl
# war schon vorher 0. Gezaehlt wird jetzt die ScreenCast-Sitzung selbst.
# Sie zaehlt auch FREMDE Bildschirmaufnahmen mit -- fuer einen Kill-Switch
# ist das die richtige Richtung: bleibt nach dem Abschalten eine Sitzung
# stehen, will man das sehen und nicht wegfiltern. Ob sie unsere war, sagt
# die Positivkontrolle (`beleg` in `stoppe`).
SCREENCAST_KLASSE = "Stream/Output/Video"


def _pw_dump_text(timeout_s: float = 5.0) -> str | None:
    try:
        lauf = subprocess.run(["pw-dump"], capture_output=True, text=True,
                              timeout=timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    return lauf.stdout if lauf.returncode == 0 else None


def videostroeme(*, dump_text: str | None = ...) -> int | None:
    """Wie viele ScreenCast-Sitzungen laufen. `None` = nicht messbar.

    `None` und `0` duerfen nie verwechselt werden: das eine heisst „niemand
    sieht zu", das andere „wir wissen es nicht".

    Was gezaehlt wird und warum nicht der Klientname: siehe oben bei
    `SCREENCAST_KLASSE`.
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
        == SCREENCAST_KLASSE)


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

    # Die Positivkontrolle gehoert IN den Bericht. War vorher keine
    # ScreenCast-Sitzung messbar, sagt "nachher keine" nichts -- dann traegt
    # allein, dass die Unit weg ist. Ohne dieses Feld sieht ein leerer
    # Nachweis genauso aus wie ein gefuehrter, und genau das hat die alte
    # Messung zwei Wochen lang getan.
    beleg = "strom_gemessen" if vorher and nachher == 0 else "nur_unit_zustand"

    return {"v": 1, "ok": ok, "unit": unit, "rc": rc, "war_aktiv": war_aktiv,
            "videostroeme_vorher": vorher, "videostroeme_nachher": nachher,
            "beleg": beleg,
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
