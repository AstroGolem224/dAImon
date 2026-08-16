"""Der Pruefer selbst, gegen bekannte Eingaben.

**Warum ein Pruefstand fuer ein Pruefwerkzeug.** `tools/final_security.py`
hat sich in einem einzigen Tag viermal geirrt, und jedes Mal in derselben
Richtung: es hielt etwas fuer belegt, das nicht gemessen war. Die
Auswertelogik ist die Stelle, an der das entsteht -- ein Deuter, der im
Zweifel "abgewiesen" sagt, ist in jedem Lauf unauffaellig und entwertet
jede Aussage darueber.

Dasselbe Muster wie Abschnitt 1 von `tests/verify/T-0.12.sh`: bevor der
Auswerter etwas ueber den Prueflung sagen darf, sagt er etwas ueber vier
Eingaben, deren Antwort feststeht.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_pfad = Path(__file__).resolve().parents[1] / "tools" / "final_security.py"
_spec = importlib.util.spec_from_file_location("final_security", _pfad)
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)


def test_keine_antwort_ist_eine_abweisung():
    """Der Dienst schliesst ohne zu reden -- die Unit kam nicht durch."""
    assert fs.deutung("") == fs.ABGEWIESEN
    assert fs.deutung("   ") == fs.ABGEWIESEN


def test_ein_rst_ebenso():
    """Ungelesene Bytes im Puffer machen den Abbruch hart. Dieselbe Wirkung
    wie ein EOF. (Beim ersten Lauf las das Werkzeug das als offenen Befund --
    ein Falschbefund des Pruefers, nicht des Systems.)"""
    assert fs.deutung("ConnectionResetError: [Errno 104] ...") == fs.ABGEWIESEN


def test_DER_BEFUND_eine_absage_ist_KEINE_abweisung():
    """Wer eine Antwort bekommt, ist ANGENOMMEN worden.

    Die alte Fassung zaehlte `{"ok": false}` als Abweisung. Damit haette ein
    Hub ganz ohne Peer-Pruefung den Befund 7.2b weiter gruen gemeldet: das
    Gate lehnt mangels Rundenmarke ohnehin ab, und genau diese Absage sieht
    im Normalbetrieb IMMER so aus.
    """
    assert fs.deutung('{"v": 1, "ok": false, "grund": "keine_marke"}') \
        == fs.BEANTWORTET


def test_und_eine_freigabe_erst_recht_nicht():
    assert fs.deutung('{"v":1,"ok":true,"eintraege":["..."]}') \
        == fs.BEANTWORTET


def test_muell_ist_unklar_und_nicht_abgewiesen():
    """Der dritte Ausgang. Ein Deuter mit zwei Ausgaengen muss Unverstandenes
    einem der beiden zuschlagen -- und "abgewiesen" waere die bequeme Wahl."""
    assert fs.deutung("FileNotFoundError: kein Socket") == fs.UNKLAR
    assert fs.deutung("<html>") == fs.UNKLAR


@pytest.mark.parametrize("eingabe,erwartet", [
    ("", "abgewiesen"),
    ('{"ok": false}', "beantwortet"),
    ("kaputt", "unklar"),
])
def test_die_drei_ausgaenge_sind_unterscheidbar(eingabe, erwartet):
    """Sonst waere die Dreiteilung nur ein laengerer Name fuer zwei."""
    assert fs.deutung(eingabe) == erwartet
