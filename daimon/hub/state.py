"""T-0.9 — Zustand ueber alle Claude-Code-Sitzungen hinweg.

Der Unterschied zum Bestandsdaemon steckt in den Leases. Der alte Zustand
vergass eine Sitzung nach einer Stunde TTL. Das reicht nicht: wird eine Sitzung
per Strg-C beendet, waehrend sie auf eine Freigabe wartet, bleibt das Pet eine
Stunde lang auf `needs_input` -- also genau in dem Zustand, der Aufmerksamkeit
verlangt, fuer etwas das es nicht mehr gibt. Nach zwei solchen Faellen schaut
niemand mehr hin, und damit ist die eine Funktion kaputt, um die es geht.

Ein Lease bindet die Sitzung deshalb an einen **lebenden Prozess**:

  * Der Hook meldet seine PID.
  * Zusaetzlich merken wir uns die Startzeit dieses Prozesses aus
    `/proc/<pid>/stat`, Feld 22. Das ist die Nonce gegen PID-Wiederverwendung:
    eine neu vergebene PID hat eine andere Startzeit, und damit faellt das
    Lease auch dann, wenn zufaellig wieder dieselbe Zahl vergeben wurde.
  * Ist der Prozess weg oder die Startzeit eine andere, verfaellt das Lease
    binnen Sekunden statt binnen einer Stunde.

Die TTL bleibt als zweite Reihe: fuer Faelle, in denen gar keine PID mitkam.

`rev` steigt nur bei echten Aenderungen. Ein Poller, der alle 250 ms fragt,
soll an `rev` erkennen koennen, ob sich etwas getan hat -- eine bei jedem
Ereignis hochgezaehlte Zahl waere dafuer wertlos.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Mood-Prioritaet. needs_input schlaegt alles -- das ist die Funktion, um die
# es geht: am Rand des Blickfelds sehen, dass ein Agent wartet.
PRIORITY: dict[str, int] = {
    "sleeping": 0,
    "idle": 1,
    "observing": 2,
    "thinking": 3,
    "working": 4,
    "done": 5,
    "failed": 6,
    "needs_input": 7,
}

SESSION_TTL_S = 3600.0
LEASE_GRACE_S = 2.0      # so lange nach dem letzten Ereignis nicht nachsehen

# T-3.14, die drei Obergrenzen des Sprachzustands. Alle sind Ausfallgrenzen,
# keine Fachlogik: sie greifen nur, wenn die zustaendige Quelle das Ende einer
# Phase NICHT meldet.
#
#   * DENK_FRIST_S -- der Mind ist gestorben, waehrend er dachte. Ohne die
#     Frist stuende `processing` fuer immer, und ein haengender Zustand ist von
#     einem arbeitenden nicht zu unterscheiden.
#   * PTT_FRIST_S -- der Auth-Agent hat das Ablaufen seines eigenen Zeitlimits
#     nicht gemeldet. 150 > 120 (Vorgabe des `PTTAutomat`), damit im Normalfall
#     immer die Quelle zuerst spricht und diese Grenze nur im Ausfall greift.
#   * SPRECH_FRIST_S -- ein Sprecher hat `beginnt` gemeldet und `gesprochen`
#     nie. Am 09.08. live passiert: der TTS-Dienst war inaktiv, der Hub stand
#     seit Minuten auf `tts_active: true`, und die Rueckkopplungssperre des
#     Ohren-Dienstes verwarf daraufhin JEDEN Mikrofonblock. Die Ohren waren
#     dauerhaft taub, und nichts im System hat es gemeldet. Der Wert deckt die
#     Sprechfreigabe des Hubs ab (TTS_FRIST_S, 30 s): danach ist die Freigabe
#     ohnehin verfallen, und "spricht noch" ist eine Behauptung ohne Grundlage.
DENK_FRIST_S = 30.0
PTT_FRIST_S = 150.0
SPRECH_FRIST_S = 30.0


def proc_starttime(pid: int) -> int | None:
    """Startzeit aus /proc/<pid>/stat, Feld 22. None, wenn es den Prozess
    nicht gibt.

    Der Kommentarname des Prozesses (Feld 2) darf Leerzeichen und Klammern
    enthalten -- deshalb wird ab der letzten schliessenden Klammer geteilt und
    nicht am ersten Leerzeichen. Ein Prozess namens ")  (" waere sonst eine
    Luecke.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            roh = fh.read()
    except OSError:
        return None
    schluss = roh.rfind(b")")
    if schluss < 0:
        return None
    felder = roh[schluss + 2:].split()
    # Feld 22 insgesamt = Index 19 nach dem Namen (Felder 1 und 2 sind weg).
    if len(felder) < 20:
        return None
    try:
        return int(felder[19])
    except ValueError:
        return None


@dataclass
class Session:
    session_id: str
    mood: str = "observing"
    cwd: str = ""
    project: str = ""
    pid: int | None = None
    starttime: int | None = None
    nonce: str = ""
    ts: float = field(default_factory=time.time)

    def lebt(self, jetzt: float) -> bool:
        """Lease gueltig?

        Ohne PID bleibt nur die TTL. Mit PID wird nachgesehen -- aber erst nach
        einer kurzen Schonfrist, damit nicht bei jedem Ereignis /proc gelesen
        wird.
        """
        if jetzt - self.ts > SESSION_TTL_S:
            return False
        if self.pid is None:
            return True
        if jetzt - self.ts < LEASE_GRACE_S:
            return True
        jetzige = proc_starttime(self.pid)
        if jetzige is None:
            return False
        # Andere Startzeit heisst: die PID wurde neu vergeben, der Prozess von
        # damals ist weg.
        return self.starttime is None or jetzige == self.starttime


class HubState:
    """Zustand ueber alle Sitzungen. Threadsicher, ohne globalen Zustand."""

    def __init__(self, *, ttl_s: float = SESSION_TTL_S) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._bubble: dict[str, Any] | None = None
        self._rev = 0
        self._ttl = ttl_s
        self._voice = {"listening": False, "tts_active": False, "denkt": False}
        # Monotone Zeitmarken der beiden Fristen. `None` heisst: laeuft nicht.
        self._denkt_seit: float | None = None
        self._listening_seit: float | None = None
        self._sprechen_seit: float | None = None
        self._perception = {"ears": False, "eyes": False, "gpu_loaded": []}
        # T-7.3: `None` heisst "kein Mitschnitt moeglich" und ergibt False.
        # Gesetzt wird er vom Hub-Dienst, der den Herzschlag des Recorders
        # liest -- siehe `daimon/recorder/pause.py`.
        self._mitschnitt: Callable[[], bool] | None = None

    def set_mitschnitt_quelle(self, quelle: "Callable[[], bool] | None") -> None:
        self._mitschnitt = quelle

    # -- Schreiben ---------------------------------------------------------

    def apply(self, mood: str | None, *, session_id: str,
              bubble: dict | None = None, cwd: str = "",
              project: str = "", pid: int | None = None,
              nonce: str = "") -> bool:
        """Ereignis einarbeiten. Gibt zurueck, ob sich etwas geaendert hat.

        `mood is None` heisst: unbekanntes Ereignis. Dann passiert nichts --
        auch `rev` bleibt stehen. Das ist in T-0.3.t festgeschrieben.
        """
        if mood is None:
            return False
        with self._lock:
            alt = self._sessions.get(session_id)
            starttime = proc_starttime(pid) if pid else None
            neu = Session(session_id=session_id, mood=mood, cwd=cwd,
                          project=project or (alt.project if alt else ""),
                          pid=pid, starttime=starttime, nonce=nonce)
            geaendert = (
                alt is None
                or alt.mood != neu.mood
                or alt.project != neu.project
                or bubble is not None
            )
            self._sessions[session_id] = neu
            if bubble is not None:
                self._bubble = bubble
            if mood == "sleeping":
                self._sessions.pop(session_id, None)
            if geaendert:
                self._rev += 1
            return geaendert

    def warnblase(self, titel: str, text: str) -> None:
        """Eine dringende Blase OHNE Sitzung. T-4.6 K5.

        `apply()` ist der Weg fuer Blasen, die zu einem Ereignis gehoeren --
        es verlangt eine `session_id` und eine Stimmung. Der Hub hat beim
        Start weder das eine noch das andere, und genau dort entsteht die
        Meldung, um die es hier geht: die Audit-Kette wird beim Start
        geprueft.

        BEFUND T-4.6 K5 (Reviewer-Sitzung 19.08.): eine gerissene Kette
        meldete sich nur im Journal. Design 7.5 verlangt eine "Bubble mit
        hoher Dringlichkeit", und es gab auf diesem Weg keinen Aufrufer --
        das Muster aus CLAUDE.md, diesmal an der Stelle, an der jemand
        MERKEN soll, dass die Vergangenheit verfaelscht wurde. Eine Warnung
        im Journal liest niemand, der nicht ohnehin schon sucht.

        `urgent` ist fest und kein Parameter: diese Methode existiert fuer
        genau die Faelle, die der Nutzer sehen MUSS. Wer eine beilaeufige
        Blase will, hat ein Ereignis und nimmt `apply()`.
        """
        with self._lock:
            self._bubble = {"title": titel, "body": text, "urgent": True}
            self._rev += 1

    def clear_bubble(self) -> None:
        with self._lock:
            if self._bubble is not None:
                self._bubble = None
                self._rev += 1

    def set_voice(self, *, jetzt: float | None = None, **felder: Any) -> None:
        """`voice.tts_active` und Verwandte. T-3.9, erweitert in T-3.14.

        Das Feld gab es seit Beginn im Schnappschuss und **niemand konnte es
        setzen** -- es stand also seit Wochen als `false` da und behauptete
        damit eine Aussage ("es spricht gerade nichts"), die gar nicht gemessen
        wurde. Hier kommt der Setter dazu.

        Die Rueckkopplungssperre (`daimon/ears/interlock.py`) ist eine
        In-Prozess-Klasse, und es gibt heute keinen Prozess, der eine Instanz
        haelt. Bis der Ohren-Dienst existiert, ist dieses Feld der einzige
        prozessuebergreifende Weg, "ich spreche" zu sagen.

        ponytail: nur ein Wahrheitswert plus Ablauf. Obergrenze: die Sperre
        braucht zusaetzlich die **Echo-Referenz** (16 kHz mono int16, siehe
        `interlock.echo_referenz`) -- ueber ein Zustandsfeld geht Audio nicht,
        das braucht den Ohren-Dienst und einen eigenen Kanal.

        T-3.14: `state` ist KEIN Feld mehr, sondern gerechnet -- ein
        uebergebenes `state=` wird stillschweigend fallengelassen. Wer den
        Zustand setzen koennte, koennte ihn behaupten, ohne dass das
        zugrundeliegende Ereignis stattgefunden hat; dann waere die Anzeige
        eine Meinung. Vertrag T-3.14 §1.
        """
        felder.pop("state", None)
        with self._lock:
            vorher = dict(self._voice)
            self._voice.update(felder)
            marke = jetzt if jetzt is not None else time.monotonic()
            if self._voice["listening"] and not vorher["listening"]:
                self._listening_seit = marke
            elif not self._voice["listening"]:
                self._listening_seit = None
            if self._voice["tts_active"] and not vorher["tts_active"]:
                self._sprechen_seit = marke
            elif not self._voice["tts_active"]:
                self._sprechen_seit = None
            if self._voice != vorher:
                self._rev += 1

    def voice_denkt_an(self, *, jetzt: float | None = None) -> None:
        """Eine Aeusserung ist eingegangen und noch nicht beantwortet."""
        with self._lock:
            self._denkt_seit = jetzt if jetzt is not None else time.monotonic()
            if not self._voice["denkt"]:
                self._voice["denkt"] = True
                self._rev += 1

    def voice_denkt_aus(self) -> None:
        with self._lock:
            self._denkt_seit = None
            if self._voice["denkt"]:
                self._voice["denkt"] = False
                self._rev += 1

    def _voice_flags(self, jetzt: float | None) -> dict[str, bool]:
        """Die drei Flags, NACH Ablauf der beiden Fristen.

        Die Fristen werden gerechnet und nicht per Timer geloescht: ein
        Zustand, der nur beim Hinsehen richtig wird, war beim Wegsehen falsch.
        Der Schnappschuss zeigt deshalb das Ergebnis der Rechnung -- ein
        `listening: true`, das nirgends mehr gilt, waere die gefaehrlichste
        Anzeige, die dieses Feld haben kann.
        """
        jetzt = jetzt if jetzt is not None else time.monotonic()
        flags = dict(self._voice)
        if (flags["denkt"] and (self._denkt_seit is None
                                or jetzt - self._denkt_seit >= DENK_FRIST_S)):
            flags["denkt"] = False
        if (flags["listening"] and (self._listening_seit is None
                                    or jetzt - self._listening_seit >= PTT_FRIST_S)):
            flags["listening"] = False
        if (flags["tts_active"] and (self._sprechen_seit is None
                                     or jetzt - self._sprechen_seit >= SPRECH_FRIST_S)):
            flags["tts_active"] = False
        return flags

    @staticmethod
    def _zustand(flags: dict[str, bool]) -> str:
        """Die Ableitung. Die Reihenfolge ist die ganze Aussage: `listening`
        schlaegt `speaking`, weil ein Tastendruck waehrend des Sprechens ein
        Einwurf ist -- die neueste Handlung des Nutzers gewinnt vor der
        laufenden Ausgabe der Maschine."""
        if flags["listening"]:
            return "listening"
        if flags["tts_active"]:
            return "speaking"
        if flags["denkt"]:
            return "processing"
        return "idle"

    def voice_state(self, *, jetzt: float | None = None) -> str:
        """Der abgeleitete Sprachzustand. Vertrag T-3.14 §2."""
        with self._lock:
            return self._zustand(self._voice_flags(jetzt))

    def set_perception(self, **felder: Any) -> None:
        with self._lock:
            vorher = dict(self._perception)
            self._perception.update(felder)
            if self._perception != vorher:
                self._rev += 1

    # -- Lesen -------------------------------------------------------------

    def _aufraeumen(self, jetzt: float) -> bool:
        tot = [sid for sid, s in self._sessions.items() if not s.lebt(jetzt)]
        for sid in tot:
            del self._sessions[sid]
        return bool(tot)

    def _voice_schnappschuss(self, jetzt: float | None = None) -> dict:
        """Nur unter `self._lock` rufen.

        Laeuft eine Frist im Stillen ab, steigt hier `rev` -- sonst bekaeme das
        Face den Rueckfall auf `idle` erst beim naechsten fremden Ereignis zu
        sehen, und bis dahin stuende dort eine Aussage, die nicht mehr gilt.

        `jetzt` ist dieselbe einspeisbare Uhr wie in `voice_state()`. Sie fehlte
        hier, und damit galt die Zusage aus Vertrag T-3.14 §4 -- Fristen ohne
        Warten pruefbar -- nur fuer `voice_state()`: der Schnappschuss las
        immer `time.monotonic()`. Wer eine synthetische Zeit einspeiste und
        danach hier las, mischte zwei Zeitbasen. Gefunden vom blinden
        T-3.14.v-Pruefstand, der genau das tat, was der Vertrag anbot.
        """
        flags = self._voice_flags(jetzt)
        if flags != self._voice:
            self._voice.update(flags)
            self._rev += 1
        zustand = ("listening" if flags["listening"] else
                   "speaking" if flags["tts_active"] else
                   "processing" if flags["denkt"] else "idle")
        return {"state": zustand, **flags}

    def snapshot(self, *, voice_jetzt: float | None = None) -> dict:
        """State nach Design 9, Schema v2.

        `voice_jetzt` ist die monotone Uhr des Sprachzustands und NUR eine
        Pruefhilfe -- ohne sie gilt `time.monotonic()` wie im Betrieb. Sie
        heisst nicht `jetzt`, weil die Sitzungsalterung darunter die WANDUHR
        braucht: zwei Zeitbasen in einem Namen waeren die naechste Falle.
        """
        jetzt = time.time()
        with self._lock:
            if self._aufraeumen(jetzt):
                self._rev += 1

            if not self._sessions:
                mood = "sleeping"
                focus = None
            else:
                gewinner = max(self._sessions.values(),
                               key=lambda s: (PRIORITY.get(s.mood, 0), s.ts))
                mood = gewinner.mood
                # focus.project ist erst bei mehreren Sitzungen interessant --
                # bei einer weiss man ohnehin, welche gemeint ist. Gesetzt wird
                # es trotzdem immer, damit der Client keine Sonderfaelle hat.
                focus = {"session_id": gewinner.session_id,
                         "project": gewinner.project}

            # ERST den Sprachzustand rechnen, DANN `rev` lesen. Ein Dict wertet
            # seine Werte in Reihenfolge aus, und `_voice_schnappschuss()`
            # erhoeht `rev`, wenn eine Frist im Stillen abgelaufen ist. Stand
            # `"rev": self._rev` davor, ging der neue Zustand mit dem ALTEN
            # `rev` hinaus -- und ein Poller, der `rev` vergleicht, haelt das
            # fuer "nichts passiert". Genau der Ausfall, gegen den die Zusage
            # in Vertrag T-3.14 §4 steht. Gefunden ueber den blinden
            # T-3.14.v-Pruefstand.
            voice = self._voice_schnappschuss(voice_jetzt)
            return {
                "v": 2,
                "rev": self._rev,
                "mood": mood,
                "sessions": len(self._sessions),
                "focus": focus,
                "bubble": self._bubble,
                "voice": voice,
                "perception": dict(self._perception),
                # T-7.3: laeuft gerade ein Mitschnitt? Gelesen und nicht
                # gemerkt -- die Anzeige soll den ECHTEN Zustand zeigen und
                # nicht den letzten Befehl. Der Aufruf wird eingespeist,
                # damit der Hub den Recorder nicht importieren muss.
                "mitschnitt": bool(self._mitschnitt()) if self._mitschnitt
                              else False,
            }

    @property
    def rev(self) -> int:
        with self._lock:
            return self._rev
