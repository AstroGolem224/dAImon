"""T-0.13 -- die Diagnose als BEFEHL, nicht nur als Klasse.

`docs/INSTALL.md` nennt `python -m daimon.hub.diag` unter "Pruefen, dass es
steht" -- und bis zum 17.08. hatte das Modul kein `main()`. `python -m`
importierte es, fuehrte nichts aus und endete mit 0: **eine dokumentierte
Pruefung, die stumm gelingt.** Wer sie fuhr, sah nichts und hielt das fuer
Erfolg.

Dieselbe Gestalt wie ein Zaehler ohne Ableser (`CLAUDE.md`): das Stueck ist
da, sein Zulauf fehlt. Diesmal war der Zulauf die Dokumentation, die es
aufrief.

Gemessen wird der BEFEHL gegen einen echten Socket -- eine Attrappe des
Sockets pruefte die Klasse, und die war nie das Problem.
"""
from __future__ import annotations

import json
import socket
import threading

import pytest

from daimon.hub import diag as D


@pytest.fixture
def diag_socket(tmp_path):
    """Ein Socket, der EINE Zeile JSON liefert -- wie der Hub es tut."""
    pfad = tmp_path / "diag.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    srv.listen(1)
    antwort = {"v": 1, "laufzeit_s": 12.5, "verworfen_gesamt": 0,
               "zaehler": {"rundenmarke": {"ausgegeben": 3, "eingeloest": 2}}}

    def bedienen():
        try:
            conn, _ = srv.accept()
            with conn:
                conn.sendall(json.dumps(antwort).encode() + b"\n")
        except OSError:
            pass

    faden = threading.Thread(target=bedienen, daemon=True)
    faden.start()
    yield tmp_path, antwort
    srv.close()
    faden.join(timeout=2.0)


def test_der_befehl_gibt_den_schnappschuss_aus(diag_socket, capsys):
    """DER BEFUND: vorher kam hier NICHTS und rc=0."""
    verzeichnis, antwort = diag_socket
    rc = D.main(["--runtime-dir", str(verzeichnis)])
    assert rc == 0
    ausgabe = capsys.readouterr().out
    assert ausgabe.strip(), "stumm -- genau der Fehler, der behoben werden soll"
    assert json.loads(ausgabe) == antwort


def test_ohne_hub_sagt_er_es_und_scheitert(tmp_path, capsys):
    """Ein Diagnosewerkzeug, das bei fehlendem Dienst schweigt, ist genau
    dann nutzlos, wenn man es braucht. Der Pfad gehoert in die Meldung --
    sonst sucht der Nutzer am falschen Ort."""
    rc = D.main(["--runtime-dir", str(tmp_path / "gibtsnicht")])
    assert rc == 1
    ausgabe = capsys.readouterr().out
    assert "gibtsnicht" in ausgabe and "diag.sock" in ausgabe


def test_eine_unlesbare_antwort_ist_kein_erfolg(tmp_path, capsys):
    """Sonst faende der Nutzer `rc=0` und keine Zahlen -- und wuesste nicht,
    ob der Hub schweigt oder Muell sendet."""
    pfad = tmp_path / "diag.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    srv.listen(1)

    def bedienen():
        try:
            conn, _ = srv.accept()
            with conn:
                conn.sendall(b"kein json\n")
        except OSError:
            pass

    faden = threading.Thread(target=bedienen, daemon=True)
    faden.start()
    try:
        rc = D.main(["--runtime-dir", str(tmp_path)])
    finally:
        srv.close()
        faden.join(timeout=2.0)
    assert rc == 1
    assert "unlesbar" in capsys.readouterr().out


def test_das_modul_ist_als_befehl_aufrufbar():
    """Die Zusage, die in INSTALL.md steht. Ein Modul ohne `main` laesst sich
    per `python -m` importieren und tut nichts -- rc=0, keine Ausgabe."""
    assert callable(getattr(D, "main", None)), (
        "daimon.hub.diag braucht ein main(), sonst ist `python -m "
        "daimon.hub.diag` in INSTALL.md eine Pruefung ohne Wirkung")
