"""T-3.15 — der Ohren-Dienst: das Gelenk zwischen Mikrofon und Antwort.

Warum es diese Datei ueberhaupt gibt
----------------------------------------------------------------------------
Der Implementierungsplan sieht sie nicht vor. `capture`, `vad`, `ring` und
`interlock` stehen seit Block 1 fertig da -- **ohne einen einzigen Aufrufer**,
weil `T-3.6` (Aufnahmepfad unter Rundenmarke) mit Plan C entfallen ist. Damit
fehlt der Kette Mikrofon -> STT -> Mind -> TTS genau ein Stueck, und T-3.15
verlangt zwei Dinge, die es voraussetzen: eine Unit, die man abschalten kann,
und zwanzig Ende-zu-Ende-Messungen.

Dieses Modul ruft nur, was schon da ist. Es enthaelt keine Erkennung, keine
Segmentierung und keine Sperre -- das steht alles in den Nachbarmodulen.

Kein Mikrofon ohne Push-to-Talk
----------------------------------------------------------------------------
Die Zusage aus Design 1.1 ist nicht "wir werfen weg, was ohne PTT hereinkommt",
sondern "es kommt nichts herein". Ohne `voice.listening` existiert deshalb
**kein `Aufnahme`-Objekt** -- ein offener Strom mit verworfenen Bloecken waere
ein Mikrofonsymbol in Plasma und damit genau die Anzeige, die dieses Projekt
nicht haben will.

Warum hier kein Ring liegt
----------------------------------------------------------------------------
`ring.Ring` haelt einen gelockten Vorlauf fuer den Fall, dass die Aufnahme erst
*nach* dem Sprechbeginn ausgeloest wird -- die Wake-Word-Lage. Bei
Push-to-Talk laeuft die Aufnahme ab dem Tastendruck, und der Puffer dieser
Runde enthaelt den Vorlauf ohnehin. Ein zweiter Mechanismus dafuer waere kein
zusaetzlicher Schutz, sondern ein zweiter Ort, an dem Mikrofonmaterial liegt.

ponytail: der Vorlauf sind hier acht Chunks (256 ms) vor dem ersten lauten
Block, aus dem Puffer geschnitten. Obergrenze: sobald es eine Ausloesung OHNE
vorherige Aufnahme gibt (Wake-Word, Dauerlauschen), gehoert `ring.Ring` zurueck
in diesen Pfad -- dann ist der gelockte, nullbare Speicher der Punkt.
"""

from __future__ import annotations

import json
import math
import os
import socket
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger
from daimon.ears import vad
from daimon.ears.interlock import Sperre

RATE = 16000
CHUNK_MS = 32.0
# Acht Chunks vor dem ersten lauten Block. Der VAD-Einsatz liegt bei 0,5 --
# der Anlaut davor ist leiser und faellt sonst weg.
VORLAUF_CHUNKS = 8
# Obergrenze fuer eine einzelne Aeusserung. 60 s bei 16 kHz mono int16 sind
# knapp 2 MB; ohne Deckel waechst der Puffer mit dem PTT-Zeitlimit.
MAX_CHUNKS = int(60_000 / CHUNK_MS)

EVENTS_SOCKET = "events.sock"
STT_SOCKET = "stt.sock"
MIND_SOCKET = "mind.sock"
SAY_SOCKET = "tts-say.sock"
# Echo-Referenz vom TTS (Vertrag: Echo-Referenz-Plan.md). SOCK_DGRAM: der
# Sender ist der Sprech-Thread des TTS und darf an nichts haengen bleiben.
ECHO_SOCKET = "echo.sock"
ECHO_MAX_DGRAM = 200_000
LATENZ_DATEI = "latenz.jsonl"

STUFEN = ("wake_to_audio", "audio_to_stt", "stt_to_mind", "mind_to_tts",
          "tts_to_audio", "gesamt")


def marke_fuer(*, listening_bei_beginn: bool) -> str:
    """Die Marke haengt am BEGINN des Segments, nicht an seinem Ende.

    Wer beim Loslassen den Satz zu Ende spricht, hat ihn unter offener Runde
    begonnen. Die Gegenrichtung waere schlimmer: ein Satz, der erst nach dem
    Loslassen anfaengt, wuerde `user_ptt` erben und damit Werkzeugrechte, die
    niemand erteilt hat.
    """
    return "user_ptt" if listening_bei_beginn else "user_audio"


def ruf_socket(pfad: str, anfrage: dict, *, timeout_s: float = 30.0) -> dict:
    """Eine Zeile JSON hin, eine zurueck. Der einzige Weg nach draussen."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(timeout_s)
            c.connect(str(pfad))
            c.sendall(json.dumps(anfrage, ensure_ascii=False).encode() + b"\n")
            with c.makefile("rb") as fh:
                zeile = fh.readline()
        return json.loads(zeile) if zeile else {"v": 1, "ok": False,
                                                "grund": "keine_antwort"}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"v": 1, "ok": False, "grund": "socket",
                "meldung": str(exc)[:200]}


class Ohren:
    """Die Schleife. Zustand vom Hub, Bloecke vom Mikrofon, Runden zum Mind."""

    def __init__(self, cfg: Config | None = None, *,
                 runtime_dir: Path | None = None, log: Logger | None = None,
                 aufnahme_fabrik: Callable[..., Any] | None = None,
                 erkenner: Any = None,
                 ruf: Callable[[str, dict], dict] = ruf_socket,
                 uhr: Callable[[], float] = time.monotonic,
                 echt: bool = True,
                 verbinde_timeout_s: float = 5.0) -> None:
        self.cfg = cfg or load_config()
        self.runtime_dir = Path(runtime_dir or self.cfg.runtime_dir)
        self.log = log or get_logger("daimon-ears")
        self._ruf = ruf
        self._uhr = uhr
        # `echt` unterscheidet einen gesprochenen Satz von einem eingespielten
        # WAV. Ohne diese Unterscheidung waeren die zwanzig Sprachanfragen aus
        # dem Plan von zwanzig abgespielten Dateien nicht zu trennen.
        self.echt = bool(echt)

        self._fabrik = aufnahme_fabrik
        self._aufnahme: Any = None
        self.sperre = Sperre()
        self._erkenner = erkenner
        schwellen = vad.einstellungen(self.cfg)
        self._hysterese = vad.Hysterese(**schwellen)

        self._puffer: list[Any] = []
        self._puffer_start = 0          # Chunk-Index des ersten Eintrags
        self._segment_listening = False
        self._listening = False
        self._tts_active = False
        self._ptt_seit: float | None = None

        self.segmente = 0
        self.runden = 0
        self.echo_pakete = 0
        self.letzte_latenz: dict | None = None
        self._echo_sock: socket.socket | None = None
        self._echo_thread: threading.Thread | None = None

        self._verbinde_timeout_s = float(verbinde_timeout_s)
        self._stop = threading.Event()
        self._leser: threading.Thread | None = None

    # -- Zustand vom Hub ---------------------------------------------------

    def zustand_uebernehmen(self, schnappschuss: dict) -> None:
        """Ein Hub-Schnappschuss. Nur die zwei Felder, die uns angehen."""
        voice = schnappschuss.get("voice") or {}
        listening = bool(voice.get("listening"))
        tts_active = bool(voice.get("tts_active"))

        if tts_active != self._tts_active:
            self._tts_active = tts_active
            if tts_active:
                self.sperre.wiedergabe_an("tts")
            else:
                self.sperre.wiedergabe_aus()
                # Eine Referenz, die die Wiedergabe ueberlebt, koennte
                # spaeter echte Sprache als Echo verwerfen.
                self.sperre.echo_referenz_leeren()
                # Zaehler, kein Audio: die einzige von aussen sichtbare
                # Spur, dass die Referenz wirklich ankommt.
                self.log.info("Echo-Referenz geleert",
                              DAIMON_ACTION="echo_zyklus",
                              DAIMON_PAKETE=self.echo_pakete)

        if listening != self._listening:
            self._listening = listening
            if listening:
                self._ptt_seit = self._uhr()
                self._aufnahme_oeffnen()
            else:
                self._aufnahme_schliessen()

    # -- Der Strom ---------------------------------------------------------

    def _aufnahme_oeffnen(self) -> None:
        if self._aufnahme is not None:
            return
        fabrik = self._fabrik
        if fabrik is None:
            from daimon.ears.capture import Aufnahme  # erst hier: braucht sounddevice
            fabrik = Aufnahme
        self._aufnahme = fabrik(senke=self.block, rate=RATE)
        self._aufnahme.start()
        self.log.info("Aufnahme offen", DAIMON_ACTION="ears_auf")

    def _aufnahme_schliessen(self) -> None:
        if self._aufnahme is None:
            return
        self._aufnahme.stop()
        self._aufnahme = None
        # Das offene Segment gilt trotzdem: das letzte Wort ist bei
        # Push-to-Talk das haeufigste (siehe `vad.Hysterese.abschluss`).
        segment = self._hysterese.abschluss()
        if segment is not None:
            self._segment_fertig(segment)
        self._puffer_leeren()
        self.log.info("Aufnahme zu", DAIMON_ACTION="ears_zu")

    def _puffer_leeren(self) -> None:
        # ZUERST den naechsten Index merken, DANN leeren. Andersherum rechnet
        # `_naechster_index()` auf der bereits leeren Liste und liefert den
        # ALTEN Startwert zurueck -- waehrend der Chunk-Zaehler der Hysterese
        # weiterlaeuft. Ab der zweiten Aeusserung zeigte der Segmentindex
        # dadurch an der Liste vorbei, `stuecke` war leer, und die Runde fiel
        # STILL aus. Am 09.08. live gefunden: die erste Runde nach dem Start
        # lief, jede weitere nicht.
        naechster = self._naechster_index()
        # Nicht nur `clear()`: die Bloecke sollen nicht als Kopie in einer
        # anderen Liste weiterleben. Der Puffer ist Mikrofonmaterial.
        for chunk in self._puffer:
            try:
                chunk[:] = 0
            except (TypeError, ValueError):
                pass
        self._puffer.clear()
        self._puffer_start = naechster

    def _naechster_index(self) -> int:
        return self._puffer_start + len(self._puffer)

    # -- Bloecke -----------------------------------------------------------

    def block(self, chunk: Any) -> None:
        """Die Senke der Aufnahme. Ein Block, 512 Samples int16, 32 ms."""
        if not self.sperre.annehmen(chunk, ptt_gedrueckt=self._listening):
            # Verworfen heisst verworfen: kein Puffer, kein VAD-Schritt. Ein
            # gepufferter Block waere spaeter nicht mehr von einem
            # angenommenen zu unterscheiden.
            return
        index = self._naechster_index()
        if self._hysterese._start is None:
            self._segment_listening = self._listening
        self._puffer.append(chunk)
        if len(self._puffer) > MAX_CHUNKS:
            self._puffer.pop(0)
            self._puffer_start += 1
        p = self._erkenner.wahrscheinlichkeit(chunk) if self._erkenner else 0.0
        segment = self._hysterese.schritt(float(p))
        if segment is not None:
            self._segment_fertig(segment, ende_index=index)

    def _segment_fertig(self, segment: tuple[int, int],
                        ende_index: int | None = None) -> None:
        start, ende = segment
        von = max(0, start - VORLAUF_CHUNKS - self._puffer_start)
        bis = min(len(self._puffer), ende - self._puffer_start + 1)
        stuecke = self._puffer[von:bis]
        if not stuecke:
            return
        self.segmente += 1
        self._runde(stuecke, listening_bei_beginn=self._segment_listening)

    # -- Eine Runde --------------------------------------------------------

    def _wav_schreiben(self, stuecke: list[Any]) -> Path:
        pfad = self.runtime_dir / f"ears-{os.getpid()}-{self.segmente}.wav"
        pfad.parent.mkdir(parents=True, exist_ok=True)
        # 0600 VOR dem Schreiben: eine Datei, die kurz lesbar ist, ist lesbar.
        fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as roh, wave.open(roh, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            for chunk in stuecke:
                w.writeframes(memoryview(chunk).cast("B"))
        return pfad

    def _runde(self, stuecke: list[Any], *, listening_bei_beginn: bool) -> None:
        t_segment = self._uhr()
        wav = self._wav_schreiben(stuecke)
        try:
            t0 = self._uhr()
            stt = self._ruf(str(self.runtime_dir / STT_SOCKET),
                            {"v": 1, "art": "transkribiere", "wav": str(wav)})
            t1 = self._uhr()
        finally:
            # Immer, auch wenn der STT gestorben ist: das Aufgenommene bleibt
            # nicht auf der Platte liegen.
            try:
                wav.unlink()
            except OSError:
                pass

        text = str(stt.get("text") or "").strip()
        if not stt.get("ok") or not text:
            self.log.info("Kein Transkript", DAIMON_ACTION="ears_leer",
                          DAIMON_GRUND=str(stt.get("grund", "leer"))[:60])
            return

        antwort = self._ruf(str(self.runtime_dir / MIND_SOCKET),
                            {"v": 1, "art": "frage", "text": text,
                             "marke": marke_fuer(
                                 listening_bei_beginn=listening_bei_beginn)})
        t2 = self._uhr()
        # Gesprochen wird, was der Mind als `antwort` mitgibt -- AUCH bei
        # ok=False: eine Absage mit kuratierter Rueckmeldung (T-4.19,
        # "Aktion ohne Absichtsmarke") ist fuer den Sprecher kein Fehler.
        # Eine Absage OHNE Rueckmeldung bleibt stumm wie bisher.
        satz = str(antwort.get("antwort") or "").strip()
        if not satz:
            self.log.info("Keine Antwort", DAIMON_ACTION="ears_stumm",
                          DAIMON_GRUND=str(antwort.get("grund", "leer"))[:60])
            self._latenz_schreiben(t_segment, t0, t1, t2, None, None)
            return

        # Der Ohren-Dienst holt KEINE Sprechfreigabe. Das tut der TTS-Dienst
        # beim Hub -- der Torwaechter bleibt an einer Stelle (Design 8.3).
        gesprochen = self._ruf(str(self.runtime_dir / SAY_SOCKET),
                               {"v": 1, "art": "sprich", "kanal": "reaktion",
                                "text": satz})
        t3 = self._uhr()
        self.runden += 1
        self._latenz_schreiben(t_segment, t0, t1, t2, t3, gesprochen)

    # -- Latenz ------------------------------------------------------------

    def _latenz_schreiben(self, t_segment: float, t0: float, t1: float,
                          t2: float, t3: float | None,
                          gesprochen: dict | None) -> None:
        ms = lambda a, b: round((b - a) * 1000.0, 3)  # noqa: E731
        ttfa = None
        if isinstance(gesprochen, dict):
            wert = gesprochen.get("ttfa_ms")
            ttfa = float(wert) if isinstance(wert, (int, float)) else None
        zeile = {
            "v": 1,
            # `wake_to_audio` heisst hier: vom ENDE der Aeusserung bis zum
            # ersten Ton. Das ist die Wartezeit, die der Nutzer erlebt. Vom
            # PTT-Druck an zu messen waere die Zeit, die er selbst geredet hat.
            "wake_to_audio_ms": ms(t_segment, t3) if t3 is not None else None,
            "audio_to_stt_ms": ms(t0, t1),
            "stt_to_mind_ms": ms(t1, t2),
            "mind_to_tts_ms": ms(t2, t3) if t3 is not None else None,
            "tts_to_audio_ms": ttfa,
            "gesamt_ms": ms(self._ptt_seit or t_segment, t3 or t2),
            "echt": self.echt,
        }
        self.letzte_latenz = zeile
        pfad = self.runtime_dir / LATENZ_DATEI
        try:
            pfad.parent.mkdir(parents=True, exist_ok=True)
            with pfad.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.log.warn("Latenzzeile nicht geschrieben",
                          DAIMON_GRUND=str(exc)[:120])

    # -- Leben --------------------------------------------------------------

    def zustand(self) -> dict:
        return {"v": 1,
                "aufnahme_offen": self._aufnahme is not None,
                "listening": self._listening,
                "segmente": self.segmente,
                "runden": self.runden,
                "verworfen_sperre": self.sperre.verworfen_sperre,
                "echo_pakete": self.echo_pakete,
                "letzte_latenz_ms": self.letzte_latenz}

    def start(self) -> None:
        """Haengt am Push-Endpunkt des Hubs. Kein eigener Steuer-Socket --
        wer die Ohren abschalten will, stoppt die Unit (T-3.15)."""
        if self._erkenner is None:
            self._erkenner = vad.Erkenner()
        self._leser = threading.Thread(target=self._horchen, daemon=True)
        self._leser.start()
        self._echo_oeffnen()

    # -- Echo-Referenz (Vertrag: Echo-Referenz-Plan.md) --------------------

    def _echo_oeffnen(self) -> None:
        pfad = self.runtime_dir / ECHO_SOCKET
        try:
            pfad.unlink()          # RuntimeDirectoryPreserve laesst sie liegen
        except OSError:
            pass
        self._echo_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._echo_sock.bind(str(pfad))
        self._echo_sock.settimeout(1.0)
        self._echo_thread = threading.Thread(target=self._echo_lauf,
                                             daemon=True)
        self._echo_thread.start()

    def _echo_lauf(self) -> None:
        while not self._stop.is_set():
            try:
                daten = self._echo_sock.recv(ECHO_MAX_DGRAM)
            except socket.timeout:
                continue
            except OSError:
                return
            self._echo_verarbeiten(daten)

    def _echo_verarbeiten(self, daten: bytes) -> None:
        """Ein Datagramm. Gueltig -> in die Sperre; alles andere wird
        verworfen. Kein Audio in Logzeilen (Auflage aus dem Interlock)."""
        import base64

        try:
            paket = json.loads(daten)
            if (not isinstance(paket, dict) or paket.get("art") != "echo"
                    or paket.get("rate") != 16000):
                return
            pcm = base64.b64decode(str(paket.get("pcm") or ""), validate=True)
        except (json.JSONDecodeError, ValueError, TypeError):
            return
        if not pcm:
            return
        self.sperre.echo_referenz(pcm)
        self.echo_pakete += 1

    def _horchen(self) -> None:
        pfad = str(self.runtime_dir / EVENTS_SOCKET)
        while not self._stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                    c.settimeout(self._verbinde_timeout_s)
                    c.connect(pfad)
                    # Das Timeout gilt fuer das VERBINDEN, nicht fuers Lesen.
                    #
                    # Mit einem Lese-Timeout riss die Verbindung nach jeder
                    # Stille ab -- und weil der Wiederaufbau als "kein Hub"
                    # gilt, ging bei jedem Zyklus das Mikrofon zu und wieder
                    # auf. Am 09.08. live gemessen: "Aufnahme offen"/"Aufnahme
                    # zu" im Fuenf-Sekunden-Takt, mitten in einer Aeusserung.
                    #
                    # Der Hub schickt nur bei Aenderung. Stille ist hier der
                    # Normalfall, kein Fehler -- ein abgerissener Socket faellt
                    # als EOF auf, und darauf reagiert die Schleife ohnehin.
                    c.settimeout(None)
                    with c.makefile("rb") as fh:
                        for zeile in fh:
                            if self._stop.is_set():
                                return
                            try:
                                self.zustand_uebernehmen(json.loads(zeile))
                            except (json.JSONDecodeError, ValueError):
                                continue
            except OSError:
                # Kein Hub heisst: nicht zuhoeren. Fail-safe ist hier zu.
                self._aufnahme_schliessen()
                self._listening = False
            self._stop.wait(1.0)

    def stop(self) -> None:
        self._stop.set()
        if self._echo_sock is not None:
            try:
                self._echo_sock.close()
            except OSError:
                pass
        self._aufnahme_schliessen()


def _perzentil(werte: list[float], anteil: float) -> float | None:
    """Naechstliegender Rang, nicht interpoliert. Bei n=20 ist p95 der
    zweitgroesste Wert -- eine interpolierte Zahl zwischen zwei Messungen ist
    keine gemessene Latenz."""
    if not werte:
        return None
    sortiert = sorted(werte)
    i = min(len(sortiert) - 1, max(0, math.ceil(anteil * len(sortiert)) - 1))
    return sortiert[i]


def bericht(quelle: Path | str, *, jetzt: str = "") -> dict:
    """`latenz.jsonl` zu `phase3-latency.json`.

    **Nur `echt: true` zaehlt in `n`.** Ein eingespieltes WAV misst dieselbe
    Kette, aber nicht dieselbe Zusage: der Plan verlangt zwanzig gesprochene
    Anfragen. Synthetische Laeufe bleiben trotzdem in `laeufe` stehen -- sie
    sind Diagnose, nur eben kein Beleg.

    Eine fehlende Quelle ergibt `n: 0` statt eines Fehlers. Eine Datei, die
    ehrlich Null sagt, ist besser als keine: sie ist rot und sichtbar, statt
    rot und vergessen.
    """
    pfad = Path(quelle)
    laeufe: list[dict] = []
    if pfad.exists():
        for zeile in pfad.read_text(encoding="utf-8").splitlines():
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                laeufe.append(json.loads(zeile))
            except (json.JSONDecodeError, ValueError):
                continue

    echte = [l for l in laeufe if l.get("echt") is True]
    def werte(feld: str) -> list[float]:
        return [float(l[feld]) for l in echte
                if isinstance(l.get(feld), (int, float))]

    stufen = {}
    for stufe in STUFEN:
        w = werte(f"{stufe}_ms")
        stufen[stufe] = {"n": len(w), "p50": _perzentil(w, 0.50),
                         "p95": _perzentil(w, 0.95)}
    haupt = werte("wake_to_audio_ms")
    return {"v": 1, "n": len(echte), "erzeugt": jetzt,
            "p50_wake_to_audio_ms": _perzentil(haupt, 0.50),
            "p95_wake_to_audio_ms": _perzentil(haupt, 0.95),
            "stufen": stufen, "laeufe": laeufe}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="T-3.15: der Ohren-Dienst")
    ap.add_argument("--bericht", default="",
                    help="latenz.jsonl auswerten und als JSON ausgeben")
    ap.add_argument("--runtime-dir", default="")
    args = ap.parse_args(argv)
    if args.bericht:
        print(json.dumps(bericht(args.bericht), ensure_ascii=False, indent=1))
        return 0
    # make_dirs=False, wie STT, TTS und Mind: `load()` legt sonst
    # $XDG_STATE_HOME/daimon an und setzt dessen Modus -- unter
    # `ProtectHome=read-only` ist das ein OSError beim Start. Dieser Dienst
    # schreibt ohnehin nur unter $XDG_RUNTIME_DIR, das systemd als
    # RuntimeDirectory selbst anlegt. Am 09.08. beim ersten echten Start
    # gemessen: drei Neustarts, jedes Mal `Errno 30` auf `st.chmod(0o700)`.
    cfg = load_config(make_dirs=False)
    ohren = Ohren(cfg, runtime_dir=Path(args.runtime_dir) if args.runtime_dir
                  else None)
    ohren.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        ohren.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
