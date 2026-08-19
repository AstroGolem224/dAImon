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


# ---------------------------------------------------------------------------
# `_unit` -- die Grundlage JEDER Peer-Pruefung, und sie hatte keinen Test
# ---------------------------------------------------------------------------
#
# BEFUND vom 19.08.: der Regex konsumierte den abschliessenden Schraegstrich
# (`(?:/|$)` statt `(?=/|$)`) und uebersprang damit jedes zweite Segment des
# cgroup-Pfades. Getroffen wurde die richtige Unit nur bei GERADER Zahl
# uebriger Segmente.
#
# Aufgefallen ist es erst, als der GPU-Worker ueber seinen echten Weg ans
# Gate sollte: er lief als `daimon-gpu@sonde.service` und wurde vom Hub als
# `app-daimon\x2dgpu.slice` abgewiesen. systemd gruppiert Template-Instanzen
# unter `app.slice` in eine eigene `app-<template>.slice` -- eine Ebene mehr,
# und damit kippt die Paritaet. Der GPU-Worker ist das einzige Template im
# Projekt und laeuft socket-aktiviert nur Sekunden.
#
# Diese Pfade sind ECHT, an dieser Maschine ausgelesen. Ein selbst gebauter
# Pfad haette hier genau die Ebene weggelassen, um die es geht.

CGROUP_FS = ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
             "daimon-fs.service")
CGROUP_GPU = ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
              "app-daimon\\x2dgpu.slice/daimon-gpu@sonde.service")
CGROUP_SCOPE = ("0::/user.slice/user-1000.slice/user@1000.service/"
                "app.slice/app-org.kde.konsole.slice/"
                "vte-spawn-1234.scope")


def _unit_aus(text: str, tmp_path, monkeypatch) -> str:
    """`_unit` gegen einen echten cgroup-Text, ohne echten Prozess.

    Ersetzt wird `open` fuer GENAU diesen Pfad -- nicht `_unit` selbst. Eine
    Attrappe an der Funktion haette den Befund nicht finden koennen, weil sie
    genau die Zeile ersetzt haette, in der er stand.
    """
    datei = tmp_path / "cgroup"
    datei.write_text(text, encoding="utf-8")
    echt = open
    monkeypatch.setattr(
        "builtins.open",
        lambda p, *a, **kw: (echt(datei, *a, **kw)
                             if str(p).endswith("/cgroup")
                             else echt(p, *a, **kw)))
    return ipc._unit(4711)


def test_die_unit_steht_am_ENDE_des_pfades(tmp_path, monkeypatch):
    """DER BEFUND: hier stand `app-daimon\\x2dgpu.slice`."""
    assert _unit_aus(CGROUP_GPU, tmp_path, monkeypatch) == \
        "daimon-gpu@sonde.service"


def test_der_einfache_fall_bleibt_richtig(tmp_path, monkeypatch):
    """Er war es auch vorher -- aber aus dem falschen Grund. Ohne diese Zeile
    bestuende der Test darueber auch eine Fassung, die immer das letzte
    Segment nimmt, egal ob es eine Unit ist."""
    assert _unit_aus(CGROUP_FS, tmp_path, monkeypatch) == "daimon-fs.service"


def test_ein_scope_wird_auch_erkannt(tmp_path, monkeypatch):
    """Ein Terminal ist ein Scope, keine Service-Unit. Wer von Hand startet,
    soll einen Namen bekommen und nicht die Slice darueber."""
    assert _unit_aus(CGROUP_SCOPE, tmp_path, monkeypatch) == \
        "vte-spawn-1234.scope"


def test_ohne_erkennbare_unit_kommt_leer_zurueck(tmp_path, monkeypatch):
    """Fail-closed: ein leerer Name steht auf keiner Allowlist."""
    assert _unit_aus("0::/\n", tmp_path, monkeypatch) == ""


def test_die_paritaet_entscheidet_NICHT_mehr(tmp_path, monkeypatch):
    """Der Kern des Befunds als eigene Zeile: derselbe Dienst, einmal eine
    Ebene tiefer. Vorher kippte das Ergebnis, jetzt nicht mehr."""
    flach = "0::/app.slice/daimon-x.service"
    tief = "0::/app.slice/app-daimon.slice/daimon-x.service"
    assert _unit_aus(flach, tmp_path, monkeypatch) == "daimon-x.service"
    assert _unit_aus(tief, tmp_path, monkeypatch) == "daimon-x.service"
