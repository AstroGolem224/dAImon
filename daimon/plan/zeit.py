"""T-8.2 -- „in 20 Minuten" wird ein Zeitstempel. Deterministisch, ohne Modell.

Warum kein Modell: ein Sprachmodell, das „morgen um 8" parst, hat an einem
Schaltjahr- oder Sommerzeit-Rand einen schlechten Tag -- und niemand merkt
es, weil die Antwort fliessend klingt. Ein endlicher Parser hat eine
endliche Fehlermenge, und die steht in `tests/test_plan_zeit.py`.

Was dieser Parser nicht kann, kann er nicht: `parse` wirft `ValueError`,
statt zu raten. Ein geratener Zeitpunkt ist schlimmer als eine Rueckfrage.

Die Uhr ist injizierbar (`jetzt`), wie ueberall in diesem Projekt. Alles
rechnet in Epochensekunden; lokale Uhrzeit/DST geht ueber
`time.localtime`/`mktime` -- dieselbe Quelle, die der Rest des Systems
benutzt.
"""
from __future__ import annotations

import datetime
import re
import time
from typing import Callable

# "in 45 minuten", "in 2 stunden", "in 90 min", "in 3h" -- Zahl vor Einheit.
_IN = re.compile(
    r"^\s*in\s+(\d+(?:[.,]\d+)?)\s*"
    r"(min(?:uten?)?|mins?|m|stunden?|std|h|sek(?:unden?)?|s)\s*$", re.IGNORECASE)

# "um 18:30", "um 8", "um 8 uhr", "um 8 uhr 15". Heute -- oder morgen,
# wenn die Zeit heute schon vorbei ist.
_UM = re.compile(
    r"^\s*(?:um\s+)?(\d{1,2})(?::(\d{2})|\s*uhr(?:\s*(\d{1,2}))?)?\s*$",
    re.IGNORECASE)

# "morgen um 8", "morgen 18:30".
_MORGEN = re.compile(
    r"^\s*morgen\s+(?:um\s+)?(\d{1,2})(?::(\d{2})|\s*uhr(?:\s*(\d{1,2}))?)?\s*$",
    re.IGNORECASE)

# "2026-08-24 08:00", "2026-08-24T08:00", "2026-08-24".
_ISO = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[Tt ](\d{1,2}):(\d{2}))?\s*$")

_SEKUNDE = 1.0
_MINUTE = 60.0
_STUNDE = 3600.0
_TAG = 86400.0

# Die Obergrenze eines Zeitpunkts: zehn Jahre. Sie ist keine Bequemlichkeit,
# sondern die Schranke vor `time.localtime` -- die wirft bei riesigen
# Zeitstempeln `OSError(75)`, und ein Eintrag, der einmal in der Datenbank
# steht, vergiftet danach jedes `--liste`, ueber Neustarts hinweg. Die Zusage
# steht hier, weil hier der Wert entsteht.
MAX_VORLAUF_S = 10 * 365.25 * _TAG


class ZeitFehler(ValueError):
    """Der Text beschreibt keinen Zeitpunkt -- raten ist keine Option."""


def _lokal_zu_epoche(jahr: int, monat: int, tag: int,
                     stunde: int, minute: int) -> float:
    """Lokalzeit -> Epoche, ueber dieselbe libc, die das System fragt.

    `mktime` mit `tm_isdst=-1` laesst die libc die Sommerzeit entscheiden;
    in der nichtexistierenden Stunde der Umstellung (02:30 am letzten
    Maerzsonntag) liefert sie den naechsten gueltigen Punkt zurueck. Das ist
    akzeptabel -- es ist dieselbe Antwort, die jede Uhr auf diesem System
    geben wuerde.

    Das Datum wird VORHER gegen `datetime.date` geprueft: `mktime`
    normalisiert still, und der 31. Februar wurde damit zum 3. Maerz. Der
    `except`-Zweig darunter hat das nie gesehen, weil gar nichts flog.
    """
    try:
        datetime.date(jahr, monat, tag)
    except ValueError as f:
        raise ZeitFehler(f"Ungueltiges Datum: {jahr}-{monat}-{tag} "
                         f"{stunde}:{minute}") from f
    try:
        return float(time.mktime(
            (jahr, monat, tag, stunde, minute, 0, 0, 0, -1)))
    except (ValueError, OverflowError, OSError) as f:
        raise ZeitFehler(f"Ungueltiges Datum: {jahr}-{monat}-{tag} "
                         f"{stunde}:{minute}") from f


def _tag_nach(nun: float) -> datetime.date:
    """Der morgige Kalendertag. NICHT `nun + 86400`: in der Nacht der
    Zeitumstellung ist ein Tag 23 oder 25 Stunden lang, und aus „morgen um 8"
    wurde damit uebermorgen."""
    try:
        return datetime.date.fromtimestamp(nun) + datetime.timedelta(days=1)
    except (ValueError, OverflowError, OSError) as f:
        raise ZeitFehler(f"Zeitstempel ausserhalb des Kalenders: {nun}") from f


def _in_reichweite(ziel: float, nun: float) -> float:
    """Ein Zeitpunkt, den `time.localtime` noch darstellen kann."""
    if ziel > nun + MAX_VORLAUF_S:
        raise ZeitFehler("Zeitpunkt liegt weiter als zehn Jahre in der Zukunft")
    return ziel


def parse(text: str, *, jetzt: Callable[[], float] = time.time) -> float:
    """Ein deutscher Zeitpunkt-Schnipsel -> Epochensekunden (Zukunft).

    Versteht: „in X minuten/stunden/sekunden", „um 18:30", „um 8",
    „morgen um 8", „2026-08-24 08:00". Alles andere: `ZeitFehler`.

    Vergangenheit ist kein Ergebnis: „um 8" um 9 Uhr morgens meint morgen
    um 8. Wer das nicht meint, sagt ein Datum.
    """
    if not isinstance(text, str) or not text.strip():
        raise ZeitFehler("Leerer Zeitpunkt")
    roh = text.strip().lower()
    nun = float(jetzt())

    m = _IN.match(roh)
    if m:
        wert = float(m.group(1).replace(",", "."))
        einheit = m.group(2)
        if einheit.startswith(("min", "m")) and not einheit.startswith("mo"):
            faktor = _MINUTE
        elif einheit.startswith(("st", "h")):
            faktor = _STUNDE
        else:
            faktor = _SEKUNDE
        if wert <= 0:
            raise ZeitFehler("Eine Dauer muss groesser als null sein")
        return _in_reichweite(nun + wert * faktor, nun)

    m = _MORGEN.match(roh)
    if m:
        stunde = int(m.group(1))
        minute = int(m.group(2) or m.group(3) or 0)
        _uhrzeit_pruefen(stunde, minute)
        d = _tag_nach(nun)
        return _in_reichweite(
            _lokal_zu_epoche(d.year, d.month, d.day, stunde, minute), nun)

    m = _ISO.match(roh)
    if m:
        jahr, monat, tag = int(m.group(1)), int(m.group(2)), int(m.group(3))
        stunde = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        _uhrzeit_pruefen(stunde, minute)
        ziel = _lokal_zu_epoche(jahr, monat, tag, stunde, minute)
        if ziel <= nun:
            raise ZeitFehler("Der Zeitpunkt liegt in der Vergangenheit")
        return _in_reichweite(ziel, nun)

    m = _UM.match(roh)
    if m:
        stunde = int(m.group(1))
        minute = int(m.group(2) or m.group(3) or 0)
        _uhrzeit_pruefen(stunde, minute)
        lt = time.localtime(nun)
        ziel = _lokal_zu_epoche(lt.tm_year, lt.tm_mon, lt.tm_mday,
                                stunde, minute)
        if ziel <= nun:
            # Heute ist die Zeit vorbei -- gemeint ist morgen.
            d = _tag_nach(nun)
            ziel = _lokal_zu_epoche(d.year, d.month, d.day, stunde, minute)
        return _in_reichweite(ziel, nun)

    raise ZeitFehler(f"Kein Zeitpunkt erkennbar in {text!r}")


def _uhrzeit_pruefen(stunde: int, minute: int) -> None:
    if not (0 <= stunde <= 23 and 0 <= minute <= 59):
        raise ZeitFehler(f"Keine Uhrzeit: {stunde}:{minute}")


def beschreibe(ts: float, *, jetzt: Callable[[], float] = time.time) -> str:
    """Ein Zeitstempel als kurze deutsche Angabe, fuer Blase und Sprache.

    Bewusst knapp: der Sprechweg erlaubt 140 Zeichen fuer den GANZEN Satz,
    und `sprechtext.pruefe` lehnt Pfade ab -- ein ISO-String mit Doppelpunkten
    ist hier am richtigen Platz formatiert, nicht woanders halb.

    Ein Zeitstempel, den der Kalender nicht darstellen kann, ergibt eine
    Ersatzangabe statt eines Wurfs: `parse` laesst so etwas seit der
    Obergrenze nicht mehr durch, aber eine Zeile, die vorher angelegt wurde,
    darf `--liste` nicht auf Dauer unlesbar machen.
    """
    nun = float(jetzt())
    try:
        lt = time.localtime(ts)
        heute = datetime.date.fromtimestamp(nun)
        morgen = _tag_nach(nun)
    except (ValueError, OverflowError, OSError, ZeitFehler):
        return "zu einem unlesbaren Zeitpunkt"
    uhr = f"{lt.tm_hour}:{lt.tm_min:02d} Uhr"
    tag = (lt.tm_year, lt.tm_mon, lt.tm_mday)
    if tag == (heute.year, heute.month, heute.day):
        return f"heute um {uhr}"
    if tag == (morgen.year, morgen.month, morgen.day):
        return f"morgen um {uhr}"
    return time.strftime("am %d.%m. um ", lt) + uhr
