"""T-1.7 Teil 2 — der Umschaltautomat, geprueft ohne Plasma-Sitzung.

Genau dafuer wurde `ptt.py` vom Agenten abgetrennt: der fuenfte Mutant
dieses Tasks ("halten statt umschaltung") greift die Kipplogik an, und der
muss im venv unter 3.12 fallen koennen -- ohne `gi`, ohne Fenster, ohne KDE.

Erwartete Werte stehen als Literale im Test. Eine Zusicherung, die gegen
dieselbe Formel rechnet wie die Implementierung, beweist nichts.
"""

import io

from daimon.auth.ptt import PTTAutomat
from daimon.common.logging import get_logger


class Uhr:
    """Injizierte Zeitquelle. Startet bei 1000.0, damit nullnahe Arithmetik
    keine Rolle spielt."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def vor(self, sekunden: float) -> None:
        self.t += sekunden


def automat(zeitlimit: float = 120.0):
    """Automat mit Uhr und Audit-Sammler. Gibt (automat, uhr, zeilen)."""
    uhr = Uhr()
    puffer = io.StringIO()
    log = get_logger("test-ptt", socket_path="/nicht/da", stream=puffer)
    return PTTAutomat(zeitlimit_s=zeitlimit, jetzt=uhr, log=log), uhr, puffer


def zeilen(puffer: io.StringIO) -> list[str]:
    return [z for z in puffer.getvalue().splitlines() if z.strip()]


# ---------------------------------------------------------------------------
# Umschaltung, nicht Halten
# ---------------------------------------------------------------------------

def test_einmal_umschalten_ist_an():
    a, _, _ = automat()
    assert a.umschalten() is True
    assert a.ist_aktiv() is True


def test_zweimal_umschalten_ist_wieder_aus():
    a, _, _ = automat()
    a.umschalten()
    assert a.umschalten() is False
    assert a.ist_aktiv() is False
    assert a.restsekunden() == 0.0


def test_zeitlimit_schaltet_ueber_die_uhr_ab():
    a, uhr, _ = automat(zeitlimit=120.0)
    a.umschalten()
    uhr.vor(119.0)
    assert a.ist_aktiv() is True
    uhr.vor(2.0)  # 121 s seit der Aktivierung
    assert a.ist_aktiv() is False
    assert a.restsekunden() == 0.0


def test_nach_ablauf_schaltet_umschalten_wieder_an():
    """Der Automat gilt nach dem Ablauf als aus. Die naechste Umschaltung
    muss ANschalten -- sonst ginge der erste Tastendruck nach jedem Ablauf
    still verloren."""
    a, uhr, _ = automat(zeitlimit=120.0)
    a.umschalten()
    uhr.vor(3600.0)
    assert a.ist_aktiv() is False
    assert a.umschalten() is True
    assert a.ist_aktiv() is True
    # Und die Uhr wurde neu gestellt: volles Zeitlimit ab JETZT.
    uhr.vor(119.0)
    assert a.ist_aktiv() is True


# ---------------------------------------------------------------------------
# Auskunft ohne Seiteneffekte
# ---------------------------------------------------------------------------

def test_ist_aktiv_veraendert_nichts():
    a, uhr, puffer = automat()
    a.umschalten()
    stand = puffer.getvalue()
    for _ in range(5):
        assert a.ist_aktiv() is True
    assert a.restsekunden() == 120.0
    # Keine neue Audit-Zeile, kein verstellter Zustand.
    assert puffer.getvalue() == stand
    uhr.vor(200.0)
    assert a.ist_aktiv() is False


def test_restsekunden_als_literale():
    a, uhr, _ = automat(zeitlimit=120.0)
    assert a.restsekunden() == 0.0
    a.umschalten()
    assert a.restsekunden() == 120.0
    uhr.vor(30.0)
    assert a.restsekunden() == 90.0


# ---------------------------------------------------------------------------
# aus()
# ---------------------------------------------------------------------------

def test_aus_beendet_eine_runde():
    a, _, puffer = automat()
    a.umschalten()
    a.aus()
    assert a.ist_aktiv() is False
    assert a.restsekunden() == 0.0
    assert len(zeilen(puffer)) == 2  # an, aus


def test_aus_bei_inaktiv_ist_harmlos():
    a, _, puffer = automat()
    a.aus()
    a.aus()
    assert a.ist_aktiv() is False
    assert zeilen(puffer) == []


# ---------------------------------------------------------------------------
# Audit und Eingangskontrolle
# ---------------------------------------------------------------------------

def test_jede_zustandsaenderung_erzeugt_eine_audit_zeile():
    a, _, puffer = automat()
    a.umschalten()          # an
    a.umschalten()          # aus
    assert len(zeilen(puffer)) == 2
    assert "ptt an" in zeilen(puffer)[0]
    assert "ptt aus" in zeilen(puffer)[1]


def test_audit_ist_nicht_leer():
    a, _, puffer = automat()
    a.umschalten()
    assert zeilen(puffer) != []


def test_zeitlimit_null_oder_negativ_wird_abgelehnt():
    import pytest
    with pytest.raises(ValueError):
        PTTAutomat(zeitlimit_s=0.0)
    with pytest.raises(ValueError):
        PTTAutomat(zeitlimit_s=-5.0)


def test_kein_gi_im_modul():
    """Der ganze Grund fuer die Abteilung: der Automat muss im venv laufen.
    Ein `import gi` -- auch ein bedingter -- macht ihn dort unbrauchbar."""
    import sys
    import daimon.auth.ptt  # noqa: F401
    assert "gi" not in sys.modules


# -- T-3.14 L2: der Ablauf will gemeldet werden ---------------------------
#
# `ist_aktiv` RECHNET den Ablauf, meldet ihn aber niemandem -- eine Auskunft
# veraendert nichts. Fuer den Sprachzustand im Overlay reicht das nicht: der
# Hub sieht das Ablaufen sonst nie, und `listening` haengt am Overlay, bis
# der Nutzer erneut drueckt. `melden()` schliesst genau diese Luecke.


def test_melden_gibt_beim_einschalten_true():
    a, _, _ = automat()
    assert a.melden() is None          # nichts passiert, nichts zu melden
    a.umschalten()
    assert a.melden() is True


def test_melden_meldet_jeden_wechsel_nur_einmal():
    a, _, _ = automat()
    a.umschalten()
    assert a.melden() is True
    assert a.melden() is None
    a.umschalten()
    assert a.melden() is False
    assert a.melden() is None


def test_ablauf_wird_gemeldet_ohne_dass_jemand_umschaltet():
    """Die eigentliche Luecke: niemand hat gehandelt, es ist nur Zeit
    vergangen -- und trotzdem muss der Hub es erfahren."""
    a, uhr, _ = automat(zeitlimit=120.0)
    a.umschalten()
    assert a.melden() is True
    uhr.vor(119.0)
    assert a.melden() is None
    uhr.vor(2.0)
    assert a.melden() is False


def test_ausdrueckliches_beenden_wird_gemeldet():
    a, _, _ = automat()
    a.umschalten()
    a.melden()
    a.aus()
    assert a.melden() is False


def test_melden_veraendert_den_automaten_nicht():
    """Eine Meldung ist eine Auskunft ueber einen Wechsel, kein Ereignis:
    sie darf weder den Zustand noch die Restzeit bewegen."""
    a, _, puffer = automat()
    a.umschalten()
    vorher = a.restsekunden()
    zeilen_vorher = len(zeilen(puffer))
    a.melden()
    assert a.ist_aktiv() is True
    assert a.restsekunden() == vorher
    assert len(zeilen(puffer)) == zeilen_vorher
