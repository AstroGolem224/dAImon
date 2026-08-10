"""T-0.7 — Tests fuer die authentifizierte IPC-Schicht.

Der Positiv-Kanarienvogel steht zuerst und ist nicht optional: eine Umsetzung,
die *alles* ablehnt, wuerde saemtliche Negativtests bestehen. Ohne den Beweis,
dass der richtige Fall durchgeht, beweisen die Ablehnungen nichts.
"""

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from daimon.common import ipc


@pytest.fixture
def rt(tmp_path):
    d = tmp_path / "daimon"
    d.mkdir()
    return d


def verbinde(pfad: Path, *, halten: float = 0.0):
    """Client in einem Thread, damit accept() nicht blockiert."""
    def lauf():
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(str(pfad))
        if halten:
            time.sleep(halten)
        c.close()
    t = threading.Thread(target=lauf, daemon=True)
    t.start()
    return t


# --------------------------------------------------------------------------
# Positiv-Kanarienvogel
# --------------------------------------------------------------------------

def test_kanarienvogel_richtiger_socket_richtiger_typ(rt):
    srv = ipc.listen(rt, "hookbridge")
    try:
        verbinde(ipc.socket_path(rt, "hookbridge"), halten=0.4)
        conn, peer = ipc.accept(srv, "hookbridge")
        assert peer.pid == os.getpid()
        assert peer.uid == os.getuid()
        assert peer.lebt
        ipc.pruefe_typ("hookbridge", "hook")   # muss durchgehen
        conn.close()
    finally:
        srv.close()


# --------------------------------------------------------------------------
# Vier Negativfaelle
# --------------------------------------------------------------------------

def test_negativ_falscher_typ_auf_richtigem_socket():
    """eyes darf kein hook-Event senden -- selbst wenn eyes kompromittiert ist,
    kann es nicht in die Rolle der Hook-Bridge schluepfen."""
    with pytest.raises(ipc.MessageTypeError):
        ipc.pruefe_typ("eyes", "hook")


def test_negativ_richtiger_typ_auf_falschem_socket():
    with pytest.raises(ipc.MessageTypeError):
        ipc.pruefe_typ("hookbridge", "screen")


def test_ears_darf_keine_rundenmarke_behaupten():
    """Design 2.4: dem Wake-Word war nur ein API-Kontingent zugesagt, keine
    Rundenmarke -- und mit Plan C gibt es nicht einmal das Wake-Word. Die
    Ohren melden Aeusserungen, sonst nichts; wer ein Wake-Word nachruestet,
    bekommt einen EIGENEN Typ auf dem Kontingentweg (Kommentar an der
    Tabelle), nicht diesen zurueck."""
    assert ipc.PRODUZENTEN["ears"] == frozenset({"utterance"})
    with pytest.raises(ipc.MessageTypeError):
        ipc.pruefe_typ("ears", "intent_mark")
    ipc.pruefe_typ("ears", "utterance")   # Positivkontrolle


def test_negativ_falsche_uid(rt):
    srv = ipc.listen(rt, "hookbridge")
    try:
        verbinde(ipc.socket_path(rt, "hookbridge"), halten=0.4)
        with pytest.raises(ipc.PeerError) as ex:
            ipc.accept(srv, "hookbridge", erlaubte_uid=os.getuid() + 12345)
        assert "uid" in str(ex.value)
    finally:
        srv.close()


def test_negativ_fremde_unit(rt):
    srv = ipc.listen(rt, "hookbridge")
    try:
        verbinde(ipc.socket_path(rt, "hookbridge"), halten=0.4)
        with pytest.raises(ipc.PeerError) as ex:
            ipc.accept(srv, "hookbridge", erlaubte_units={"gibt-es-nicht.service"})
        assert "Unit" in str(ex.value)
    finally:
        srv.close()


def test_ablehnung_erzeugt_audit_eintrag(rt):
    """Eine abgewiesene Verbindung ohne Spur waere unsichtbar -- und gerade
    die will man sehen."""
    gesehen = []
    srv = ipc.listen(rt, "hookbridge")
    try:
        verbinde(ipc.socket_path(rt, "hookbridge"), halten=0.4)
        with pytest.raises(ipc.PeerError):
            ipc.accept(srv, "hookbridge", erlaubte_uid=os.getuid() + 12345,
                       audit=lambda was, peer: gesehen.append((was, peer.pid)))
        assert gesehen and gesehen[0][0] == "uid_abweichung"
    finally:
        srv.close()


# --------------------------------------------------------------------------
# Das Rennen: gestorbene Gegenstelle
# --------------------------------------------------------------------------

def test_gestorbene_gegenstelle_wird_abgewiesen(rt):
    """Ein Prozess, der zwischen connect und accept stirbt, darf weder zum
    Absturz fuehren noch stillschweigend akzeptiert werden."""
    srv = ipc.listen(rt, "hookbridge")
    try:
        pfad = ipc.socket_path(rt, "hookbridge")
        kind = subprocess.Popen([
            sys.executable, "-c",
            f"import socket; c=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); "
            f"c.connect({str(pfad)!r})",
        ])
        kind.wait(timeout=10)
        time.sleep(0.2)
        try:
            conn, peer = ipc.accept(srv, "hookbridge")
            # Wenn der Kernel den pidfd noch aufloest, muss die PID stimmen und
            # der Prozess als tot erkennbar sein -- niemals eine fremde PID.
            assert peer.pid == kind.pid
            conn.close()
        except ipc.PeerError as ex:
            assert "beendet" in str(ex) or "auflösbar" in str(ex)
    finally:
        srv.close()


def test_pid_aus_pidfd_meldet_minus_eins_statt_zu_raten():
    assert ipc._pid_aus_pidfd(999999) == -1


# --------------------------------------------------------------------------
# Socketpfade und Rechte
# --------------------------------------------------------------------------

def test_socket_hat_modus_0600(rt):
    srv = ipc.listen(rt, "hookbridge")
    try:
        assert ipc.ist_0600(ipc.socket_path(rt, "hookbridge"))
    finally:
        srv.close()


def test_ein_socket_je_produzent(rt):
    a = ipc.listen(rt, "hookbridge")
    b = ipc.listen(rt, "eyes")
    try:
        assert ipc.socket_path(rt, "hookbridge") != ipc.socket_path(rt, "eyes")
        assert ipc.socket_path(rt, "hookbridge").exists()
        assert ipc.socket_path(rt, "eyes").exists()
    finally:
        a.close()
        b.close()


def test_unbekannter_produzent_wird_abgelehnt(rt):
    with pytest.raises(ValueError):
        ipc.listen(rt, "gibt-es-nicht")


def test_alter_socket_wird_ersetzt(rt):
    """Ein Rest aus einem Absturz darf das Binden nicht verhindern."""
    a = ipc.listen(rt, "hookbridge")
    a.close()
    b = ipc.listen(rt, "hookbridge")
    b.close()


# --------------------------------------------------------------------------
# Der Weg selbst
# --------------------------------------------------------------------------

def test_so_peerpidfd_wird_benutzt():
    """Waere hier SO_PEERCRED, haetten wir das Wiederverwendungsrennen."""
    quelle = Path(ipc.__file__).read_text()
    assert "SO_PEERPIDFD" in quelle
    assert ipc.SO_PEERPIDFD == getattr(socket, "SO_PEERPIDFD", 77)


def test_so_peercred_wird_nicht_aufgerufen():
    """Erwaehnen darf man es -- der Modulkopf erklaert ausfuehrlich, warum es
    der falsche Weg ist. AUFRUFEN darf man es nicht. Geprueft wird deshalb der
    Syntaxbaum, nicht der Text: eine Erklaerung im Docstring ist erwuenscht,
    ein getsockopt(..., SO_PEERCRED) waere das Rennen.
    """
    import ast

    baum = ast.parse(Path(ipc.__file__).read_text())
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        namen = {n.attr for n in ast.walk(knoten) if isinstance(n, ast.Attribute)}
        namen |= {n.id for n in ast.walk(knoten) if isinstance(n, ast.Name)}
        assert "SO_PEERCRED" not in namen, (
            "SO_PEERCRED wird aufgerufen -- das ist das PID-Wiederverwendungsrennen")


def test_aufloesung_haelt_den_pidfd_bis_zum_schluss():
    """Wandert os.close(pidfd) vor die /proc-Zugriffe, ist das Rennen zurueck.
    Geprueft wird, dass das Schliessen in einem finally steht und die
    /proc-Zugriffe davor liegen."""
    import ast, inspect

    quelle = inspect.getsource(ipc.peer_of)
    baum = ast.parse(quelle.strip())
    trys = [n for n in ast.walk(baum) if isinstance(n, ast.Try) and n.finalbody]
    assert trys, "peer_of schliesst den pidfd nicht in einem finally"
    schliesst_im_finally = any(
        isinstance(k, ast.Call) and getattr(k.func, "attr", "") == "close"
        for t in trys for stmt in t.finalbody for k in ast.walk(stmt)
    )
    assert schliesst_im_finally, "os.close(pidfd) steht nicht im finally"
