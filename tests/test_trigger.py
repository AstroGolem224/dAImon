"""T-5.4 -- wann hingesehen wird.

Die Klasse laesst sich mit einer Uhr und einem Tor aus dem Testcode
vollstaendig durchspielen. Kein Bildschirm, kein PipeWire, kein Mensch, der
etwas anklickt -- und deshalb laeuft das hier auch dann, wenn niemand vor der
Maschine sitzt.
"""
from __future__ import annotations

import pytest

from daimon.eyes import trigger as tg


class Uhr:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def weiter(self, s: float) -> None:
        self.t += s


class Tor:
    def __init__(self, offen: bool = True) -> None:
        self.offen = offen

    def __call__(self) -> bool:
        return self.offen


def bauen(uhr, tor, **kw):
    return tg.Ausloeser(tor=tor, jetzt=uhr, **kw)


# -- Beide Quellen loesen aus ----------------------------------------------

def test_fokus_und_timer_loesen_beide_aus():
    uhr, tor = Uhr(), Tor()
    a = bauen(uhr, tor)
    assert a.fokus() == tg.GRUND_FOKUS
    uhr.weiter(tg.PERIODE_S)
    assert a.tick() == tg.GRUND_TIMER
    z = a.zaehler()
    assert z[tg.GRUND_FOKUS] == 1 and z[tg.GRUND_TIMER] == 1


def test_der_timer_traegt_den_grossteil():
    """T--1.9: `captionChanged` feuert nur bei TITELaenderung.

    Terminalausgabe, Scrollen, ein neuer Absatz erzeugen nichts. Wer nur auf
    Fokus hoert, sieht einem Fenster beim Stillstehen zu. Hier nachgestellt:
    ein Fokuswechsel, danach eine Stunde Arbeit im selben Fenster.
    """
    uhr, tor = Uhr(), Tor()
    a = bauen(uhr, tor)
    a.fokus()
    for _ in range(3600):
        uhr.weiter(1.0)
        a.tick()
    z = a.zaehler()
    assert z[tg.GRUND_FOKUS] == 1
    assert z[tg.GRUND_TIMER] > 700          # 3600 s / 5 s, minus Anlauf
    assert z[tg.GRUND_TIMER] > z[tg.GRUND_FOKUS] * 100


# -- Das Tor ---------------------------------------------------------------

def test_geschlossenes_tor_haelt_BEIDE_quellen_an():
    """Auch das Fokus-Ereignis -- es kommt ja gerade dann, wenn der
    Sperrschirm den Fokus uebernimmt."""
    uhr, tor = Uhr(), Tor(offen=False)
    a = bauen(uhr, tor)
    assert a.fokus() is None
    uhr.weiter(tg.PERIODE_S * 3)
    assert a.tick() is None
    assert a.zaehler()["tor_zu"] == 2
    assert a.zaehler()[tg.GRUND_TIMER] == 0


def test_nach_dem_oeffnen_geht_es_weiter():
    """Ein Tor, das sich nicht wieder oeffnet, waere ein Ausschalter."""
    uhr, tor = Uhr(), Tor(offen=False)
    a = bauen(uhr, tor)
    a.fokus()
    tor.offen = True
    assert a.fokus() == tg.GRUND_FOKUS


# -- Der Deckel ------------------------------------------------------------

def test_der_deckel_gilt_fuer_beide_quellen_gemeinsam():
    """Zwoelfmal Alt-Tab in zwei Sekunden saehe zwoelfmal denselben Schirm."""
    uhr, tor = Uhr(), Tor()
    a = bauen(uhr, tor)
    assert a.fokus() == tg.GRUND_FOKUS
    for _ in range(11):
        uhr.weiter(0.05)
        assert a.fokus() is None
    assert a.zaehler()[tg.GRUND_FOKUS] == 1
    assert a.zaehler()["zu_dicht"] == 11


def test_abgewiesenes_wird_gezaehlt():
    """Eine abgewiesene Erfassung, die niemand zaehlt, ist von einer nicht
    stattgefundenen nicht zu unterscheiden."""
    uhr, tor = Uhr(), Tor()
    a = bauen(uhr, tor)
    a.fokus()
    a.fokus()
    tor.offen = False
    uhr.weiter(10.0)
    a.fokus()
    z = a.zaehler()
    assert z["zu_dicht"] == 1 and z["tor_zu"] == 1


# -- Die Periode zaehlt ab der Erfassung -----------------------------------

def test_ein_fokusereignis_verschiebt_den_timer():
    """Sonst waere der Timer direkt danach faellig und saehe dasselbe Bild."""
    uhr, tor = Uhr(), Tor()
    a = bauen(uhr, tor)
    uhr.weiter(tg.PERIODE_S)
    a.tick()                                    # Timer feuert
    uhr.weiter(tg.PERIODE_S - 1.0)
    assert a.fokus() == tg.GRUND_FOKUS          # Fokus dazwischen
    uhr.weiter(1.0)
    assert a.tick() is None                     # noch keine volle Periode
    uhr.weiter(tg.PERIODE_S)
    assert a.tick() == tg.GRUND_TIMER


# -- Die Einstellung -------------------------------------------------------

def test_der_erste_tick_feuert_sofort():
    """Ein Beobachter, der nach dem Start erst eine Periode wartet, ist
    schlechter -- die erste Beobachtung ist die, auf die alles andere sich
    bezieht."""
    uhr, tor = Uhr(), Tor()
    assert bauen(uhr, tor).tick() == tg.GRUND_TIMER


def test_die_rate_ist_einstellbar():
    uhr, tor = Uhr(), Tor()
    a = bauen(uhr, tor, periode_s=60.0, mindestabstand_s=2.0)
    assert a.tick() == tg.GRUND_TIMER           # der erste feuert sofort
    uhr.weiter(59.0)
    assert a.tick() is None
    uhr.weiter(1.0)
    assert a.tick() == tg.GRUND_TIMER


def test_die_vorgabe_ist_konservativ():
    """39 ms je Erfassung (T-5.3) alle 5 s sind 0,8 % der Zeit."""
    assert tg.PERIODE_S >= 5.0
    assert 0.039 / tg.PERIODE_S < 0.01


def test_ein_deckel_groesser_als_die_periode_wird_abgelehnt():
    """Sonst frisst der Deckel den Timer, und es traegt nur noch der Fokus --
    also gerade die Quelle, die laut T--1.9 nichts traegt."""
    with pytest.raises(ValueError):
        tg.Ausloeser(tor=Tor(), periode_s=1.0, mindestabstand_s=5.0)


# -- Das echte Tor ---------------------------------------------------------

def test_das_systemtor_ist_bei_totem_bus_ZU():
    """Nichtstun ist der harmlose Fehlerfall, wenn man Bildschirme mitliest."""
    t = tg.SystemTor()
    assert t.gesperrt() is True or t.gesperrt() is False
    # Kein Bus im Testlauf (venv-Python hat kein `dbus`) -> zu.
    try:
        import dbus                                     # noqa: F401
    except ImportError:
        assert t.gesperrt() is True
        assert t.leerlauf() is True
        assert t() is False


def test_der_leerlauf_ist_ausdruecklich_als_ungeprueft_markiert():
    """`GetSessionIdleTime` gibt es hier nicht, und von `IdleHint` ist nur
    bekannt, dass sie `false` liefert -- nicht, dass sie je kippt."""
    assert tg.SystemTor.LEERLAUF_UNGEPRUEFT is True
