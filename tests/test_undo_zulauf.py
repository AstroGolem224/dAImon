"""Der Undo-Broker hat keinen Aufrufer -- weil es nichts zurückzuholen gibt.

BEFUND T-4.8 K7 (Reviewer-Sitzung 19.08.):

    `daimon/brokers/fs/undo.py` trägt `vorbereiten`, `in_den_trash`,
    `kopie_anlegen`, `stash_anlegen`. Einziger Aufrufer im Produktbaum ist
    `vorbereiten` selbst. Die Zusage „schlägt die Vorbereitung fehl, wird die
    Mutation abgebrochen" hat im Betrieb keinen Fall, in dem sie greift.

**Nachgemessen, und das Bild ist größer als der Befund.** Es fehlt nicht der
Aufruf, es fehlt die ganze Kategorie:

    17 Aktionen im Katalog, `destructive: true` -> KEINE
    Zielgruppe je Aktion                        -> keine nennt eine
    (`audience` fällt damit auf die Vorgabe `dbus`, daemon.py:951)
    Absender an `fs-broker.sock`                -> nur der Hub

Medien, Audio, Fenster, Screenshots. Nichts davon fasst eine Datei an. Ein
Rückholpunkt für eine Mutation, die es nicht gibt, wäre Vorratscode -- und
zwar der Fehler aus CLAUDE.md §6, „derselbe Fehler von der anderen Seite".

**Warum hier kein Fix steht, obwohl ein Verifizierer rot ist.** `undo=` an
den Koordinator zu hängen, würde T-4.8 K7 grün machen, ohne dass sich im
Betrieb irgendetwas ändert: der Zweig `if self.undo is not None`
(`coordinator.py:187`) liefe weiterhin nie. Das wäre genau der zu freundliche
Fix, vor dem die eigene Übergabe in Abschnitt 4.1 warnt -- gebaut gegen die
Messung statt gegen die Zusage.

Stattdessen ein Wächter, wie ihn CLAUDE.md vorsieht: er fällt auf, sobald der
Zulauf entsteht. Wer die erste mutierende Aktion in den Katalog schreibt,
wird hier rot und findet, was dann zu tun ist.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
KATALOG = REPO / "config" / "actions" / "core.yaml"
UNDO = REPO / "daimon" / "brokers" / "fs" / "undo.py"
DAEMON = REPO / "daimon" / "hub" / "daemon.py"

# Zielgruppen, deren Broker Zustand ÄNDERN. `dbus` und `input` steuern
# fremde Anwendungen (Lautstärke, Fenster) -- das ist umkehrbar durch die
# Gegenaktion und braucht kein Artefakt. `fs` und `exec` fassen Dateien an.
MUTIERENDE_ZIELE = {"fs", "exec"}


def _aktionen() -> list[dict]:
    roh = yaml.safe_load(KATALOG.read_text(encoding="utf-8")) or {}
    akt = roh.get("actions") or []
    assert akt, "der Katalog ist leer -- dann misst diese Datei nichts"
    return akt


def _mutierende() -> list[dict]:
    return [a for a in _aktionen()
            if a.get("destructive")
            or a.get("audience") in MUTIERENDE_ZIELE]


# -- Der Stand, festgehalten ----------------------------------------------

def test_es_gibt_heute_keine_mutierende_aktion():
    """Die Grundlage für alles Weitere hier. Fällt diese Zeile, ist es kein
    Fehler, sondern das Signal: ab jetzt gilt der Wächter unten."""
    assert _mutierende() == [], (
        "es gibt jetzt mutierende Aktionen: "
        f"{[a['id'] for a in _mutierende()]} -- siehe den Wächter unten")


def test_der_katalog_ist_ueberhaupt_lesbar():
    """Positivkontrolle gegen den stummen Vergleich: wäre `actions` leer oder
    umbenannt, wäre `_mutierende()` ebenfalls leer und der Test darüber
    grün, ohne etwas gemessen zu haben. Vier Falschbefunde an einem Tag kamen
    genau daher."""
    akt = _aktionen()
    assert len(akt) >= 10
    assert all("id" in a for a in akt)
    # Und der Schlüssel, an dem die Unterscheidung hängt, kommt vor:
    assert any("destructive" in a for a in akt), (
        "kein Eintrag nennt `destructive` -- dann prüft der Wächter ein Feld, "
        "das der Katalog nicht führt")


# -- Der Wächter für den Tag, an dem es eine gibt -------------------------

def test_WAECHTER_eine_mutierende_aktion_verlangt_den_undo_zulauf():
    """Sobald der Katalog eine Aktion trägt, die Dateien anfasst, MUSS der
    Hub dem Koordinator ein `undo` übergeben.

    Der Zweig dafür steht seit jeher da (`coordinator.py:187`: `if self.undo
    is not None`), und `undo.vorbereiten` ist gebaut und geprüft. Was fehlt,
    ist die eine Zeile in `_aktionsteile`.

    Dieser Test schreibt sie NICHT vorsorglich hin -- welche Undo-Art zu
    welcher Aktion gehört (`trash`, `kopie`, `git-stash`), hängt an der
    Aktion, und die gibt es noch nicht. Geraten wäre er schlimmer als
    fehlend.
    """
    mutierend = _mutierende()
    if not mutierend:
        pytest.skip("keine mutierende Aktion im Katalog -- nichts zu sichern")

    baum = ast.parse(DAEMON.read_text(encoding="utf-8"))
    verdrahtet = any(
        isinstance(k, ast.Call)
        and any(kw.arg == "undo" for kw in k.keywords)
        for k in ast.walk(baum))
    assert verdrahtet, (
        f"der Katalog trägt jetzt {[a['id'] for a in mutierend]}, aber der "
        "Hub übergibt dem Koordinator kein `undo=` -- die Mutation liefe "
        "ohne Rückholpunkt (T-4.8 K7). `undo.vorbereiten` ist gebaut; es "
        "fehlt die Zuordnung Aktion -> Undo-Art (trash/kopie/git-stash).")


def test_WAECHTER_die_abbruchzusage_steht_im_koordinator():
    """Die andere Hälfte, und sie gilt heute schon: SCHLÄGT die Vorbereitung
    fehl, wird die Mutation abgebrochen -- nicht ungeschützt ausgeführt.

    Der Zweig läuft im Betrieb nie, weil `undo` None ist. Er muss trotzdem
    stehen bleiben: wer ihn entfernt, während niemand hinsieht, hat die
    Zusage entfernt, und der Tag, an dem die erste Mutation kommt, findet
    einen Koordinator ohne Abbruch.
    """
    quelle = (REPO / "daimon" / "hub" / "coordinator.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    gefunden = False
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Try):
            continue
        ruft_undo = any(
            isinstance(k, ast.Call)
            and getattr(k.func, "attr", "") == "undo"
            for stmt in knoten.body for k in ast.walk(stmt))
        bricht_ab = any(isinstance(k, ast.Return)
                        for h in knoten.handlers for k in ast.walk(h))
        gefunden = gefunden or (ruft_undo and bricht_ab)
    assert gefunden, (
        "der Koordinator ruft `undo` nicht mehr in einem try, das bei "
        "Fehlschlag zurückkehrt -- die Zusage aus T-4.8 ist weg, und sie "
        "fehlt genau dann, wenn sie zum ersten Mal gebraucht wird")


# -- Der Broker selbst bleibt, was er ist ---------------------------------

def test_undo_wirft_statt_ein_feld_zu_setzen():
    """`vorbereiten` hat kein `ok`-Feld und gibt kein `None` zurück -- ein
    Aufrufer, der ein Feld prüfen muss, vergisst es. Das ist die Bauform, auf
    die sich der Abbruch oben verlässt."""
    from daimon.brokers.fs.undo import UndoFehler, vorbereiten

    with pytest.raises(UndoFehler):
        vorbereiten("unbekannte-art")


def test_die_drei_arten_sind_erreichbar():
    """Ohne diese Zeile bestünde der Test darüber auch für eine Fassung, die
    JEDE Art ablehnt -- und die wäre im Betrieb ein Totalausfall des
    Rückholpunkts, sobald es einen gäbe."""
    quelle = UNDO.read_text(encoding="utf-8")
    for art in ("trash", "kopie", "git-stash"):
        assert f'art == "{art}"' in quelle, art
