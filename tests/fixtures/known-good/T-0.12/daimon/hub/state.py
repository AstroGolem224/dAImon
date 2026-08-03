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
from typing import Any

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
        self._voice = {"state": "idle", "listening": False, "tts_active": False}
        self._perception = {"ears": False, "eyes": False, "gpu_loaded": []}

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

    def clear_bubble(self) -> None:
        with self._lock:
            if self._bubble is not None:
                self._bubble = None
                self._rev += 1

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

    def snapshot(self) -> dict:
        """State nach Design 9, Schema v2."""
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

            return {
                "v": 2,
                "rev": self._rev,
                "mood": mood,
                "sessions": len(self._sessions),
                "focus": focus,
                "bubble": self._bubble,
                "voice": dict(self._voice),
                "perception": dict(self._perception),
            }

    @property
    def rev(self) -> int:
        with self._lock:
            return self._rev
