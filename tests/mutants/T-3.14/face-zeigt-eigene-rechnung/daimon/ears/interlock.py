"""T-3.4 — Rueckkopplungssperre. Die eigene Stimme kann sich nicht selbst reaktivieren.

Unter Plan C (T−1.1) gibt es kein Wake-Word mehr, und **damit ist dieser Task
gefaehrlicher geworden, nicht harmloser.** Beim Wake-Word haette sich das Pet
selbst wecken muessen -- ein auffaelliger Fehler, den jeder sofort sieht. Unter
Push-to-Talk haelt der Nutzer die Taste, das Pet antwortet, das Mikrofon hoert
die Antwort, und sie landet als **Nutzeraeusserung** im STT. Das Pet redet mit
sich selbst, und die Spur davon sieht aus wie eine Eingabe des Nutzers. Genau
deshalb ist Kriterium 2 ("die Sperre gilt auch bei gedruecktem PTT") hier der
Kern und keine Randbedingung: die gedrueckte Taste ist nicht die Erlaubnis,
irgendetwas durchzulassen.

Fail-closed, und das ist eine Entscheidung
----------------------------------------------------------------------------
Ist der Zustand unklar, **sperrt** die Sperre. Konkret gesperrt wird bei:

  * angemeldeter Wiedergabe ohne gemeldetes Ende -- es gibt **keinen** Zeitablauf,
    der eine haengengebliebene Anmeldung von selbst aufhebt. Ein solcher Ablauf
    waere genau die Tuer, durch die die Rueckkopplung kommt: er wirkt am
    zuverlaessigsten dann, wenn die Wiedergabe wirklich noch laeuft und nur
    niemand abgemeldet hat,
  * `wiedergabe_aus()` ohne vorheriges `wiedergabe_an()` -- der Nachlauf gilt
    trotzdem; wer abmeldet, hat gespielt,
  * einer Uhr, die wirft oder rueckwaerts springt (siehe unten),
  * einem Chunk, der sich nicht mit der Echo-Referenz vergleichen laesst
    (falsche Groesse, stumm, numpy fehlt), waehrend eine Referenz vorliegt.

Die Kosten der einen Richtung sind eine verlorene Aeusserung, die der Nutzer
wiederholt. Die Kosten der anderen sind eine Schleife, in der das Pet sich
selbst antwortet. Deshalb gibt `annehmen()` im Zweifel `False` zurueck und
**wirft nicht** -- anders als `vad.py` und `ring.py`, die bei falscher
Chunkgroesse eine Ausnahme werfen. Der Unterschied ist Absicht: die beiden sind
Verarbeiter, dieses Modul ist ein Tor. Eine Ausnahme aus einem Tor landet beim
naechsten `except` des Aufrufers, und ein `except`, das den Fehler verschluckt
und weitermacht, **oeffnet** das Tor. Ein `False` kann man nicht verschlucken.

Threadsicherheit -- der offene Punkt aus T-3.3, hier entschieden
----------------------------------------------------------------------------
`ring.py` sagt im Modulkopf ausdruecklich "nicht threadsicher". Die Sperre wird
aus mindestens zwei Richtungen beruehrt: dem PortAudio-Callback (eigener Thread,
siehe capture.py) und dem, was die Wiedergabe an- und abmeldet (Hauptschleife
oder spaeter T-3.9). Die Pfade **ueberschneiden sich**, eine Zusicherung des
Gegenteils waere unwahr.

**Entscheidung: ein `threading.RLock` in `Sperre`, und der Ringzugriff laeuft
mit darunter.** Wer einen Ring uebergibt, benutzt ihn ab dann ueber
`Sperre.schreibe()` / `.vorlauf()` / `.verwirf()` statt direkt -- damit gibt es
genau ein Schloss fuer beides und keine zweite Rangordnung, in der man sich
verklemmen kann. `Ring.schreibe()` ist `self._mm[pos:pos+n] = roh; self._n += 1`;
das sind zwei Bytecode-Schritte, und der GIL garantiert zwischen ihnen gar
nichts -- ein `verwirf()` dazwischen setzt `_n = 0` und der naechste Schreiber
ueberschreibt Chunk 0, waehrend ein Leser noch glaubt, dort stehe der Vorlauf.
`RLock` statt `Lock`, weil `annehmen()` intern `_gesperrt()` ruft und
verschachtelte Aufrufe sonst der naechste Umbau selbst herbeifuehrt.

Das Schloss wird **nie ueber einem Modellaufruf oder einer Ein-/Ausgabe
gehalten** -- was darunter laeuft, sind Vergleiche und ein `memcpy`. Der
Audio-Callback darf nicht blockieren.

Monotone Zeit
----------------------------------------------------------------------------
`time.monotonic()`, nie Wanduhr: eine NTP-Korrektur oder eine Zeitumstellung
darf keine Sperre aufheben. Die Uhr ist **einspeisbar** (`uhr=`), damit der
Nachlauf ohne echtes Warten messbar ist -- und weil eine Sperre, die man nur
durch Schlafen pruefen kann, in der Praxis nicht geprueft wird. Ein
Rueckwaertssprung der eingespeisten Uhr hebt nichts auf: `bis` ist ein absoluter
Zeitpunkt, ein Rueckwaertssprung verlaengert also. Springt sie um mehr als
`_UHR_SPRUNG_S` rueckwaerts, wird das zusaetzlich gemeldet und gesperrt --
danach ist die Uhr keine Grundlage mehr, auf der man entsperren sollte.

Echo-Referenz -- und wo ihre Grenze liegt
----------------------------------------------------------------------------
T-3.9 (TTS) existiert nicht, `piper` ist nicht installiert. Die Referenz wird
deshalb **eingespeist**: `echo_referenz(chunk)` nimmt genau die Bytes, die an
die Ausgabe gehen (16 kHz, mono, int16 -- das Umrechnen ist Sache dessen, der
sie einspeist, und diese Signatur muss T-3.9 nicht mehr aendern). Verglichen
wird normierte Kreuzkorrelation ueber ein gleitendes Fenster; ein Treffer wird
verworfen.

**Was das belegt und was nicht:** normierte Korrelation ist unempfindlich gegen
Daempfung -- eine leiser gewordene Kopie korreliert weiter mit 1,0. Sie ist
empfindlich gegen alles andere: Raumhall, Nachhall, die Frequenzgaenge von
Lautsprecher und Mikrofon, Resampling im PipeWire-Graph. Dieser Vergleich faengt
also den **digitalen** Weg (Monitor-Quelle, derselbe Puffer zweimal) sehr
zuverlaessig und den **akustischen** nur, solange der Raum wenig tut. Der
akustische Weg braucht einen echten AEC und gehoert nach T-3.15; das hier ist
die Stolperdraht-Lage davor, und die eigentliche Absicherung des akustischen
Wegs sind Kriterium 1 und 2 -- waehrend der Wiedergabe geht ohnehin nichts
durch, egal wie es klingt.

Ungemessen bleibt die **Fehlalarmrate an echter Sprache**: belegt ist, dass
fremdes Rauschen gegen eine Rausch-Referenz durchgeht -- nicht, wie oft ein
fremder Satz gegen eine gesprochene Referenz zufaellig ueber 0,7 kommt. Dafuer
braucht es TTS-Material, also T-3.9. Gemessen ist dagegen der Preis im
Audio-Callback: 0,54 ms je Chunk bei 1 s Fenster und 0,86 ms bei 2 s (1,7 %
bzw. 2,7 % des 32-ms-Budgets), gesperrt ohne Vergleich 0,0002 ms.

Kein Audio in irgendeinem Logaufruf dieses Moduls. Zaehler und Zeiten, sonst
nichts.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

_LOG = logging.getLogger("daimon.ears.interlock")

# Dieselben Zahlen wie capture.py (T-3.1), vad.py (T-3.2), ring.py (T-3.3).
RATE = 16000
CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2

# Kriterium 1. Der Nachlauf ist kein Komfort: der Raumhall der letzten Silbe
# erreicht das Mikrofon, nachdem die Wiedergabe zu Ende ist.
NACHLAUF_S = 0.5

# Referenzfenster und Trefferschwelle. 1,0 s deckt jede Latenz ab, die zwischen
# "an die Ausgabe gegeben" und "am Mikrofon" plausibel ist; laenger kostet nur
# Rechenzeit im Audio-Callback. 0,7 ist der Wert, unter dem eine Kopie durch
# Raum und Wandler noch erkennbar bleibt, ohne dass fremde Sprache zufaellig
# trifft -- geeicht ist er nicht, siehe Modulkopf.
ECHO_FENSTER_S = 1.0
ECHO_SCHWELLE = 0.7

# Ab diesem Rueckwaertssprung gilt die Uhr als unbrauchbar.
_UHR_SPRUNG_S = 0.001


class Sperre:
    """Das Tor zwischen Mikrofon und STT. Im Zweifel zu.

    Threadsicher (RLock); wer einen `ring` uebergibt, greift ab dann ueber
    diese Klasse darauf zu -- siehe Modulkopf.
    """

    def __init__(
        self,
        *,
        nachlauf_s: float = NACHLAUF_S,
        echo_fenster_s: float = ECHO_FENSTER_S,
        echo_schwelle: float = ECHO_SCHWELLE,
        uhr: Callable[[], float] = time.monotonic,
        ring: Any = None,
    ) -> None:
        self.nachlauf_s = float(nachlauf_s)
        self.echo_schwelle = float(echo_schwelle)
        self._ref_max = max(CHUNK_BYTES, int(float(echo_fenster_s) * RATE) * 2)
        self._uhr = uhr
        self._ring = ring
        self._lock = threading.RLock()

        self._wiedergabe = False
        self._bis: float | None = None      # monotoner Zeitpunkt, nie Wanduhr
        self._grund: str | None = None
        self._letzte_zeit: float | None = None
        self._uhr_kaputt = False
        self._ref = bytearray()

        self.angenommen = 0
        self.verworfen_sperre = 0
        self.verworfen_echo = 0
        self.verworfen_unklar = 0
        self.ptt_waehrend_sperre = 0

    # -- Uhr ---------------------------------------------------------------

    def _jetzt(self) -> float | None:
        """Monotone Zeit, oder None -- und None heisst gesperrt."""
        try:
            t = float(self._uhr())
        except Exception:  # noqa: BLE001 - eine kaputte Uhr sperrt, sie wirft nicht
            self._uhr_kaputt = True
            _LOG.warning("ears.interlock: Uhr hat geworfen -- gesperrt.")
            return None
        if self._letzte_zeit is not None and t < self._letzte_zeit - _UHR_SPRUNG_S:
            # Mit time.monotonic() unerreichbar; mit eingespeister Uhr nicht.
            self._uhr_kaputt = True
            _LOG.warning(
                "ears.interlock: Uhr sprang um %.3f s rueckwaerts -- gesperrt.",
                self._letzte_zeit - t,
            )
            return None
        self._letzte_zeit = t
        return t

    # -- Wiedergabe an- und abmelden ---------------------------------------

    def wiedergabe_an(self, grund: str = "wiedergabe") -> None:
        """Ab jetzt spricht das Pet. Kein Zeitablauf hebt das auf."""
        with self._lock:
            self._jetzt()
            self._wiedergabe = True
            self._grund = grund

    def wiedergabe_aus(self) -> None:
        """Die Wiedergabe ist zu Ende -- der Nachlauf beginnt.

        Gilt auch ohne vorheriges `wiedergabe_an()`: wer abmeldet, hat gespielt,
        und der Hall danach ist derselbe.
        """
        with self._lock:
            t = self._jetzt()
            self._wiedergabe = False
            if t is None:
                return
            ende = t + self.nachlauf_s
            # Nie verkuerzen: zwei Wiedergaben dicht hintereinander duerfen den
            # Nachlauf der ersten nicht abschneiden.
            self._bis = ende if self._bis is None else max(self._bis, ende)
            self._grund = "nachlauf"

    # -- Echo-Referenz (einspeisbar; T-3.9 aendert diese Signatur nicht) ----

    def echo_referenz(self, chunk: Any) -> None:
        """Was an die Ausgabe geht: 16 kHz, mono, int16. Beliebige Laenge."""
        roh = memoryview(chunk).cast("B")
        with self._lock:
            self._ref += roh
            ueber = len(self._ref) - self._ref_max
            if ueber > 0:
                # Auf gerade Grenze schneiden, sonst verschiebt sich die
                # int16-Ausrichtung und die Korrelation misst Unsinn.
                del self._ref[: ueber + (ueber & 1)]

    def echo_referenz_leeren(self) -> None:
        with self._lock:
            self._ref.clear()

    # -- das Tor -----------------------------------------------------------

    def annehmen(self, chunk: Any = None, *, ptt_gedrueckt: bool = False) -> bool:
        """Darf dieser Chunk zum STT? Im Zweifel nein.

        `ptt_gedrueckt` geht **nicht** in die Entscheidung ein -- der Parameter
        existiert, damit Kriterium 2 an dieser Signatur pruefbar ist und damit
        der Zaehler `ptt_waehrend_sperre` zeigt, dass der Fall wirklich eintritt.
        Wer hier je ein `or ptt_gedrueckt` einbaut, hat den Task entfernt.
        """
        with self._lock:
            gesperrt, grund = self._gesperrt()
            if gesperrt:
                self.verworfen_sperre += 1
                if ptt_gedrueckt:
                    self.ptt_waehrend_sperre += 1
                return False
            if not self._ref:
                self.angenommen += 1
                return True
            echo = self._ist_echo(chunk)
            if echo is None:
                self.verworfen_unklar += 1
                return False
            if echo:
                self.verworfen_echo += 1
                return False
            self.angenommen += 1
            return True

    def _gesperrt(self) -> tuple[bool, str | None]:
        if self._wiedergabe:
            return True, self._grund or "wiedergabe"
        t = self._jetzt()
        if t is None or self._uhr_kaputt:
            return True, "uhr"
        if self._bis is not None:
            if t < self._bis:
                return True, self._grund or "nachlauf"
            self._bis = None
            self._grund = None
        return False, None

    def _ist_echo(self, chunk: Any) -> bool | None:
        """True/False, oder None fuer "nicht vergleichbar" (= gesperrt).

        Normierte gleitende Kreuzkorrelation ueber das Referenzfenster. numpy
        ist ueber `sounddevice` ohnehin da; fehlt es, ist das Ergebnis keine
        Aussage und damit None -- nicht "kein Echo".

        ponytail: eine Zeile numpy statt eines AEC. Obergrenze: sobald der
        akustische Weg wirklich gemessen wird (T-3.15) oder das Fenster ueber
        ~1 s waechst, gehoert hier ein richtiger Echoausloescher hin
        (`speexdsp`/WebRTC-AEC) -- eine Korrelation daempft nichts, sie sagt nur
        ja oder nein.
        """
        if chunk is None:
            return None
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - numpy haengt an sounddevice
            _LOG.warning("ears.interlock: numpy fehlt -- Echo ungeprueft, gesperrt.")
            return None
        roh = memoryview(chunk).cast("B").tobytes()
        if len(roh) < 2 or len(roh) & 1:
            return None
        x = np.frombuffer(roh, dtype="<i2").astype(np.float64)
        ref = np.frombuffer(bytes(self._ref), dtype="<i2").astype(np.float64)
        n = x.size
        if ref.size < n:
            return None
        xn = float(np.sqrt(x @ x))
        if xn == 0.0:
            # Stiller Chunk: nicht vergleichbar. Ihn wegzuwerfen kostet nichts,
            # ihn durchzulassen waere eine Aussage, die die Zahlen nicht hergeben.
            return None
        k = np.correlate(ref, x, mode="valid")
        e = np.concatenate(([0.0], np.cumsum(ref * ref)))
        fenster = e[n:] - e[:-n]
        gut = fenster > 0.0
        if not bool(gut.any()):
            return None
        r = np.abs(k[gut]) / (np.sqrt(fenster[gut]) * xn)
        return bool(float(r.max()) >= self.echo_schwelle)

    # -- Ringzugriff unter demselben Schloss (siehe Modulkopf) --------------

    def schreibe(self, chunk: Any) -> None:
        with self._lock:
            self._ring.schreibe(chunk)

    def vorlauf(self, sekunden: float | None = None) -> list:
        with self._lock:
            return self._ring.vorlauf(sekunden)

    def verwirf(self) -> None:
        with self._lock:
            self._ring.verwirf()

    # -- Diagnose (Kriterium 4) --------------------------------------------

    def zustand(self) -> dict:
        with self._lock:
            gesperrt, grund = self._gesperrt()
            t = self._letzte_zeit
            rest = 0.0
            if self._bis is not None and t is not None:
                rest = max(0.0, self._bis - t)
            return {
                "v": 1,
                "gesperrt": gesperrt,
                "grund": grund,
                "bis": self._bis,          # monotone Zeit, nie Wanduhr
                "restsekunden": round(rest, 3),
                "wiedergabe": self._wiedergabe,
                "nachlauf_s": self.nachlauf_s,
                "uhr_kaputt": self._uhr_kaputt,
                "echo_schwelle": self.echo_schwelle,
                "referenz_bytes": len(self._ref),
                "angenommen": self.angenommen,
                "verworfen_sperre": self.verworfen_sperre,
                "verworfen_echo": self.verworfen_echo,
                "verworfen_unklar": self.verworfen_unklar,
                "ptt_waehrend_sperre": self.ptt_waehrend_sperre,
            }


def einstellungen(cfg: Any) -> dict[str, float]:
    """`[ears.interlock]` aus einer `daimon.common.config.Config` holen."""
    return {
        "nachlauf_s": float(cfg.get("ears.interlock.nachlauf_s", NACHLAUF_S)),
        "echo_fenster_s": float(cfg.get("ears.interlock.echo_fenster_s", ECHO_FENSTER_S)),
        "echo_schwelle": float(cfg.get("ears.interlock.echo_schwelle", ECHO_SCHWELLE)),
    }


def main(argv: list[str] | None = None) -> int:
    """Selbsttest: der Sperrzustand ueber einen Unix-Socket, von aussen lesbar.

    Kriterium 4 verlangt den Zustand im Diagnose-Socket. Den Ohren-Dienst gibt
    es erst spaeter (T-3.7); bis dahin ist das hier der Endpunkt, an dem
    `gesperrt`/`grund`/`bis` von aussen nachweisbar sind -- eine Zeile JSON je
    Verbindung, dasselbe Format wie `daimon/auth/agent.py`.

    ponytail: `select`-Schleife, kein Dienstgeruest, keine Steuerung. Obergrenze:
    sobald der Ohren-Dienst existiert, wandert `zustand()` in dessen Diagnose und
    dieses `main()` faellt weg.
    """
    import argparse
    import json
    import os
    import select
    import socket

    ap = argparse.ArgumentParser(description="T-3.4 Selbsttest: Sperre am Socket")
    ap.add_argument("--diag-socket", required=True)
    ap.add_argument("--sekunden", type=float, default=3.0)
    ap.add_argument("--wiedergabe-s", type=float, default=1.0,
                    help="so lange ist die Wiedergabe angemeldet")
    ap.add_argument("--nachlauf-s", type=float, default=NACHLAUF_S)
    args = ap.parse_args(argv)

    s = Sperre(nachlauf_s=args.nachlauf_s)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if os.path.exists(args.diag_socket):
        os.unlink(args.diag_socket)
    srv.bind(args.diag_socket)
    os.chmod(args.diag_socket, 0o600)  # nach dem Binden: bind() beachtet die umask
    srv.listen(8)

    t0 = time.monotonic()
    s.wiedergabe_an()
    ab = t0 + args.wiedergabe_s
    ende = t0 + min(args.sekunden, 30.0)
    try:
        while True:
            jetzt = time.monotonic()
            if jetzt >= ende:
                break
            if ab is not None and jetzt >= ab:
                s.wiedergabe_aus()
                ab = None
            bereit, _, _ = select.select([srv], [], [], 0.01)
            if bereit:
                conn, _ = srv.accept()
                with conn:
                    conn.sendall(json.dumps(s.zustand()).encode() + b"\n")
    finally:
        srv.close()
        os.unlink(args.diag_socket)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
