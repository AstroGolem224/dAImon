"""T-3.9 -- der Sprechdienst. sherpa-onnx VITS, CPU, 0 VRAM.

Referenz des Reviewers, blind gegen die Akzeptanzliste geschrieben.

Der Dienst spricht NUR mit Marke aus dem Hub. Der Validator sitzt im Hub
(Design §8.3); hier bleibt die Mechanik: Synthese, Ausgabe, Unterbrechung.

`pw-cat` wird OHNE absoluten Pfad aufgerufen -- ein Verifizierer legt einen
Stub in den PATH und misst, was der Dienst ausgeben WOLLTE, ohne die
Soundkarte zu beruehren (Lehre aus T-2.7 und T-3.7). Ein absoluter Pfad
mauert diesen Messpunkt zu.

Kein Leerlauf-Exit: das Modell im Speicher IST das TTFA-Kriterium (p95 <
200 ms); anders als der GPU-Worker gibt dieser Prozess kein VRAM frei, das
er zurueckgeben muesste.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from daimon.common.config import load as load_config
from daimon.common.logging import Logger, get_logger
from daimon.gpu.worker import eigener_socket, hub_anfrage, sd_socket

TTS_SOCKET = "tts.sock"          # der Torwaechter-Endpunkt des Hubs
MAX_ZEILE = 1 << 16

# Stimmlizenzen, die vorgelesen werden duerfen (Design §8.2). pavoque ist
# CC-BY-NC-SA und scheidet aus -- auch wenn die Dateien vorhanden sind.
ERLAUBTE_LIZENZEN = ("CC0",)

# Der Pfad kommt aus der Konfiguration (tts.modell_dir); das ist nur die
# Vorgabe. Ein Produktivdienst laedt nicht aus einem Spike-Verzeichnis --
# sobald T-3.10 die Persona-Dateien anlegt, gehoert die Stimme nach
# ~/.local/share/daimon/voices/.
VORGABE_STIMME = "de_DE-thorsten-high"


def _finde_onnx(modell_dir: Path) -> Path | None:
    treffer = sorted(modell_dir.glob("*.onnx"))
    return treffer[0] if treffer else None


def _lizenz_erlaubt(modell_dir: Path) -> bool | None:
    """True/False aus der MODEL_CARD, None wenn es keine gibt. Eine fehlende
    Karte ist KEINE Erlaubnis -- aber sie ist ein anderer Befund als eine
    verbotene Lizenz, und die Absage soll das unterscheiden."""
    karte = modell_dir / "MODEL_CARD"
    if not karte.is_file():
        return None
    try:
        text = karte.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return any(lizenz in text for lizenz in ERLAUBTE_LIZENZEN)


class Sprecher:
    """Ein Dienst, ein Modell, ein Ausgabepfad. Kein Zustandsautomat."""

    def __init__(self, *, hub_socket: str, cfg, log: Logger) -> None:
        self.hub_socket = hub_socket
        self.cfg = cfg
        self.log = log
        # Die Stimme kommt aus persona.voice (Kriterium 3), der PFAD aus
        # tts.modell_dir. Vorgabe fuer beides: thorsten aus dem Spike-Bestand.
        self.stimme = str(cfg.get("persona.voice", VORGABE_STIMME))
        vorgabe_basis = Path(__file__).resolve().parents[2] / "spikes" /             "nvidia-voice" / "models"
        basis = Path(str(cfg.get("tts.modell_dir", str(vorgabe_basis))))
        self.modell_dir = self._stimmen_dir(basis)
        self._tts = None
        self.ladefehler: str | None = None
        self.modell = ""
        self.rate = 22050
        self._pwc: subprocess.Popen | None = None
        self._pwc_lock = threading.Lock()
        self._epoche = 0         # steigt bei jeder Unterbrechung

    def _stimmen_dir(self, basis: Path) -> Path:
        """persona.voice WAEHLT die Stimme: <basis>/vits-piper-<stimme> oder
        <basis>/<stimme>. Nur wenn basis selbst ein Stimmverzeichnis ist und
        die Vorgabestimme gemeint ist, gilt basis direkt -- sonst waere der
        Wert aus persona.voice fest verdrahtet statt gelesen."""
        for kandidat in (basis / f"vits-piper-{self.stimme}",
                         basis / self.stimme):
            if kandidat.is_dir():
                return kandidat
        if basis.is_dir() and any(basis.glob("*.onnx"))                 and self.stimme == VORGABE_STIMME:
            return basis
        return basis / f"vits-piper-{self.stimme}"   # existiert nicht -> ehrliche Absage

    # -- Lizenz und Laden ---------------------------------------------------

    def laden(self) -> None:
        """Laedt das Modell in den Speicher -- oder legt den Grund ab, warum
        nicht. Die Absage kommt bei JEDER Anfrage ehrlich zurueck, der
        Prozess bleibt erreichbar (eine ehrliche Absage, kein stiller Tod)."""
        if not self.modell_dir.is_dir():
            self.ladefehler = f"stimme_fehlt:{self.stimme}"
            return
        erlaubt = _lizenz_erlaubt(self.modell_dir)
        if erlaubt is not True:
            self.ladefehler = ("lizenz_fehlt" if erlaubt is None
                               else "lizenz_verboten")
            return
        onnx = _finde_onnx(self.modell_dir)
        tokens = self.modell_dir / "tokens.txt"
        daten = self.modell_dir / "espeak-ng-data"
        if onnx is None or not tokens.is_file():
            self.ladefehler = f"stimme_fehlt:{self.stimme}"
            return
        try:
            import sherpa_onnx
            vits = sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(onnx), lexicon="", tokens=str(tokens),
                data_dir=str(daten) if daten.is_dir() else "")
            modell = sherpa_onnx.OfflineTtsModelConfig(
                vits=vits, num_threads=2, debug=False, provider="cpu")
            self._tts = sherpa_onnx.OfflineTts(
                sherpa_onnx.OfflineTtsConfig(model=modell, max_num_sentences=1))
        except Exception as exc:
            self.ladefehler = f"laden_fehlgeschlagen:{type(exc).__name__}"
            return
        self.modell = onnx.stem

    # -- Ausgabe --------------------------------------------------------------

    def _unterbreche(self) -> None:
        """Eine neue Aeusserung bricht die laufende ab. Kein Warten auf das
        Satzende, kein Mischen zweier Stimmen. Die Epochenmarke faellt mit:
        ein Strom, der noch SYNTHETISIERT (also noch keinen pw-cat hat),
        legt danach keinen mehr an -- sonst kaeme die alte Stimme verspaetet
        und mischte sich unter die neue."""
        with self._pwc_lock:
            self._epoche += 1
            if self._pwc is not None and self._pwc.poll() is None:
                self._pwc.kill()      # Unterbrechung = Prozess toeten

            self._pwc = None

    def _beobachte_ende(self, pwc: subprocess.Popen, marke: str) -> None:
        """Meldet `gesprochen`, wenn der pw-cat-Prozess ENDET -- die
        Abkuehlung zaehlt ab dem letzten Ton, nicht ab der Anfrage."""
        pwc.wait()
        antwort = hub_anfrage(self.hub_socket,
                              {"v": 1, "art": "gesprochen", "marke": marke})
        if not antwort.get("ok"):
            self.log.warn("gesprochen-Meldung abgelehnt",
                          DAIMON_GRUND=str(antwort.get("grund", ""))[:80])

    def sprich(self, text: str, marke: object) -> dict:
        basis = {"engine": "sherpa-onnx", "modell": self.modell or self.stimme,
                 "provider": "cpu"}
        if self.ladefehler is not None or self._tts is None:
            return {"v": 1, "ok": False, "grund": self.ladefehler or "nicht_geladen",
                    **basis}
        freigabe = hub_anfrage(self.hub_socket,
                               {"v": 1, "art": "beginnt", "marke": marke})
        if not freigabe.get("ok"):
            return {"v": 1, "ok": False,
                    "grund": str(freigabe.get("grund", "marke")), **basis}
        kanal = str(freigabe.get("kanal", ""))
        # Der Validator sitzt im HUB (Design §8.3). Wer hierher kommt, hat
        # eine Marke -- und eine Marke gab es nur fuer geprueften Text.


        # Die laufende Wiedergabe faellt, BEVOR die neue Synthese beginnt.
        self._unterbreche()
        with self._pwc_lock:
            epoche = self._epoche

        # Synthese und Streaming in einem Strom-Thread: pw-cat nimmt die
        # Daten nur im Abspieltempo ab, ein schreibender Aufruf wuerde die
        # ganze Wiedergabe lang blockieren -- und eine neue Aeusserung
        # koennte nie unterbrechen, weil sie hinter der alten anstuende.
        threading.Thread(target=self._stroeme,
                         args=(text, str(marke), epoche), daemon=True).start()
        return {"v": 1, "ok": True, "kanal": kanal, **basis}

    def _stroeme(self, text: str, marke: str, epoche: int) -> None:
        saetze = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        pwc = None
        try:
            for satz in saetze:
                audio = self._tts.generate(satz)
                self.rate = int(audio.sample_rate)
                with self._pwc_lock:
                    if epoche != self._epoche:
                        return          # unterbrochen, noch bevor es klang
                    if pwc is None:
                        # OHNE absoluten Pfad -- der PATH entscheidet (Kopf).
                        pwc = subprocess.Popen(
                            ["pw-cat", "--playback", "--rate", str(self.rate),
                             "--channels", "1", "--format", "s16", "-"],
                            stdin=subprocess.PIPE)
                        self._pwc = pwc
                roh = bytearray()
                for s in audio.samples:
                    roh += struct.pack("<h", max(-32768, min(32767, int(s * 32767))))
                pwc.stdin.write(bytes(roh))
            if pwc is not None:
                pwc.stdin.close()
        except (OSError, BrokenPipeError):
            # BrokenPipe beim Schreiben heisst: diese Wiedergabe wurde
            # unterbrochen. Das ist der Regelfall, kein Fehler.
            pass
        if pwc is not None:
            self._beobachte_ende(pwc, marke)

    # -- Bedienung ------------------------------------------------------------

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "engine": "sherpa-onnx",
                "modell": self.modell or self.stimme, "provider": "cpu",
                "stimme": self.stimme, "geladen": self._tts is not None,
                "ladefehler": self.ladefehler, "pid": os.getpid()}

    def lauf(self, srv: socket.socket) -> int:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return 1
            with conn:
                conn.settimeout(30.0)
                try:
                    roh = conn.makefile("rb").readline(MAX_ZEILE)
                    anfrage = json.loads(roh)
                except (json.JSONDecodeError, ValueError):
                    antwort = {"v": 1, "ok": False, "grund": "unlesbar"}
                else:
                    art = anfrage.get("art") if isinstance(anfrage, dict) else None
                    if art == "sprich":
                        antwort = self.sprich(str(anfrage.get("text", "")),
                                              anfrage.get("marke"))
                    elif art == "status":
                        antwort = self.zustand()
                    else:
                        antwort = {"v": 1, "ok": False, "grund": "unbekannte_art"}
                try:
                    conn.sendall(json.dumps(antwort).encode() + b"\n")
                except OSError:
                    pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon TTS-Dienst (T-3.9)")
    ap.add_argument("--socket", default=None,
                    help="ohne systemd: hier selbst horchen")
    ap.add_argument("--hub-socket", default=None)
    args = ap.parse_args(argv)

    # make_dirs=False: load() legt sonst $XDG_STATE_HOME/daimon an und
    # chmod-et es -- unter ProtectHome=read-only ist das ein EROFS (am 03.08.
    # real passiert, siehe daimon/gpu/worker.py).
    cfg = load_config(make_dirs=False)
    hub_socket = args.hub_socket or str(cfg.runtime_dir / TTS_SOCKET)

    srv = sd_socket()
    if srv is None:
        if not args.socket:
            raise SystemExit(
                "Weder Socket-Aktivierung (LISTEN_FDS) noch --socket. Der "
                "Dienst legt ohne beides keinen Socket an: er waere dann "
                "gestartet, aber unerreichbar.")
        srv = eigener_socket(args.socket)

    sprecher = Sprecher(hub_socket=hub_socket, cfg=cfg,
                        log=get_logger("daimon-tts"))
    sprecher.laden()           # das Modell im Speicher IST das TTFA-Kriterium
    try:
        return sprecher.lauf(srv)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
