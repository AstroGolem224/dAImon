"""T-8.1 -- Termine und Fokusbloecke, die Neustarts ueberleben.

Drei Entscheidungen tragen dieses Modul:

**Eine eigene Datenbank, nicht die des Mind.** `memory.db` gehoert dem
Mind, das Archiv dem Recorder -- und dieser Speicher gehoert dem
Plan-Dienst allein. Zwei Prozesse mit Schreibrecht auf derselben Datei
waeren eine Grenze auf dem Papier. Die Datei heisst `plan.db` und liegt in
einem EIGENEN Unterverzeichnis `state_dir()/plan/` -- so kann die Unit
genau dieses Verzeichnis schreibbar machen und nicht das ganze
state-Verzeichnis samt `audit/audit.jsonl`.

**Titel und Meta ueberleben als `Marked`.** Ein Titel, der per Sprache
reinkam, ist Transkript und damit `tainted`; einer aus der CLI kann
`trusted` sein. Wer die Markierung wegwirft, traegt spaeter Bildschirmtext
in den Sprechweg. Geschrieben wird `Marked.to_wire()`, gelesen
`from_wire()` -- wie in `mind/store.py`, aus demselben Grund.

**Migrationen laufen in beide Richtungen.** Wie ueberall in diesem Projekt:
eine Migration ohne Rueckweg ist eine Einbahnstrasse mit Schemaversion.

Die Fälligkeitslogik ist hier NICHT. Der Speicher beantwortet nur
„was ist offen und faellig", die Entscheidung „und was jetzt" gehoert dem
Dienst (`daimon/plan/daemon.py`).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from daimon.common.config import state_dir
from daimon.common.protocol import Mark, Marked

# Ein EIGENES Unterverzeichnis, nicht `state_dir()` selbst. Der Grund steht
# in `config/systemd/daimon-plan.service`: `ReadWritePaths` kann nur ein
# Verzeichnis freigeben (das WAL entsteht zur Laufzeit), und das ganze
# state-Verzeichnis freizugeben hiesse `audit/audit.jsonl` schreibbar zu
# machen -- fuer einen Dienst, der Termine merkt.
UNTERVERZEICHNIS = "plan"
DATEI = "plan.db"
MODUS = 0o600

# Die geschlossene Menge dessen, was der Planer kennt. `quelle` ist der
# Haken fuer den spaeteren CalDAV-Import: heute ist sie immer "intern",
# ein Import-Task schreibt "caldav" -- ohne Schemaaenderung.
ARTEN = frozenset({"termin", "fokus"})
STATUS = frozenset({"offen", "gemeldet", "gestoppt"})

# (version, hinauf, hinunter). Die Rueckwege sind Teil der Migration und
# nicht ihre Nachbereitung.
MIGRATIONEN: tuple[tuple[int, str, str], ...] = (
    (1,
     """
     CREATE TABLE eintraege (
       id          INTEGER PRIMARY KEY AUTOINCREMENT,
       art         TEXT    NOT NULL CHECK (art IN ('termin', 'fokus')),
       titel       TEXT    NOT NULL,
       ts_faellig  REAL    NOT NULL,
       ts_erstellt REAL    NOT NULL,
       status      TEXT    NOT NULL DEFAULT 'offen'
                           CHECK (status IN ('offen', 'gemeldet', 'gestoppt')),
       quelle      TEXT    NOT NULL DEFAULT 'intern',
       meta        TEXT    NOT NULL DEFAULT '{}'
     );
     CREATE INDEX eintraege_status_faellig ON eintraege (status, ts_faellig);
     """,
     """
     DROP INDEX IF EXISTS eintraege_status_faellig;
     DROP TABLE IF EXISTS eintraege;
     """),
)

SCHEMA_VERSION = MIGRATIONEN[-1][0]


class StoreFehler(RuntimeError):
    """Etwas an der Datenbank oder am Eintrag ist unbrauchbar."""


class Store:
    """Die Datenbank des Zeitplaners. Kennt Fälligkeiten, keine Entscheidung."""

    def __init__(self, pfad: Path | None = None, *,
                 uhr: Callable[[], float] = time.time) -> None:
        self.pfad = Path(pfad or (state_dir() / UNTERVERZEICHNIS / DATEI))
        self._uhr = uhr
        self._db: sqlite3.Connection | None = None
        # Der Dienst fragt aus Threads: Anfragen laufen in eigenen Threads,
        # die Abtastung in einem weiteren (daemon.py `lauf`). sqlite3 wirft
        # sonst `ProgrammingError ... same thread` -- live gemessen am
        # 24.08., die erste `liste`-Anfrage an den laufenden Dienst.
        # Die Sperre serialisiert die Zugriffe; `check_same_thread=False`
        # allein waere nur die halbe Zusage (parallele Cursor auf EINER
        # Verbindung sind nicht sicher).
        self._sperre = threading.RLock()

    # -- Oeffnen -----------------------------------------------------------

    def oeffnen(self) -> sqlite3.Connection:
        with self._sperre:
            if self._db is not None:
                return self._db
            self.pfad.parent.mkdir(parents=True, exist_ok=True)
            self._umziehen()
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

    def _umziehen(self) -> None:
        """Eine Datenbank aus der alten Lage (`state_dir()/plan.db`) KOPIEREN.

        Bis zum 26.08. lag sie direkt im state-Verzeichnis; mit ihr lag dort
        `ReadWritePaths` fuer den ganzen Ordner. Ohne diesen Umzug faengt der
        Dienst nach dem Update mit einer leeren Terminliste an -- und die
        alten Termine liegen still daneben. Geholt wird nur, wenn am neuen
        Ort nichts steht; die Nebendateien gehen mit, sonst liest SQLite ein
        WAL ohne seine Datenbank.

        **Kopiert, nicht verschoben.** `os.replace` braucht Schreibrecht am
        QUELLverzeichnis -- und genau das hat die Unit nicht mehr, seit
        `ReadWritePaths` nur noch `.../plan` freigibt. Der Umzug warf dort
        `OSError`, `main()` fing nichts, `Restart=on-failure` machte daraus
        eine Dauerschleife: genau die Anlage, fuer die die Migration gebaut
        wurde, waere nicht mehr gestartet. Der Altbestand bleibt liegen; er
        ist ab dem ersten Schreiben tot, und ihn zu loeschen braucht dasselbe
        Schreibrecht, das hier fehlt.

        Ein misslungener Umzug kostet die alten Termine, nie den Dienst --
        deshalb `except OSError` und keine Ausnahme nach oben.
        """
        if self.pfad.parent.name != UNTERVERZEICHNIS or self.pfad.exists():
            return
        alt = self.pfad.parent.parent / self.pfad.name
        if not alt.exists():
            return
        kopiert: list[Path] = []
        try:
            for endung in ("", "-wal", "-shm"):
                quelle = Path(str(alt) + endung)
                if quelle.exists():
                    ziel = Path(str(self.pfad) + endung)
                    shutil.copy2(quelle, ziel)
                    kopiert.append(ziel)
        except OSError:
            # Halb kopiert ist schlimmer als gar nicht: eine Datenbank ohne
            # ihr WAL verliert die letzten Zeilen, ein WAL ohne Datenbank ist
            # unbrauchbar. Also zurueck auf leer und mit leerer Liste starten.
            for z in kopiert:
                try:
                    z.unlink()
                except OSError:
                    pass

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
        with self._sperre:
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
            ziel = SCHEMA_VERSION if ziel is None else int(ziel)
            if not 0 <= ziel <= SCHEMA_VERSION:
                raise StoreFehler(
                    f"Zielversion {ziel} liegt ausserhalb von 0..{SCHEMA_VERSION}")

            while self.version() < ziel:
                v = self.version() + 1
                _, hinauf, _ = MIGRATIONEN[v - 1]
                db.executescript(hinauf)
                db.execute(f"PRAGMA user_version={v}")
            while self.version() > ziel:
                v = self.version()
                _, _, hinunter = MIGRATIONEN[v - 1]
                db.executescript(hinunter)
                db.execute(f"PRAGMA user_version={v - 1}")
            self._rechte_ziehen()
            return self.version()

    # -- Schreiben und Lesen -----------------------------------------------

    def anlegen(self, art: str, titel: Any, ts_faellig: float, *,
                meta: Any = None, quelle: str = "intern",
                ts_erstellt: float | None = None) -> int:
        """Ein Termin oder Fokusblock. `titel`/`meta` duerfen `Marked` sein
        oder nackt -- nackt heisst `tainted`, das ist die harmlose Richtung,
        wenn jemand die Markierung vergisst."""
        art = str(art).strip().lower()
        if art not in ARTEN:
            raise StoreFehler(f"Unbekannte art {art!r}, erwartet {sorted(ARTEN)}")
        quelle = str(quelle).strip().lower() or "intern"

        titel_m = titel if isinstance(titel, Marked) else Marked.from_wire(titel)
        meta_m = meta if isinstance(meta, Marked) else Marked.from_wire(meta or {})
        if not str(titel_m.value or "").strip():
            raise StoreFehler("Titel fehlt")

        db = self.oeffnen()
        with self._sperre:
            cur = db.execute(
                "INSERT INTO eintraege (art, titel, ts_faellig, ts_erstellt,"
                " quelle, meta) VALUES (?,?,?,?,?,?)",
                (art, json.dumps(titel_m.to_wire(), ensure_ascii=False),
                 float(ts_faellig),
                 float(ts_erstellt if ts_erstellt is not None else self._uhr()),
                 quelle, json.dumps(meta_m.to_wire(), ensure_ascii=False)))
            self._rechte_ziehen()
            return int(cur.lastrowid)

    def faellige(self, jetzt: float) -> list[dict]:
        """Offene Eintraege mit `ts_faellig <= jetzt`, frueheste zuerst.

        Die Abtastschleife des Dienstes fragt nur das -- ein Termin, der
        waehrend eines Suspend oder Stopps faellig wurde, ist beim naechsten
        Lauf einfach faellig. Das ist die Suspend-Sicherheit dieses Moduls:
        sie liegt im Lesen, nicht in einem Wecker.
        """
        with self._sperre:
            zeilen = self.oeffnen().execute(
                "SELECT * FROM eintraege WHERE status = 'offen' AND ts_faellig <= ?"
                " ORDER BY ts_faellig ASC, id ASC", (float(jetzt),)).fetchall()
            return [self._zeile(z) for z in zeilen]

    def liste(self, status: str | None = None) -> list[dict]:
        """Eintraege, neueste Faelligkeit zuerst. `status=None` heisst alle."""
        with self._sperre:
            if status is not None:
                status = str(status).strip().lower()
                if status not in STATUS:
                    raise StoreFehler(
                        f"Unbekannter status {status!r}, erwartet {sorted(STATUS)}")
                zeilen = self.oeffnen().execute(
                    "SELECT * FROM eintraege WHERE status = ?"
                    " ORDER BY ts_faellig DESC, id DESC", (status,)).fetchall()
            else:
                zeilen = self.oeffnen().execute(
                    "SELECT * FROM eintraege"
                    " ORDER BY ts_faellig DESC, id DESC").fetchall()
            return [self._zeile(z) for z in zeilen]

    @staticmethod
    def _zeile(z: sqlite3.Row) -> dict:
        def marked_roh(roh: Any) -> Marked:
            try:
                roh = json.loads(roh)
            except (ValueError, TypeError):
                # Unlesbar heisst `tainted`, nicht "leer" -- wie im Mind-Store.
                pass
            return Marked.from_wire(roh)
        return {"id": z["id"], "art": z["art"],
                "titel": marked_roh(z["titel"]),
                "ts_faellig": z["ts_faellig"], "ts_erstellt": z["ts_erstellt"],
                "status": z["status"], "quelle": z["quelle"],
                "meta": marked_roh(z["meta"])}

    def markiere(self, eintrag_id: int, status: str) -> bool:
        """Status setzen. `True`, wenn es den Eintrag gab."""
        status = str(status).strip().lower()
        if status not in STATUS:
            raise StoreFehler(
                f"Unbekannter status {status!r}, erwartet {sorted(STATUS)}")
        with self._sperre:
            cur = self.oeffnen().execute(
                "UPDATE eintraege SET status = ? WHERE id = ?",
                (status, int(eintrag_id)))
            return cur.rowcount > 0

    def loeschen(self, eintrag_id: int) -> bool:
        """Ein einzelner Eintrag. `True`, wenn es ihn gab."""
        with self._sperre:
            cur = self.oeffnen().execute("DELETE FROM eintraege WHERE id = ?",
                                         (int(eintrag_id),))
            return cur.rowcount > 0

    def alles_loeschen(self) -> int:
        """Zeilen weg, Datei weg, Nebendateien weg. Gibt die Zahl zurueck."""
        with self._sperre:
            anzahl = int(self.oeffnen().execute(
                "SELECT COUNT(*) FROM eintraege").fetchone()[0])
            self._db.execute("DELETE FROM eintraege")
            self.schliessen()
            for endung in ("", "-wal", "-shm"):
                try:
                    Path(str(self.pfad) + endung).unlink()
                except OSError:
                    pass
            return anzahl
