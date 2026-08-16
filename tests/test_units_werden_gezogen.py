"""Jede Unit braucht jemanden, der sie startet.

**Der Befund, der diese Datei ausgeloest hat.** `daimon-mind.service` hatte
kein `[Install]`, keine `daimon-mind.socket` und keine andere Unit, die sie
zieht -- `PartOf=` propagiert nur Stopp und Neustart, nie einen Start. Der
Dienst lief nicht, und das fiel erst auf, als zwei Messungen des
Abschlussreviews auf `unbelegt` standen: nicht weil eine Zusage gebrochen
war, sondern weil der Dienst gar nicht da war. Die Fussnote der Unit sagte
„gestartet wird ueber den Socket" und widersprach damit ihrem eigenen Kopf,
der Socket-Aktivierung ausdruecklich ablehnt.

Das ist wieder die Gestalt, die dieses Projekt bisher fuenfmal getroffen hat:
das Stueck ist gebaut und geprueft, sein ZULAUF fehlt -- Ticketbuch ohne
Verbraucher, Gate ohne Aufrufer, Kontextspeicher ohne `laden()`, DRM-Gatter
ohne Eingabe, jetzt ein Dienst ohne Starter. Ein Test je Stueck findet das
nie; dieser hier fragt nach dem Zulauf.

Geprueft werden die Dateien im Repo, nicht die Sitzung: eine Unit, die nur
auf DIESER Maschine von Hand gestartet wurde, ist fuer den naechsten Neustart
genauso weg.
"""
from __future__ import annotations

from pathlib import Path

import pytest

UNITS = Path(__file__).resolve().parents[1] / "config" / "systemd"
DIENSTE = sorted(UNITS.glob("*.service"))


def _hat(datei: Path, abschnitt: str) -> bool:
    return any(z.strip() == abschnitt
               for z in datei.read_text(encoding="utf-8").splitlines())


def test_es_gibt_ueberhaupt_units():
    """Sonst waere die Schleife unten leer und jede Aussage daraus wertlos."""
    assert len(DIENSTE) >= 15


@pytest.mark.parametrize("unit", DIENSTE, ids=lambda p: p.name)
def test_jede_unit_wird_von_etwas_gezogen(unit: Path):
    """`[Install]`, oder eine gleichnamige `.socket`/`.timer`. Nichts sonst.

    `PartOf=` und `Wants=` in der Unit selbst zaehlen NICHT: das erste
    propagiert nur Stopp und Neustart, das zweite zieht andere und nicht
    sich.
    """
    ziehende = [p.name for p in (unit.with_suffix(".socket"),
                                 unit.with_suffix(".timer")) if p.exists()]
    assert _hat(unit, "[Install]") or ziehende, (
        f"{unit.name} hat kein [Install] und keine .socket/.timer -- "
        "nach einem Neustart startet sie niemand")


def test_POSITIVKONTROLLE_die_regel_kann_ueberhaupt_reissen(tmp_path):
    """Ohne diese Zeile bestuende der Test oben auch eine Fassung, die immer
    wahr sagt -- und genau so eine haette den Befund nicht gefunden."""
    verwaist = tmp_path / "daimon-verwaist.service"
    verwaist.write_text("[Unit]\nDescription=x\nPartOf=graphical-session.target\n"
                        "[Service]\nExecStart=/bin/true\n")
    ziehende = [p for p in (verwaist.with_suffix(".socket"),
                            verwaist.with_suffix(".timer")) if p.exists()]
    assert not (_hat(verwaist, "[Install]") or ziehende)


def test_die_mind_unit_widerspricht_sich_nicht_mehr():
    """Der konkrete Befund, festgenagelt: der Kopf lehnt Socket-Aktivierung
    ab, also darf die Fussnote nicht auf einen Socket verweisen."""
    text = (UNITS / "daimon-mind.service").read_text(encoding="utf-8")
    assert "[Install]" in text
    assert not (UNITS / "daimon-mind.socket").exists()
    assert "Kein `[Install]`: gestartet wird ueber den Socket" not in text
