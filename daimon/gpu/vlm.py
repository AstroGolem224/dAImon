"""T-5.8 -- der VLM-Worker. Semantik, wenn OCR nicht reicht.

Vier Dinge ueber diese Maschine, die am 12.08. gemessen wurden und in keiner
Dokumentation stehen:

**Der Unix-Socket-Pfad darf hoechstens 108 Zeichen haben.** Ein laengerer
scheitert mit `couldn't bind HTTP server socket` und OHNE Angabe des Grundes.
Der erste Versuch lag bei 113 Zeichen und sah aus wie ein Rechtefehler.

**`GGML_BACKEND_PATH` will eine DATEI, kein Verzeichnis.** Auf das Verzeichnis
gezeigt, meldet der Server `cannot read file data: Is a directory` und laeuft
danach still auf der CPU weiter -- mit `--gpu-layers option will be ignored`
als einziger Warnung. Ein VLM auf der CPU antwortet, es dauert nur Minuten
statt Sekunden, und das sieht nach Last aus, nicht nach Fehlkonfiguration.

**Der Projektor fehlt, und Ollama liefert ihn nicht mit.** Diese Einheit hat
zuerst das Gegenteil behauptet: das Manifest von `qwen3-vl:8b` hat keine
Projektor-Schicht, und der Server meldet beim Laden `detected Ollama-format
qwen3vl GGUF; applying compatibility fixes` -- daraus wurde geschlossen, der
Projektor stecke im GGUF. Gemessen wurde dann:

    HTTP 500: image input is not supported
              hint: you may need to provide the mmproj
    /props:   modalities = {"vision": false, "video": false, "audio": false}

Die Meldung beim Laden betrifft TENSORNAMEN, nicht das Sehen. Ollama benutzt
fuer dieses Modell seine eigene Engine und braucht darum kein
llama.cpp-mmproj; wer `llama-server` benutzt, braucht eins und muss es
getrennt beschaffen.

Deshalb prueft `starten()` `modalities.vision` und bricht ab, statt den
Fehler bis zur ersten Bildanfrage zu tragen. Ein Server, der laeuft, VRAM
haelt und bei jedem Bild 500 sagt, sieht aus wie ein kaputtes Modell und
nicht wie eine fehlende Datei.

**`llama-server` liegt bei, ohne dass der Ollama-Daemon laeuft.** Das Binaerprogramm
gehoert zum Ollama-Paket, ist aber llama.cpp und braucht `ollama serve` nicht.
Der Plan verbietet den DAEMON, nicht die Bibliothek.

Dazu die Entscheidung, die den Spike traegt: **`max_pixels` wird gesetzt.** Die
mitgelieferte Vorgabe ist die Architekturobergrenze. T--1.10 hat gemessen, was
daraus folgt -- auf einem Vollbild gibt `qwen3-vl:8b` NULL Zeichen zurueck,
weil `done_reason=length` schon im Denken erreicht wird, waehrend `response`
leer bleibt. Ein hoeheres Budget verschiebt die Wand nur.
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import struct
import subprocess
import time
import zlib
from pathlib import Path

import numpy as np

# Gemessen: das Binaerprogramm aus dem Ollama-Paket, ohne laufenden Daemon.
LLAMA_SERVER = "/usr/lib/ollama/llama-server"
CUDA_BACKEND = "/usr/lib/ollama/cuda_v13/libggml-cuda.so"
OLLAMA_MODELLE = Path("/usr/share/ollama/.ollama/models")

# Die Grenze des Kernels, nicht eine Vorliebe. `sockaddr_un.sun_path` fasst
# 108 Bytes einschliesslich der abschliessenden Null.
SOCKET_MAX = 107

LANGE_KANTE = 1920
# Nicht die Architekturobergrenze. 1920x1080 ist das, was nach dem
# Herunterskalieren tatsaechlich hereinkommt.
MAX_PIXELS = 1920 * 1080

# Der Kontext wird GESETZT, nicht geerbt. qwen3-vl kann 256k, und
# `llama-server` legt den KV-Cache fuer das Maximum an: gemessen am 12.08.
# belegte das Modell 18 GB, obwohl die Gewichte 6,1 GB sind. Am 13.08. hat
# genau das den Projektor nicht mehr hineingelassen -- 18,8 GB frei, und
# `cudaMalloc failed: out of memory` beim Laden von 1105 MiB.
#
# 8192 Token reichen fuer eine Bildbeschreibung um Groessenordnungen. Dieselbe
# Falle wie bei `max_pixels`: die mitgelieferte Vorgabe ist die
# Architekturobergrenze und nicht ein brauchbarer Wert.
KONTEXT_TOKEN = 8192

# Das Budget muss das DENKEN mittragen. qwen3-vl denkt, bevor es antwortet,
# und das laesst sich nicht abschalten -- gemessen am 13.08.: weder
# `chat_template_kwargs={"enable_thinking": false}` noch ein vorangestelltes
# `/no_think` aendern etwas, in allen drei Faellen 850 bis 1180 Zeichen
# Denkteil. Mit 256 Token war das Budget im Denken aufgebraucht:
# finish_reason `length`, `content` leer, und im Denkteil stand eine voellig
# richtige Beschreibung, die niemand mehr bekam.
#
# 1024 traegt einen Bildschirmausschnitt. Reicht es doch nicht, sagt die
# Fehlermeldung es -- ein leerer Befund waere von "nichts zu sehen" nicht zu
# unterscheiden.
MAX_TOKENS = 1024

MODELL_VORGABE = "qwen3-vl:8b"
VRAM_MIB = 7000                      # 6,1 GB Gewichte plus Luft
LADEFRIST_S = 180.0
PR_SET_PDEATHSIG = 1


class VlmFehler(RuntimeError):
    """Der Server oder seine Umgebung ist unbrauchbar. Immer mit Grund."""


# -- Wo das Modell liegt ---------------------------------------------------

def modell_aus_ollama(name: str = MODELL_VORGABE) -> tuple[str, str | None]:
    """(Modellblob, Projektorblob) aus einem Ollama-Manifest.

    Ollama abgefragt, aber nicht benutzt: die Blobs sind gewoehnliche
    GGUF-Dateien, und sie ein zweites Mal herunterzuladen waeren sechs
    Gigabyte fuer nichts.
    """
    kurz, _, tag = name.partition(":")
    manifest = (OLLAMA_MODELLE / "manifests" / "registry.ollama.ai" /
                "library" / kurz / (tag or "latest"))
    try:
        schichten = json.loads(manifest.read_text())["layers"]
    except (OSError, ValueError, KeyError) as exc:
        raise VlmFehler(f"kein Ollama-Manifest fuer {name!r} unter {manifest}") from exc

    def blob(medientyp: str) -> str | None:
        for s in schichten:
            if s.get("mediaType", "").endswith(medientyp):
                digest = str(s["digest"]).replace(":", "-")
                pfad = OLLAMA_MODELLE / "blobs" / digest
                if pfad.exists():
                    return str(pfad)
        return None

    gewichte = blob("image.model")
    if not gewichte:
        raise VlmFehler(f"{name!r} hat keine Modellschicht im Manifest")
    # `projector` fehlt bei qwen3-vl:8b, und der Projektor steckt AUCH NICHT
    # im GGUF -- gemessen: /props meldet modalities.vision=false. Ollama
    # laesst dieses Modell ueber seine eigene Engine laufen und braucht darum
    # keins. Wer `llama-server` benutzt, muss es getrennt beschaffen.
    return gewichte, blob("image.projector")


def mmproj_suchen() -> str | None:
    """Wo der Projektor liegt. `None`, wenn nirgends.

    Dieselbe Bauart wie `tessdata_verzeichnis` in T-5.6, und aus demselben
    Grund: das Systemverzeichnis kann ihn nicht haben. Ollama liefert fuer
    dieses Modell keinen mit, weil es dort ueber eine eigene Engine laeuft --
    wer `llama-server` benutzt, muss ihn getrennt beschaffen.

    Gefunden wird der ERSTE Treffer, nicht der beste: mehrere Projektoren im
    selben Verzeichnis sind ein Zustand, den niemand herbeifuehrt, ohne zu
    wissen was er tut, und dann soll er auch `DAIMON_VLM_MMPROJ` setzen.
    """
    aus_umgebung = os.environ.get("DAIMON_VLM_MMPROJ")
    if aus_umgebung and Path(aus_umgebung).exists():
        return aus_umgebung
    basis = Path(os.environ.get("XDG_DATA_HOME")
                 or os.path.expanduser("~/.local/share")) / "daimon" / "models"
    for kandidat in sorted(basis.glob("mmproj*.gguf")):
        return str(kandidat)
    return None


# -- Bild vorbereiten ------------------------------------------------------

def herunterskalieren(rgb: np.ndarray, lange_kante: int = LANGE_KANTE,
                      max_pixels: int = MAX_PIXELS) -> np.ndarray:
    """Auf die lange Kante UND unter `max_pixels`. Kleinere bleiben, wie sie sind.

    Beides, und die zweite Bedingung ist die bindende. Die lange Kante allein
    reicht nicht: ein quadratisches 4000x4000 wird damit zu 1920x1920, und das
    sind 3 686 400 Pixel gegen ein Budget von 2 073 600. Fast das Doppelte
    ruschte durch, ohne dass jemand es merkte -- ein Bildschirm ist meistens
    breit, aber ein Fensterzuschnitt muss es nicht sein.

    Nearest-Neighbour ueber Indexfelder. Eine Flaechenmittelung waere
    huebscher, aber der Empfaenger ist ein Modell, das Text lesen soll --
    und Mittelung verwischt genau die duennen Striche, auf die es ankommt.
    """
    hoehe, breite = rgb.shape[:2]
    faktor = min(1.0, lange_kante / max(hoehe, breite))
    if hoehe * breite * faktor * faktor > max_pixels:
        faktor = min(faktor, (max_pixels / (hoehe * breite)) ** 0.5)
    if faktor >= 1.0:
        return rgb
    neu_h, neu_b = max(1, int(hoehe * faktor)), max(1, int(breite * faktor))
    ys = (np.arange(neu_h) * (hoehe / neu_h)).astype(np.int64)
    xs = (np.arange(neu_b) * (breite / neu_b)).astype(np.int64)
    return rgb[ys][:, xs]


def als_png(rgb: np.ndarray) -> bytes:
    """PNG ohne Pillow. `zlib` und `struct` reichen.

    Pillow waegt mit seinen Abhaengigkeiten mehr als dieser ganze Dienst, und
    gebraucht wird ein einziger Bildtyp: 8 Bit, drei Kanaele, keine Palette.
    """
    hoehe, breite = rgb.shape[:2]
    roh = b"".join(b"\x00" + rgb[y].tobytes() for y in range(hoehe))

    def block(art: bytes, daten: bytes) -> bytes:
        return (struct.pack(">I", len(daten)) + art + daten +
                struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF))

    kopf = struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", kopf)
            + block(b"IDAT", zlib.compress(roh, 6)) + block(b"IEND", b""))


# -- Der Server ------------------------------------------------------------

def _sterbe_mit_eltern() -> None:
    """`PR_SET_PDEATHSIG`. Der Server stirbt, wenn der Worker stirbt.

    Ohne das ueberlebte ein `llama-server` mit sechs Gigabyte VRAM den
    Worker, der ihn gestartet hat -- und niemand haette ihn mehr im
    Prozessbaum, um ihn zu finden. `beenden()` allein reicht nicht: es laeuft
    nicht mehr, wenn der Worker abstuerzt.
    """
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(
        PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)


class VlmServer:
    """`llama-server` im Prozessbaum, auf einem Unix-Socket, ohne Ollama-Daemon."""

    def __init__(self, *, modell: str | None = None,
                 mmproj: str | None = None,
                 socket_pfad: str | None = None,
                 binaer: str = LLAMA_SERVER,
                 backend: str = CUDA_BACKEND) -> None:
        self.socket_pfad = str(socket_pfad or self._vorgabe_socket())
        if len(self.socket_pfad) > SOCKET_MAX:
            # Der Server meldet sonst nur „couldn't bind" und nennt den Grund
            # nicht. Diese Meldung nennt ihn.
            raise VlmFehler(
                f"Socketpfad ist {len(self.socket_pfad)} Zeichen lang, "
                f"erlaubt sind {SOCKET_MAX} (sockaddr_un.sun_path): "
                f"{self.socket_pfad}")
        self._modell = modell
        self._mmproj = mmproj
        self._binaer = binaer
        self._backend = backend
        self._prozess: subprocess.Popen | None = None

    @staticmethod
    def _vorgabe_socket() -> Path:
        basis = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        return Path(basis) / "daimon-vlm.sock"

    def befehl(self) -> list[str]:
        modell = self._modell
        mmproj = self._mmproj
        if modell is None:
            modell, aus_manifest = modell_aus_ollama()
            # Erst das Manifest, dann das Benutzerverzeichnis. Beides kann
            # leer sein -- dann faengt `_sehen_pruefen()` es beim Start ab.
            mmproj = mmproj or aus_manifest or mmproj_suchen()
        argumente = [self._binaer, "-m", modell,
                     "--host", self.socket_pfad,
                     "-c", str(KONTEXT_TOKEN),
                     "-ngl", "99", "--no-webui"]
        if mmproj:
            argumente += ["--mmproj", mmproj]
        return argumente

    def starten(self, *, frist_s: float = LADEFRIST_S) -> None:
        try:
            os.unlink(self.socket_pfad)
        except OSError:
            pass
        umgebung = dict(os.environ)
        # Auf die DATEI, nicht auf das Verzeichnis: sonst laeuft der Server
        # still auf der CPU weiter.
        umgebung["GGML_BACKEND_PATH"] = self._backend
        umgebung["LD_LIBRARY_PATH"] = (
            f"{Path(self._backend).parent}:{Path(self._binaer).parent}"
            + (":" + umgebung["LD_LIBRARY_PATH"] if "LD_LIBRARY_PATH" in umgebung else ""))
        self._prozess = subprocess.Popen(
            self.befehl(), env=umgebung, preexec_fn=_sterbe_mit_eltern,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ende = time.monotonic() + frist_s
        while time.monotonic() < ende:
            if self._prozess.poll() is not None:
                raise VlmFehler(
                    f"llama-server endete mit {self._prozess.returncode} "
                    "beim Laden")
            if self._erreichbar():
                self._sehen_pruefen()
                return
            time.sleep(0.25)
        self.beenden()
        raise VlmFehler(f"llama-server war nach {frist_s:.0f} s nicht bereit")

    def _sehen_pruefen(self) -> None:
        """Sieht der Server ueberhaupt? Sonst sofort abbrechen.

        Ein Server, der laeuft, VRAM haelt und bei jedem Bild HTTP 500 sagt,
        sieht aus wie ein kaputtes Modell und nicht wie eine fehlende Datei.
        `/props` sagt es beim Start in einem Feld.
        """
        try:
            modalitaeten = self.anfrage("GET", "/props", frist_s=10.0).get(
                "modalities", {})
        except VlmFehler:
            return                      # aelterer Server ohne das Feld
        if modalitaeten and not modalitaeten.get("vision"):
            self.beenden()
            raise VlmFehler(
                "der Server sieht nicht (modalities.vision=false). Es fehlt "
                "eine mmproj-Datei: Ollama liefert fuer dieses Modell keine "
                "mit, weil es dort ueber eine eigene Engine laeuft. Mit "
                "`mmproj=` uebergeben.")

    def _erreichbar(self) -> bool:
        try:
            self.anfrage("GET", "/health", frist_s=2.0)
            return True
        except Exception:
            return False

    def anfrage(self, methode: str, pfad: str, koerper: dict | None = None,
                *, frist_s: float = 120.0) -> dict:
        """HTTP ueber den Unix-Socket. Ohne Bibliothek -- es ist eine Anfrage."""
        daten = json.dumps(koerper).encode() if koerper is not None else b""
        kopf = (f"{methode} {pfad} HTTP/1.1\r\nHost: daimon\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(daten)}\r\nConnection: close\r\n\r\n")
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(frist_s)
        try:
            s.connect(self.socket_pfad)
            s.sendall(kopf.encode() + daten)
            puffer = b""
            while True:
                stueck = s.recv(65536)
                if not stueck:
                    break
                puffer += stueck
        finally:
            s.close()
        kopfteil, _, rumpf = puffer.partition(b"\r\n\r\n")
        # Der Status wird GELESEN, nicht uebergangen. Der erste Entwurf hier
        # gab nur den Rumpf zurueck, und `llama-server` antwortet waehrend
        # des Ladens mit 503 und einem gueltigen JSON-Koerper
        # (`{"error":{"message":"Loading model",...}}`). Die Bereitschafts-
        # pruefung hielt das fuer einen Erfolg und gab den Server frei,
        # bevor er ein Modell hatte -- das Bequeme gemessen statt das
        # Zugesagte.
        try:
            status = int(kopfteil.split(b"\r\n", 1)[0].split()[1])
        except (IndexError, ValueError) as exc:
            raise VlmFehler(f"keine HTTP-Statuszeile: {kopfteil[:80]!r}") from exc
        daten = json.loads(rumpf) if rumpf.strip() else {}
        if not 200 <= status < 300:
            raise VlmFehler(f"HTTP {status}: {str(daten)[:200]}")
        return daten

    def beschreiben(self, rgb: np.ndarray, aufforderung: str,
                    *, max_tokens: int = MAX_TOKENS,
                    frist_s: float = 120.0) -> str:
        """Ein Bild, eine Frage, eine Antwort.

        `max_tokens` ist gedeckelt und nicht offen: T--1.10 hat gemessen, dass
        ein hoeheres Budget die Wand nur verschiebt -- `done_reason=length`
        wird im Denken erreicht, und `response` bleibt leer.
        """
        klein = herunterskalieren(rgb)
        if klein.shape[0] * klein.shape[1] > MAX_PIXELS:
            raise VlmFehler(
                f"{klein.shape[1]}x{klein.shape[0]} ueberschreitet "
                f"max_pixels={MAX_PIXELS}")
        import base64
        b64 = base64.b64encode(als_png(klein)).decode()
        antwort = self.anfrage("POST", "/v1/chat/completions", {
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": aufforderung},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }, frist_s=frist_s)
        try:
            wahl = antwort["choices"][0]
            text = wahl["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise VlmFehler(f"unerwartete Antwort: {str(antwort)[:200]}") from exc

        if not text.strip():
            # NICHT still leer zurueckgeben. Ein leerer Bildschirmbefund ist
            # von „nichts zu sehen" nicht zu unterscheiden, und dann sucht
            # jemand den Fehler im Bildschirm statt im Budget.
            gedacht = len(str(wahl.get("message", {}).get(
                "reasoning_content") or ""))
            raise VlmFehler(
                f"leere Antwort (finish_reason={wahl.get('finish_reason')}, "
                f"{gedacht} Zeichen im Denkteil). Bei `length` reicht das "
                "Token-Budget nicht bis zur Antwort.")
        return text

    def beenden(self) -> None:
        if self._prozess is None:
            return
        self._prozess.terminate()
        try:
            self._prozess.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._prozess.kill()
            self._prozess.wait(timeout=5)
        self._prozess = None
        try:
            os.unlink(self.socket_pfad)
        except OSError:
            pass


# -- Das Gate --------------------------------------------------------------

def gate_offen(*, vram_mib: int = VRAM_MIB) -> tuple[bool, str]:
    """Vollbild und VRAM, wie in T-3.7. Gibt IMMER einen Grund mit.

    Ein `False` ohne Grund waere von „gemessen und knapp" nicht zu
    unterscheiden -- und dann sucht jemand den Fehler im VLM statt im Spiel,
    das gerade im Vollbild laeuft.
    """
    from daimon.gpu.worker import fullscreen_aktiv, vram_frei_mib

    voll = fullscreen_aktiv()
    if voll:
        return False, "Vollbild aktiv"
    frei = vram_frei_mib()
    if frei is None:
        # Unbekannt ist NICHT frei. Wer hier durchlaesst, laedt sechs
        # Gigabyte neben ein Spiel, das schon laeuft.
        return False, "VRAM unbekannt"
    if frei < vram_mib:
        return False, f"VRAM {frei} MiB frei, {vram_mib} MiB noetig"
    return True, f"VRAM {frei} MiB frei"
