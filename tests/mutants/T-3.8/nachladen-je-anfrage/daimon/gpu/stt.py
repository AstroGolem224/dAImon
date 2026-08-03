#!/usr/bin/env python3
"""Blinde Referenz fuer den CPU-STT-Dienst aus T-3.8.

Das Verzeichnis ``gpu`` ist im Projekt die Heimat der Modellprozesse. Dieser
Dienst benutzt absichtlich nur die CPU und laedt das Modell genau einmal.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import tomllib
import wave
from pathlib import Path

ENGINE = "sherpa-onnx-transducer"
MODELL = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
PROVIDER = "cpu"
SPRACHEN = ["de", "en"]
MUTATION = "nachladen-je-anfrage"


def _konfiguration() -> tuple[Path, int]:
    basis = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    datei = basis / "daimon" / "daimon.toml"
    daten: dict = {}
    if datei.is_file():
        with datei.open("rb") as fh:
            daten = tomllib.load(fh)
    stt = daten.get("stt", {})
    return Path(stt.get("modell_dir", MODELL)), int(stt.get("threads", 8))


class Dienst:
    def __init__(self) -> None:
        self.modell_dir, self.threads = _konfiguration()
        self.erkenner = None
        self.ladezeit_ms = 0.0
        self.anfragen = 0
        self.ladefehler = ""
        self._laden()

    def _basis(self) -> dict:
        provider = "cuda" if MUTATION == "cuda-provider" else PROVIDER
        return {"v": 1, "engine": ENGINE, "modell": self.modell_dir.name,
                "provider": provider}

    def _laden(self) -> None:
        pflichte = ("encoder.int8.onnx", "decoder.int8.onnx",
                    "joiner.int8.onnx", "tokens.txt")
        fehlend = [name for name in pflichte
                   if not (self.modell_dir / name).is_file()]
        if fehlend:
            self.ladefehler = "fehlende Modelldateien: " + ", ".join(fehlend)
            return
        try:
            import sherpa_onnx
            t0 = time.monotonic_ns()
            self.erkenner = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(self.modell_dir / "encoder.int8.onnx"),
                decoder=str(self.modell_dir / "decoder.int8.onnx"),
                joiner=str(self.modell_dir / "joiner.int8.onnx"),
                tokens=str(self.modell_dir / "tokens.txt"),
                num_threads=self.threads,
                provider="cpu",
                model_type="nemo_transducer",
            )
            self.ladezeit_ms = (time.monotonic_ns() - t0) / 1_000_000
            self.ladefehler = ""
        except Exception as exc:  # pragma: no cover - nur kaputtes Fremdpaket
            self.ladefehler = f"{type(exc).__name__}: {exc}"

    def _absage(self, grund: str, meldung: str) -> dict:
        return {**self._basis(), "ok": False, "grund": grund,
                "meldung": meldung}

    def _wav(self, pfad: Path) -> tuple[object, int, float] | dict:
        if not pfad.is_absolute() or not pfad.is_file() or not os.access(pfad, os.R_OK):
            return self._absage("datei_fehlt", f"nicht lesbar: {pfad}")
        try:
            with wave.open(str(pfad), "rb") as wav:
                breite = wav.getsampwidth()
                kanaele = wav.getnchannels()
                rate = wav.getframerate()
                frames = wav.getnframes()
                roh = wav.readframes(frames)
        except (OSError, EOFError, wave.Error) as exc:
            return self._absage("format_falsch", f"keine gueltige WAV-Datei: {exc}")
        if MUTATION != "format-egal" and (breite != 2 or kanaele != 1):
            return self._absage(
                "format_falsch",
                f"erwartet 16 bit mono, erhalten {breite * 8} bit/{kanaele} Kanaele",
            )
        import numpy as np
        if breite == 2:
            samples = np.frombuffer(roh, dtype="<i2").astype("float32") / 32768.0
        else:
            samples = (np.frombuffer(roh, dtype="u1").astype("float32") - 128.0) / 128.0
        if kanaele > 1:
            samples = samples.reshape(-1, kanaele).mean(axis=1)
        return samples, rate, frames / rate

    def antworte(self, anfrage: object) -> dict:
        if not isinstance(anfrage, dict):
            return self._absage("unlesbar", "erwartet wird ein JSON-Objekt")
        art = anfrage.get("art")
        if art not in ("transkribiere", "zustand"):
            return self._absage("unbekannte_art", f"unbekannte art: {art!r}")
        if art == "zustand":
            return {**self._basis(), "ok": True,
                    "geladen": self.erkenner is not None,
                    "ladezeit_ms": round(self.ladezeit_ms, 3),
                    "anfragen": self.anfragen, "threads": self.threads,
                    "sprachen": SPRACHEN, "pid": os.getpid()}
        if self.erkenner is None:
            return self._absage("modell_fehlt", self.ladefehler or "Modell fehlt")
        wav = anfrage.get("wav")
        if not isinstance(wav, str):
            return self._absage("datei_fehlt", "wav muss ein absoluter Pfad sein")
        gelesen = self._wav(Path(wav))
        if isinstance(gelesen, dict):
            return gelesen
        samples, rate, audio_s = gelesen
        if MUTATION == "nachladen-je-anfrage":
            self.erkenner = None
            self._laden()
        t0 = time.monotonic_ns()
        strom = self.erkenner.create_stream()
        strom.accept_waveform(rate, samples)
        self.erkenner.decode_stream(strom)
        latenz_ms = (time.monotonic_ns() - t0) / 1_000_000
        text = strom.result.text.strip()
        if MUTATION == "stille-halluziniert" and not text:
            text = "Untertitel erstellt von der Community"
        if MUTATION == "text-geschoent" and text:
            text = text[0].upper() + text[1:] + "."
        self.anfragen += 1
        return {**self._basis(), "ok": True, "text": text,
                "audio_s": round(audio_s, 4),
                "latenz_ms": round(latenz_ms, 3), "rate": rate,
                "threads": self.threads}


def _socket_aus_systemd() -> socket.socket | None:
    try:
        pid = int(os.environ.get("LISTEN_PID", "0"))
        n = int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError:
        return None
    if pid == os.getpid() and n == 1:
        return socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)
    return None


def _eigener_socket(pfad: Path) -> socket.socket:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    try:
        pfad.unlink()
    except FileNotFoundError:
        pass
    alt = os.umask(0o177)
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(pfad))
    finally:
        os.umask(alt)
    os.chmod(pfad, 0o600)
    sock.listen(16)
    return sock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path)
    args = parser.parse_args()
    sock = _socket_aus_systemd()
    if sock is None:
        if args.socket is None:
            raise SystemExit("--socket fehlt und kein systemd-Socket wurde geerbt")
        sock = _eigener_socket(args.socket)
    dienst = Dienst()
    while True:
        verbindung, _ = sock.accept()
        with verbindung:
            datei = verbindung.makefile("rb")
            zeile = datei.readline(1 << 20)
            try:
                anfrage = json.loads(zeile)
            except (json.JSONDecodeError, UnicodeDecodeError):
                antwort = dienst._absage("unlesbar", "keine JSON-Zeile")
            else:
                antwort = dienst.antworte(anfrage)
            verbindung.sendall((json.dumps(antwort, ensure_ascii=False) + "\n").encode())


if __name__ == "__main__":
    sys.exit(main())
