"""Die zwei Ausgaenge aus der Quarantaene, gegen dieselben Attrappen.

Hinter dem Gate liegen zwei Speicher: der Kontextspeicher (T-5.7, was JETZT
auf dem Bildschirm steht) und die Archivsuche (T-7.5, was VORGESTERN dort
stand). Beide verlangen denselben Freigabeschein, und beide koennen ihn nicht
nachpruefen -- sie kennen keine Marken.

**Sie pruefen ihn seit dem 16.08. auch gleich.** Vorher nicht: die Archivsuche
verglich den Typnamen, der Kontextspeicher fragte nur
`getattr(schein, "turn_id", "") != ""`. Ein `Namespace(turn_id="x")` kam damit
an den Live-Bildschirmtext, nicht aber ans Archiv -- dieselbe Regel, zwei
Fassungen, und die schwaechere bewachte das Frischere.

Diese Datei faehrt beide gegen DIESELBE Liste. Ein Ausgang, der leiser ist
als der andere, faellt hier auf.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from daimon.eyes.context import ART_OCR, Kontextspeicher
from daimon.eyes.context import QuarantaeneFehler as KontextFehler
from daimon.hub.declassify import Freigabeschein
from daimon.recorder.store import ART_OCR as ARCHIV_OCR, Archiv
from daimon.recorder.suche import Archivsuche
from daimon.recorder.suche import QuarantaeneFehler as SucheFehler

KANARIE = "Kontonummer DE00 1234"

# Was KEIN Schein ist. Jeder Eintrag ist etwas, das aus einem Versehen
# entsteht -- ein durchgereichtes Wahrheitswert-Flag, ein leerer Vorgabewert,
# ein Objekt, das zufaellig ein Feld gleichen Namens traegt.
KEINE_SCHEINE = [
    None,
    True,
    1,
    "t-1",
    object(),
    SimpleNamespace(turn_id="t-1"),
    SimpleNamespace(turn_id=""),
    Freigabeschein(turn_id=""),          # richtiger Typ, leere Runde
]


@pytest.fixture
def speicher(tmp_path):
    s = Kontextspeicher(verzeichnis=tmp_path / "context")
    s.hinzufuegen(ART_OCR, "org.kde.kate", KANARIE)
    return s


@pytest.fixture
def suche(tmp_path):
    a = Archiv(tmp_path / "archiv.db")
    a.migrieren()
    a.schreiben(ARCHIV_OCR, KANARIE, fenster="Mail")
    a.schliessen()
    return Archivsuche(tmp_path / "archiv.db")


@pytest.mark.parametrize("schein", KEINE_SCHEINE, ids=lambda s: repr(s)[:30])
def test_der_kontextspeicher_gibt_nichts_ohne_schein(speicher, schein):
    with pytest.raises(KontextFehler):
        speicher.freigeben(schein)


@pytest.mark.parametrize("schein", KEINE_SCHEINE, ids=lambda s: repr(s)[:30])
def test_die_archivsuche_ebenso(suche, schein):
    with pytest.raises(SucheFehler):
        suche.freigeben(schein, "Kontonummer")


def test_POSITIVKONTROLLE_mit_schein_kommt_beides_heraus(speicher, suche):
    """Ohne diese Zeile bestuenden beide Reihen oben auch Speicher, die IMMER
    verweigern -- und die waeren von aussen nicht von dichten zu
    unterscheiden."""
    schein = Freigabeschein(turn_id="t-1")
    eintraege = speicher.freigeben(schein)
    assert any(KANARIE in e.inhalt for e in eintraege[ART_OCR])
    treffer = suche.freigeben(schein, "Kontonummer")
    assert treffer and any(KANARIE in str(t.value) for t in treffer)


def test_beide_ausgaenge_urteilen_gleich(speicher, suche):
    """Der eigentliche Befund: nicht dass einer zu leise war, sondern dass sie
    verschieden waren. Ein Angreifer sucht sich den leiseren."""
    for schein in KEINE_SCHEINE:
        kontext_zu = archiv_zu = False
        try:
            speicher.freigeben(schein)
        except KontextFehler:
            kontext_zu = True
        try:
            suche.freigeben(schein, "Kontonummer")
        except SucheFehler:
            archiv_zu = True
        assert kontext_zu == archiv_zu, f"{schein!r}: {kontext_zu=} {archiv_zu=}"
