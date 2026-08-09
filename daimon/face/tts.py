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
import signal
import socket
import subprocess
import threading
import time

from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger
from daimon.face import mimic as mimic_client

TTS_SOCKET = "tts.sock"            # der Hub-Endpunkt (Validator + Abkuehlung)
LISTEN_FDS_START = 3
MAX_ZEILE = 1 << 16

# Vorgaben. Stehen zusaetzlich in daimon/common/config.py -- hier, damit das
# Modul ohne Konfiguration lauffaehig bleibt.
STIMME = "de_DE-thorsten-high"
# Seit T-3.10 unter $XDG_DATA_HOME. Vorher lag die Stimme im Spike-Verzeichnis
# von T−1.12 -- dort, wo sie heruntergeladen wurde, und ein Produktivdienst laedt
# nicht aus einem Verzeichnis, das "wegwerfbar" heisst. Der Spike-Pfad ist jetzt
# ein Symlink hierher, damit die Messskripte weiterlaufen.
# Das ist die SAMMLUNG; welche Stimme daraus genommen wird, sagt `persona.voice`
# (seit T-3.10 aus der Persona-Datei, mit daimon.toml als Rueckfall).
MODELL_DIR = "~/.local/share/daimon/voices"
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

# T-3.16, PHASE2.md Schritt 16. Die Schwelle ist ein STARTWERT, kein
# Messergebnis: sherpa liegt bei 132 ms p95, Mimic warm bei ~250 ms und kalt
# bei 7.1 s. Unter der Schwelle ist der Unterschied hoerbar, darueber wiegt
# Mimics Stimme ihn auf. Nach zwei Wochen Alltag gehoert die Zahl nachgemessen.
MIMIC_STIMME = "matthias"
MIMIC_AB_ZEICHEN = 80
MIMIC_NUR_WARM = True

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


def stimmverzeichnis(modell_dir: str, stimme: str) -> str:
    """Das Verzeichnis der Stimme, ohne die Gewichte zu verlangen.

    Getrennt von `stimme_aufloesen()`, damit die LIZENZ vor den Gewichten
    geprueft werden kann: eine unfreie Stimme wird abgelehnt, auch wenn ihre
    Dateien fehlen oder anders heissen. Sonst heisst die Absage `stimme_fehlt`
    und der Lizenzbefund verschwindet hinter einem Dateiproblem.
    """
    kandidaten = [
        modell_dir if os.path.exists(os.path.join(modell_dir, f"{stimme}.onnx"))
        else "",
        os.path.join(modell_dir, stimme),
        os.path.join(modell_dir, f"vits-piper-{stimme}"),
    ]
    for k in kandidaten:
        if k and os.path.isdir(k):
            return k
    raise StimmFehler(
        f"kein Verzeichnis fuer Stimme {stimme!r} unter {modell_dir!r}. "
        f"Geprueft: {'; '.join(k for k in kandidaten if k)}")


def stimme_aufloesen(modell_dir: str, stimme: str) -> tuple[str, str]:
    """`(stimmverzeichnis, onnx_pfad)` aus Basisverzeichnis und Stimmnamen.

    Design §10.1 nennt **beides**: `voice = "de_DE-thorsten-high"` (also den
    Namen der Gewichtsdatei) und ein Sammelverzeichnis
    `~/.local/share/daimon/voices/`. Ob `tts.modell_dir` demnach *das*
    Stimmverzeichnis ist oder eines *mit Stimmverzeichnissen darin*, steht
    nirgends -- und beim Gegenlesen am 03.08. hatten Builder und Reviewer
    genau darueber verschiedene Annahmen: der eine legte `modell_dir` auf die
    Stimme, der andere auf die Sammlung, mit `voice` als Verzeichnisnamen.

    Beide Lesarten sind vertretbar, also wird keine erzwungen. Gesucht wird in
    dieser Reihenfolge, und die Fehlermeldung nennt jeden geprueften Pfad --
    eine Suche, die nur "nicht gefunden" sagt, zwingt zum Raten:

      1. `<basis>/<stimme>.onnx`            -- basis IST das Stimmverzeichnis
      2. `<basis>/<stimme>/<stimme>.onnx`   -- Sammlung, Verzeichnis wie Stimme
      3. `<basis>/vits-piper-<stimme>/<stimme>.onnx`  -- die sherpa-Benennung
      4. `<basis>/<stimme>/` mit genau EINER `.onnx` -- `voice` ist der
         Verzeichnisname (`vits-piper-de_DE-thorsten-high`)

    Genau eine `.onnx` in Fall 4, nicht die erste von mehreren: bei zwei
    Dateien waere die Wahl geraten, und eine geratene Stimme ist eine, deren
    Lizenz man nicht geprueft hat.
    """
    versucht: list[str] = []

    def nimm(verzeichnis: str, onnx: str) -> tuple[str, str] | None:
        versucht.append(onnx)
        return (verzeichnis, onnx) if os.path.exists(onnx) else None

    kandidaten = [
        nimm(modell_dir, os.path.join(modell_dir, f"{stimme}.onnx")),
        nimm(os.path.join(modell_dir, stimme),
             os.path.join(modell_dir, stimme, f"{stimme}.onnx")),
        nimm(os.path.join(modell_dir, f"vits-piper-{stimme}"),
             os.path.join(modell_dir, f"vits-piper-{stimme}", f"{stimme}.onnx")),
    ]
    for treffer in kandidaten:
        if treffer:
            return treffer

    unter = os.path.join(modell_dir, stimme)
    if os.path.isdir(unter):
        onnx = sorted(f for f in os.listdir(unter) if f.endswith(".onnx"))
        if len(onnx) == 1:
            return (unter, os.path.join(unter, onnx[0]))
        versucht.append(f"{unter}/*.onnx ({len(onnx)} gefunden, gebraucht: 1)")

    raise StimmFehler(
        f"Stimme {stimme!r} nicht gefunden unter {modell_dir!r}. Geprueft: "
        + "; ".join(versucht))


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
                 mimic_socket: str = "", mimic_stimme: str = MIMIC_STIMME,
                 mimic_ab_zeichen: int = MIMIC_AB_ZEICHEN,
                 mimic_nur_warm: bool = MIMIC_NUR_WARM,
                 log: Logger | None = None) -> None:
        self.hub_socket = hub_socket
        self.modell_dir = modell_dir
        self.stimme = stimme
        self.threads = int(threads)
        self.mimic_socket = mimic_socket
        self.mimic_stimme = mimic_stimme
        self.mimic_ab_zeichen = int(mimic_ab_zeichen)
        self.mimic_nur_warm = bool(mimic_nur_warm)
        # Leer heisst: Mimic ist nutzbar. Gesetzt wird das von der
        # Startpruefung -- und dann bleibt es gesetzt, bis jemand den Dienst
        # neu startet. Ein Pfad, der beim Start nicht antwortet, soll nicht bei
        # jeder Aeusserung erneut 300 ms kosten.
        self.mimic_aus: str = ""
        self._mimic_sitzung = None
        self._mimic_gen = -1
        self._warm_laeuft = False
        self.log = log or get_logger("daimon-tts")
        # Erst die Stimme finden, dann ihre Lizenz pruefen -- und beides VOR
        # dem Laden. Eine Lizenz, die nach dem Laden geprueft wird, ist eine
        # Lizenz, die man schon benutzt hat.
        #
        # Scheitert eines von beiden, wird NICHT geworfen. Ein socket-
        # aktivierter Dienst, der beim Start stirbt, hinterlaesst dem Aufrufer
        # eine geschlossene Verbindung ohne Grund -- und eine tote Unit ist von
        # einer stummen nicht zu unterscheiden. Stattdessen laeuft der Dienst
        # an und sagt jeder Anfrage ehrlich ab (`stimme_unerlaubt`,
        # `stimme_fehlt`). Befund aus dem Gegenlesen am 03.08.
        self.stimm_dir, self.onnx, self.lizenz = "", "", ""
        self.absage: str = ""
        self.absage_meldung: str = ""
        try:
            # Reihenfolge: erst das Verzeichnis, dann die LIZENZ, dann die
            # Gewichte. Eine unfreie Stimme muss abgelehnt werden, auch wenn
            # ihre Gewichte fehlen oder anders heissen -- sonst heisst die
            # Absage `stimme_fehlt`, und der Lizenzbefund verschwindet hinter
            # einem Dateiproblem. Am 03.08. beim Gegenlesen aufgefallen: der
            # Pruefstand baut eine pavoque-Karte in ein Verzeichnis mit
            # thorsten-Gewichten, und genau dieser Fall trennt "Lizenz geprueft"
            # von "Dateien gefunden".
            self.stimm_dir = stimmverzeichnis(modell_dir, stimme)
            self.lizenz = lizenz_pruefen(self.stimm_dir)
            _, self.onnx = stimme_aufloesen(modell_dir, stimme)
        except StimmFehler as exc:
            self.absage = ("stimme_unerlaubt" if "nicht erlaubt" in str(exc)
                           else "stimme_fehlt")
            self.absage_meldung = str(exc)[:300]
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
        self._mimic_startpruefung()
        if self.absage:
            self.log.warn("Stimme nicht verwendbar -- der Dienst laeuft und "
                          "sagt ab", DAIMON_ACTION="tts_absage",
                          DAIMON_GRUND=self.absage,
                          DAIMON_STIMME=self.stimme,
                          DAIMON_MELDUNG=self.absage_meldung[:120])
            return
        import sherpa_onnx
        t0 = time.monotonic()
        modell = self.onnx
        tokens = os.path.join(self.stimm_dir, "tokens.txt")
        daten = os.path.join(self.stimm_dir, "espeak-ng-data")
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

    # -- Mimic -------------------------------------------------------------

    def _mimic_startpruefung(self) -> None:
        """Einmal `GET /status`, mit harter Frist (Schritt 6).

        Scheitert sie, wird **nur** der Mimic-Pfad abgeschaltet und
        protokolliert. Ein Mimic, der da ist und schweigt, darf den Start von
        `daimon-tts` nicht aufhalten -- sonst haette ausgerechnet der
        Rueckfalldienst eine Abhaengigkeit vom Dienst, den er ersetzen soll.
        """
        if not self.mimic_socket:
            self.mimic_aus = "aus"
            return
        t0 = time.monotonic()
        antwort = mimic_client.status(self.mimic_socket)
        dauer_ms = round((time.monotonic() - t0) * 1000, 2)
        stimmen = antwort.get("voices") if isinstance(antwort, dict) else None
        if not antwort:
            self.mimic_aus = "status_weg"
        elif isinstance(stimmen, list) and self.mimic_stimme not in [
                s.get("name") if isinstance(s, dict) else s for s in stimmen]:
            # Ein Profil, das Mimic nicht laden kann, ist kein Bereitschafts-
            # signal. Lieber sherpa als eine Aeusserung, die erst am Socket
            # scheitert.
            self.mimic_aus = "stimme_fehlt"
        else:
            self.mimic_aus = ""
        self.log.info("Mimic-Startpruefung", DAIMON_ACTION="tts_mimic_start",
                      DAIMON_GRUND=self.mimic_aus or "ok",
                      DAIMON_DAUER_MS=dauer_ms,
                      DAIMON_SOCKET=self.mimic_socket[:120])

    def _mimic_gewuenscht(self, satz: str) -> bool:
        return (not self.mimic_aus and bool(self.mimic_socket)
                and len(satz) >= self.mimic_ab_zeichen)

    def _warmlauf_anstossen(self) -> None:
        """Best effort, hoechstens einer gleichzeitig (Schritt 12a).

        Wird **nach** dem sherpa-Start aufgerufen, nie davor: ein haengendes
        `/warm` soll die Zukunft verbessern und nicht die Gegenwart aufhalten.
        """
        with self._lock:
            if self._warm_laeuft or not self.mimic_socket or self.mimic_aus:
                return
            self._warm_laeuft = True

        def lauf() -> None:
            try:
                grund = mimic_client.warmlauf(self.mimic_socket)
                self.log.info("Warmlauf angestossen",
                              DAIMON_ACTION="tts_mimic_warm",
                              DAIMON_GRUND=grund or "ok")
            finally:
                with self._lock:
                    self._warm_laeuft = False

        threading.Thread(target=lauf, daemon=True).start()

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
            # Die Mimic-Sitzung atomar mitnehmen, geschlossen wird ausserhalb
            # des Locks: `shutdown()` kann blockieren, und der Lock haelt hier
            # den ganzen Dienst auf (Schritt 10).
            sitzung = self._mimic_sitzung
            self._mimic_sitzung = None
        if sitzung is not None:
            sitzung.schliessen()
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
        if self.absage and not self.mimic_socket:
            # Nur wenn es AUCH keinen Mimic gibt. Bis T-3.16 stand diese
            # Ruecckehr vor dem Hub, und damit haette eine fehlende
            # sherpa-Stimme auch Mimic totgelegt -- obwohl der sprechen
            # koennte. Die Absage gilt jetzt dort, wo sie gilt: als Ergebnis
            # der Vorgabestufe (Schritt 11).
            return {"v": 1, "ok": False, "grund": self.absage,
                    "gesprochen": False, "meldung": self.absage_meldung,
                    "stimme": self.stimme, "engine": "sherpa-onnx-vits",
                    "provider": "cpu"}

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
                       "rest_s": frei.get("rest_s"), **self.kennung()}
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
        """Engine waehlen, dann ausgeben. Eine Generation fuer beide Wege.

        Schritt 9: **einmal** abbrechen und eine Generation reservieren -- sie
        gilt fuer Mimic UND den sherpa-Rueckfall und wird nicht erneut erhoeht.
        Sonst koennte eine aeltere Aeusserung, deren Mimic-Frist spaeter
        ablaeuft, sherpa ueber einer neueren starten, und es laegen zwei
        `pw-cat` uebereinander.
        """
        gen = self.abbrechen()
        # Der Zeitnullpunkt der AEUSSERUNG, nicht der der Wiedergabe. Wer den
        # Mimic-TTFA erst ab `pw-cat` misst, meldet 0.2 ms neben sherpas 206 --
        # und verschweigt genau die Wartezeit, die zwischen beiden entscheidet.
        t_start = time.monotonic()
        mimic_grund = ""
        if self._mimic_gewuenscht(satz):
            try:
                sitzung = mimic_client.sprechen(
                    self.mimic_socket, satz, stimme=self.mimic_stimme,
                    nur_warm=self.mimic_nur_warm)
            except mimic_client.MimicFehler as exc:
                mimic_grund = exc.grund
            else:
                with self._lock:
                    if gen != self._gen:
                        fremd = True
                    else:
                        fremd = False
                        self._mimic_sitzung = sitzung
                        self._mimic_gen = gen
                if fremd:
                    sitzung.schliessen()
                    return {"v": 1, "ok": False, "grund": "abgebrochen",
                            **self.kennung()}
                return self._mimic_ausgeben(sitzung, satz, kanal=kanal,
                                            marke=marke, gen=gen, t0=t_start)
        antwort = self._sherpa_ausgeben(satz, kanal=kanal, marke=marke, gen=gen,
                                        mimic_grund=mimic_grund)
        if mimic_grund:
            # ERST sherpa, DANN /warm. Die Reihenfolge ist bindend (12a).
            self._warmlauf_anstossen()
        return antwort

    def _mimic_ausgeben(self, sitzung, satz: str, *, kanal: str, marke: str,
                        gen: int, t0: float) -> dict:
        """Der Mimic-Pfad: `pw-cat` mit der Rate **aus dem H-Rahmen**.

        Die Rate steht beim Oeffnen fest, deshalb wird sie hier und nicht
        vorher entschieden (Schritt 12). Ein Abbruch mitten im Strom laesst den
        Satz halb -- kein Stimmwechsel mitten im Satz, keine Wiederholung
        (Schritt 15).
        """
        p = subprocess.Popen(
            ["pw-cat", "--playback", "--raw", "--format=s16",
             f"--rate={sitzung.rate}", "--channels=1", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        with self._lock:
            if gen != self._gen:
                p.kill()
                sitzung.schliessen()
                return {"v": 1, "ok": False, "grund": "abgebrochen",
                        **self.mimic_kennung()}
            self._wiedergabe = p
        stand: dict = {"ttfa_ms": None, "bytes": 0}

        def schreibe(block: bytes) -> bool:
            if gen != self._gen:
                return False
            try:
                p.stdin.write(block[:PIPE_STUECK])
                p.stdin.flush()
                if stand["ttfa_ms"] is None:
                    stand["ttfa_ms"] = round((time.monotonic() - t0) * 1000, 2)
                    hub_anfrage(self.hub_socket,
                                {"v": 1, "art": "beginnt", "marke": marke})
                if len(block) > PIPE_STUECK:
                    p.stdin.write(block[PIPE_STUECK:])
                    p.stdin.flush()
            except (OSError, ValueError):
                return False
            stand["bytes"] += len(block)
            return True

        weiter = schreibe(sitzung.erster_block)

        def rest() -> None:
            try:
                if weiter:
                    for block in sitzung.bloecke():
                        if not schreibe(block):
                            break
            finally:
                sitzung.schliessen()
                try:
                    if p.stdin is not None:
                        p.stdin.close()
                except OSError:
                    pass
                if gen == self._gen:
                    try:
                        p.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        p.kill()
                    with self._lock:
                        if self._wiedergabe is p:
                            self._wiedergabe = None
                        if self._mimic_sitzung is sitzung and self._mimic_gen == gen:
                            self._mimic_sitzung = None
                if stand["ttfa_ms"] is not None:
                    hub_anfrage(self.hub_socket,
                                {"v": 1, "art": "gesprochen", "marke": marke})
                if gen == self._gen and not sitzung.grund:
                    self.gesprochen += 1
                self.log.info("Gesprochen (Mimic)", DAIMON_ACTION="tts_sprich",
                              DAIMON_ENGINE="mimic", DAIMON_KANAL=kanal[:20],
                              DAIMON_TTFA_MS=stand["ttfa_ms"],
                              DAIMON_ZEICHEN=len(satz),
                              DAIMON_KORRELATION=sitzung.correlation_id,
                              DAIMON_GRUND=sitzung.grund or "ok")

        if stand["ttfa_ms"] is None:
            rest()
            return {"v": 1, "ok": False, "grund": "ausgabe_weg",
                    "gesprochen": False, "ttfa_ms": None,
                    "wiedergabe_rc": p.returncode, **self.mimic_kennung()}
        threading.Thread(target=rest, daemon=True).start()
        return {"v": 1, "ok": True, "gesprochen": None,
                "ttfa_ms": stand["ttfa_ms"], "text": satz, "kanal": kanal,
                "korrelation": sitzung.correlation_id,
                "samplerate": sitzung.rate, **self.mimic_kennung()}

    def mimic_kennung(self) -> dict:
        return {"engine": "mimic", "modell": self.mimic_stimme,
                "provider": "cuda-extern", "lizenz": "dots.tts Apache-2.0"}

    def _sherpa_ausgeben(self, satz: str, *, kanal: str, marke: str, gen: int,
                         mimic_grund: str = "") -> dict:
        """Erstes Segment im Vordergrund, Rest im Hintergrund.

        Die Antwort geht raus, sobald die ersten Samples beim
        Wiedergabeprozess sind -- nicht am Ende der Wiedergabe. Wer bis zum
        Ende wartet, kann nicht unterbrechen (Kriterium 4). Am 03.08. beim
        Gegenlesen aufgefallen.

        Zur Abkuehlung: sie wird im Hub bei `beginnt` vermerkt und bei
        `gesprochen` NEU gesetzt -- die Frist zaehlt damit ab dem letzten Ton,
        greift aber schon ab dem ersten. Nur am Ende zu vermerken war der erste
        Versuch und liess zwei schnelle Anfragen beide durch; die Begruendung
        steht in `daimon/hub/daemon.py`.
        """
        if self.absage:
            # Hier gilt sie: die Vorgabestufe kann nicht sprechen. Mimic wurde
            # zu diesem Zeitpunkt schon versucht oder gar nicht gewollt.
            return {"v": 1, "ok": False, "grund": self.absage,
                    "gesprochen": False, "meldung": self.absage_meldung,
                    "stimme": self.stimme, "mimic_grund": mimic_grund,
                    **self.kennung()}
        stuecke = segmente(satz)
        t0 = time.monotonic()
        p = subprocess.Popen(
            # OHNE absoluten Pfad -- siehe Modulkopf.
            ["pw-cat", "--playback", "--raw", "--format=s16",
             f"--rate={self.samplerate}", "--channels=1", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        with self._lock:
            if gen != self._gen:        # schon wieder abgebrochen
                p.kill()
                return {"v": 1, "ok": False, "grund": "abgebrochen",
                        **self.kennung()}
            self._wiedergabe = p

        stand: dict = {"ttfa_ms": None, "gesprochen": None, "segmente": 0}

        def schreibe(stueck: str) -> bool:
            """Ein Segment synthetisieren und ausgeben. False heisst: Schluss."""
            if gen != self._gen:
                return False
            audio = self._tts.generate(stueck, sid=0, speed=1.0)
            if gen != self._gen:
                return False
            pcm = als_pcm(audio.samples)
            try:
                # ERST ein Stueck, das in die Pipe passt, DANN stempeln, dann
                # der Rest. Ein `write()` des ganzen Segments blockiert, sobald
                # es groesser als der Pipe-Puffer ist (64 KiB), weil `pw-cat`
                # in Echtzeit liest -- gemessen 1014 ms im Median fuer 96 KB.
                # Wer nach dem vollen Schreiben stempelt, misst die
                # Abspieldauer und nennt sie Latenz. Genau dieser Fehler ist
                # hier am 03.08. passiert.
                p.stdin.write(pcm[:PIPE_STUECK])
                p.stdin.flush()
                if stand["ttfa_ms"] is None:
                    # Jetzt sind Samples beim Wiedergabeprozess -- nicht in
                    # unserem Puffer, nicht in einer Warteschlange.
                    stand["ttfa_ms"] = round((time.monotonic() - t0) * 1000, 2)
                    # Erst jetzt "ich spreche" melden: vorher war noch kein Ton
                    # unterwegs, und eine Sperre, die zu frueh zugeht, sperrt
                    # das Mikrofon fuer eine Stille.
                    hub_anfrage(self.hub_socket,
                                {"v": 1, "art": "beginnt", "marke": marke})
                if len(pcm) > PIPE_STUECK:
                    p.stdin.write(pcm[PIPE_STUECK:])
                    p.stdin.flush()
            except (OSError, ValueError):
                return False            # Wiedergabe weg (Abbruch oder Fehler)
            stand["segmente"] += 1
            return True

        weiter = schreibe(stuecke[0]) if stuecke else False

        def rest() -> None:
            """Der Hintergrundteil: restliche Segmente, dann aufraeumen."""
            try:
                if weiter:
                    for stueck in stuecke[1:]:
                        if not schreibe(stueck):
                            break
            finally:
                try:
                    if p.stdin is not None:
                        p.stdin.close()
                except OSError:
                    pass
                if gen == self._gen:
                    try:
                        p.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        p.kill()
                    with self._lock:
                        if self._wiedergabe is p:
                            self._wiedergabe = None
                stand["gesprochen"] = gen == self._gen and stand["ttfa_ms"] is not None
                if stand["ttfa_ms"] is not None:
                    # Auch nach einem Abbruch: sonst bleibt `tts_active` stehen
                    # und die Rueckkopplungssperre haelt das Mikrofon fuer immer
                    # zu. Der Hub setzt die Abkuehlungsfrist hier NEU, damit sie
                    # ab dem letzten Ton zaehlt -- vermerkt hat er sie schon bei
                    # `beginnt`.
                    hub_anfrage(self.hub_socket,
                                {"v": 1, "art": "gesprochen", "marke": marke})
                if stand["gesprochen"]:
                    self.gesprochen += 1
                self.log.info("Gesprochen" if stand["gesprochen"] else "Abgebrochen",
                              DAIMON_ACTION="tts_sprich",
                              DAIMON_KANAL=kanal[:20],
                              DAIMON_TTFA_MS=stand["ttfa_ms"],
                              DAIMON_SEGMENTE=stand["segmente"],
                              DAIMON_ZEICHEN=len(satz))

        if stand["ttfa_ms"] is None:
            # Kein einziges Sample ist beim Wiedergabeprozess angekommen. Das
            # ist KEIN Erfolg, auch wenn nichts abgebrochen wurde: `pw-cat`
            # kann sofort gestorben sein (kein PipeWire erreichbar, falsches
            # XDG_RUNTIME_DIR, Geraet weg). Am 03.08. genau so aufgetreten --
            # der Dienst meldete `gesprochen: true` mit `ttfa_ms: null`, und
            # das ist die Sorte Selbstauskunft, die in der Fehlerliste des
            # HANDOVER unter Nummer 9 steht.
            rest()
            self.log.warn("Nichts ausgegeben -- Wiedergabe nicht erreichbar",
                          DAIMON_ACTION="tts_stumm", DAIMON_KANAL=kanal[:20],
                          DAIMON_RC=p.returncode)
            return {"v": 1, "ok": False, "grund": "ausgabe_weg",
                    "gesprochen": False, "ttfa_ms": None,
                    "wiedergabe_rc": p.returncode, "mimic_grund": mimic_grund,
                    **self.kennung()}

        self._rest_thread = threading.Thread(target=rest, daemon=True)
        self._rest_thread.start()
        # KEIN `join`, auch nicht kurz. Ein Warten von 250 ms "damit
        # `gesprochen` in der Antwort steht" hat die Antwortzeit von 40 ms auf
        # 291 ms gehoben -- und damit die Zusage "eine neue Aeusserung
        # unterbricht binnen 100 ms" unmessbar gemacht, weil jede Messung von
        # aussen erst nach der Antwort anfangen kann. `gesprochen: null` heisst
        # "laeuft noch"; wer das Ende braucht, fragt `zustand` (`spricht`).

        # engine/modell/provider stehen in JEDER Antwort, nicht nur in
        # `zustand`: Kriterium 1 ("sherpa-onnx VITS, CPU, nicht piper1-gpl")
        # ist sonst nur am Quelltext belegbar, und eine `grep`-Pruefung ist an
        # der Schreibweise zu umgehen (T-1.7.v3).
        return {"v": 1, "ok": True, "gesprochen": stand["gesprochen"],
                "ttfa_ms": stand["ttfa_ms"], "segmente": len(stuecke),
                "text": satz, "kanal": kanal, "mimic_grund": mimic_grund,
                **self.kennung()}

    def kennung(self) -> dict:
        """Was zur Laufzeit belegt, WAS hier spricht. Kriterium 1 und 2.

        `provider` ist fest "cpu", weil es das im Code auch ist -- ein
        Konfigurationswert waere ein Schalter, mit dem sich ein CUDA-Provider
        einschalten liesse, und dann waere die 0-VRAM-Zusage keine.
        """
        return {"engine": "sherpa-onnx-vits", "modell": self.stimme,
                "provider": "cpu", "lizenz": self.lizenz or "unbekannt"}

    def zustand(self) -> dict:
        return {
            "v": 1, "ok": True, **self.kennung(),
            "threads": self.threads,
            "stimm_dir": self.stimm_dir,
            "samplerate": self.samplerate, "geladen": self.geladen,
            "ladezeit_ms": self.ladezeit_ms,
            "warmlauf_ms": self.warmlauf_ms, "gesprochen": self.gesprochen,
            "abgebrochen": self.abgebrochen, "pid": os.getpid(),
            # Leer heisst: die Stimme ist in Ordnung. Ein Feld, das nur bei
            # Fehlern existiert, wird beim Auswerten vergessen.
            "absage": self.absage, "absage_meldung": self.absage_meldung,
            "spricht": self._wiedergabe is not None
                       and self._wiedergabe.poll() is None,
            # T-3.16. `mimic_aus` leer heisst nutzbar; `mimic_sitzung` ist die
            # Zusage aus P2-I, dass nach jedem Ende keine Verbindung offen ist.
            "mimic_socket": self.mimic_socket,
            "mimic_aus": self.mimic_aus,
            "mimic_ab_zeichen": self.mimic_ab_zeichen,
            "mimic_nur_warm": self.mimic_nur_warm,
            "mimic_sitzung": self._mimic_sitzung is not None,
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


# -- Zwei Wege, an denen dieser Dienst zu Ende geht ------------------------
#
# Der Leerlauf-Exit bleibt weg -- der Modulkopf begruendet das mit dem
# TTFA-Kriterium, und diese Begruendung traegt weiter. Was am 05.08. gefehlt
# hat, ist etwas anderes: ein Dienst, der nichts zu tun hat, soll warten; ein
# Dienst, der nichts mehr TUN KANN, soll enden.
#
# Der Befund: 140 verwaiste Prozesse, zusammen 10 GiB RSS, aeltester 15 Stunden
# alt, alle auf `systemd --user` umgehaengt, alle horchend auf Sockets in
# laengst geloeschten Temp-Verzeichnissen. Gemessen ueber einen einzigen
# T-3.9-Lauf: vorher 0, nachher 4. Der Verifizierer startet den Dienst ueber
# `systemd-socket-activate` und kennt am Ende nur die PID des Aktivators -- das
# Enkelkind ueberlebt jeden Aufraeumversuch.
#
# Zwei Netze, weil keines allein reicht:
#
#   1. `PR_SET_PDEATHSIG` -- der Kernel beendet uns, sobald der ELTERNPROZESS
#      stirbt. Deckt den geordneten Fall ab (der Aktivator wird gekillt) und
#      kostet nichts. Deckt NICHT den Fall ab, dass der Testlaeufer selbst
#      SIGKILL bekommt: dann verwaist der Aktivator, lebt aber weiter.
#   2. Der Hub-Waechter -- ist der Hub-Socket dauerhaft verschwunden, gibt es
#      niemanden mehr, der uns etwas zu sprechen gaebe. Deckt den SIGKILL-Fall
#      ab, denn das Temp-Verzeichnis geht mit.

PR_SET_PDEATHSIG = 1
WAECHTER_INTERVALL_S = 10.0
WAECHTER_GEDULD = 3


def _prctl_pdeathsig(sig: int) -> None:
    import ctypes

    ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, sig,
                                                   0, 0, 0)


def sterbe_mit_elternteil() -> bool:
    """`True`, wenn wir weiterleben duerfen.

    Das Rennen ist der Punkt: stirbt der Elternteil zwischen `fork` und
    `prctl`, kommt das Signal nie -- und genau so entsteht ein Waisenkind, das
    niemand mehr einsammelt. Deshalb wird danach nachgesehen.
    """
    try:
        _prctl_pdeathsig(signal.SIGTERM)
    except OSError:
        return True          # kein Linux oder kein libc -- kein Grund zu enden
    return os.getppid() != 1


class HubWaechter:
    """Beendet den Dienst, wenn der Hub-Socket dauerhaft weg ist.

    `geduld` statt sofortigem Ende, weil ein Hub-Neustart den Socket kurz
    verschwinden laesst -- und ein Dienst, der daran stirbt, zahlt bei der
    naechsten Aeusserung den vollen Modell-Ladevorgang. Genau den soll der
    fehlende Leerlauf-Exit ja vermeiden.
    """

    def __init__(self, hub_socket: str, *, intervall_s: float = WAECHTER_INTERVALL_S,
                 geduld: int = WAECHTER_GEDULD, ende=None, uhr=None,
                 log: Logger | None = None) -> None:
        self.pfad = hub_socket or ""
        self.intervall_s = float(intervall_s)
        self.geduld = int(geduld)
        self._fehlt = 0
        self._ende = ende or (lambda: os.kill(os.getpid(), signal.SIGTERM))
        self._uhr = uhr or time.monotonic
        self._log = log

    def runde(self) -> bool:
        """Eine Pruefung. `False` heisst: der Dienst wurde beendet."""
        if not self.pfad:
            return True                    # kein Pfad, kein Urteil
        if os.path.exists(self.pfad):
            self._fehlt = 0
            return True
        self._fehlt += 1
        if self._fehlt < self.geduld:
            return True
        if self._log is not None:
            self._log.warn("Hub-Socket dauerhaft weg -- Dienst beendet sich",
                           DAIMON_ACTION="tts_hub_weg",
                           DAIMON_SOCKET=self.pfad)
        self._ende()
        return False

    def lauf(self) -> None:
        while self.runde():
            time.sleep(self.intervall_s)

    def starten(self) -> threading.Thread:
        t = threading.Thread(target=self.lauf, daemon=True)
        t.start()
        return t


def netze_spannen(hub_socket: str, log: Logger | None = None) -> None:
    """Beide Netze, vor dem ersten `accept()`.

    Nur im DIENSTBETRIEB. Der Handlauf (`--sag`) endet ohnehin von selbst, und
    ein `prctl` dort waere eine Wirkung ohne Anlass.
    """
    if not sterbe_mit_elternteil():
        # Der Elternteil war schon tot, bevor das Signal scharf war: dieser
        # Prozess ist bereits das Waisenkind, um das es geht.
        raise SystemExit(0)
    HubWaechter(hub_socket, log=log).starten()


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
        # expanduser: `~/...` in der Konfiguration statt eines absoluten
        # Pfades mit fremdem Benutzernamen.
        "modell_dir": os.path.expanduser(
            str(cfg.get("tts.modell_dir", MODELL_DIR))),
        "threads": int(cfg.get("tts.threads", THREADS)),
        # Leerer Eintrag heisst absichtlich "Mimic aus" -- deshalb wird der
        # Vorgabewert nur genommen, wenn der Schluessel FEHLT, nicht wenn er
        # leer ist.
        "mimic_socket": str(cfg.get("tts.mimic_socket",
                                    mimic_client.socket_vorgabe())),
        "mimic_stimme": str(cfg.get("tts.mimic_stimme", MIMIC_STIMME)),
        "mimic_ab_zeichen": int(cfg.get("tts.mimic_ab_zeichen", MIMIC_AB_ZEICHEN)),
        "mimic_nur_warm": bool(cfg.get("tts.mimic_nur_warm", MIMIC_NUR_WARM)),
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
    netze_spannen(hub_socket)
    try:
        return lauf(sprecher, srv)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
