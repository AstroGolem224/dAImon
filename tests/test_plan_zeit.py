"""T-8.2 -- der Zeitparser. Deterministisch, und die Fehlermenge ist endlich.

Die Uhr ist ueberall fixiert: ein Donnerstag, 2026-08-20, 15:00:00 lokaler
Zeit. Daran haengen die Erwartungen -- nicht an `time.time` des Laufs.
"""
from __future__ import annotations

import time

import pytest

from daimon.plan import zeit

# Donnerstag, 20.08.2026 15:00:00 lokale Zeit.
JETZT = time.mktime((2026, 8, 20, 15, 0, 0, 0, 0, -1))


def jetzt() -> float:
    return JETZT


def lokal(ts: float) -> time.struct_time:
    return time.localtime(ts)


# -- „in X ..." ------------------------------------------------------------

@pytest.mark.parametrize("text,minuten", [
    ("in 45 minuten", 45), ("in 20 Minuten", 20), ("in 1 minute", 1),
    ("in 5 min", 5), ("in 30m", 30), ("in 1,5 stunden", 90),
    ("in 2 stunden", 120), ("in 1 std", 60), ("in 3h", 180),
    ("in 90 sekunden", 1.5), ("in 30s", 0.5),
])
def test_relative_zeiten(text, minuten):
    assert zeit.parse(text, jetzt=jetzt) == pytest.approx(JETZT + minuten * 60)


def test_eine_dauer_von_null_wird_abgelehnt():
    with pytest.raises(zeit.ZeitFehler):
        zeit.parse("in 0 minuten", jetzt=jetzt)


# -- „um ..." ---------------------------------------------------------------

def test_um_eine_volle_stunde_heute():
    ziel = zeit.parse("um 18:30", jetzt=jetzt)
    lt = lokal(ziel)
    assert (lt.tm_year, lt.tm_mon, lt.tm_mday) == (2026, 8, 20)
    assert (lt.tm_hour, lt.tm_min) == (18, 30)


def test_um_eine_vergangene_zeit_meint_morgen():
    """Um 15:00 ist „um 8" vorbei -- gemeint ist morgen frueh. Wer das nicht
    meint, sagt ein Datum."""
    ziel = zeit.parse("um 8", jetzt=jetzt)
    lt = lokal(ziel)
    assert (lt.tm_year, lt.tm_mon, lt.tm_mday) == (2026, 8, 21)
    assert (lt.tm_hour, lt.tm_min) == (8, 0)


def test_um_eine_zeit_in_dieser_minute_meint_morgen():
    """Gleichstand ist Vergangenheit: der Termin „jetzt" waere beim Ausloesen
    schon vorbei."""
    ziel = zeit.parse("um 15:00", jetzt=jetzt)
    assert lokal(ziel).tm_mday == 21


def test_um_8_uhr_15():
    ziel = zeit.parse("um 8 uhr 15", jetzt=jetzt)
    lt = lokal(ziel)
    assert (lt.tm_hour, lt.tm_min) == (8, 15)
    assert lt.tm_mday == 21


@pytest.mark.parametrize("text", ["um 25", "um 8:99", "um 24:00"])
def test_unmoegliche_uhrzeiten_werden_abgelehnt(text):
    with pytest.raises(zeit.ZeitFehler):
        zeit.parse(text, jetzt=jetzt)


# -- „morgen um ..." --------------------------------------------------------

def test_morgen_um_8():
    ziel = zeit.parse("morgen um 8", jetzt=jetzt)
    lt = lokal(ziel)
    assert (lt.tm_year, lt.tm_mon, lt.tm_mday) == (2026, 8, 21)
    assert (lt.tm_hour, lt.tm_min) == (8, 0)


@pytest.fixture
def berlin(monkeypatch):
    """Die Zeitzone fest, sonst misst der Test die des Rechners.

    Ohne sie ist „am DST-Rand" eine Behauptung: auf einer Maschine in UTC
    gibt es keinen Rand, und der Test wuerde Ruhe melden, wo nichts gemessen
    wurde."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_morgen_ist_ein_tag_lokal_nicht_86400_spaeter(berlin):
    """Am DST-Uebergang sind 86400 Sekunden NICHT der naechste Tag um 8 --
    die Rechnung geht ueber den Kalender, nicht ueber Sekunden.

    23:30 und nicht 15:00: um 15:00 liegt `nun + 86400` noch im selben
    Kalendertag wie das richtige Ergebnis, und der Fehler ist unsichtbar.
    Erst in der NACHT der Umstellung springt die falsche Rechnung einen Tag
    weiter -- 28.03.2026 23:30 + 86400 s ist der 30.03. um 00:30.
    """
    vor_umstellung = time.mktime((2026, 3, 28, 23, 30, 0, 0, 0, -1))
    # Positivkontrolle: die alte Rechnung landet wirklich uebermorgen --
    # sonst prueft der Test einen Rand, den es an diesem Datum nicht gibt.
    assert time.localtime(vor_umstellung + 86400).tm_mday == 30

    ziel = zeit.parse("morgen um 8", jetzt=lambda: vor_umstellung)
    lt = lokal(ziel)
    assert (lt.tm_mon, lt.tm_mday, lt.tm_hour) == (3, 29, 8)


def test_um_eine_vergangene_zeit_meint_morgen_auch_am_dst_rand(berlin):
    """Derselbe Rand im `um`-Zweig: um 23:30 ist „um 8" vorbei."""
    vor_umstellung = time.mktime((2026, 3, 28, 23, 30, 0, 0, 0, -1))
    lt = lokal(zeit.parse("um 8", jetzt=lambda: vor_umstellung))
    assert (lt.tm_mon, lt.tm_mday, lt.tm_hour) == (3, 29, 8)


def test_beschreibe_kennt_morgen_auch_am_dst_rand(berlin):
    vor_umstellung = time.mktime((2026, 3, 28, 23, 30, 0, 0, 0, -1))
    ziel = zeit.parse("morgen um 8", jetzt=lambda: vor_umstellung)
    assert zeit.beschreibe(ziel, jetzt=lambda: vor_umstellung) \
        == "morgen um 8:00 Uhr"


# -- ISO --------------------------------------------------------------------

def test_iso_mit_uhrzeit():
    ziel = zeit.parse("2026-08-24 08:00", jetzt=jetzt)
    lt = lokal(ziel)
    assert (lt.tm_year, lt.tm_mon, lt.tm_mday) == (2026, 8, 24)
    assert (lt.tm_hour, lt.tm_min) == (8, 0)


def test_iso_mit_T_trenner():
    ziel = zeit.parse("2026-08-24T08:00", jetzt=jetzt)
    assert lokal(ziel).tm_mday == 24


def test_iso_ohne_uhrzeit_meint_tagesanfang():
    ziel = zeit.parse("2026-08-24", jetzt=jetzt)
    lt = lokal(ziel)
    assert (lt.tm_mday, lt.tm_hour, lt.tm_min) == (24, 0, 0)


def test_iso_in_der_vergangenheit_wird_abgelehnt():
    with pytest.raises(zeit.ZeitFehler):
        zeit.parse("2020-01-01 08:00", jetzt=jetzt)


@pytest.mark.parametrize("text", [
    "2026-02-30 08:00",   # normalisiert waere das der 02.03.
    "2027-02-31 08:00",   # in der ZUKUNFT: die Vergangenheitsschranke greift
    "2027-13-45 08:00",   # normalisiert waere das der 14.02.2028
    "2027-00-10 08:00",
])
def test_ein_unmoegliches_datum_wird_abgelehnt(text):
    """`time.mktime` normalisiert STILL: aus dem 31. Februar wurde der
    3. Maerz, und der `except ValueError` darueber hat nie etwas gesehen.
    Zwei der Faelle liegen in der Zukunft -- sonst faengt sie die
    Vergangenheitsschranke ab, und die Pruefung waere eine Attrappe."""
    with pytest.raises(zeit.ZeitFehler):
        zeit.parse(text, jetzt=jetzt)


# -- Die Obergrenze ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    "in 99999999999999 stunden", "in 999999999 minuten", "2999-01-01 08:00",
])
def test_zeitpunkte_jenseits_von_zehn_jahren_werden_abgelehnt(text):
    """`time.localtime` wirft bei riesigen Zeitstempeln `OSError(75)`. Eine
    Zeile, die einmal angelegt ist, vergiftet danach jedes `--liste`."""
    with pytest.raises(zeit.ZeitFehler):
        zeit.parse(text, jetzt=jetzt)


def test_positivkontrolle_neun_jahre_gehen_noch():
    """Ein Parser, der jedes Datum ablehnt, bestuende den Test darueber."""
    assert zeit.parse("2035-01-01 08:00", jetzt=jetzt) > JETZT
    assert zeit.beschreibe(zeit.parse("in 8000 stunden", jetzt=jetzt),
                           jetzt=jetzt).startswith("am ")


def test_beschreibe_wirft_nicht_an_einem_unlesbaren_zeitstempel():
    """Eine Zeile aus der Zeit vor der Obergrenze darf `--liste` nicht auf
    Dauer unlesbar machen."""
    assert zeit.beschreibe(9e18, jetzt=jetzt) == "zu einem unlesbaren Zeitpunkt"
    # Positivkontrolle: derselbe Weg liefert sonst eine echte Angabe.
    assert zeit.beschreibe(JETZT, jetzt=jetzt).startswith("heute um ")


# -- Was der Parser nicht kann, kann er nicht -------------------------------

@pytest.mark.parametrize("text", [
    "", "   ", "bald", "spaeter irgendwann", "uebermorgen", "montag",
    "in einer woche", "um halb acht", "nachmittags", None, 42,
])
def test_unlesbares_wirft_statt_zu_raten(text):
    """Ein geratener Zeitpunkt ist schlimmer als eine Rueckfrage."""
    with pytest.raises(zeit.ZeitFehler):
        zeit.parse(text, jetzt=jetzt)


def test_positivkontrolle_der_parser_kann_ueberhaupt_etwas():
    """Negativtests allein beweisen nichts -- ein Parser, der alles ablehnt,
    bestuende sie alle."""
    assert zeit.parse("in 5 minuten", jetzt=jetzt) > JETZT
    assert zeit.parse("um 20", jetzt=jetzt) > JETZT


# -- beschreibe -------------------------------------------------------------

def test_beschreibe_heute_morgen_datum():
    heute = zeit.parse("um 18:30", jetzt=jetzt)
    assert zeit.beschreibe(heute, jetzt=jetzt) == "heute um 18:30 Uhr"
    morgen = zeit.parse("morgen um 8", jetzt=jetzt)
    assert zeit.beschreibe(morgen, jetzt=jetzt) == "morgen um 8:00 Uhr"
    spaeter = zeit.parse("2026-08-24 08:00", jetzt=jetzt)
    assert zeit.beschreibe(spaeter, jetzt=jetzt) == "am 24.08. um 8:00 Uhr"
