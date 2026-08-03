"""T-3.8 — Erkenner, Protokoll, Units.

Was hier geprueft wird: die Formatgrenzen, die fuenf Absagegruende, dass jede
Antwort die Kennung traegt, und am echten Prozess die Socket-Aktivierung. Wo das
Modell gebraucht wird, steht ein `importorskip` plus eine Pruefung, ob die
Gewichte da sind -- ein Test, der wegen einer fehlenden Abhaengigkeit gruen ist,
ist ein Test, der nichts sagt, und `skipped` sagt es sichtbar.

Was hier NICHT geprueft wird: WER, Latenz-p95, kein CUDA am laufenden Prozess.
Das erste braucht die 21 Referenzaufnahmen und einen Vergleich gegen die
Grundlinie, das zweite den laufenden Dienst -- beides gehoert in den Pruefstand
(`tests/verify/T-3.8.sh`), und der entsteht blind. Die Messung meiner Seite liegt
in `tests/evidence/T-3.8-dienst.json`.
"""

import io
import json
import os
import socket
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from daimon.common.logging import get_logger
from daimon.gpu import stt as S

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/stt-referenz"
MODELL = REPO / S.MODELL_DIR
UNIT_SERVICE = REPO / "config/systemd/daimon-stt.service"
UNIT_SOCKET = REPO / "config/systemd/daimon-stt.socket"

hat_modell = pytest.mark.skipif(
    bool(S.modell_pruefen(str(MODELL))),
    reason=f"Gewichte fehlen ({MODELL.name}) -- spikes/stt-referenz/modell_holen.sh")


def stiller_logger():
    return get_logger("test", socket_path="/nicht/da", stream=io.StringIO())


def wav_schreiben(pfad: Path, *, rate=16000, kanaele=1, breite=2,
                  sekunden=0.5, amplitude=6000) -> Path:
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(kanaele)
        w.setsampwidth(breite)
        w.setframerate(rate)
        n = int(rate * sekunden)
        if breite == 2:
            rahmen = b"".join(struct.pack("<h", amplitude) * kanaele
                              for _ in range(n))
        else:
            rahmen = bytes([128]) * n * kanaele
        w.writeframes(rahmen)
    return pfad


# --------------------------------------------------------------------------
# Modellpruefung: die Meldung nennt, WAS fehlt
# --------------------------------------------------------------------------

def test_fehlendes_verzeichnis_wird_benannt():
    assert "Modellverzeichnis fehlt" in S.modell_pruefen("/gibt/es/nicht")


def test_unvollstaendiges_modell_nennt_die_fehlenden_dateien(tmp_path):
    (tmp_path / "tokens.txt").write_text("x")
    meldung = S.modell_pruefen(str(tmp_path))
    assert "unvollstaendig" in meldung
    assert "encoder.int8.onnx" in meldung
    # tokens.txt ist da und darf NICHT als fehlend gemeldet werden.
    assert "tokens.txt" not in meldung


def test_leere_modelldatei_ist_ein_eigener_fall(tmp_path):
    # Ein abgebrochener Download hinterlaesst genau das: die Datei ist da und
    # hat 0 Byte. Ohne eigenen Fall meldet sherpa spaeter einen Ladefehler, der
    # die Datei nicht nennt.
    for d in S.DATEIEN:
        (tmp_path / d).touch()
    assert "leer" in S.modell_pruefen(str(tmp_path))


@hat_modell
def test_das_mitgelieferte_modell_ist_vollstaendig():
    assert S.modell_pruefen(str(MODELL)) == ""


# --------------------------------------------------------------------------
# WAV-Grenzen: Rate darf abweichen, Kanaele und Breite nicht
# --------------------------------------------------------------------------

def test_16khz_mono_wird_gelesen(tmp_path):
    samples, rate, dauer = S.wav_lesen(str(wav_schreiben(tmp_path / "a.wav")))
    assert rate == 16000 and abs(dauer - 0.5) < 0.01
    assert len(samples) == 8000
    # s16 -> float32 in [-1, 1]: 6000/32768.
    assert abs(float(samples[0]) - 6000 / 32768) < 1e-6


def test_abweichende_rate_ist_kein_fehler(tmp_path):
    # sherpa resampelt selbst, und 8 kHz Telefonqualitaet soll erkannt werden
    # statt abgewiesen. Sichtbar bleibt sie ueber den Rueckgabewert.
    _, rate, _ = S.wav_lesen(str(wav_schreiben(tmp_path / "b.wav", rate=8000)))
    assert rate == 8000


def test_stereo_und_8bit_sind_fehler(tmp_path):
    with pytest.raises(S.WavFehler) as exc:
        S.wav_lesen(str(wav_schreiben(tmp_path / "c.wav", kanaele=2)))
    assert "Kanal" in str(exc.value)
    with pytest.raises(S.WavFehler):
        S.wav_lesen(str(wav_schreiben(tmp_path / "d.wav", breite=1)))


def test_kein_wav_ist_ein_formatfehler(tmp_path):
    (tmp_path / "e.wav").write_bytes(b"das ist kein WAV")
    with pytest.raises(S.WavFehler):
        S.wav_lesen(str(tmp_path / "e.wav"))


# --------------------------------------------------------------------------
# Die fuenf Absagegruende, jeder einzeln
# --------------------------------------------------------------------------

def erkenner_ohne_modell(tmp_path):
    return S.Erkenner(modell_dir=str(tmp_path / "leer"), log=stiller_logger())


def test_fehlendes_modell_ist_eine_absage_kein_absturz(tmp_path):
    e = erkenner_ohne_modell(tmp_path)
    e.laden()          # darf NICHT werfen
    a = e.transkribiere("/beliebig.wav")
    assert not a["ok"] and a["grund"] == "modell_fehlt"
    assert a["meldung"] and a["engine"] == S.ENGINE
    assert e.zustand()["absage"]


def test_fehlende_datei_und_fehlendes_feld_heissen_datei_fehlt(tmp_path):
    e = erkenner_ohne_modell(tmp_path)
    # Absichtlich ohne Modell: die Reihenfolge der Pruefungen ist selbst eine
    # Zusage -- `modell_fehlt` schlaegt `datei_fehlt`, weil ein Dienst ohne
    # Modell auch mit gueltiger Datei nichts kann.
    assert e.transkribiere(None)["grund"] == "modell_fehlt"


@hat_modell
@pytest.mark.parametrize("wav,grund", [
    (None, "datei_fehlt"),
    ("", "datei_fehlt"),
    (42, "datei_fehlt"),
    ("/gibt/es/nicht.wav", "datei_fehlt"),
])
def test_absagegruende_mit_modell(wav, grund):
    e = S.Erkenner(modell_dir=str(MODELL), threads=2, log=stiller_logger())
    e.absage = ""      # Modell ist da; nicht laden, wir brauchen es hier nicht
    assert e.transkribiere(wav)["grund"] == grund


def test_alle_gruende_sind_benannt_und_getrennt():
    # Ein Sammelgrund waere in T-3.14 unbrauchbar: "Datei weg" braucht eine
    # andere Anzeige als "Modell weg".
    assert S.GRUENDE == {"unlesbar", "unbekannte_art", "datei_fehlt",
                         "format_falsch", "modell_fehlt"}


# --------------------------------------------------------------------------
# Am echten Modell: Text, Stille, keine Nachbearbeitung
# --------------------------------------------------------------------------

@hat_modell
def test_ein_satz_wird_erkannt_und_die_kennung_ist_dabei():
    e = S.Erkenner(modell_dir=str(MODELL), threads=8, log=stiller_logger())
    e.laden()
    a = e.transkribiere(str(FIXTURE / "aufnahmen/satz-04.wav"))
    assert a["ok"]
    # Kriterium 1, zur Laufzeit statt per grep (T-1.7.v3).
    assert a["engine"] == "sherpa-onnx-transducer" and a["provider"] == "cpu"
    assert a["modell"] == MODELL.name and a["rate"] == 16000
    assert a["latenz_ms"] > 0 and a["audio_s"] > 1
    assert "Schnittstelle" in a["text"]


@hat_modell
def test_auf_stille_kommt_kein_text():
    # Die Positivkontrolle gegen Halluzination. whisper erfindet hier gern Text;
    # ein STT, das aus Raumrauschen einen Satz macht, ist im Betrieb schlimmer
    # als eines, das schweigt.
    e = S.Erkenner(modell_dir=str(MODELL), threads=8, log=stiller_logger())
    e.laden()
    a = e.transkribiere(str(FIXTURE / "aufnahmen/satz-21.wav"))
    assert a["ok"] and a["text"] == "", a["text"]


@hat_modell
def test_der_text_wird_nicht_nachbearbeitet():
    """Keine Ersetzungstabelle, kein Grossschreiben.

    Satz 01 ist der Beleg: das Modell hoert "Der Bild ist durch" statt "Der
    Build ist durch". Wer das per `sed` richtet, hat ein Modell im Modell, das
    niemand mitmisst -- und die WER-Messung waere ab da wertlos.
    """
    e = S.Erkenner(modell_dir=str(MODELL), threads=8, log=stiller_logger())
    e.laden()
    text = e.transkribiere(str(FIXTURE / "aufnahmen/satz-01.wav"))["text"]
    assert "Bild" in text and "Build" not in text


@hat_modell
def test_das_modell_wird_einmal_geladen():
    e = S.Erkenner(modell_dir=str(MODELL), threads=8, log=stiller_logger())
    e.laden()
    assert e.ladezeit_ms and e.ladezeit_ms > 100
    zuerst = e.transkribiere(str(FIXTURE / "aufnahmen/satz-06.wav"))
    dann = e.transkribiere(str(FIXTURE / "aufnahmen/satz-06.wav"))
    # Zweimal dieselbe Datei: waere das Modell je Anfrage neu geladen, laege die
    # zweite Latenz in der Groessenordnung der Ladezeit.
    assert dann["latenz_ms"] < e.ladezeit_ms / 2
    assert zuerst["text"] == dann["text"]
    assert e.anfragen == 2


# --------------------------------------------------------------------------
# Protokoll am echten Prozess
# --------------------------------------------------------------------------

@hat_modell
def test_dienst_ueber_eigenen_socket(tmp_path):
    sock = tmp_path / "stt.sock"
    p = subprocess.Popen(
        [sys.executable, "-m", "daimon.gpu.stt", "--socket", str(sock),
         "--modell-dir", str(MODELL), "--threads", "4"],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def frage(anfrage: dict) -> dict:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(60)
        c.connect(str(sock))
        c.sendall(json.dumps(anfrage).encode() + b"\n")
        antwort = json.loads(c.makefile("rb").readline())
        c.close()
        return antwort

    try:
        for _ in range(400):
            if sock.exists():
                break
            time.sleep(0.05)
        assert sock.exists(), "kein Socket"
        # 0600: was hier durchgeht, ist alles, was ins Mikrofon gesprochen wurde.
        assert oct(sock.stat().st_mode)[-3:] == "600"

        a = frage({"v": 1, "art": "transkribiere",
                   "wav": str(FIXTURE / "aufnahmen/satz-14.wav")})
        assert a["ok"] and "Bildschirm" in a["text"]

        z = frage({"v": 1, "art": "zustand"})
        assert z["geladen"] and z["anfragen"] >= 1 and z["pid"] > 0
        assert z["sprachen"] == ["de", "en"]

        assert frage({"v": 1, "art": "quatsch"})["grund"] == "unbekannte_art"
        assert frage({"v": 1, "art": "transkribiere",
                      "wav": "/nicht/da.wav"})["grund"] == "datei_fehlt"
        stereo = wav_schreiben(tmp_path / "stereo.wav", kanaele=2)
        assert frage({"v": 1, "art": "transkribiere",
                      "wav": str(stereo)})["grund"] == "format_falsch"

        # Unlesbare Zeile: keine JSON.
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(30)
        c.connect(str(sock))
        c.sendall(b"das ist kein json\n")
        assert json.loads(c.makefile("rb").readline())["grund"] == "unlesbar"
        c.close()
    finally:
        p.kill()
        p.wait(timeout=10)


def test_ohne_socket_und_ohne_systemd_startet_er_gar_nicht():
    p = subprocess.run([sys.executable, "-m", "daimon.gpu.stt",
                        "--modell-dir", "/gibt/es/nicht"],
                       cwd=REPO, capture_output=True, text=True, timeout=120)
    assert p.returncode != 0
    assert "LISTEN_FDS" in (p.stdout + p.stderr)


def test_sd_socket_prueft_die_pid(monkeypatch):
    monkeypatch.setenv("LISTEN_FDS", "1")
    monkeypatch.setenv("LISTEN_PID", str(os.getpid() + 1))
    assert S.sd_socket() is None


# --------------------------------------------------------------------------
# Units und Konfiguration
# --------------------------------------------------------------------------

def ohne_kommentare(text: str) -> str:
    """Die Units erklaeren im Fliesstext, was sie NICHT tun -- eine Textsuche
    ueber das Ganze prueft sonst Erwaehnung statt Direktive."""
    return "\n".join(z for z in text.splitlines()
                     if not z.lstrip().startswith("#"))


def test_unit_ist_gehaertet():
    text = ohne_kommentare(UNIT_SERVICE.read_text())
    for direktive in ("NoNewPrivileges=yes", "CapabilityBoundingSet=",
                      "ProtectSystem=strict", "ProtectHome=read-only",
                      "ProtectProc=invisible", "ProcSubset=pid",
                      "PrivateTmp=yes", "LimitCORE=0", "UMask=0077",
                      "RestrictAddressFamilies=AF_UNIX",
                      "MemoryDenyWriteExecute=yes", "PrivateDevices=yes",
                      "RuntimeDirectory=daimon",
                      "RuntimeDirectoryPreserve=yes"):
        assert direktive in text, direktive


def test_resources_bleibt_ungefiltert_und_das_ist_gemessen():
    # Mit `~@resources` laedt das Modell noch und der Prozess stirbt dann mit
    # status=31/SYS: onnxruntime heftet seine Rechenthreads an Kerne. Der Test
    # haelt die Messung fest, damit die naechste "Haertung" nicht denselben
    # Abend kostet -- beim TTS war es derselbe Filter, dort wegen PipeWire.
    zeilen = [z for z in ohne_kommentare(UNIT_SERVICE.read_text()).splitlines()
              if z.startswith("SystemCallFilter=~")]
    assert zeilen
    assert all("@resources" not in z for z in zeilen), zeilen


def test_der_dienst_wird_nur_ueber_den_socket_gestartet():
    text = ohne_kommentare(UNIT_SERVICE.read_text())
    assert "Requires=daimon-stt.socket" in text
    # Kein enable: ein Modell, das beim Anmelden 843 ms und 700 MB kostet, ohne
    # dass jemand etwas gesagt hat.
    assert "[Install]" not in text
    assert "Restart=on-failure" in text


def test_socket_hoert_auf_dem_vereinbarten_pfad():
    text = ohne_kommentare(UNIT_SOCKET.read_text())
    assert "ListenStream=%t/daimon/stt.sock" in text
    assert "SocketMode=0600" in text and "Accept=no" in text


def test_beide_units_legen_das_runtime_verzeichnis_an():
    for datei in (UNIT_SERVICE, UNIT_SOCKET):
        text = ohne_kommentare(datei.read_text())
        assert "RuntimeDirectory=daimon" in text, datei.name
        assert "RuntimeDirectoryPreserve=yes" in text, datei.name


def test_konfiguration_hat_keinen_provider_schalter():
    import tomllib
    with (REPO / "config/daimon.toml").open("rb") as fh:
        cfg = tomllib.load(fh)
    assert "threads" in cfg["stt"] and "modell_dir" in cfg["stt"]
    # "cpu" steht im Code. Ein Konfigurationswert waere ein Schalter, mit dem
    # sich ein CUDA-Provider einschalten laesst -- und dann waere die
    # 0-VRAM-Zusage keine Zusage mehr.
    assert "provider" not in cfg["stt"]


def test_die_referenzaufnahmen_liegen_im_repo():
    # Ein Pruefstand, dessen Material nur auf einer Maschine liegt, ist auf jeder
    # anderen gruen ohne zu messen (Fall 7 der Fehlerliste).
    manifest = json.loads((FIXTURE / "aufnahmen/manifest.json").read_text())
    assert len(manifest["aufnahmen"]) == 21
    for eintrag in manifest["aufnahmen"].values():
        assert (FIXTURE / "aufnahmen" / eintrag["datei"]).is_file()
    grundlinie = json.loads((FIXTURE / "herkunft.json").read_text())["grundlinie"]
    assert grundlinie["wer_de"] < grundlinie["schwelle_fuer_T-3.8"]
