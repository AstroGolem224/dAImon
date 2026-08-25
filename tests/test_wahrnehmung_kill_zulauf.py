"""T-6.8.v, Befund B6: hat der Augen-Kill-Switch im Betrieb einen Ausloeser?

Design 7.4 verlangt "ein globaler Hotkey, der BEIDE Wahrnehmungs-Units
stoppt". Bis zum 25.08. war `daimon.eyes.killswitch` fertig, getestet und
ohne jeden Aufrufer ausser der Kommandozeile -- derselbe Zulauf-Fehler, den
dieses Repo laut CLAUDE.md sechsmal gemacht hat. Ein Kill-Switch, den man in
der Eile nicht erreicht, ist keiner.

**Warum statisch und nicht am laufenden Agenten.** `daimon/auth/agent.py`
importiert `gi` (PyGObject/GTK4) beim Laden und laeuft nur unter
System-Python, nicht im Projekt-venv -- ein pytest-Lauf kann das Modul gar
nicht importieren. Der eingefrorene Pruefstand von T-7.3 misst die Strecke
Tastendruck -> `_kuerzel_verteilen` -> `_ohren_abschalten` bereits am Modul
mit gestelltem `gi`; was ihm fehlt, ist das Stueck DAHINTER, denn er ersetzt
`_ohren_abschalten` durch eine Attrappe. Genau diese Luecke schliesst die
Datei hier: sie liest den Rumpf, den die Attrappe verdeckt.

Was daraus NICHT folgt: dass die Units danach wirklich aus sind. Das misst
`daimon/eyes/killswitch.py` selbst (`tests/test_eyes_killswitch.py`) und
T-6.8 K15 am laufenden System. Die drei Stuecke zusammen ergeben die Naht.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "daimon" / "auth" / "agent.py"

# Der Griff, auf den `_kuerzel_verteilen` das Kuerzel Meta+Shift+M verteilt.
EINSTIEG = "_ohren_abschalten"


@pytest.fixture(scope="module")
def klasse() -> ast.ClassDef:
    baum = ast.parse(AGENT.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ClassDef) and knoten.name == "AuthAgent":
            return knoten
    pytest.fail("AuthAgent nicht gefunden -- die Messung ist blind")


def _methoden(klasse: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {k.name: k for k in klasse.body
            if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _module_erreichbar(klasse: ast.ClassDef, start: str) -> set[str]:
    """Jedes Modul, das von `start` aus importiert wird -- ueber
    `self.<methode>()`-Aufrufe hinweg, denn die Kill-Switches werden
    absichtlich LOKAL im Rumpf importiert (der Agent laeuft ohne venv)."""
    methoden = _methoden(klasse)
    gesehen: set[str] = set()
    module: set[str] = set()
    offen = [start]
    while offen:
        name = offen.pop()
        if name in gesehen or name not in methoden:
            continue
        gesehen.add(name)
        for knoten in ast.walk(methoden[name]):
            if isinstance(knoten, ast.ImportFrom) and knoten.module:
                module.add(knoten.module)
            elif isinstance(knoten, ast.Import):
                module.update(a.name for a in knoten.names)
            elif (isinstance(knoten, ast.Call)
                    and isinstance(knoten.func, ast.Attribute)
                    and isinstance(knoten.func.value, ast.Name)
                    and knoten.func.value.id == "self"):
                offen.append(knoten.func.attr)
    return module


def test_der_hotkey_landet_beim_ohren_griff(klasse):
    """Positivkontrolle der Strecke davor: das Kuerzel der Komponente
    KG_AKTION_OHREN_AUS wird auf EINSTIEG verteilt. Faellt das weg, misst
    alles Folgende einen Griff, den nie jemand anfasst."""
    verteiler = _methoden(klasse).get("_kuerzel_verteilen")
    assert verteiler is not None, "_kuerzel_verteilen fehlt"
    quelle = ast.unparse(verteiler)
    assert "KG_AKTION_OHREN_AUS" in quelle, (
        "der Verteiler kennt die Ohren-Komponente nicht mehr")
    assert f"self.{EINSTIEG}()" in quelle, (
        f"der Verteiler ruft {EINSTIEG} nicht mehr auf")


def test_der_augen_killswitch_hat_einen_aufrufer(klasse):
    """Der Befund selbst: vom Hotkey-Griff aus muss der Augen-Schalter
    erreichbar sein."""
    module = _module_erreichbar(klasse, EINSTIEG)
    assert "daimon.eyes.killswitch" in module, (
        f"von {EINSTIEG} aus ist daimon.eyes.killswitch nicht erreichbar -- "
        "der Augen-Kill-Switch haette im Betrieb wieder keinen Ausloeser "
        "ausser der Kommandozeile (Design 7.4, Befund B6). "
        f"Gefunden wurde: {sorted(module)}")


def test_der_ohren_killswitch_ebenso(klasse):
    """Positivkontrolle der Analyse: den Ohren-Schalter gibt es an dieser
    Stelle seit T-3.15. Findet die Suche IHN nicht, dann sagt ein Fehlen der
    Augen gar nichts -- dann ist das Messband leer."""
    module = _module_erreichbar(klasse, EINSTIEG)
    assert "daimon.ears.killswitch" in module, (
        "die Suche findet nicht einmal den seit T-3.15 vorhandenen "
        "Ohren-Schalter -- sie misst nichts")


def test_gegenprobe_erfundenes_modul(klasse):
    """Negativkontrolle: die Suche darf nicht alles bejahen."""
    module = _module_erreichbar(klasse, EINSTIEG)
    assert "daimon.nase.killswitch" not in module
