"""T-4.8 — kein Artefakt, keine Mutation.

Jeder Fehlerfall wird ERZWUNGEN, nicht simuliert weggelassen, und in jedem
Fall wird danach gemessen, ob die Ursprungsdatei unveraendert ist.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from daimon.brokers.fs import undo


def datei(tmp_path: Path, name: str = "wichtig.txt", inhalt: str = "Daten") -> Path:
    p = tmp_path / name
    p.write_text(inhalt, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# Loeschen -> Trash
# --------------------------------------------------------------------------

def test_loeschen_legt_die_datei_mit_zettel_in_den_trash(tmp_path):
    quelle = datei(tmp_path)
    trash = tmp_path / "Trash"
    a = undo.vorbereiten("trash", quelle=quelle, trash=trash, jetzt=0.0)
    assert a.verifiziert and a.art == "trash"
    assert not quelle.exists()
    assert a.pfad.read_text(encoding="utf-8") == "Daten"
    info = (trash / "info" / f"{a.pfad.name}.trashinfo").read_text(encoding="utf-8")
    assert info.startswith("[Trash Info]")
    assert "Path=" in info and "DeletionDate=" in info
    assert str(quelle.resolve()).replace("/", "%2F") in info.replace("/", "%2F")


def test_der_trash_eintrag_laesst_sich_zurueckholen(tmp_path):
    quelle = datei(tmp_path)
    a = undo.vorbereiten("trash", quelle=quelle, trash=tmp_path / "Trash")
    zurueck = undo.wiederherstellen(a)
    assert zurueck == quelle
    assert quelle.read_text(encoding="utf-8") == "Daten"


def test_zwei_gleichnamige_dateien_ueberschreiben_sich_im_trash_nicht(tmp_path):
    trash = tmp_path / "Trash"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    erste = datei(tmp_path / "a", "gleich.txt", "eins")
    zweite = datei(tmp_path / "b", "gleich.txt", "zwei")
    a1 = undo.vorbereiten("trash", quelle=erste, trash=trash)
    a2 = undo.vorbereiten("trash", quelle=zweite, trash=trash)
    assert a1.pfad != a2.pfad
    assert {a1.pfad.read_text(), a2.pfad.read_text()} == {"eins", "zwei"}


def test_ueber_die_dateisystemgrenze_wird_abgewiesen_und_nichts_angefasst(tmp_path, monkeypatch):
    """Verschieben waere hier Kopieren plus Loeschen -- mit einem Moment
    dazwischen, in dem beides halb passiert ist."""
    quelle = datei(tmp_path)
    echt = os.stat

    def fremdes_geraet(pfad, *a, **kw):
        ergebnis = echt(pfad, *a, **kw)
        if "Trash" in str(pfad):
            class S:
                st_dev = ergebnis.st_dev + 1
                st_size = ergebnis.st_size
            return S()
        return ergebnis

    monkeypatch.setattr(undo.os, "stat", fremdes_geraet)
    with pytest.raises(undo.UndoFehler) as f:
        undo.vorbereiten("trash", quelle=quelle, trash=tmp_path / "Trash")
    assert "Dateisystem" in str(f.value)
    assert quelle.read_text(encoding="utf-8") == "Daten"


# --------------------------------------------------------------------------
# Ueberschreiben -> Kopie
# --------------------------------------------------------------------------

def test_kopie_wird_angelegt_und_verifiziert(tmp_path):
    quelle = datei(tmp_path, inhalt="A" * 4096)
    a = undo.vorbereiten("kopie", quelle=quelle, ablage=tmp_path / "undo")
    assert a.verifiziert and a.groesse == 4096
    assert a.pfad.read_text(encoding="utf-8") == "A" * 4096
    assert quelle.exists(), "die Quelle bleibt, es wird nur kopiert"


def test_eine_halb_geschriebene_kopie_gilt_nicht_als_artefakt(tmp_path):
    """`cp` meldet auch dann 0, wenn der letzte Block nicht mehr passte.

    Der Fall wird hier erzwungen: rc=0, und die Zieldatei ist zu kurz. Ohne
    die Groessenpruefung waere das ein Artefakt, das es nicht gibt -- und die
    Mutation liefe ungeschuetzt.
    """
    quelle = datei(tmp_path, inhalt="A" * 4096)

    def luegender_cp(argv, **kw):
        Path(argv[-1]).write_text("A" * 10, encoding="utf-8")

        class E:
            returncode = 0
            stdout = stderr = ""
        return E()

    with pytest.raises(undo.UndoFehler) as f:
        undo.kopie_anlegen(quelle, tmp_path / "undo", lauf=luegender_cp)
    assert "unvollstaendig" in str(f.value)
    assert quelle.read_text(encoding="utf-8") == "A" * 4096


def test_ein_fehlgeschlagenes_cp_bricht_ab(tmp_path):
    quelle = datei(tmp_path)

    def volles_dateisystem(argv, **kw):
        class E:
            returncode = 1
            stdout = ""
            stderr = "cp: Schreiben fehlgeschlagen: Auf dem Gerät ist kein Speicherplatz mehr verfügbar"
        return E()

    with pytest.raises(undo.UndoFehler) as f:
        undo.kopie_anlegen(quelle, tmp_path / "undo", lauf=volles_dateisystem)
    assert "Speicherplatz" in str(f.value)
    assert quelle.read_text(encoding="utf-8") == "Daten"


def test_die_kopie_laesst_sich_zurueckspielen(tmp_path):
    quelle = datei(tmp_path, inhalt="alt")
    a = undo.vorbereiten("kopie", quelle=quelle, ablage=tmp_path / "undo")
    quelle.write_text("neu", encoding="utf-8")  # die Mutation
    undo.wiederherstellen(a)
    assert quelle.read_text(encoding="utf-8") == "alt"


# --------------------------------------------------------------------------
# Git-Verwerfen -> stash
# --------------------------------------------------------------------------

def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "Test")
    (r / "a.txt").write_text("erste Fassung\n", encoding="utf-8")
    git(r, "add", "a.txt")
    git(r, "commit", "-qm", "erst")
    return r


def test_stash_legt_die_aenderung_ab_und_meldet_sie(repo):
    (repo / "a.txt").write_text("geaendert\n", encoding="utf-8")
    a = undo.vorbereiten("git-stash", repo=repo)
    assert a.verifiziert and a.art == "git-stash"
    assert "daimon-undo" in a.hinweis
    # Der Arbeitsbaum ist sauber, die Aenderung liegt im Stash.
    zustand = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                             capture_output=True, text=True)
    assert zustand.stdout.strip() == ""
    subprocess.run(["git", "-C", str(repo), "stash", "pop"], check=True,
                   capture_output=True, text=True)
    assert (repo / "a.txt").read_text(encoding="utf-8") == "geaendert\n"


def test_ohne_aenderung_gibt_es_kein_artefakt_und_damit_keine_mutation(repo):
    """`git stash` liefert auch bei "nichts zu sichern" den Rueckgabewert 0.

    Ohne die Zaehlung haetten wir ein Artefakt behauptet, das nicht existiert.
    """
    with pytest.raises(undo.UndoFehler) as f:
        undo.vorbereiten("git-stash", repo=repo)
    assert "nichts abgelegt" in str(f.value)


def test_git_stash_wird_nicht_automatisch_zurueckgeholt(repo):
    (repo / "a.txt").write_text("geaendert\n", encoding="utf-8")
    a = undo.vorbereiten("git-stash", repo=repo)
    with pytest.raises(undo.UndoFehler) as f:
        undo.wiederherstellen(a)
    assert "Menschen" in str(f.value)


# --------------------------------------------------------------------------
# Die Zusage selbst
# --------------------------------------------------------------------------

def test_eine_unbekannte_art_wird_abgewiesen(tmp_path):
    with pytest.raises(undo.UndoFehler):
        undo.vorbereiten("irgendwas", quelle=datei(tmp_path))


def test_der_fehler_ist_eine_ausnahme_und_kein_feld(tmp_path):
    """Ein Aufrufer kann ein `ok`-Feld uebersehen, eine Ausnahme nicht."""
    with pytest.raises(undo.UndoFehler):
        undo.vorbereiten("trash", quelle=tmp_path / "gibtesnicht",
                         trash=tmp_path / "Trash")
