"""T-3.8 — Audio rein, Text raus. sherpa-onnx parakeet-tdt-0.6b-v3, CPU, 0 VRAM.

Warum das hier unter `daimon/gpu/` liegt und trotzdem auf der CPU laeuft
----------------------------------------------------------------------------
Das Verzeichnis ist die Heimat der **Modellprozesse**, nicht eine Aussage ueber
das Rechenwerk. Der Plan sah fuer T-3.8 einen GPU-Worker vor: `onnxruntime-gpu`
nackt gepinnt, keine `nvidia-*`-pip-Pakete, Ladegatter aus T-3.7, VRAM-Rueckgabe
nach dem Prozessende. Gemessen am 03.08. gegen 21 eigene Aufnahmen braucht es
das alles nicht:

    WER deutsch 5,17 %, englisch 0,0 %
    Latenz Median 117 ms, p95 152 ms, RTF 0,02
    Modell laden 843 ms

Der Plan verlangt selbst "wo moeglich ueber sherpa-onnx", und moeglich ist es --
k2-fsa veroeffentlicht parakeet-tdt-0.6b-v3 vorkonvertiert. Damit entfallen rund
2 GB Installation, drei Pinning-Kriterien und das GPU-Gate; das Kriterium
"Prozessende gibt VRAM vollstaendig frei" ist erfuellt, weil nie VRAM belegt
wird. Und der STT laeuft, waehrend ein Spiel die Karte haelt -- was mit dem
GPU-Pfad gerade nicht gegangen waere.

Eine Apache-2.0-Abhaengigkeit deckt jetzt Wake-Word, VAD, TTS und STT ab.

Kein Leerlauf-Exit
----------------------------------------------------------------------------
Anders als der GPU-Worker aus T-3.7, und aus demselben Grund wie beim TTS: dort
war die belastbare Groesse die VRAM-Rueckgabe nach dem Exit. Hier gibt es kein
VRAM zurueckzugeben, und ein Neustart kostet 843 ms Ladezeit. Das Modell im
Speicher IST die Latenzzusage. Socket-aktiviert bleibt der Dienst trotzdem:
gestartet wird er beim ersten Wort und nicht beim Anmelden.

Der Text wird NICHT nachbearbeitet
----------------------------------------------------------------------------
Keine Grossschreibung, kein angefuegtes Satzzeichen, keine Ersetzungstabelle fuer
Fachwoerter. Nur Leerraum an den Raendern faellt. Zwei Gruende: die
Wortfehlerrate wird gegen normalisierten Text gerechnet, jede Schoenung verschiebt
also die Messung -- und eine Ersetzungstabelle waere ein Modell im Modell, das
niemand mitmisst. Wenn "Build" als "Bild" ankommt, ist das ein Befund am Modell
und keine Aufgabe fuer ein `sed`.

Eine fehlende Stimme toetet den Dienst nicht
----------------------------------------------------------------------------
Fehlen die Gewichte, laeuft der Dienst an und sagt jeder Anfrage `modell_fehlt`.
Das ist die Lehre aus T-3.9: ein socket-aktivierter Dienst, der beim Start
stirbt, hinterlaesst dem Aufrufer eine geschlossene Verbindung ohne Grund -- und
eine tote Unit ist von einer stummen nicht zu unterscheiden.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import wave
from pathlib import Path

# numpy hier und NICHT faul in `wav_lesen`: ein Import beim ersten Aufruf
# kostet rund 115 ms, und die liegen AUSSERHALB des gemessenen Fensters -- die
# Selbstauskunft `latenz_ms` haette die erste Anfrage also zu guenstig gemeldet.
# Gefunden hat das die Wanduhr-Gegenprobe des Pruefstands: 226 ms Wanduhr gegen
# 111 ms Selbstauskunft bei der ersten Aeusserung, 0,6 ms Differenz bei allen
# folgenden. Genau dafuer gibt es zwei Uhren.
import numpy as np

from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger

LISTEN_FDS_START = 3
MAX_ZEILE = 1 << 16
STT_SOCKET = "stt.sock"

# Vorgaben. Stehen zusaetzlich in daimon/common/config.py -- hier, damit das
# Modul ohne Konfiguration lauffaehig bleibt.
MODELL_DIR = ("spikes/stt-referenz/models/"
              "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
# Acht Threads: gemessen 117 ms Median. Der Wert ist der Kalibrierknopf, kein
# Naturgesetz -- auf einer Maschine mit vier Kernen gehoert er nach unten.
THREADS = 8
ENGINE = "sherpa-onnx-transducer"
# v3 deckt 25 europaeische Sprachen ab und braucht KEINEN Sprachschalter.
# Belegt an zwei englischen und 18 deutschen Aufnahmen derselben Instanz.
SPRACHEN = ("de", "en")

# Die vier Dateien, aus denen ein sherpa-Transducer besteht. Vollstaendig
# geprueft, bevor geladen wird: ein abgebrochener Download hinterlaesst ein
# Verzeichnis, dem eine Datei fehlt, und der Ladefehler nennt dann nicht sie.
DATEIEN = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx",
           "tokens.txt")

GRUENDE = frozenset({"unlesbar", "unbekannte_art", "datei_fehlt",
                     "format_falsch", "modell_fehlt"})


def modell_pruefen(modell_dir: str) -> str:
    """Fehlt eine der vier Dateien, sagt die Meldung WELCHE."""
    if not os.path.isdir(modell_dir):
        return f"Modellverzeichnis fehlt: {modell_dir}"
    pfade = {d: os.path.join(modell_dir, d) for d in DATEIEN}
    nicht_da = [d for d, pf in pfade.items() if not os.path.isfile(pf)]
    if nicht_da:
        return f"Modell unvollstaendig, fehlt: {', '.join(nicht_da)}"
    # Leere Dateien getrennt: ein abgebrochener Download hinterlaesst sie, und
    # `from_transducer` meldet dann einen Ladefehler, der die Datei nicht nennt.
    leer = [d for d, pf in pfade.items() if os.path.getsize(pf) == 0]
    if leer:
        return f"Modelldatei leer: {', '.join(leer)}"
    return ""


def wav_lesen(pfad: str) -> tuple[object, int, float]:
    """`(samples float32, rate, sekunden)`. Wirft `WavFehler`.

    Keine Vorbehandlung: kein Trimmen, kein Normalisieren, kein Filter. Die
    Aufnahme ist die Messgroesse.

    Eine abweichende SAMPLERATE ist kein Fehler -- sherpa resampelt selbst, und
    8 kHz Telefonqualitaet soll erkannt werden statt abgewiesen. Sie wird
    gemeldet, damit sie nicht unsichtbar bleibt. Stereo oder 8 bit dagegen sind
    ein Fehler: was dann durchlaufen wuerde, waere halbes Signal oder Rauschen,
    und das Ergebnis waere eine WER, die niemand deuten kann.
    """
    try:
        with wave.open(pfad, "rb") as w:
            breite, kanaele = w.getsampwidth(), w.getnchannels()
            rate, rahmen = w.getframerate(), w.getnframes()
            if breite != 2 or kanaele != 1:
                raise WavFehler(
                    f"erwartet 16 bit mono, ist {breite * 8} bit / "
                    f"{kanaele} Kanal")
            rohdaten = w.readframes(rahmen)
    except wave.Error as exc:
        raise WavFehler(f"kein lesbares WAV: {exc}") from exc
    samples = np.frombuffer(rohdaten, dtype="<i2").astype("float32") / 32768.0
    return (samples, rate, len(samples) / rate if rate else 0.0)


class WavFehler(ValueError):
    """Format stimmt nicht. Nicht: Datei fehlt."""


class Erkenner:
    """Ein Modell im Speicher. Datei rein, Text raus."""

    def __init__(self, *, modell_dir: str, threads: int = THREADS,
                 log: Logger | None = None) -> None:
        self.modell_dir = modell_dir
        self.threads = int(threads)
        self.log = log or get_logger("daimon-stt")
        self.absage = modell_pruefen(modell_dir)
        self._erkenner = None
        self._lock = threading.Lock()
        self.geladen = False
        self.ladezeit_ms: float | None = None
        self.anfragen = 0

    # -- Modell ------------------------------------------------------------

    def laden(self) -> None:
        """Einmal, beim Start -- nicht bei der ersten Anfrage.

        843 ms Ladezeit in der ersten Aeusserung waeren genau die Aeusserung, bei
        der jemand wartet. Und ein Dienst, der je Anfrage laedt, erfuellt jede
        WER und ist unbenutzbar.
        """
        if self.absage:
            self.log.warn("Modell nicht verwendbar -- der Dienst laeuft und "
                          "sagt ab", DAIMON_ACTION="stt_absage",
                          DAIMON_GRUND="modell_fehlt",
                          DAIMON_MELDUNG=self.absage[:150])
            return
        import sherpa_onnx
        t0 = time.monotonic()
        self._erkenner = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=os.path.join(self.modell_dir, "encoder.int8.onnx"),
            decoder=os.path.join(self.modell_dir, "decoder.int8.onnx"),
            joiner=os.path.join(self.modell_dir, "joiner.int8.onnx"),
            tokens=os.path.join(self.modell_dir, "tokens.txt"),
            num_threads=self.threads,
            # provider="cpu" steht hier und NICHT in der Konfiguration: ein
            # Konfigurationswert waere ein Schalter, mit dem sich ein
            # CUDA-Provider einschalten laesst, und dann waere die 0-VRAM-Zusage
            # keine Zusage mehr.
            provider="cpu",
            model_type="nemo_transducer",
        )
        self.ladezeit_ms = round((time.monotonic() - t0) * 1000, 2)
        self.geladen = True
        self.log.info("Modell geladen", DAIMON_ACTION="stt_laden",
                      DAIMON_MODELL=os.path.basename(self.modell_dir),
                      DAIMON_LADEZEIT_MS=self.ladezeit_ms,
                      DAIMON_THREADS=self.threads)

    # -- Erkennen ----------------------------------------------------------

    def kennung(self) -> dict:
        """Was zur Laufzeit belegt, WAS hier erkennt. Kriterium 1."""
        return {"engine": ENGINE,
                "modell": os.path.basename(self.modell_dir.rstrip("/")),
                "provider": "cpu", "threads": self.threads}

    def transkribiere(self, wav: object) -> dict:
        if self.absage:
            return {"v": 1, "ok": False, "grund": "modell_fehlt",
                    "meldung": self.absage, **self.kennung()}
        if not isinstance(wav, str) or not wav:
            return {"v": 1, "ok": False, "grund": "datei_fehlt",
                    "meldung": "Feld `wav` fehlt oder ist kein Pfad",
                    **self.kennung()}
        if not os.path.isfile(wav):
            return {"v": 1, "ok": False, "grund": "datei_fehlt",
                    "meldung": f"nicht lesbar: {wav[:200]}", **self.kennung()}
        try:
            samples, rate, dauer_s = wav_lesen(wav)
        except WavFehler as exc:
            return {"v": 1, "ok": False, "grund": "format_falsch",
                    "meldung": str(exc), **self.kennung()}
        except OSError as exc:
            return {"v": 1, "ok": False, "grund": "datei_fehlt",
                    "meldung": str(exc)[:200], **self.kennung()}

        # Ein Modell, zwei gleichzeitige Anfragen: sherpa erlaubt mehrere
        # Stroeme, aber die Latenzzusage gilt fuer den sequentiellen Fall. Die
        # Sperre macht die Messung ehrlich, statt zwei Anfragen um dieselben
        # acht Threads streiten zu lassen.
        with self._lock:
            t0 = time.monotonic()
            strom = self._erkenner.create_stream()
            strom.accept_waveform(rate, samples)
            self._erkenner.decode_stream(strom)
            latenz_ms = round((time.monotonic() - t0) * 1000, 2)
            self.anfragen += 1

        # Nur Leerraum an den Raendern faellt -- siehe Modulkopf.
        text = strom.result.text.strip()
        self.log.info("Transkribiert", DAIMON_ACTION="stt_text",
                      DAIMON_LATENZ_MS=latenz_ms,
                      DAIMON_AUDIO_S=round(dauer_s, 2),
                      DAIMON_ZEICHEN=len(text), DAIMON_RATE=rate)
        return {"v": 1, "ok": True, "text": text,
                "audio_s": round(dauer_s, 2), "latenz_ms": latenz_ms,
                "rate": rate, **self.kennung()}

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "geladen": self.geladen,
                "ladezeit_ms": self.ladezeit_ms, "anfragen": self.anfragen,
                "sprachen": list(SPRACHEN), "pid": os.getpid(),
                # Leer heisst: das Modell ist in Ordnung. Ein Feld, das nur bei
                # Fehlern existiert, wird beim Auswerten vergessen.
                "absage": self.absage, **self.kennung()}


# -- Sockets (Muster aus daimon/gpu/worker.py) ------------------------------

def sd_socket() -> socket.socket | None:
    """Der von systemd uebergebene Socket, oder None. `LISTEN_PID` wird gegen
    die eigene PID geprueft -- die Variablen werden vererbt, und ein Kind, das
    fd 3 fuer seinen haelt, uebernimmt einen fremden Deskriptor."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    try:
        n = int(os.environ.get("LISTEN_FDS", "0") or 0)
    except ValueError:
        return None
    if n < 1:
        return None
    return socket.socket(fileno=LISTEN_FDS_START, family=socket.AF_UNIX,
                         type=socket.SOCK_STREAM)


def eigener_socket(pfad: str) -> socket.socket:
    """Ohne systemd. Modus 0600 nach dem Binden -- bind() beachtet die umask."""
    if os.path.exists(pfad):
        os.unlink(pfad)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(pfad)
    os.chmod(pfad, 0o600)
    srv.listen(8)
    return srv


def _antworte(conn: socket.socket, daten: dict) -> None:
    try:
        conn.sendall(json.dumps(daten).encode() + b"\n")
    except OSError:
        pass


def bediene(erkenner: Erkenner, conn: socket.socket) -> None:
    """Eine Verbindung, eine Anfrage, eine Antwort."""
    with conn:
        conn.settimeout(30.0)
        try:
            roh = conn.makefile("rb").readline(MAX_ZEILE)
            anfrage = json.loads(roh)
        except (OSError, json.JSONDecodeError, ValueError):
            # Jede Absage traegt eine `meldung`. Ein Grund ohne Detail zwingt
            # den Aufrufer zum Raten, und beim Protokollfehler ist das Detail
            # das Einzige, was ihn weiterbringt.
            _antworte(conn, {"v": 1, "ok": False, "grund": "unlesbar",
                             "meldung": "keine lesbare JSON-Zeile",
                             **erkenner.kennung()})
            return
        if not isinstance(anfrage, dict):
            _antworte(conn, {"v": 1, "ok": False, "grund": "unlesbar",
                             "meldung": "JSON ist kein Objekt",
                             **erkenner.kennung()})
            return
        art = anfrage.get("art")
        if art == "zustand":
            _antworte(conn, erkenner.zustand())
        elif art == "transkribiere":
            _antworte(conn, erkenner.transkribiere(anfrage.get("wav")))
        else:
            _antworte(conn, {"v": 1, "ok": False, "grund": "unbekannte_art",
                             "meldung": f"art={str(art)[:40]!r}",
                             **erkenner.kennung()})


def lauf(erkenner: Erkenner, srv: socket.socket) -> int:
    """Kein Leerlauf-Exit -- siehe Modulkopf."""
    threads: list[threading.Thread] = []
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        t = threading.Thread(target=bediene, args=(erkenner, conn), daemon=True)
        t.start()
        threads = [x for x in threads if x.is_alive()] + [t]
    return 0


def einstellungen(cfg: Config) -> dict:
    return {"modell_dir": str(cfg.get("stt.modell_dir", MODELL_DIR)),
            "threads": int(cfg.get("stt.threads", THREADS))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon STT (T-3.8)")
    ap.add_argument("--socket", default=None,
                    help="ohne systemd: hier selbst horchen")
    ap.add_argument("--modell-dir", default=None)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--wav", default=None,
                    help="einmal transkribieren und beenden (Handlauf)")
    args = ap.parse_args(argv)

    # make_dirs=False: `load()` legt sonst $XDG_STATE_HOME/daimon an und
    # chmod-et es -- unter `ProtectHome=read-only` ein EROFS, und der Dienst
    # stirbt vor der ersten Zeile. Am 03.08. beim GPU-Worker genau so passiert.
    cfg = load_config(make_dirs=False)
    werte = einstellungen(cfg)
    if args.modell_dir:
        werte["modell_dir"] = args.modell_dir
    if args.threads is not None:
        werte["threads"] = args.threads

    erkenner = Erkenner(**werte)
    erkenner.laden()

    if args.wav:
        print(json.dumps(erkenner.transkribiere(args.wav), ensure_ascii=False))
        return 0

    srv = sd_socket()
    if srv is None:
        if not args.socket:
            raise SystemExit(
                "Weder Socket-Aktivierung (LISTEN_FDS) noch --socket. Der "
                "Dienst legt ohne beides keinen Socket an: er waere gestartet, "
                "aber unerreichbar.")
        srv = eigener_socket(args.socket)
    try:
        return lauf(erkenner, srv)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
