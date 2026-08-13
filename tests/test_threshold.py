"""T-6.5 -- die vier Sprech-Stufen.

Der Pruefstand spielt je Stufe DIESELBE Ereignisfolge ab und prueft die Zahl
der Aeusserungen gegen eine erwartete Matrix. Genau das steht hier -- eine
Folge, vier Stufen, vier erwartete Zahlen, und ein Test je Stufe daneben.
"""
from __future__ import annotations

import pytest

from daimon.mind import threshold as th


# Dieselbe Folge fuer alle Stufen: zwei Antworten auf Fragen, dazu je ein
# ungefragter Anlass pro Dringlichkeit.
FOLGE = [
    ("antwort", th.BEILAEUFIG),
    ("beobachtung", th.KRITISCH),
    ("beobachtung", th.NUETZLICH),
    ("beobachtung", th.BEILAEUFIG),
    ("rueckfrage", th.BEILAEUFIG),
]

# stufe -> erwartete Zahl der Aeusserungen. Zwei davon sind Antworten und
# gehen IMMER durch -- deshalb faengt die Matrix bei 2 an und nicht bei 0.
MATRIX = {
    "silent": 2,
    "urgent": 3,
    "helpful": 4,
    "chatty": 5,
}


def abspielen(stufe: str) -> int:
    s = th.Schwelle(stufe)
    return sum(1 for anlass, d in FOLGE if s.darf_sprechen(anlass, d))


# -- Die Matrix ------------------------------------------------------------

@pytest.mark.parametrize("stufe, erwartet", sorted(MATRIX.items()))
def test_je_stufe_dieselbe_folge_und_eine_erwartete_zahl(stufe, erwartet):
    assert abspielen(stufe) == erwartet


def test_die_matrix_ist_streng_monoton():
    """Eine Stufe, die MEHR verschweigt als die naechststrengere, waere ein
    Fehler, den eine Zahl allein nicht zeigt."""
    zahlen = [MATRIX[s] for s in th.STUFEN]
    assert zahlen == sorted(zahlen)
    assert len(set(zahlen)) == len(zahlen)


# -- Ein Test je Stufe -----------------------------------------------------

def test_silent_sagt_ungefragt_nichts():
    s = th.Schwelle("silent")
    assert s.darf_sprechen("beobachtung", th.KRITISCH) is False
    assert s.darf_sprechen("beobachtung", th.NUETZLICH) is False
    assert s.darf_sprechen("beobachtung", th.BEILAEUFIG) is False


def test_silent_antwortet_trotzdem():
    """Sonst waere `silent` kein Assistent, sondern ein abgeschaltetes
    Geraet -- und dafuer gibt es den Kill-Switch."""
    s = th.Schwelle("silent")
    assert s.darf_sprechen("antwort") is True
    assert s.darf_sprechen("rueckfrage") is True


def test_urgent_nur_bei_kritischem():
    s = th.Schwelle("urgent")
    assert s.darf_sprechen("beobachtung", th.KRITISCH) is True
    assert s.darf_sprechen("beobachtung", th.NUETZLICH) is False


def test_helpful_auch_bei_nuetzlichem_aber_nicht_beilaeufig():
    s = th.Schwelle("helpful")
    assert s.darf_sprechen("beobachtung", th.NUETZLICH) is True
    assert s.darf_sprechen("beobachtung", th.KRITISCH) is True
    assert s.darf_sprechen("beobachtung", th.BEILAEUFIG) is False


def test_chatty_laesst_alles_durch():
    s = th.Schwelle("chatty")
    for d in (th.KRITISCH, th.NUETZLICH, th.BEILAEUFIG):
        assert s.darf_sprechen("beobachtung", d) is True


def test_der_vergleich_ist_groesser_gleich_und_nicht_gleich():
    """Eine Stufe, die nur genau ihre eigene Dringlichkeit durchliesse,
    verschluckte gerade das Wichtigste."""
    assert th.Schwelle("helpful").darf_sprechen("x", th.KRITISCH) is True


# -- Zur Laufzeit umschaltbar ---------------------------------------------

def test_die_stufe_laesst_sich_zur_laufzeit_wechseln():
    s = th.Schwelle("silent")
    assert s.darf_sprechen("beobachtung", th.KRITISCH) is False
    s.setzen("urgent")
    assert s.stufe == "urgent"
    assert s.darf_sprechen("beobachtung", th.KRITISCH) is True
    s.setzen("silent")
    assert s.darf_sprechen("beobachtung", th.KRITISCH) is False


def test_die_vorgabe_ist_helpful():
    assert th.Schwelle().stufe == "helpful"
    assert th.VORGABE == "helpful"


# -- Tippfehler fallen auf ------------------------------------------------

def test_eine_unbekannte_stufe_wird_abgelehnt():
    """Kein stiller Rueckfall auf die Vorgabe: ein Tippfehler in der
    Persona-Datei soll nicht dazu fuehren, dass ein als `silent` gemeintes
    Pet `helpful` ist."""
    with pytest.raises(th.SchwellenFehler):
        th.Schwelle("stumm")
    with pytest.raises(th.SchwellenFehler):
        th.Schwelle("helpful").setzen("gespraechig")


def test_eine_unbekannte_dringlichkeit_wird_abgelehnt():
    with pytest.raises(th.SchwellenFehler):
        th.Schwelle("chatty").darf_sprechen("beobachtung", "sehr wichtig")


def test_silent_ist_None_und_keine_hohe_zahl():
    """Eine Zahl laedt dazu ein, sie zu ueberbieten -- und dann spricht die
    Stufe, die „still" heisst."""
    assert th.SCHWELLE["silent"] is None


# -- Gezaehlt wird beides --------------------------------------------------

def test_abgewiesenes_wird_nach_stufe_und_dringlichkeit_gezaehlt():
    s = th.Schwelle("urgent")
    s.darf_sprechen("beobachtung", th.NUETZLICH)
    s.darf_sprechen("beobachtung", th.NUETZLICH)
    s.darf_sprechen("beobachtung", th.BEILAEUFIG)
    z = s.zaehler()
    assert z["abgewiesen"]["urgent:nuetzlich"] == 2
    assert z["abgewiesen"]["urgent:beilaeufig"] == 1
    assert z["durchgelassen"] == 0


def test_der_zaehler_nennt_die_aktuelle_stufe():
    s = th.Schwelle("chatty")
    assert s.zaehler()["stufe"] == "chatty"
