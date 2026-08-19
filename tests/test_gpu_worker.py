"""T-3.7 — GPU-Worker-Geruest und die Ladesperre im Hub.

Ohne echtes Modell, ohne echte GPU und ohne Compositor. Was hier NICHT geprueft
werden kann, steht im Verifizierer: dass der Prozess wirklich endet und dass
das VRAM danach zurueckkommt. Ein Test im selben Interpreter kann ueber ein
Prozessende nichts sagen -- deshalb startet der Prozesstest hier einen echten
Unterprozess und misst an dessen Exit, nicht an einer Flagge.

`nvidia-smi` und `busctl` werden ueber einen PATH-Stub ersetzt. Beide Aufrufe
stehen im Pruefling ohne absoluten Pfad -- das ist die Voraussetzung dafuer und
die Lehre aus T-2.7: gemessen wird, was der Pruefling tun WOLLTE.
"""

import io
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from daimon.common.logging import get_logger
from daimon.gpu import worker as W
from daimon.hub.daemon import GPU_GRUENDE, GPU_SOCKET, Hub
from daimon.hub.focus import FocusReceiver
from conftest import eigene_unit

REPO = Path(__file__).resolve().parents[1]


def stiller_logger():
    return get_logger("test", socket_path="/nicht/da", stream=io.StringIO())


def stub(dir_: Path, name: str, rc: int, aus: str) -> None:
    """Ein ausfuehrbarer Stub im PATH. Er protokolliert nichts und tut nichts."""
    p = dir_ / name
    p.write_text(f"#!/bin/sh\nprintf '%s\\n' {json.dumps(aus)}\nexit {rc}\n")
    p.chmod(0o755)


@pytest.fixture(autouse=True)
def _der_worker_darf_fragen(monkeypatch, tmp_path):
    """Seit dem 19.08. laesst `gpu.sock` nur `daimon-gpu@*.service` heran.

    Der Pruefstand laeuft unter keiner Instanz dieses Templates. Erlaubt wird
    die Unit des Testprozesses, echt gemessen; geprueft wird die Sperre selbst
    -- samt Instanzvergleich -- in `test_hub_socket_allowlisten.py`.
    """
    from daimon.hub import daemon as _D
    monkeypatch.setattr(_D, "GPU_UNITS", (eigene_unit(tmp_path),))


@pytest.fixture
def gpu_umgebung(tmp_path, monkeypatch):
    """PATH-Stubs: 30000 MiB frei, kein Vollbild."""
    b = tmp_path / "bin"
    b.mkdir()
    stub(b, "nvidia-smi", 0, "30000")
    stub(b, "busctl", 0, json.dumps({"type": "bd", "data": [False, 1.5]}))
    monkeypatch.setenv("PATH", f"{b}:{os.environ.get('PATH','')}")
    return b


@pytest.fixture
def hub(tmp_path, gpu_umgebung):
    h = Hub(runtime_dir=tmp_path / "rt", log=stiller_logger())
    h.start()
    time.sleep(0.3)
    yield h
    h.stop()


def frage(rt: Path, anfrage: dict) -> dict:
    return W.hub_anfrage(str(rt / GPU_SOCKET), anfrage, timeout_s=10.0)


# --------------------------------------------------------------------------
# Kriterium 5 -- drei unterscheidbare Absagegruende
# --------------------------------------------------------------------------

def test_die_drei_gruende_sind_getrennt_benannt():
    """Ein gemeinsames `error: true` waere fuer T-3.14 unbrauchbar."""
    assert GPU_GRUENDE == {"vram", "fullscreen", "lade_sperre"}


def test_zu_wenig_vram_wird_als_vram_abgesagt(hub, gpu_umgebung):
    stub(gpu_umgebung, "nvidia-smi", 0, "1500")
    a = frage(hub.runtime_dir, {"art": "laden", "modell": "stt",
                                "vram_mib": 2600})
    assert a["ok"] is False
    assert a["grund"] == "vram"
    # Die Zahlen gehoeren in die Absage: "zu wenig" ohne "wieviel" ist
    # fuer die Fehlersuche wertlos.
    assert a["frei_mib"] == 1500 and a["noetig_mib"] == 2600
    assert a["reserve_mib"] > 0


def test_reserve_wird_eingerechnet(hub, gpu_umgebung):
    """Genau passend ist nicht genug -- der naechste, der nachfordert, ist
    der Compositor."""
    stub(gpu_umgebung, "nvidia-smi", 0, "2700")
    a = frage(hub.runtime_dir, {"art": "laden", "modell": "stt",
                                "vram_mib": 2600})
    assert a["grund"] == "vram"


def test_unmessbares_vram_sagt_ab(hub, gpu_umgebung):
    """Fehlrichtung: bei unbekanntem VRAM wird NICHT geladen. Ein Modell im
    vollen VRAM nimmt den Compositor mit, und dann ist die Sitzung weg."""
    stub(gpu_umgebung, "nvidia-smi", 1, "")
    a = frage(hub.runtime_dir, {"art": "laden", "modell": "stt",
                                "vram_mib": 100})
    assert a["grund"] == "vram" and a["frei_mib"] is None


def test_fullscreen_wird_als_fullscreen_abgesagt(hub, gpu_umgebung):
    stub(gpu_umgebung, "busctl", 0,
         json.dumps({"type": "bd", "data": [True, 0.4]}))
    a = frage(hub.runtime_dir, {"art": "laden", "modell": "stt",
                                "vram_mib": 100})
    assert a["ok"] is False and a["grund"] == "fullscreen"


def test_zweiter_lader_bekommt_lade_sperre(hub):
    erste = frage(hub.runtime_dir, {"art": "laden", "modell": "stt",
                                    "vram_mib": 100})
    assert erste["ok"] is True and erste["sperre"]
    zweite = frage(hub.runtime_dir, {"art": "laden", "modell": "tts",
                                     "vram_mib": 100})
    assert zweite["ok"] is False and zweite["grund"] == "lade_sperre"
    assert zweite["rest_s"] > 0


# --------------------------------------------------------------------------
# Kriterium 4 -- Serialisierung im Hub, und die Sperre verfaellt
# --------------------------------------------------------------------------

def test_nach_fertig_darf_der_naechste(hub):
    erste = frage(hub.runtime_dir, {"art": "laden", "modell": "stt",
                                    "vram_mib": 100})
    assert frage(hub.runtime_dir, {"art": "fertig",
                                   "sperre": erste["sperre"]})["ok"] is True
    assert frage(hub.runtime_dir, {"art": "laden", "modell": "tts",
                                   "vram_mib": 100})["ok"] is True


def test_sperre_verfaellt_nach_frist(hub):
    """Ein beim Laden gestorbener Worker darf nicht alles dauerhaft
    blockieren. Fail-safe ist hier OEFFNEN nach Frist."""
    hub.gpu_frist_s = 0.3
    assert frage(hub.runtime_dir, {"art": "laden", "vram_mib": 100})["ok"]
    assert frage(hub.runtime_dir, {"art": "laden",
                                   "vram_mib": 100})["grund"] == "lade_sperre"
    time.sleep(0.4)
    assert frage(hub.runtime_dir, {"art": "laden", "vram_mib": 100})["ok"]


def test_fremde_marke_gibt_nicht_frei(hub):
    """Sonst raeumt ein verspaeteter `fertig`-Ruf eines toten Workers die
    Sperre des naechsten weg."""
    frage(hub.runtime_dir, {"art": "laden", "vram_mib": 100})
    assert frage(hub.runtime_dir, {"art": "fertig",
                                   "sperre": "deadbeef"})["ok"] is False
    assert frage(hub.runtime_dir, {"art": "laden",
                                   "vram_mib": 100})["grund"] == "lade_sperre"


def test_die_vram_pruefung_laeuft_unter_der_sperre(hub, gpu_umgebung):
    """Der eigentliche Punkt des Tasks: waehrend eine Sperre gehalten wird,
    kommt gar niemand bis zur VRAM-Pruefung. Belegt, indem nvidia-smi so
    gestellt wird, dass es JEDE Anfrage bewilligen wuerde -- und die zweite
    trotzdem an der Sperre haengenbleibt."""
    stub(gpu_umgebung, "nvidia-smi", 0, "999999")
    assert frage(hub.runtime_dir, {"art": "laden", "vram_mib": 1})["ok"]
    assert frage(hub.runtime_dir, {"art": "laden",
                                   "vram_mib": 1})["grund"] == "lade_sperre"


def test_unlesbares_und_unbekanntes_sind_keine_absagegruende(hub):
    """Protokollfehler duerfen sich nicht als einer der drei Gruende tarnen."""
    for a in ({"art": "quatsch"}, ["kein", "objekt"]):
        antwort = hub.gpu_anfrage(a)
        assert antwort["ok"] is False
        assert antwort["grund"] not in GPU_GRUENDE


# --------------------------------------------------------------------------
# Kriterium 3 -- die Fullscreen-Pruefung geht ueber T-0.12
# --------------------------------------------------------------------------

def test_fullscreen_unbekannt_erlaubt_und_wird_gemeldet(hub, gpu_umgebung):
    """Ist der Fokus-Dienst tot, kostet ein Ladevorgang ein paar Frames; ihn
    zu verweigern kostet das Sprachsystem dauerhaft. Aber die Nachsicht muss
    SICHTBAR sein."""
    stub(gpu_umgebung, "busctl", 1, "")
    a = frage(hub.runtime_dir, {"art": "laden", "vram_mib": 100})
    assert a["ok"] is True and a["fullscreen_bekannt"] is False


def test_noch_kein_fenster_gesehen_ist_nicht_kein_vollbild(gpu_umgebung):
    """alter_s < 0 heisst "niemand weiss es" -- nicht "kein Vollbild"."""
    assert FocusReceiver().zustand() == (False, -1.0)
    stub(gpu_umgebung, "busctl", 0,
         json.dumps({"type": "bd", "data": [False, -1.0]}))
    assert W.fullscreen_aktiv() is None


def test_focus_zustand_meldet_vollbild_und_alter():
    rx = FocusReceiver()
    rx.handle("activated", "u1", "Spiel", "steam", "steam", True, 7,
              0, 0, 5120, 1440)
    voll, alter = rx.zustand()
    assert voll is True and 0.0 <= alter < 5.0


def test_fullscreen_aktiv_liest_busctl(gpu_umgebung):
    stub(gpu_umgebung, "busctl", 0,
         json.dumps({"type": "bd", "data": [True, 0.2]}))
    assert W.fullscreen_aktiv() is True


# --------------------------------------------------------------------------
# Worker -- Laden, Kaltstart, Absage
# --------------------------------------------------------------------------

def worker(hub, **kw) -> W.Worker:
    kw.setdefault("modell", "stt")
    kw.setdefault("idle_s", 0.5)
    kw.setdefault("ladedauer_s", 0.0)
    kw.setdefault("vram_mib", 100)
    return W.Worker(hub_socket=str(hub.runtime_dir / GPU_SOCKET),
                    log=stiller_logger(), **kw)


def test_kaltstartzeit_wird_gemeldet(hub):
    w = worker(hub, ladedauer_s=0.2)
    a = w.laden()
    assert a["ok"] is True and a["geladen"] is True
    # Kriterium 6. Die Zahl schliesst den Interpreterstart ein, ist also
    # groesser als die Ladedauer allein.
    assert a["kaltstart_ms"] >= 200.0
    # Und sie steht in JEDER Antwort, nicht nur in der ersten.
    assert w.zustand()["kaltstart_ms"] == a["kaltstart_ms"]


def test_der_worker_gibt_die_sperre_zurueck(hub):
    worker(hub).laden()
    assert frage(hub.runtime_dir, {"art": "laden", "vram_mib": 100})["ok"]


def test_absage_geht_mit_grund_durch_bis_zum_client(hub, gpu_umgebung):
    stub(gpu_umgebung, "nvidia-smi", 0, "10")
    a = worker(hub).laden()
    assert a["ok"] is False and a["grund"] == "vram"
    assert a["frei_mib"] == 10


def test_ohne_hub_wird_nicht_geladen(tmp_path):
    """Ohne den Hub gibt es keine Serialisierung. Das ist die vierte, ehrliche
    Absage -- und ausdruecklich keiner der drei Gruende."""
    w = W.Worker(modell="stt", hub_socket=str(tmp_path / "gibts-nicht.sock"),
                 log=stiller_logger())
    a = w.laden()
    assert a["ok"] is False and a["grund"] == "hub_weg"
    assert a["grund"] not in GPU_GRUENDE
    assert w.geladen is False


def test_alter_s_zaehlt_ab_prozessstart():
    """Nicht ab Import: bei einem socket-aktivierten Prozess ist der
    Interpreterstart der groesste Posten des Kaltstarts."""
    aus = subprocess.run(
        [sys.executable, "-c",
         "from daimon.gpu.worker import alter_s; print(alter_s())"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert aus.returncode == 0
    assert 0.0 < float(aus.stdout.strip()) < 60.0


# --------------------------------------------------------------------------
# Kriterium 2 -- der Leerlauf endet im PROZESSENDE
# --------------------------------------------------------------------------

def test_leerlauf_beendet_den_prozess(hub, gpu_umgebung, tmp_path):
    """Der Kern des Tasks, und er ist nur an einem echten Prozess messbar:
    keine Flagge, kein "entladen", sondern ein Exitcode und eine PID, die
    danach nicht mehr existiert."""
    sock = tmp_path / "w.sock"
    p = subprocess.Popen(
        [sys.executable, "-m", "daimon.gpu.worker", "--modell", "stt",
         "--socket", str(sock), "--hub-socket",
         str(hub.runtime_dir / GPU_SOCKET), "--idle-s", "1.0",
         "--ladedauer-s", "0.0", "--vram-mib", "100"],
        cwd=REPO, env={**os.environ, "PATH": os.environ["PATH"]},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.05)
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(10.0)
        c.connect(str(sock))
        c.sendall(b'{"art":"status"}\n')
        a = json.loads(c.makefile("rb").readline())
        c.close()
        assert a["ok"] is True and a["geladen"] is True
        assert a["kaltstart_ms"] > 0
        # Kein Signal, kein terminate: die Frist laeuft ab, und der Prozess
        # geht von selbst.
        assert p.wait(timeout=20) == 0
    finally:
        if p.poll() is None:
            p.kill()
    # Danach ist der Socket unbedient -- der Worker ist wirklich weg und
    # nicht bloss still.
    with pytest.raises(OSError):
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(2.0)
        c.connect(str(sock))
        c.sendall(b'{"art":"status"}\n')
        if not c.makefile("rb").readline():
            raise ConnectionResetError("niemand da")


def test_absage_beendet_den_prozess_ebenfalls(hub, gpu_umgebung, tmp_path):
    """Ein Worker ohne Modell hat nichts zu bedienen. Ihn warten zu lassen
    waere ein Prozess, der auf eine Bedingung pollt, die der naechste Aufruf
    ohnehin neu prueft."""
    stub(gpu_umgebung, "nvidia-smi", 0, "10")
    sock = tmp_path / "w2.sock"
    p = subprocess.Popen(
        [sys.executable, "-m", "daimon.gpu.worker", "--modell", "stt",
         "--socket", str(sock), "--hub-socket",
         str(hub.runtime_dir / GPU_SOCKET), "--idle-s", "30",
         "--ladedauer-s", "0.0", "--vram-mib", "2600"],
        cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.05)
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(10.0)
        c.connect(str(sock))
        c.sendall(b'{"art":"status"}\n')
        a = json.loads(c.makefile("rb").readline())
        c.close()
        assert a["ok"] is False and a["grund"] == "vram"
        # Absage zuerst zustellen, DANN gehen. Ein Prozess, der vor der
        # Antwort endet, ist keine geordnete Absage.
        assert p.wait(timeout=20) == 0
    finally:
        if p.poll() is None:
            p.kill()


def test_ohne_socket_und_ohne_systemd_startet_er_gar_nicht():
    """Sonst waere er gestartet und unerreichbar -- der Zustand, in dem ein
    Dienst gruen aussieht und nichts tut."""
    aus = subprocess.run(
        [sys.executable, "-m", "daimon.gpu.worker", "--modell", "stt"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert aus.returncode != 0
    assert "LISTEN_FDS" in aus.stderr


def test_sd_socket_prueft_die_pid(monkeypatch):
    """LISTEN_PID wird vererbt. Ein Kind, das fd 3 fuer seinen haelt,
    uebernimmt einen fremden Deskriptor."""
    monkeypatch.setenv("LISTEN_FDS", "1")
    monkeypatch.setenv("LISTEN_PID", str(os.getpid() + 1))
    assert W.sd_socket() is None


# --------------------------------------------------------------------------
# Kriterium 1 -- eine Template-Unit, ein Socket je Modelltyp
# --------------------------------------------------------------------------

UNIT = REPO / "config" / "systemd" / "daimon-gpu@.service"
SOCK_UNIT = REPO / "config" / "systemd" / "daimon-gpu@.socket"


def test_genau_eine_template_unit():
    vorhanden = sorted(p.name for p in UNIT.parent.glob("daimon-gpu*"))
    assert vorhanden == ["daimon-gpu@.service", "daimon-gpu@.socket"]


def test_socket_traegt_die_instanz_im_pfad():
    """Ein Socket je Modelltyp -- sonst teilten sich zwei Modelltypen einen
    Prozess, und das Prozessende des einen naehme das andere mit."""
    text = ohne_kommentare(SOCK_UNIT.read_text())
    assert "ListenStream=%t/daimon/gpu-%i.sock" in text
    assert "Accept=no" in text
    assert "SocketMode=0600" in text


def ohne_kommentare(text: str) -> str:
    """Die Unit erklaert im Fliesstext, was sie NICHT tut. Eine Textsuche ueber
    das Ganze schluege daran an und pruefte Erwaehnung statt Direktive --
    derselbe Fehler wie im ersten Anlauf des SO_PEERCRED-Tests."""
    return "\n".join(z for z in text.splitlines()
                     if not z.lstrip().startswith("#"))


def test_unit_startet_ueber_den_socket_und_nicht_von_allein():
    text = ohne_kommentare(UNIT.read_text())
    assert "Requires=daimon-gpu@%i.socket" in text
    assert "Restart=no" in text
    assert "[Install]" not in text, "ein enable waere VRAM beim Anmelden"


def test_unit_uebergibt_die_instanz_als_modell():
    assert "--modell %i" in ohne_kommentare(UNIT.read_text())


def test_unit_ist_gehaertet():
    text = ohne_kommentare(UNIT.read_text())
    for direktive in ("NoNewPrivileges=yes", "CapabilityBoundingSet=",
                      "ProtectSystem=strict", "ProtectHome=read-only",
                      "ProtectProc=invisible", "ProcSubset=pid",
                      "PrivateTmp=yes", "LimitCORE=0", "UMask=0077",
                      "RestrictAddressFamilies=AF_UNIX"):
        assert direktive in text, direktive
