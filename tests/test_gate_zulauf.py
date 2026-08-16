"""Woran die Gate-Zusagen WIRKLICH haengen -- und ein Waechter fuer den Tag,
an dem sich das aendert.

**Was das unabhaengige Review gefunden hat, und wie es sich beim Nachsehen
verschoben hat.** Der Befund lautete: `kontingent` und `proaktiv` werden von
`Hub.kontext_anfrage` nie uebergeben, die Gruende `kontingent_deklassifiziert_
nicht` und `ohne_nutzerhandlung` sind im Betrieb also unerreichbar. Beides
stimmt. Der SCHLUSS daraus -- die Zusage sei unerreichbar -- stimmt nicht:

* Ein Wake-Word kann nichts deklassifizieren, weil es keine RUNDENMARKE
  erzeugen kann. `MarkenBuch.ausgeben` nimmt ausser `quelle="auth"` nichts an.
* Proaktives Verhalten sieht nichts, weil `mind.proactive` das Gate gar nicht
  aufruft -- und ohne Nutzerhandlung gibt es keine offene Marke.

Die Parameter sind damit die ZWEITE Stelle derselben Zusage, und die
wirksame ist die andere. Diese Datei belegt die wirksame Stelle und bewacht
die Bedingung, unter der das so bleibt.

**Warum ein Waechter und kein Vorratscode.** Dieses Projekt hat sechsmal
denselben Fehler gemacht: ein Stueck gebaut und geprueft, dessen ZULAUF
fehlt. Der umgekehrte Fehler waere, heute eine Uebergabe zu bauen, die
niemand aufruft -- also dasselbe noch einmal. Stattdessen faellt der
Pruefstand auf, sobald der Zulauf entsteht.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daimon.hub.declassify import (Deklassifizierung, GRUND_KEINE_MARKE,
                                   GateFehler)
from daimon.hub.marks import KontingentBuch, MarkenBuch, MarkenFehler

REPO = Path(__file__).resolve().parents[1]
QUELLE = REPO / "daimon"
FRAGE = "was steht auf dem Bildschirm"


def _aufrufe(name: str, *, ausser: tuple[str, ...] = (),
             nur: Path | None = None) -> list[str]:
    """Wo wird `name` im Produktivcode wirklich AUFGERUFEN?

    Ueber den Syntaxbaum und nicht per `grep`: die erste Fassung dieser Datei
    suchte im Text und schlug an einem Docstring an, der den Aufruf nur
    ERWAEHNT -- „hier gibt es keinen Aufruf" stand da, und genau das war der
    Treffer. Ein Prueferfehler derselben Sorte, die dieses Repo schon
    viermal gesehen hat.
    """
    dateien = [nur] if nur else sorted(QUELLE.rglob("*.py"))
    treffer = []
    for datei in dateien:
        if datei.name in ausser or "__pycache__" in str(datei):
            continue
        try:
            baum = ast.parse(datei.read_text(encoding="utf-8"))
        except SyntaxError:                     # pragma: no cover
            continue
        for k in ast.walk(baum):
            if not isinstance(k, ast.Call):
                continue
            ziel = k.func
            getroffen = (isinstance(ziel, ast.Attribute) and ziel.attr == name
                         or isinstance(ziel, ast.Name) and ziel.id == name)
            if getroffen:
                treffer.append(f"{datei.relative_to(REPO)}:{k.lineno}")
    return treffer


# -- Die WIRKSAME Sperre ----------------------------------------------------

def test_ein_wake_word_kann_keine_rundenmarke_erzeugen():
    """DAS ist die Sperre, nicht der Parameter am Gate. Design 2.4: ein
    Kontingent sagt "es darf geredet werden", die Marke sagt "ein Mensch hat
    die Taste gedrueckt"."""
    buch = MarkenBuch()
    with pytest.raises(MarkenFehler):
        buch.ausgeben(quelle="wake_word", turn_id="t-1")
    with pytest.raises(MarkenFehler):
        buch.ausgeben(quelle="rundenmarke", turn_id="t-1")
    assert buch.aktuelle() is None          # nichts ist entstanden


def test_und_ein_kontingent_erlaubt_keine_deklassifizierung():
    """Die zweite Haelfte, als konstante Zusage statt als Kommentar."""
    kb = KontingentBuch()
    kid = kb.ausgeben(quelle="wake_word")
    assert kb.erlaubt_deklassifizierung(kid) is False


def test_ohne_marke_gibt_das_gate_nichts_heraus():
    """Der Weg, den eine Wake-Word-Runde heute nimmt: sie hat keine Marke,
    also endet sie hier -- ohne dass jemand `kontingent` uebergeben muesste."""
    class Speicher:
        def freigeben(self, _schein):
            raise AssertionError("haette nicht gefragt werden duerfen")

    gate = Deklassifizierung(marken=MarkenBuch(), speicher=Speicher())
    with pytest.raises(GateFehler) as f:
        gate.freigeben(aeusserung=FRAGE, turn_id=None)
    assert f.value.grund == GRUND_KEINE_MARKE


# -- Der Waechter -----------------------------------------------------------

def test_WAECHTER_das_kontingent_hat_keinen_produktiven_aufrufer():
    """Solange niemand `KontingentBuch` baut, kann kein Kontingent an das Gate
    geraten -- und `kontingent=` dort nicht zu uebergeben ist folgenlos.

    Wer das aendert, aendert damit auch die Voraussetzung dieser Datei: dann
    MUSS `Hub.kontext_anfrage` das Kontingent durchreichen, sonst faellt die
    Bedingung „ein Kontingent deklassifiziert nichts" auf die Marke allein
    zurueck -- und die kann aus einer ANDEREN Runde stammen.
    """
    aufrufer = _aufrufe("KontingentBuch", ausser=("marks.py",))
    if aufrufer:
        hub = (QUELLE / "hub" / "daemon.py").read_text(encoding="utf-8")
        assert "kontingent=" in hub, (
            "KontingentBuch ist jetzt verdrahtet (" + ", ".join(aufrufer)
            + "), also muss kontext_anfrage `kontingent=` an das Gate "
            "uebergeben. Siehe den Docstring von Deklassifizierung.freigeben.")


def test_WAECHTER_das_gate_hat_genau_einen_produktiven_aufrufer():
    """Heute `Hub.kontext_anfrage`. Ein zweiter Aufrufer ist keine
    Verschlechterung -- aber er muss selbst beantworten, ob seine Runde eine
    Nutzerhandlung ist (`proaktiv=`). Der einzige heutige tut das nicht, und
    das ist richtig: er wird vom Sprachpfad erreicht, nie von selbst."""
    # `suche.py` und `context.py` haben eigene `freigeben`-Methoden -- sie
    # sind die EMPFAENGER des Scheins, nicht Aufrufer des Gates. Sie stehen
    # deshalb draussen, und das ist die Schwaeche dieser Zeile: sie zaehlt
    # Namen, nicht Ziele.
    aufrufer = _aufrufe("freigeben",
                        ausser=("declassify.py", "suche.py", "context.py"))
    assert len(aufrufer) == 1, (
        f"Aufrufer von freigeben im Produktivcode: {aufrufer}")
    assert aufrufer[0].startswith("daimon/hub/daemon.py:"), aufrufer


def test_WAECHTER_proaktiv_ruft_das_gate_nicht():
    """Die strukturelle Haelfte der Zusage: das Modul ENTSCHEIDET nur. Eine
    Klasse, die selbst freigeben koennte und es nur unterlaesst, gibt frei,
    sobald jemand eine Zeile ergaenzt."""
    datei = QUELLE / "mind" / "proactive.py"
    assert _aufrufe("freigeben", nur=datei) == []
    assert _aufrufe("Deklassifizierung", nur=datei) == []
