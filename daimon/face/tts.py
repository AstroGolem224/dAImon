"""T-3.9 — die Stimme. sherpa-onnx VITS, CPU, 0 VRAM.

Warum ein eigener Dienst und nicht ein Thread im Hub
----------------------------------------------------------------------------
Das Modell muss im Speicher bleiben: Laden kostet 414 ms (gemessen), und
Kriterium 11 verlangt einen TTFA-p95 unter 200 ms. Ein Thread im Hub haette dem
Prozess, der Policy, Marken und Tickets haelt, eine C-Extension und einen
Audio-Ausgabepfad dazugegeben -- beides Angriffsflaeche an genau der falschen
Stelle. Der Dienst ist socket-aktiviert wie der GPU-Worker aus T-3.7, aber
**ohne Leerlauf-Exit**: dort war die Begruendung die VRAM-Rueckgabe, und die
traegt hier nicht (0 VRAM). Das Modell im Speicher IST das Kriterium.

Warum segmentweise synthetisiert wird
----------------------------------------------------------------------------
sherpa-VITS ruft seinen Callback **einmal je Satz**, mit dem ganzen Satz. Bei
einer Aeusserung als Ganzem ist "erstes Sample" deshalb gleich "letztes Sample",
und die Latenz waechst mit der Textlaenge. Also wird an den Satzzeichen getrennt
und das **erste** Segment sofort in die Ausgabe geschrieben, waehrend der Rest
noch entsteht: der TTFA haengt damit an der Laenge des ersten Segments und nicht
mehr an der der Aeusserung.

Gemessen ueber 20 Aeusserungen, ganze Kette, 8 Threads: **p95 148 ms**, Median
76 ms, Maximum 155 ms. Mit 4 Threads p95 187 ms, mit 2 Threads 316 ms und damit
verfehlt. Rohwerte in `tests/evidence/T-3.9-tts.json`.

Design §8.2 nennt "~40 ms TTFA". Das gilt fuer ein einzelnes Wort ("Fertig." =
33 ms), nicht fuer einen Satz. Der Wert im Design ist zu optimistisch, und das
ist am 03.08. nachgemessen worden -- nicht geraten.

Der Text kommt vom Hub, nicht vom Aufrufer
----------------------------------------------------------------------------
Dieser Dienst spricht **ausschliesslich** die Zeichenkette, die der Hub in
seiner Freigabe zurueckgibt -- nicht die, die der Client geschickt hat. Das ist
der Unterschied zwischen "der Validator sitzt im Hub" und "der Validator wird
im Hub auch gefragt": ohne Marke und ohne Hub-Text passiert hier nichts, und
ein Direktzugriff auf diesen Socket erreicht die Ausgabe nicht.

Warum `pw-cat` als Unterprozess
----------------------------------------------------------------------------
Die Unterbrechung ist damit ein `kill()` und nichts weiter -- deterministisch,
von aussen messbar am Prozessende, ohne Callback-Thread, dessen Abbruch man
glauben muss. Der Aufruf steht **ohne absoluten Pfad** da, damit eine Pruefung
einen Stub in den PATH legen und messen kann, was hier ausgegeben werden
wollte, ohne die Soundkarte anzufassen (Lehre aus T-2.7 und T-3.7).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import threading
import time

from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger

TTS_SOCKET = "tts.sock"            # der Hub-Endpunkt (Validator + Abkuehlung)
LISTEN_FDS_START = 3
MAX_ZEILE = 1 << 16

# Vorgaben. Stehen zusaetzlich in daimon/common/config.py -- hier, damit das
# Modul ohne Konfiguration lauffaehig bleibt.
STIMME = "de_DE-thorsten-high"
# Das Modell liegt heute im Spike-Verzeichnis von T−1.12.
# ponytail: Spike-Pfad als Vorgabe. Obergrenze: sobald T-3.10 die Persona-
# Dateien anlegt, gehoert die Stimme nach ~/.local/share/daimon/voices/ und
# dieser Wert in die Persona statt in die Hauptkonfiguration.
MODELL_DIR = ("spikes/nvidia-voice/models/vits-piper-de_DE-thorsten-high")
# Gemessen ueber 20 Aeusserungen der ganzen Kette (Anfrage bis erste Samples
# beim Wiedergabeprozess), 24 Kerne, Last 1,4:
#   2 Threads: p95 316 ms  -- verfehlt das Kriterium
#   4 Threads: p95 187 ms  -- erfuellt mit 13 ms Marge, Maximum 213 ms
#   8 Threads: p95 148 ms  -- Maximum 155 ms
# Acht, nicht vier: ein Kriterium mit 13 ms Luft ist beim naechsten Hintergrund-
# Build keins mehr, und der Einsatz sind ~100 ms auf acht von 24 Kernen. Auf
# einer Maschine mit weniger Kernen gehoert der Wert nach unten -- deshalb ist
# er konfigurierbar und nicht verdrahtet.
THREADS = 8
SAMPLERATE = 22050                 # kommt aus dem Modell, hier nur als Vorgabe
HUB_TIMEOUT_S = 10.0

# Erlaubte Stimmlizenzen. Design §8.2: thorsten und kerstin sind CC0, pavoque
# ist CC-BY-NC-SA und scheidet aus. Geprueft wird die MODEL_CARD der Stimme --
# nicht eine Liste von Namen, denn die waere beim naechsten Modell veraltet und
# wuerde eine unfreie Stimme durchlassen, die zufaellig "kerstin" heisst.
LIZENZEN_OK = ("CC0", "PUBLIC DOMAIN")

# Trennstellen fuer die Segmentierung. Satzzeichen, weil dort ohnehin eine
# Sprechpause liegt -- eine Trennung mitten im Wort waere hoerbar.
_TRENNER = re.compile(r"(?<=[.!?,;:])\s+")
MIN_SEGMENT_ZEICHEN = 12           # kuerzere Fetzen kosten mehr Overhead als
                                   # sie an TTFA bringen

# So viel wird zuerst in die Wiedergabe-Pipe geschrieben. Linux gibt einer Pipe
# 64 KiB; ein `write()` darueber blockiert, bis der Leser abnimmt -- und
# `pw-cat` nimmt in Echtzeit ab. 32 KiB passen sicher (0,74 s Audio bei
# 22050 Hz mono s16) und blockieren nicht.
PIPE_STUECK = 32 * 1024

# Gruende, nach denen das Pet den Ersatzsatz spricht: die Regelverletzungen aus
# Design §8.3. Positivliste, siehe Begruendung in `sprich()`. `abkuehlung` und
# `hub_weg` stehen absichtlich NICHT drin.
ERSATZ_ANLASS = "steht_am_bildschirm"
_ERSATZ_GRUENDE = frozenset({
    "zu_lang", "mehrzeilig", "code", "url", "pfad", "geheimnis", "leer",
    "freier_text", "nicht_trusted", "unbekannte_vorlage", "unbekannter_kanal",
})


class StimmFehler(RuntimeError):
    """Modell fehlt, Lizenz unbekannt, Modell nicht ladbar."""


def stimmlizenz(modell_dir: str) -> str:
    """Die Lizenz der Stimmgewichte aus ihrer MODEL_CARD.

    Wirft, wenn die Karte fehlt oder keine Lizenzzeile hat. Eine Stimme ohne
    nachweisbare Lizenz ist keine erlaubte Stimme -- "keine Angabe" ist nicht
    dasselbe wie "frei", und der Unterschied ist hier der ganze Punkt.
    """
    pfad = os.path.join(modell_dir, "MODEL_CARD")
    try:
        with open(pfad, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        raise StimmFehler(f"keine MODEL_CARD unter {pfad}: {exc}") from exc
    treffer = re.search(r"(?im)^\s*\*?\s*License:\s*(.+?)\s*$", text)
    if not treffer:
        raise StimmFehler(f"MODEL_CARD ohne Lizenzzeile: {pfad}")
    return treffer.group(1).strip()


def lizenz_pruefen(modell_dir: str) -> str:
    lizenz = stimmlizenz(modell_dir)
    if not any(ok in lizenz.upper() for ok in LIZENZEN_OK):
        raise StimmFehler(
            f"Stimmlizenz {lizenz!r} ist nicht erlaubt (erlaubt: "
            f"{', '.join(LIZENZEN_OK)}). Design §8.2: pavoque ist CC-BY-NC-SA "
            f"und scheidet aus.")
    return lizenz


def segmente(text: str) -> list[str]:
    """An Satzzeichen trennen, zu kurze Stuecke anhaengen.

    Das erste Segment bestimmt den TTFA, alle weiteren entstehen waehrend der
    Wiedergabe. Ein Segment unter `MIN_SEGMENT_ZEICHEN` wird ans naechste
    geklebt: "Ja, und?" in drei Fetzen zu synthetisieren kostet mehr
    Prozessaufrufe als es an Latenz spart.
    """
    rohe = [s for s in _TRENNER.split(text.strip()) if s]
    if not rohe:
        return []
    aus: list[str] = []
    for stueck in rohe:
        if aus and len(aus[-1]) < MIN_SEGMENT_ZEICHEN:
            aus[-1] = f"{aus[-1]} {stueck}"
        else:
            aus.append(stueck)
    return aus


def hub_anfrage(hub_socket: str, anfrage: dict, *,
                timeout_s: float = HUB_TIMEOUT_S) -> dict:
    """Eine Zeile hin, eine zurueck. Dasselbe Muster wie im GPU-Worker."""
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout_s)
    try:
        c.connect(hub_socket)
        c.sendall(json.dumps(anfrage).encode() + b"\n")
        roh = c.makefile("rb").readline(MAX_ZEILE)
    except OSError as exc:
        return {"v": 1, "ok": False, "grund": "hub_weg",
                "meldung": str(exc)[:120]}
    finally:
        c.close()
    try:
        antwort = json.loads(roh)
    except (json.JSONDecodeError, ValueError):
        return {"v": 1, "ok": False, "grund": "hub_weg",
                "meldung": "Antwort unlesbar"}
    return antwort if isinstance(antwort, dict) else {
        "v": 1, "ok": False, "grund": "hub_weg", "meldung": "kein Objekt"}


def als_pcm(samples: object) -> bytes:
    """float32 [-1,1] nach s16le. Geklemmt, nicht skaliert -- eine
    Normalisierung ueber die Aeusserung wuerde die Lautstaerke von der
    Satzlaenge abhaengig machen."""
    import numpy as np
    feld = np.asarray(samples, dtype="float32")
    return (np.clip(feld, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class Sprecher:
    """Ein Modell im Speicher, hoechstens eine Wiedergabe.

    `_gen` ist der Generationszaehler: jede neue Aeusserung erhoeht ihn, und
    jede laufende prueft ihn vor dem naechsten Segment. So bricht die alte ab,
    ohne dass zwei Stimmen uebereinander liegen -- und ohne eine Warteschlange,
    die man leeren muss.
    """

    def __init__(self, *, hub_socket: str, modell_dir: str,
                 stimme: str = STIMME, threads: int = THREADS,
                 log: Logger | None = None) -> None:
        self.hub_socket = hub_socket
        self.modell_dir = modell_dir
        self.stimme = stimme
        self.threads = int(threads)
        self.log = log or get_logger("daimon-tts")
        self.lizenz = lizenz_pruefen(modell_dir)   # vor dem Laden, nicht danach
        self._lock = threading.Lock()
        self._gen = 0
        self._wiedergabe: subprocess.Popen | None = None
        self._tts = None
        self.samplerate = SAMPLERATE
        self.geladen = False
        self.ladezeit_ms: float | None = None
        self.warmlauf_ms: float | None = None
        self.gesprochen = 0
        self.abgebrochen = 0

    # -- Modell ------------------------------------------------------------

    def laden(self) -> None:
        """Einmal, beim Start. Nicht bei der ersten Anfrage: die Ladezeit
        wuerde sonst in den TTFA der ersten Aeusserung wandern, und genau die
        erste ist die, bei der jemand hinhoert."""
        import sherpa_onnx
        t0 = time.monotonic()
        modell = os.path.join(self.modell_dir, f"{self.stimme}.onnx")
        tokens = os.path.join(self.modell_dir, "tokens.txt")
        daten = os.path.join(self.modell_dir, "espeak-ng-data")
        for p in (modell, tokens, daten):
            if not os.path.exists(p):
                raise StimmFehler(f"Stimme unvollstaendig, fehlt: {p}")
        self._tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=modell, tokens=tokens, data_dir=daten),
                # provider="cpu" ist die Zusage "0 VRAM". Sie steht hier und
                # nicht in der Konfiguration: ein Konfigurationswert waere ein
                # Schalter, mit dem sich ein CUDA-Provider einschalten laesst,
                # und damit waere die Zusage keine Zusage mehr.
                provider="cpu",
                num_threads=self.threads,
                debug=False),
            max_num_sentences=1))
        self.samplerate = int(self._tts.sample_rate)

        # Warmlauf. Die ERSTE Synthese in einem Prozess kostet ein Mehrfaches
        # der folgenden -- gemessen 534 ms gegen 126 ms, weil onnxruntime seine
        # Arenen und Kernel erst beim ersten Lauf einrichtet. Ohne diesen
        # Wegwurfsatz traegt genau die erste Aeusserung die Rechnung, also die,
        # bei der jemand hinhoert, und ein TTFA-p95 aus 20 Laeufen wuerde den
        # Ausreisser glatt buegeln, statt ihn zu zeigen. Es wird nichts
        # ausgegeben: das Ergebnis wird verworfen.
        t_warm = time.monotonic()
        self._tts.generate("Warmlauf.", sid=0, speed=1.0)
        self.warmlauf_ms = round((time.monotonic() - t_warm) * 1000, 2)

        self.ladezeit_ms = round((time.monotonic() - t0) * 1000, 2)
        self.geladen = True
        self.log.info("Stimme geladen", DAIMON_ACTION="tts_laden",
                      DAIMON_STIMME=self.stimme, DAIMON_LIZENZ=self.lizenz,
                      DAIMON_LADEZEIT_MS=self.ladezeit_ms,
                      DAIMON_WARMLAUF_MS=self.warmlauf_ms,
                      DAIMON_THREADS=self.threads)

    # -- Unterbrechen ------------------------------------------------------

    def abbrechen(self) -> int:
        """Laufende Wiedergabe beenden. Rueckgabe ist die neue Generation.

        `kill` und nicht `terminate`: `pw-cat` hat einen Puffer, und ein
        geordnetes Ende spielt ihn noch aus -- das waere eine Unterbrechung,
        die man noch hoert.
        """
        with self._lock:
            self._gen += 1
            p = self._wiedergabe
            self._wiedergabe = None
        if p is not None and p.poll() is None:
            # NUR toeten. `p.stdin` wird hier ABSICHTLICH nicht angefasst:
            # der Sprech-Thread steht gerade in einem blockierenden `write()`
            # auf demselben `BufferedWriter`, und dessen Lock haelt er dabei.
            # Ein `close()` von hier wartet also auf genau den Schreiber, den
            # der Abbruch beenden soll -- gemessen 4002 ms fuer einen Abbruch,
            # der unter 100 ms liegen muss. Nach dem `kill` bekommt der
            # Schreiber EPIPE und raeumt selbst auf (siehe `_ausgeben`).
            p.kill()
            p.wait(timeout=2)
            self.abgebrochen += 1
        return self._gen

    # -- Sprechen ----------------------------------------------------------

    def sprich(self, *, kanal: str, text: object = None, anlass: object = None,
               werte: object = None, markierung: str = "trusted",
               nur_melden: bool = False) -> dict:
        """Freigabe holen, segmentweise synthetisieren, ausgeben.

        Reihenfolge: **erst der Hub, dann die Synthese.** Ein Text, der die
        Regeln verletzt, darf nicht einmal Rechenzeit kosten -- und vor allem
        darf nichts entstehen, was danach noch versehentlich ausgegeben werden
        koennte.
        """
        anfrage: dict = {"v": 1, "art": "freigabe", "kanal": kanal}
        if anlass is not None:
            anfrage["anlass"] = anlass
            anfrage["werte"] = werte if isinstance(werte, dict) else {}
            anfrage["markierung"] = markierung
        else:
            anfrage["text"] = text
        frei = hub_anfrage(self.hub_socket, anfrage)
        if not frei.get("ok"):
            grund = str(frei.get("grund", "unbekannt"))
            self.log.info("Nicht gesprochen", DAIMON_ACTION="tts_abgelehnt",
                          DAIMON_GRUND=grund, DAIMON_KANAL=kanal[:20])
            antwort = {"v": 1, "ok": False, "grund": grund,
                       "ersatz": frei.get("ersatz", ""),
                       "rest_s": frei.get("rest_s")}
            # Design §8.3: verletzt eine Antwort eine Regel, "sagt das Pet,
            # dass die Antwort auf dem Bildschirm steht". Es schweigt also
            # nicht -- Schweigen waere von einem abgestuerzten Dienst nicht zu
            # unterscheiden, und der Nutzer wartet auf etwas, was nie kommt.
            #
            # NUR bei Regelverletzungen. Bei `abkuehlung` waere der Ersatzsatz
            # genau die Aeusserung, die die Abkuehlung verhindern soll, und bei
            # `hub_weg` gibt es niemanden, der ihn freigeben koennte. Deshalb
            # eine Positivliste und keine Ausnahmeliste: ein neuer Grund ist
            # dann stumm, bis jemand entscheidet, und nicht versehentlich
            # gesprochen.
            if not nur_melden and grund in _ERSATZ_GRUENDE:
                ersatz = self.sprich(kanal=kanal, anlass=ERSATZ_ANLASS,
                                     nur_melden=True)
                antwort["ersatz_gesprochen"] = bool(ersatz.get("ok"))
                antwort["ersatz_ttfa_ms"] = ersatz.get("ttfa_ms")
            return antwort

        # AUSSCHLIESSLICH der Text des Hubs. Nicht `text`.
        satz = str(frei.get("text", ""))
        marke = str(frei.get("marke", ""))
        if not satz or not marke:
            return {"v": 1, "ok": False, "grund": "freigabe_unvollstaendig"}
        return self._ausgeben(satz, kanal=kanal, marke=marke)

    def _ausgeben(self, satz: str, *, kanal: str, marke: str) -> dict:
        gen = self.abbrechen()          # eine neue Aeusserung bricht die alte ab
        stuecke = segmente(satz)
        t0 = time.monotonic()
        ttfa_ms: float | None = None
        p = subprocess.Popen(
            # OHNE absoluten Pfad -- siehe Modulkopf.
            ["pw-cat", "--playback", "--raw", "--format=s16",
             f"--rate={self.samplerate}", "--channels=1", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        with self._lock:
            if gen != self._gen:        # schon wieder abgebrochen
                p.kill()
                return {"v": 1, "ok": False, "grund": "abgebrochen"}
            self._wiedergabe = p

        hub_gemeldet = False
        try:
            for stueck in stuecke:
                if gen != self._gen:
                    break
                audio = self._tts.generate(stueck, sid=0, speed=1.0)
                if gen != self._gen:
                    break
                pcm = als_pcm(audio.samples)
                try:
                    # ERST ein Stueck, das in die Pipe passt, DANN stempeln,
                    # dann der Rest. Ein `write()` des ganzen Segments
                    # blockiert, sobald es groesser als der Pipe-Puffer ist
                    # (64 KiB), weil `pw-cat` in Echtzeit liest -- gemessen
                    # 1014 ms im Median fuer 96 KB. Wer nach dem vollen Schreiben
                    # stempelt, misst die Abspieldauer und nennt sie Latenz.
                    # Genau dieser Fehler ist hier am 03.08. passiert.
                    p.stdin.write(pcm[:PIPE_STUECK])
                    p.stdin.flush()
                    if ttfa_ms is None:
                        # Jetzt sind Samples beim Wiedergabeprozess -- nicht in
                        # unserem Puffer, nicht in einer Warteschlange.
                        ttfa_ms = round((time.monotonic() - t0) * 1000, 2)
                    if len(pcm) > PIPE_STUECK:
                        p.stdin.write(pcm[PIPE_STUECK:])
                        p.stdin.flush()
                except (OSError, ValueError):
                    break               # Wiedergabe weg (Abbruch oder Fehler)
                if ttfa_ms is not None and not hub_gemeldet:
                    # Erst jetzt "ich spreche" melden: vorher war noch kein Ton
                    # unterwegs, und eine Sperre, die zu frueh zugeht, sperrt
                    # das Mikrofon fuer eine Stille.
                    hub_anfrage(self.hub_socket,
                                {"v": 1, "art": "beginnt", "marke": marke})
                    hub_gemeldet = True
        finally:
            try:
                if p.stdin is not None:
                    p.stdin.close()
            except OSError:
                pass
            if gen == self._gen:
                p.wait(timeout=60)
                with self._lock:
                    if self._wiedergabe is p:
                        self._wiedergabe = None
            if hub_gemeldet:
                # Auch nach einem Abbruch: sonst bleibt `tts_active` stehen und
                # die Rueckkopplungssperre haelt das Mikrofon fuer immer zu.
                hub_anfrage(self.hub_socket,
                            {"v": 1, "art": "gesprochen", "marke": marke})

        if ttfa_ms is None:
            # Kein einziges Sample ist beim Wiedergabeprozess angekommen. Das
            # ist KEIN Erfolg, auch wenn nichts abgebrochen wurde: `pw-cat`
            # kann sofort gestorben sein (kein PipeWire erreichbar, falsches
            # XDG_RUNTIME_DIR, Geraet weg). Am 03.08. genau so aufgetreten --
            # der Dienst meldete `gesprochen: true` mit `ttfa_ms: null`, und
            # das ist die Sorte Selbstauskunft, die in dieser Fehlerliste
            # unter Nummer 9 steht.
            self.log.warn("Nichts ausgegeben -- Wiedergabe nicht erreichbar",
                          DAIMON_ACTION="tts_stumm", DAIMON_KANAL=kanal[:20],
                          DAIMON_RC=p.returncode)
            return {"v": 1, "ok": False, "grund": "ausgabe_weg",
                    "gesprochen": False, "ttfa_ms": None,
                    "wiedergabe_rc": p.returncode}

        vollstaendig = gen == self._gen
        self.gesprochen += 1 if vollstaendig else 0
        self.log.info("Gesprochen" if vollstaendig else "Abgebrochen",
                      DAIMON_ACTION="tts_sprich", DAIMON_KANAL=kanal[:20],
                      DAIMON_TTFA_MS=ttfa_ms, DAIMON_SEGMENTE=len(stuecke),
                      DAIMON_ZEICHEN=len(satz))
        return {"v": 1, "ok": True, "gesprochen": vollstaendig,
                "ttfa_ms": ttfa_ms, "segmente": len(stuecke),
                "text": satz, "kanal": kanal}

    def zustand(self) -> dict:
        return {
            "v": 1, "ok": True, "engine": "sherpa-onnx-vits",
            "modell": self.stimme, "provider": "cpu",
            "lizenz": self.lizenz, "threads": self.threads,
            "samplerate": self.samplerate, "geladen": self.geladen,
            "ladezeit_ms": self.ladezeit_ms,
            "warmlauf_ms": self.warmlauf_ms, "gesprochen": self.gesprochen,
            "abgebrochen": self.abgebrochen, "pid": os.getpid(),
        }


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


def bediene(sprecher: Sprecher, conn: socket.socket) -> None:
    """Eine Verbindung, eine Anfrage, eine Antwort.

    Jede Verbindung bekommt einen eigenen Thread, damit eine **neue** Anfrage
    eine laufende Wiedergabe unterbrechen kann. Ohne das waere "unterbrechbar"
    unmoeglich: der Annahmeschleife fehlte die Gelegenheit, die zweite Anfrage
    ueberhaupt zu sehen.
    """
    with conn:
        conn.settimeout(10.0)
        try:
            roh = conn.makefile("rb").readline(MAX_ZEILE)
            anfrage = json.loads(roh)
        except (OSError, json.JSONDecodeError, ValueError):
            _antworte(conn, {"v": 1, "ok": False, "grund": "unlesbar"})
            return
        if not isinstance(anfrage, dict):
            _antworte(conn, {"v": 1, "ok": False, "grund": "unlesbar"})
            return
        art = anfrage.get("art", "sprich")
        if art == "zustand":
            _antworte(conn, sprecher.zustand())
            return
        if art == "still":
            sprecher.abbrechen()
            _antworte(conn, {"v": 1, "ok": True, "still": True})
            return
        if art != "sprich":
            _antworte(conn, {"v": 1, "ok": False, "grund": "unbekannte_art"})
            return
        _antworte(conn, sprecher.sprich(
            kanal=str(anfrage.get("kanal", "reaktion")),
            text=anfrage.get("text"), anlass=anfrage.get("anlass"),
            werte=anfrage.get("werte"),
            markierung=str(anfrage.get("markierung", "trusted"))))


def lauf(sprecher: Sprecher, srv: socket.socket) -> int:
    """Kein Leerlauf-Exit. Siehe Modulkopf: das Modell im Speicher IST das
    TTFA-Kriterium, und 0 VRAM heisst, dass Warten nichts kostet."""
    threads: list[threading.Thread] = []
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        t = threading.Thread(target=bediene, args=(sprecher, conn), daemon=True)
        t.start()
        threads = [x for x in threads if x.is_alive()] + [t]
    return 0


def einstellungen(cfg: Config) -> dict:
    return {
        "stimme": str(cfg.get("persona.voice", STIMME)),
        "modell_dir": str(cfg.get("tts.modell_dir", MODELL_DIR)),
        "threads": int(cfg.get("tts.threads", THREADS)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon TTS (T-3.9)")
    ap.add_argument("--socket", default=None,
                    help="ohne systemd: hier selbst horchen")
    ap.add_argument("--hub-socket", default=None)
    ap.add_argument("--stimme", default=None)
    ap.add_argument("--modell-dir", default=None)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--sag", default=None,
                    help="einmal sprechen und beenden (Handlauf, kein Dienst)")
    ap.add_argument("--kanal", default="reaktion")
    args = ap.parse_args(argv)

    # make_dirs=False: `load()` legt sonst $XDG_STATE_HOME/daimon an und
    # chmod-et es -- unter `ProtectHome=read-only` ein EROFS, und der Dienst
    # stirbt vor der ersten Zeile. Am 03.08. beim GPU-Worker genau so passiert.
    cfg = load_config(make_dirs=False)
    werte = einstellungen(cfg)
    if args.stimme:
        werte["stimme"] = args.stimme
    if args.modell_dir:
        werte["modell_dir"] = args.modell_dir
    if args.threads is not None:
        werte["threads"] = args.threads
    hub_socket = args.hub_socket or str(cfg.runtime_dir / TTS_SOCKET)

    sprecher = Sprecher(hub_socket=hub_socket, **werte)
    sprecher.laden()

    if args.sag is not None:
        print(json.dumps(sprecher.sprich(kanal=args.kanal, text=args.sag),
                         ensure_ascii=False))
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
        return lauf(sprecher, srv)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
