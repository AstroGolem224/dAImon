"""T-4.9 — einmal aufloesen, dann nur noch ueber den Deskriptor.

Der Wettlauf wird ECHT gefahren: zwischen Aufloesung und Operation wird ein
Symlink eingeschoben. Ein Broker, der den Pfad ein zweites Mal aufloest,
schreibt dann in die untergeschobene Datei.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from daimon.brokers.fs import broker as fs


def test_openat2_ist_auf_dieser_maschine_da():
    """Positivkontrolle. Ohne sie sagt jede Abweisung darunter nichts."""
    assert fs.verfuegbar(), "ohne openat2 sind die Zusagen unten wertlos"


def test_eine_datei_unterhalb_der_wurzel_laesst_sich_lesen(tmp_path):
    (tmp_path / "unter").mkdir()
    (tmp_path / "unter" / "a.txt").write_text("Daten", encoding="utf-8")
    with fs.aufloesen(tmp_path, "unter/a.txt") as griff:
        assert fs.lesen(griff) == b"Daten"


def test_ein_absoluter_pfad_wird_abgewiesen(tmp_path):
    with pytest.raises(fs.FSFehler):
        fs.aufloesen(tmp_path, "/etc/passwd")


def test_punkt_punkt_wird_abgewiesen(tmp_path):
    (tmp_path / "unter").mkdir()
    with pytest.raises(fs.FSFehler):
        fs.aufloesen(tmp_path, "unter/../../etc/passwd")


def test_ein_symlink_im_pfad_wird_abgewiesen(tmp_path):
    geheim = tmp_path / "geheim"
    geheim.mkdir()
    (geheim / "id_ed25519").write_text("privat", encoding="utf-8")
    wurzel = tmp_path / "arbeit"
    wurzel.mkdir()
    os.symlink(geheim, wurzel / "brücke")
    with pytest.raises(fs.FSFehler):
        fs.aufloesen(wurzel, "brücke/id_ed25519")


def test_ein_symlink_als_ziel_wird_abgewiesen(tmp_path):
    geheim = tmp_path / "id_ed25519"
    geheim.write_text("privat", encoding="utf-8")
    wurzel = tmp_path / "arbeit"
    wurzel.mkdir()
    os.symlink(geheim, wurzel / "harmlos.txt")
    # `aufloesen` oeffnet den letzten Namensteil selbst (K2) -- die Abweisung
    # kommt jetzt HIER, nicht erst bei `lesen`.
    with pytest.raises(fs.FSFehler):
        fs.aufloesen(wurzel, "harmlos.txt")


def test_der_wettlauf_zwischen_genehmigung_und_ausfuehrung(tmp_path):
    """Der eigentliche Test: der Tausch kommt NACH der Aufloesung.

    So sieht der Angriff aus: die Vorschau zeigt `arbeit/notiz.txt`, der
    Mensch klickt, und in der Zwischenzeit wird der Name durch einen Symlink
    auf den Schluessel ersetzt. `aufloesen` haelt den Ziel-FD schon offen
    (K2) -- der Tausch danach aendert an diesem FD nichts mehr: `schreiben`
    trifft weiter die urspruengliche (jetzt entlinkte) Datei, nicht den
    Schluessel. Genau das ist die Zusage: kein zweiter Namenslookup zwischen
    Genehmigung und Mutation, nicht bloss eine Abweisung bei einem Symlink.
    """
    schluessel = tmp_path / "id_ed25519"
    schluessel.write_text("privat", encoding="utf-8")
    wurzel = tmp_path / "arbeit"
    wurzel.mkdir()
    ziel = wurzel / "notiz.txt"
    ziel.write_text("alt", encoding="utf-8")

    with fs.aufloesen(wurzel, "notiz.txt", schreibend=True) as griff:
        # Der Angreifer schlaegt zu, NACHDEM aufgeloest wurde.
        ziel.unlink()
        os.symlink(schluessel, ziel)

        # Der FD zeigt weiter auf die urspruengliche Datei -- kein Fehler,
        # kein zweiter Lookup.
        fs.schreiben(griff, b"neu")

    # Der Schluessel ist unberuehrt.
    assert schluessel.read_text(encoding="utf-8") == "privat"
    # Und der Name `notiz.txt` zeigt weiter auf den Symlink des Angreifers --
    # der Broker hat ihn nicht angefasst.
    assert ziel.is_symlink()


def test_schreiben_trifft_die_datei_am_griff(tmp_path):
    (tmp_path / "a.txt").write_text("alt", encoding="utf-8")
    with fs.aufloesen(tmp_path, "a.txt", schreibend=True) as griff:
        fs.schreiben(griff, b"neu")
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "neu"


def test_umbenennen_nimmt_keinen_pfad(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with fs.aufloesen(tmp_path, "a.txt") as griff:
        with pytest.raises(fs.FSFehler):
            fs.umbenennen(griff, "../anderswo.txt")
        fs.umbenennen(griff, "b.txt")
    assert (tmp_path / "b.txt").is_file()


def test_der_griff_schliesst_seinen_deskriptor(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    griff = fs.aufloesen(tmp_path, "a.txt")
    fd = griff.dirfd
    griff.schliessen()
    with pytest.raises(OSError):
        os.fstat(fd)


def test_es_gibt_keinen_stillen_rueckfall_auf_os_open():
    """Gelesen im Quelltext -- die Zusage ist eine Abwesenheit.

    Ein Rueckfall waere hier nicht als Fehlverhalten messbar, solange der
    Kernel openat2 kennt. Also wird nachgesehen, dass keiner da ist.
    """
    quelle = Path("daimon/brokers/fs/broker.py").read_text(encoding="utf-8")
    assert "ENOSYS" in quelle
    kern = quelle.split("def openat2")[1].split("@dataclass")[0]
    assert "os.open(" not in kern, "openat2 faellt auf os.open zurueck"
