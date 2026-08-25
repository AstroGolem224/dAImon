"""T-6.1 -- SQLite fuer alles, was Neustarts ueberleben soll.

Vier Entscheidungen tragen dieses Modul:

**Die Markierung ueberlebt als TYP, nicht als Spalte, die man vergessen kann.**
Geschrieben wird `Marked.to_wire()`, gelesen wird `Marked.from_wire()` -- und
`from_wire` macht aus einem nackten Wert `tainted`. Wer die Markierungsspalte
also verliert, bekommt beim Lesen Misstrauen zurueck und nicht Vertrauen. Das
ist der Unterschied zwischen einem Fehler, der auffaellt, und einem, der eine
Bildschirmzeile in den werkzeugfaehigen Durchgang traegt.

**Der Live-Sitzungszustand wird NICHT persistiert, und zwar aktiv verweigert.**
`mood`, `listening`, `tts_active` sind Zustaende, keine Erinnerungen. Wer sie
speichert, hat nach einem Absturz ein Pet, das sich fuer wach haelt, waehrend
niemand da ist -- und der Zustand nach einem Neustart ist `sleeping`. Eine
Ausnahme ist hier besser als eine Konvention: eine Konvention haelt sich, bis
jemand es eilig hat.

**Migrationen laufen in beide Richtungen.** Eine Migration ohne Rueckweg ist
keine Migration, sondern eine Einbahnstrasse mit Schemaversion. Der Pruefstand
verlangt ausdruecklich „eine Migration vor UND zurueck".

**0600, und die Nebendateien auch.** SQLite legt im WAL-Modus `-wal` und
`-shm` daneben; darin steht dasselbe. Eine Datenbank mit 0600 neben einem WAL
mit 0644 ist eine Datenbank mit 0644.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from daimon.common.config import state_dir
from daimon.common.protocol import Mark, Marked

DATEI = "memory.db"
MODUS = 0o600

# Was hier NICHT hineingehoert. Zustand ist keine Erinnerung.
VERBOTENE_ARTEN = frozenset({
    "mood", "session_mood", "listening", "tts_active", "state", "zustand",
})

# (version, hinauf, hinunter). Die Rueckwege sind Teil der Migration und
# nicht ihre Nachbereitung.
MIGRATIONEN: tuple[tuple[int, str, str], ...] = (
    (1,
     """
     CREATE TABLE eintraege (
       id      INTEGER PRIMARY KEY AUTOINCREMENT,
       art     TEXT    NOT NULL,
       ts      REAL    NOT NULL,
       wert    TEXT    NOT NULL
     );
     CREATE INDEX eintraege_art_ts ON eintraege (art, ts);
     """,
     """
     DROP INDEX IF EXISTS eintraege_art_ts;
     DROP TABLE IF EXISTS eintraege;
     """),
    (2,
     # `turn_id` kam nach: ohne sie laesst sich nicht sagen, aus welcher Runde
     # ein Eintrag stammt, und T-6.7b prueft genau Rundengrenzen.
     """
     ALTER TABLE eintraege ADD COLUMN turn_id TEXT NOT NULL DEFAULT '';
     """,
     # SQLite kann Spalten seit 3.35 loeschen. Faellt das aus, bleibt die
     # Spalte stehen und die Version geht trotzdem zurueck -- eine Spalte zu
     # viel ist kein Datenverlust, ein fehlgeschlagener Rueckweg schon.
     """
     ALTER TABLE eintraege DROP COLUMN turn_id;
     """),
)

SCHEMA_VERSION = MIGRATIONEN[-1][0]


class StoreFehler(RuntimeError):
    """Etwas an der Datenbank oder am Eintrag ist unbrauchbar."""


class Store:
    """Die Datenbank. Kennt Markierungen, kennt keinen Sitzungszustand."""

    def __init__(self, pfad: Path | None = None, *,
                 uhr: Callable[[], float] = time.time) -> None:
        self.pfad = Path(pfad or (state_dir() / DATEI))
        self._uhr = uhr
        self._db: sqlite3.Connection | None = None
        # T-6.1-3.v, Befund am Echtbaum: der Mind-Dienst bedient jede
        # Verbindung in einem eigenen Thread (daemon.py) und teilt sich EINEN
        # Store. Ohne `check_same_thread=False` stirbt der erste Schreibzugriff
        # aus einem fremden Thread, ohne zu antworten -- die Frage wurde nie
        # gestellt, weil der Store bis dahin keinen Aufrufer hatte.
        self._sperre = threading.RLock()

    # -- Oeffnen -----------------------------------------------------------

    def oeffnen(self) -> sqlite3.Connection:
        if self._db is not None:
            return self._db
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        neu = not self.pfad.exists()
        db = sqlite3.connect(self.pfad, isolation_level=None,
                             check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        if neu:
            os.chmod(self.pfad, MODUS)
        self._db = db
        self._rechte_ziehen()
        return db

    def _rechte_ziehen(self) -> None:
        """0600 auf die Datenbank UND ihre Nebendateien.

        Im WAL-Modus stehen dieselben Zeilen in `-wal`; eine Datenbank mit
        0600 neben einem WAL mit 0644 ist eine Datenbank mit 0644.
        """
        for endung in ("", "-wal", "-shm"):
            p = Path(str(self.pfad) + endung)
            try:
                os.chmod(p, MODUS)
            except OSError:
                pass

    def schliessen(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # -- Migrationen -------------------------------------------------------

    def version(self) -> int:
        with self._sperre:
            return int(self.oeffnen().execute(
                "PRAGMA user_version").fetchone()[0])

    def migrieren(self, ziel: int | None = None) -> int:
        """Auf `ziel` bringen -- hinauf oder hinunter. Gibt die Version zurueck."""
        with self._sperre:
            db = self.oeffnen()
            zielv = SCHEMA_VERSION if ziel is None else int(ziel)
            if not 0 <= zielv <= SCHEMA_VERSION:
                raise StoreFehler(
                    f"Zielversion {zielv} liegt ausserhalb von 0..{SCHEMA_VERSION}")

            while self.version() < zielv:
                v = self.version() + 1
                _, hinauf, _ = MIGRATIONEN[v - 1]
                db.executescript(hinauf)
                db.execute(f"PRAGMA user_version={v}")
            while self.version() > zielv:
                v = self.version()
                _, _, hinunter = MIGRATIONEN[v - 1]
                db.executescript(hinunter)
                db.execute(f"PRAGMA user_version={v - 1}")
            self._rechte_ziehen()
            return self.version()

    # -- Schreiben und Lesen -----------------------------------------------

    def schreiben(self, art: str, wert: Any, *, turn_id: str = "",
                  ts: float | None = None) -> int:
        """Ein Eintrag. `wert` darf `Marked` sein oder nackt.

        Nackt heisst `tainted` -- nicht, weil das haeufig richtig ist, sondern
        weil das die harmlose Richtung ist, wenn jemand es vergisst.
        """
        art = str(art).strip().lower()
        if art in VERBOTENE_ARTEN:
            raise StoreFehler(
                f"{art!r} ist Sitzungszustand und keine Erinnerung. Nach einem "
                "Neustart gilt `sleeping`, nicht der alte Wert.")
        if not art:
            raise StoreFehler("Art fehlt")

        markiert = wert if isinstance(wert, Marked) else Marked.from_wire(wert)
        with self._sperre:
            db = self.oeffnen()
            cur = db.execute(
                "INSERT INTO eintraege (art, ts, wert, turn_id) VALUES (?,?,?,?)",
                (art, float(ts if ts is not None else self._uhr()),
                 json.dumps(markiert.to_wire(), ensure_ascii=False), str(turn_id)))
            return int(cur.lastrowid)

    def lesen(self, art: str | None = None, *, seit: float | None = None,
              hoechstens: int = 100) -> list[dict]:
        """Eintraege, jeweils mit `Marked` im Feld `wert`."""
        with self._sperre:
            db = self.oeffnen()
            bedingungen, werte = [], []
            if art is not None:
                bedingungen.append("art = ?")
                werte.append(str(art).strip().lower())
            if seit is not None:
                bedingungen.append("ts >= ?")
                werte.append(float(seit))
            wo = (" WHERE " + " AND ".join(bedingungen)) if bedingungen else ""
            zeilen = db.execute(
                f"SELECT id, art, ts, wert, turn_id FROM eintraege{wo} "
                "ORDER BY ts DESC, id DESC LIMIT ?", (*werte, int(hoechstens))
            ).fetchall()
            return [self._zeile(z) for z in zeilen]

    @staticmethod
    def _zeile(z: sqlite3.Row) -> dict:
        try:
            roh = json.loads(z["wert"])
        except (ValueError, TypeError):
            # Unlesbar heisst `tainted`, nicht "leer". Ein kaputter Eintrag,
            # der als vertrauenswuerdig zurueckkommt, waere der schlimmste
            # Ausgang dieses Moduls.
            roh = z["wert"]
        return {"id": z["id"], "art": z["art"], "ts": z["ts"],
                "turn_id": z["turn_id"] if "turn_id" in z.keys() else "",
                "wert": Marked.from_wire(roh)}

    def loeschen(self, eintrag_id: int) -> bool:
        """Ein einzelner Eintrag. `True`, wenn es ihn gab.

        Einzeln loeschbar ist eine Zusage von T-6.3: wer sich etwas merken
        laesst, muss es auch wieder loswerden koennen, ohne alles zu
        verlieren.
        """
        with self._sperre:
            db = self.oeffnen()
            cur = db.execute("DELETE FROM eintraege WHERE id = ?",
                             (int(eintrag_id),))
            return cur.rowcount > 0

    # -- Ein Befehl loescht alles ------------------------------------------

    def alles_loeschen(self) -> int:
        """Zeilen weg, Datei weg, Nebendateien weg. Gibt die Zahl zurueck.

        Beides: `DELETE FROM` allein laesst die Seiten in der Datei stehen,
        und wer die Datenbank danach mit einem Hex-Editor oeffnet, findet
        seine Erinnerungen wieder.
        """
        with self._sperre:
            db = self.oeffnen()
            anzahl = int(db.execute(
                "SELECT COUNT(*) FROM eintraege").fetchone()[0])
            db.execute("DELETE FROM eintraege")
            self.schliessen()
            for endung in ("", "-wal", "-shm"):
                try:
                    Path(str(self.pfad) + endung).unlink()
                except OSError:
                    pass
            return anzahl


def main(argv: list[str] | None = None) -> int:
    """`python -m daimon.mind.store --loeschen` -- der eine Befehl."""
    import argparse

    zerleger = argparse.ArgumentParser(description=__doc__)
    zerleger.add_argument("--loeschen", action="store_true",
                          help="alles vergessen, Datei entfernen")
    zerleger.add_argument("--version", action="store_true",
                          help="Schemaversion melden")
    args = zerleger.parse_args(argv)

    s = Store()
    if args.loeschen:
        print(json.dumps({"geloescht": s.alles_loeschen(),
                          "datei": str(s.pfad)}))
        return 0
    s.migrieren()
    print(json.dumps({"version": s.version(), "datei": str(s.pfad),
                      "modus": oct(s.pfad.stat().st_mode & 0o777)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
