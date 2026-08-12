"""T-5.8 -- der VLM-Worker.

Was hier NICHT geprueft wird: dass das Modell etwas Richtiges sagt. Das
braucht sechs Gigabyte VRAM und eine Minute, und es gehoert in den
zurueckgestellten Pruefstand. Pruefbar ohne GPU ist alles, was am 12.08. an
dieser Maschine gemessen wurde -- und jede dieser Messungen hat einen Test,
weil keine davon aus einer Dokumentation stammt.
"""
from __future__ import annotations

import struct
import zlib

import numpy as np
import pytest

from daimon.gpu import vlm


# -- Der Socketpfad --------------------------------------------------------

def test_ein_zu_langer_socketpfad_wird_mit_grund_abgelehnt(tmp_path):
    """Der Server meldet sonst nur `couldn't bind` und nennt den Grund nicht.

    Der erste Versuch am 12.08. lag bei 113 Zeichen und sah aus wie ein
    Rechtefehler.
    """
    lang = "/tmp/" + "a" * 120 + ".sock"
    with pytest.raises(vlm.VlmFehler) as fehler:
        vlm.VlmServer(socket_pfad=lang, modell="egal")
    assert "108" in str(fehler.value) or "107" in str(fehler.value)
    assert "sun_path" in str(fehler.value)


def test_ein_kurzer_socketpfad_geht_durch(tmp_path):
    s = vlm.VlmServer(socket_pfad="/run/user/1000/x.sock", modell="egal")
    assert s.socket_pfad.endswith(".sock")


# -- Der Backend-Pfad ------------------------------------------------------

def test_der_backendpfad_zeigt_auf_eine_datei_nicht_auf_ein_verzeichnis():
    """Auf das Verzeichnis gezeigt, meldet der Server `Is a directory` und
    laeuft danach STILL auf der CPU weiter -- ein VLM auf der CPU antwortet,
    es dauert nur Minuten, und das sieht nach Last aus."""
    assert vlm.CUDA_BACKEND.endswith(".so")


# -- max_pixels ------------------------------------------------------------

def test_max_pixels_ist_gesetzt_und_nicht_die_architekturobergrenze():
    """T--1.10: auf einem Vollbild gibt qwen3-vl:8b NULL Zeichen zurueck --
    `done_reason=length` wird im Denken erreicht, `response` bleibt leer."""
    assert vlm.MAX_PIXELS == 1920 * 1080
    assert vlm.LANGE_KANTE == 1920


@pytest.mark.parametrize("hoehe, breite", [
    (4000, 4000),        # quadratisch -- hier rutschte fast das Doppelte durch
    (1440, 5120),        # der echte Bildschirm
    (3000, 2000),        # hochkant
    (2100, 2100),        # knapp ueber der Grenze
])
def test_max_pixels_bindet_auch_wenn_die_lange_kante_passt(hoehe, breite):
    """Die lange Kante allein reicht NICHT.

    4000x4000 wird mit ihr zu 1920x1920, und das sind 3 686 400 Pixel gegen
    ein Budget von 2 073 600. Ein Bildschirm ist meistens breit, ein
    Fensterzuschnitt muss es nicht sein.
    """
    klein = vlm.herunterskalieren(np.zeros((hoehe, breite, 3), np.uint8))
    assert max(klein.shape[:2]) <= vlm.LANGE_KANTE
    assert klein.shape[0] * klein.shape[1] <= vlm.MAX_PIXELS


# -- Herunterskalieren -----------------------------------------------------

def test_die_lange_kante_wird_auf_1920_gebracht():
    gross = np.zeros((1440, 5120, 3), dtype=np.uint8)
    klein = vlm.herunterskalieren(gross)
    assert max(klein.shape[:2]) == 1920
    assert klein.shape[0] == 540               # Seitenverhaeltnis bleibt


def test_kleine_bilder_bleiben_unveraendert():
    """Hochskalieren erfindet Pixel, die nie auf dem Bildschirm standen."""
    klein = np.zeros((300, 600, 3), dtype=np.uint8)
    assert vlm.herunterskalieren(klein).shape == klein.shape


def test_das_herunterskalieren_verwischt_keine_striche():
    """Nearest-Neighbour, keine Mittelung: der Empfaenger soll Text lesen,
    und Mittelung verwischt genau die duennen Striche."""
    bild = np.zeros((100, 4000, 3), dtype=np.uint8)
    bild[:, 2000] = 255                        # ein einzelner heller Strich
    klein = vlm.herunterskalieren(bild)
    assert klein.max() == 255                  # er ist entweder da oder weg
    assert set(np.unique(klein)) <= {0, 255}   # nichts dazwischen


# -- PNG ohne Pillow -------------------------------------------------------

def test_das_png_ist_ein_gueltiges_png():
    bild = np.zeros((4, 6, 3), dtype=np.uint8)
    bild[1, 2] = (10, 20, 30)
    daten = vlm.als_png(bild)
    assert daten[:8] == b"\x89PNG\r\n\x1a\n"
    breite, hoehe, tiefe, farbtyp = struct.unpack(">IIBB", daten[16:26])
    assert (breite, hoehe, tiefe, farbtyp) == (6, 4, 8, 2)


def test_das_png_traegt_die_pixel_zurueck():
    """Ein Kopf, der stimmt, und Daten, die nicht stimmen, sind schlimmer als
    gar kein Bild -- das Modell beschriebe dann etwas Erfundenes."""
    rng = np.random.default_rng(7)
    bild = rng.integers(0, 256, (5, 9, 3), dtype=np.uint8)
    daten = vlm.als_png(bild)
    # IDAT herausziehen und entpacken -- ohne Pillow, wie beim Schreiben.
    pos, idat = 8, b""
    while pos < len(daten):
        laenge = struct.unpack(">I", daten[pos:pos + 4])[0]
        art = daten[pos + 4:pos + 8]
        if art == b"IDAT":
            idat += daten[pos + 8:pos + 8 + laenge]
        pos += 12 + laenge
    roh = zlib.decompress(idat)
    zeilen = [roh[i * (9 * 3 + 1) + 1:(i + 1) * (9 * 3 + 1)] for i in range(5)]
    zurueck = np.frombuffer(b"".join(zeilen), np.uint8).reshape(5, 9, 3)
    assert np.array_equal(zurueck, bild)


# -- Der Befehl ------------------------------------------------------------

def test_ohne_projektor_kein_mmproj_schalter():
    """Ohne Datei kein Schalter -- ein leerer `--mmproj` waere ein Startfehler.

    Dass der Server dann NICHT sieht, faengt `_sehen_pruefen()` ab; hier geht
    es nur darum, dass kein Schalter ohne Wert entsteht.
    """
    s = vlm.VlmServer(socket_pfad="/run/user/1000/x.sock", modell="/pfad/m.gguf")
    assert "--mmproj" not in s.befehl()


def test_ein_blinder_server_wird_beim_START_abgelehnt(monkeypatch):
    """Gemessen am 12.08.: ohne mmproj antwortet der Server auf JEDES Bild mit
    HTTP 500, laeuft aber weiter und haelt VRAM. Das sieht aus wie ein
    kaputtes Modell und nicht wie eine fehlende Datei. `/props` sagt es beim
    Start: `modalities = {"vision": false, ...}`.
    """
    s = vlm.VlmServer(socket_pfad="/run/user/1000/x.sock", modell="/m.gguf")
    monkeypatch.setattr(s, "anfrage", lambda *a, **k: {
        "modalities": {"vision": False, "video": False, "audio": False}})
    monkeypatch.setattr(s, "beenden", lambda: None)
    with pytest.raises(vlm.VlmFehler) as fehler:
        s._sehen_pruefen()
    assert "mmproj" in str(fehler.value)
    assert "vision=false" in str(fehler.value)


def test_ein_sehender_server_kommt_durch(monkeypatch):
    s = vlm.VlmServer(socket_pfad="/run/user/1000/x.sock", modell="/m.gguf")
    monkeypatch.setattr(s, "anfrage", lambda *a, **k: {
        "modalities": {"vision": True}})
    s._sehen_pruefen()          # darf nicht werfen


def test_mit_projektor_steht_der_schalter_da():
    """Der Schalter bleibt: das naechste Modell kann getrennt kommen."""
    s = vlm.VlmServer(socket_pfad="/run/user/1000/x.sock",
                      modell="/pfad/m.gguf", mmproj="/pfad/p.gguf")
    b = s.befehl()
    assert "--mmproj" in b and "/pfad/p.gguf" in b


def test_der_server_hoert_nur_auf_dem_unix_socket():
    """Kein `--port`, kein Host mit Punkten: ein TCP-Ohr waere ein Netzweg in
    einem Dienst, der keinen haben darf."""
    s = vlm.VlmServer(socket_pfad="/run/user/1000/x.sock", modell="/m.gguf")
    b = s.befehl()
    assert "--port" not in b
    assert b[b.index("--host") + 1].endswith(".sock")


def test_kein_ollama_daemon_im_befehl():
    s = vlm.VlmServer(socket_pfad="/run/user/1000/x.sock", modell="/m.gguf")
    assert not any("serve" == a for a in s.befehl())


# -- Der Server stirbt mit dem Worker --------------------------------------

def test_pdeathsig_wird_gesetzt():
    """`beenden()` allein reicht nicht -- es laeuft nicht mehr, wenn der
    Worker abstuerzt, und dann ueberlebte ein llama-server mit sechs
    Gigabyte VRAM den Prozess, der ihn gestartet hat."""
    import inspect
    quelle = inspect.getsource(vlm)
    assert "PR_SET_PDEATHSIG" in quelle
    assert "preexec_fn=_sterbe_mit_eltern" in quelle


# -- Das Gate --------------------------------------------------------------

def test_unbekanntes_vram_ist_nicht_frei(monkeypatch):
    """Wer hier durchlaesst, laedt sechs Gigabyte neben ein Spiel."""
    import daimon.gpu.worker as w
    monkeypatch.setattr(w, "fullscreen_aktiv", lambda **k: False)
    monkeypatch.setattr(w, "vram_frei_mib", lambda **k: None)
    offen, grund = vlm.gate_offen()
    assert offen is False and "unbekannt" in grund


def test_vollbild_schliesst_das_gate(monkeypatch):
    import daimon.gpu.worker as w
    monkeypatch.setattr(w, "fullscreen_aktiv", lambda **k: True)
    monkeypatch.setattr(w, "vram_frei_mib", lambda **k: 40_000)
    offen, grund = vlm.gate_offen()
    assert offen is False and "Vollbild" in grund


def test_zu_wenig_vram_schliesst_das_gate(monkeypatch):
    import daimon.gpu.worker as w
    monkeypatch.setattr(w, "fullscreen_aktiv", lambda **k: False)
    monkeypatch.setattr(w, "vram_frei_mib", lambda **k: 100)
    offen, grund = vlm.gate_offen()
    assert offen is False and "100" in grund


def test_ein_offenes_gate_nennt_trotzdem_seinen_grund(monkeypatch):
    """Ein Wahrheitswert ohne Grund ist von „gemessen und knapp" nicht zu
    unterscheiden."""
    import daimon.gpu.worker as w
    monkeypatch.setattr(w, "fullscreen_aktiv", lambda **k: False)
    monkeypatch.setattr(w, "vram_frei_mib", lambda **k: 40_000)
    offen, grund = vlm.gate_offen()
    assert offen is True and "40000" in grund.replace(" ", "")


# -- Der HTTP-Status -------------------------------------------------------

class Faden:
    """Ein Socket-Ersatz, der eine vorgegebene Antwort ausliefert."""

    def __init__(self, antwort: bytes) -> None:
        self.antwort = antwort
        self.gesendet = b""

    def settimeout(self, *_a): pass
    def connect(self, *_a): pass
    def sendall(self, d): self.gesendet += d
    def close(self): pass

    def recv(self, _n):
        d, self.antwort = self.antwort, b""
        return d


def _mit_antwort(monkeypatch, antwort: bytes):
    import socket as sockmod
    monkeypatch.setattr(vlm.socket, "socket", lambda *a, **k: Faden(antwort))
    return vlm.VlmServer(socket_pfad="/run/user/1000/x.sock", modell="/m.gguf")


def test_ein_503_beim_laden_ist_kein_erfolg(monkeypatch):
    """Gemessen am 12.08.: `llama-server` antwortet waehrend des Ladens mit
    503 und einem GUELTIGEN JSON-Koerper. Der erste Entwurf las nur den
    Rumpf, hielt das fuer einen Erfolg und gab den Server frei, bevor er ein
    Modell hatte.
    """
    koerper = b'{"error":{"message":"Loading model","code":503}}'
    s = _mit_antwort(monkeypatch, b"HTTP/1.1 503 Service Unavailable\r\n"
                     b"Content-Type: application/json\r\n\r\n" + koerper)
    with pytest.raises(vlm.VlmFehler) as fehler:
        s.anfrage("GET", "/health")
    assert "503" in str(fehler.value)
    assert "Loading model" in str(fehler.value)


def test_die_bereitschaftspruefung_faellt_auf_ein_503_nicht_herein(monkeypatch):
    koerper = b'{"error":{"message":"Loading model","code":503}}'
    s = _mit_antwort(monkeypatch, b"HTTP/1.1 503 Service Unavailable\r\n\r\n"
                     + koerper)
    assert s._erreichbar() is False


def test_ein_200_geht_durch(monkeypatch):
    s = _mit_antwort(monkeypatch,
                     b'HTTP/1.1 200 OK\r\n\r\n{"status":"ok"}')
    assert s.anfrage("GET", "/health") == {"status": "ok"}


def test_eine_antwort_ohne_statuszeile_wird_gemeldet(monkeypatch):
    """Ein leerer Rumpf ohne Kopf sah vorher aus wie `{}` -- also wie eine
    gueltige, leere Antwort."""
    s = _mit_antwort(monkeypatch, b"kaputt\r\n\r\n")
    with pytest.raises(vlm.VlmFehler) as fehler:
        s.anfrage("GET", "/health")
    assert "Statuszeile" in str(fehler.value)
