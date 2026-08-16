"""T-7.1 -- das Archiv. Ein Ort fuer alles Mitgeschnittene, mit Verfallsdatum
ab Zeile eins.

Vier Entscheidungen tragen dieses Modul:

**`tainted` ist hier kein Spaltenwert, sondern der Rueckgabetyp.** Der
Kontextspeicher (T-5.7) und das Gedaechtnis (T-6.1) fuehren eine Markierung
mit, weil dort auch Vertrauenswuerdiges liegt. Im Archiv nicht: alles darin
stammt vom Bildschirm oder aus einem Mikrofon. Es gibt deshalb GAR KEINE
Markierungsspalte -- `lesen` und `suchen` verpacken jeden Treffer in
`Marked(..., Mark.TAINTED)`. Eine Spalte kann man vergessen zu setzen; einen
Rueckgabetyp nicht.

**Aufbewahrung je Art getrennt, und der Text ueberlebt das Bild.** Design
§7.2d: ein Fenstertitel ist wenig verraeterisch und lange nuetzlich, ein
Frame ist der Bildschirm selbst. 48 Stunden fuer Frames, 30 Tage fuer Text,
und Rohaudio gar nicht -- die Art existiert nicht, `schreiben` weist sie ab.
Die Fristen stehen im Code und nicht in der Konfiguration: sie sind eine
Zusage, und eine Zusage, die man in einer TOML-Datei umstellen kann, ist
keine.

**Die harte Obergrenze verdraengt, sie meldet keinen Fehler.** Eine volle
Platte ist kein Betriebszustand. Gerechnet wird auf einer mitgefuehrten
Groessenspalte und nicht auf der Dateigroesse: SQLite gibt Seiten nach einem
DELETE nicht ans Dateisystem zurueck, ein Deckel auf `stat().st_size` wuerde
also nach der ersten Verdraengung nie wieder unterschritten.

**Der Volltextindex haengt an der Tabelle, nicht neben ihr.** FTS5 mit
`content='archiv'` und Triggern: wer eine Zeile loescht -- Verfall,
Verdraengung, Kill-Switch --, loescht sie damit auch aus dem Index. Ein
zweiter Speicher, den man getrennt aufraeumen muss, waere ein zweiter Ort,
an dem der Mitschnitt einen Verfall ueberlebt.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from daimon.common.config import data_dir
from daimon.common.protocol import Mark, Marked

DATEI = "archiv.db"
MODUS = 0o600
VERZEICHNIS_MODUS = 0o700

# Design §7.2d, die vier Stufen. Vorgabe ist `redacted`, nicht `full`.
STUFE_TRANSIENT = "transient"
STUFE_METADATA = "metadata_only"
STUFE_REDACTED = "redacted"
STUFE_FULL = "full"
STUFEN = (STUFE_TRANSIENT, STUFE_METADATA, STUFE_REDACTED, STUFE_FULL)

ART_TITEL = "titel"
ART_OCR = "ocr"
ART_TRANSKRIPT = "transkript"
ART_FRAME = "frame"

# Die Aufbewahrung je Art, in Sekunden. Aus dem Plan (T-7.1) und Design §7.2d.
# Der Text ueberlebt das Bild -- das ist der Punkt dieser Tabelle, nicht ihre
# Existenz.
AUFBEWAHRUNG: dict[str, float] = {
    ART_TITEL: 30 * 24 * 3600.0,
    ART_OCR: 30 * 24 * 3600.0,
    ART_TRANSKRIPT: 30 * 24 * 3600.0,
    ART_FRAME: 48 * 3600.0,
}

# Rohaudio wird nie geschrieben (Plan T-7.4, und schon hier durchgesetzt: eine
# Zusage, die erst der spaetere Task haelt, haelt bis dahin niemand). Die
# Namen sind die, unter denen es jemand versehentlich ablegen wuerde.
VERBOTENE_ARTEN = frozenset({"audio", "rohaudio", "pcm", "wav", "samples"})

MIGRATIONEN: tuple[tuple[int, str, str], ...] = (
    (1,
     """
     CREATE TABLE archiv (
       id      INTEGER PRIMARY KEY AUTOINCREMENT,
       art     TEXT    NOT NULL,
       ts      REAL    NOT NULL,
       stufe   TEXT    NOT NULL DEFAULT 'redacted',
       fenster TEXT    NOT NULL DEFAULT '',
       text    TEXT    NOT NULL DEFAULT '',
       daten   BLOB,
       bytes   INTEGER NOT NULL DEFAULT 0
     );
     CREATE INDEX archiv_art_ts ON archiv (art, ts);
     CREATE VIRTUAL TABLE archiv_fts USING fts5(
       text, fenster, content='archiv', content_rowid='id');
     CREATE TRIGGER archiv_fts_ein AFTER INSERT ON archiv BEGIN
       INSERT INTO archiv_fts (rowid, text, fenster)
         VALUES (new.id, new.text, new.fenster);
     END;
     CREATE TRIGGER archiv_fts_weg AFTER DELETE ON archiv BEGIN
       INSERT INTO archiv_fts (archiv_fts, rowid, text, fenster)
         VALUES ('delete', old.id, old.text, old.fenster);
     END;
     """,
     """
     DROP TRIGGER IF EXISTS archiv_fts_weg;
     DROP TRIGGER IF EXISTS archiv_fts_ein;
     DROP TABLE IF EXISTS archiv_fts;
     DROP INDEX IF EXISTS archiv_art_ts;
     DROP TABLE IF EXISTS archiv;
     """),
)

SCHEMA_VERSION = MIGRATIONEN[-1][0]


class ArchivFehler(RuntimeError):
    """Etwas am Archiv oder am Eintrag ist unbrauchbar."""


class Archiv:
    """Die Archivdatenbank. Einziger Schreiber ist `daimon-recorder`."""

    def __init__(self, pfad: Path | None = None, *,
                 uhr: Callable[[], float] = time.time,
                 grenze_bytes: int = 5 * 1024 ** 3) -> None:
        self.pfad = Path(pfad or (data_dir() / DATEI))
        self.grenze_bytes = int(grenze_bytes)
        self._uhr = uhr
        self._db: sqlite3.Connection | None = None

    # -- Oeffnen -----------------------------------------------------------

    def oeffnen(self) -> sqlite3.Connection:
        if self._db is not None:
            return self._db
        # ANLEGEN mit den richtigen Rechten, nicht nachtraeglich chmodden.
        # `mkdir(mode=)` und `os.open(..., 0o600)` setzen sie beim Erzeugen;
        # die umask kann Bits nur WEGnehmen, aus 0700 wird also nie 0755.
        # Vorher lagen zwischen `connect()` und `chmod` ein paar
        # Mikrosekunden, in denen Datenbank und Verzeichnis mit den Rechten
        # der Shell dastanden -- im Dienst deckt das `UMask=0077` der Unit
        # zu, beim CLI-Aufruf (`python -m daimon.recorder.store`) nicht.
        #
        # Ein Angriffsweg ist daraus im Bedrohungsmodell nicht abzuleiten
        # (Design 1.3 deckt same-uid ohnehin nicht, und ein fremder uid
        # scheitert am 0700-Verzeichnis). Es ist die richtige Reihenfolge,
        # und sie kostet zwei Zeilen -- `_rechte_ziehen` bleibt fuer
        # Datenbanken, die schon liegen.
        self.pfad.parent.mkdir(parents=True, exist_ok=True,
                               mode=VERZEICHNIS_MODUS)
        os.chmod(self.pfad.parent, VERZEICHNIS_MODUS)
        if not self.pfad.exists():
            os.close(os.open(self.pfad, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                             MODUS))
        db = sqlite3.connect(self.pfad, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        self._db = db
        self._rechte_ziehen()
        return db

    def _rechte_ziehen(self) -> None:
        """0600 auf die Datenbank UND ihre Nebendateien -- im WAL steht
        dasselbe wie in der Datenbank."""
        for endung in ("", "-wal", "-shm"):
            try:
                os.chmod(Path(str(self.pfad) + endung), MODUS)
            except OSError:
                pass

    def schliessen(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # -- Migrationen -------------------------------------------------------

    def version(self) -> int:
        return int(self.oeffnen().execute("PRAGMA user_version").fetchone()[0])

    def migrieren(self, ziel: int | None = None) -> int:
        db = self.oeffnen()
        ziel = SCHEMA_VERSION if ziel is None else int(ziel)
        if not 0 <= ziel <= SCHEMA_VERSION:
            raise ArchivFehler(
                f"Zielversion {ziel} liegt ausserhalb von 0..{SCHEMA_VERSION}")
        while self.version() < ziel:
            v = self.version() + 1
            db.executescript(MIGRATIONEN[v - 1][1])
            db.execute(f"PRAGMA user_version={v}")
        while self.version() > ziel:
            v = self.version()
            db.executescript(MIGRATIONEN[v - 1][2])
            db.execute(f"PRAGMA user_version={v - 1}")
        self._rechte_ziehen()
        return self.version()

    # -- Schreiben ---------------------------------------------------------

    def schreiben(self, art: str, text: str = "", *, fenster: str = "",
                  stufe: str = STUFE_REDACTED, daten: bytes | None = None,
                  ts: float | None = None) -> int:
        """Ein Eintrag. Gibt die id zurueck.

        `stufe` ist `redacted`, wenn niemand etwas anderes sagt -- und
        `transient` schreibt gar nicht: die Stufe heisst "nur im
        Arbeitsspeicher", und ein transienter Eintrag auf der Platte waere
        ein Widerspruch, den erst jemand beim Lesen bemerkt.
        """
        art = str(art).strip().lower()
        if art in VERBOTENE_ARTEN:
            raise ArchivFehler(
                f"{art!r} waere Rohaudio auf der Platte. Nur das Transkript "
                "ueberlebt den Abschnitt (Plan T-7.4).")
        if art not in AUFBEWAHRUNG:
            raise ArchivFehler(
                f"{art!r} hat keine Aufbewahrungsfrist. Wer eine Art "
                f"ergaenzt, ergaenzt sie in AUFBEWAHRUNG -- sonst laege sie "
                "fuer immer. Bekannt: " + ", ".join(sorted(AUFBEWAHRUNG)))
        if stufe not in STUFEN:
            raise ArchivFehler(f"unbekannte Stufe {stufe!r}")
        if stufe == STUFE_TRANSIENT:
            return 0
        if stufe == STUFE_METADATA:
            text, daten = "", None

        wert = str(text)
        gross = len(wert.encode("utf-8")) + (len(daten) if daten else 0)
        db = self.oeffnen()
        cur = db.execute(
            "INSERT INTO archiv (art, ts, stufe, fenster, text, daten, bytes) "
            "VALUES (?,?,?,?,?,?,?)",
            (art, float(ts if ts is not None else self._uhr()), stufe,
             str(fenster), wert, daten, gross))
        return int(cur.lastrowid)

    # -- Lesen -------------------------------------------------------------

    @staticmethod
    def _zeile(z: sqlite3.Row) -> dict:
        """Jeder Treffer kommt `tainted` zurueck -- ohne Spalte, ohne Wahl."""
        return {"id": z["id"], "art": z["art"], "ts": z["ts"],
                "stufe": z["stufe"], "fenster": z["fenster"],
                "wert": Marked(z["text"], Mark.TAINTED)}

    def lesen(self, art: str | None = None, *, seit: float | None = None,
              hoechstens: int = 100) -> list[dict]:
        bedingungen, werte = [], []
        if art is not None:
            bedingungen.append("art = ?")
            werte.append(str(art).strip().lower())
        if seit is not None:
            bedingungen.append("ts >= ?")
            werte.append(float(seit))
        wo = (" WHERE " + " AND ".join(bedingungen)) if bedingungen else ""
        zeilen = self.oeffnen().execute(
            f"SELECT id, art, ts, stufe, fenster, text FROM archiv{wo} "
            "ORDER BY ts DESC, id DESC LIMIT ?", (*werte, int(hoechstens))
        ).fetchall()
        return [self._zeile(z) for z in zeilen]

    def suchen(self, anfrage: str, *, hoechstens: int = 20) -> list[dict]:
        """Volltextsuche ueber Text und Fenstertitel.

        Der Treffer ist `tainted` wie alles hier -- er stammt vom Bildschirm
        und wird nicht dadurch vertrauenswuerdig, dass er den Umweg ueber die
        eigene Datenbank genommen hat. Das Deklassifizierungs-Gate steht in
        T-7.5; dieses Modul kennt keine Runden und darf deshalb auch nicht
        entscheiden, wer lesen darf.
        """
        text = str(anfrage).strip()
        if not text:
            return []
        try:
            zeilen = self.oeffnen().execute(
                "SELECT a.id, a.art, a.ts, a.stufe, a.fenster, a.text "
                "FROM archiv_fts f JOIN archiv a ON a.id = f.rowid "
                "WHERE archiv_fts MATCH ? ORDER BY rank LIMIT ?",
                (text, int(hoechstens))).fetchall()
        except sqlite3.OperationalError as exc:
            # Ein Suchbegriff mit FTS5-Syntaxfehler ist eine Nutzereingabe,
            # kein Programmfehler. Leere Treffermenge statt Absturz.
            raise ArchivFehler(f"Suchbegriff nicht auswertbar: {exc}") from exc
        return [self._zeile(z) for z in zeilen]

    # -- Aufraeumen --------------------------------------------------------

    def belegung(self) -> int:
        return int(self.oeffnen().execute(
            "SELECT COALESCE(SUM(bytes), 0) FROM archiv").fetchone()[0])

    def aufraeumen(self) -> dict:
        """Verfall je Art, danach Verdraengung bis unter die Grenze."""
        db = self.oeffnen()
        jetzt = self._uhr()
        verfallen: dict[str, int] = {}
        for art, frist in AUFBEWAHRUNG.items():
            cur = db.execute("DELETE FROM archiv WHERE art = ? AND ts < ?",
                             (art, jetzt - frist))
            if cur.rowcount > 0:
                verfallen[art] = cur.rowcount

        # Verdraengt wird GENAU so viel wie noetig, nicht blockweise: ein
        # fester Block von n Zeilen raeumt bei kleinen Eintraegen weit ueber
        # die Grenze hinaus und wirft Mitschnitt weg, den niemand verlangt
        # hat. Die laufende Summe ueber `bytes`, aeltester zuerst, sagt
        # stattdessen genau, wo der Schnitt liegt.
        verdraengt = 0
        ueberschuss = self.belegung() - self.grenze_bytes
        if ueberschuss > 0:
            lauf = ("SELECT id, SUM(bytes) OVER (ORDER BY ts ASC, id ASC) "
                    "AS lauf FROM archiv")
            ids = [z["id"] for z in db.execute(
                f"SELECT id FROM ({lauf}) WHERE lauf <= ? ORDER BY lauf",
                (ueberschuss,)).fetchall()]
            # Die laufende Summe trifft die Grenze selten genau -- eine Zeile
            # mehr, sonst bliebe das Archiv einen Rest ueber dem Deckel.
            rest = db.execute(
                f"SELECT id FROM ({lauf}) WHERE lauf > ? ORDER BY lauf LIMIT 1",
                (ueberschuss,)).fetchone()
            if rest is not None:
                ids.append(rest["id"])
            if ids:
                marken = ",".join("?" * len(ids))
                verdraengt = db.execute(
                    f"DELETE FROM archiv WHERE id IN ({marken})",
                    ids).rowcount

        return {"verfallen": verfallen, "verdraengt": verdraengt,
                "bytes": self.belegung()}

    # -- Ein Befehl loescht alles ------------------------------------------

    def alles_loeschen(self) -> int:
        """Zeilen weg, Datei weg, Nebendateien weg -- wie im Gedaechtnis.

        `DELETE FROM` allein laesst die Seiten in der Datei stehen; wer sie
        danach mit einem Hex-Editor oeffnet, findet den Mitschnitt wieder.
        """
        db = self.oeffnen()
        anzahl = int(db.execute("SELECT COUNT(*) FROM archiv").fetchone()[0])
        db.execute("DELETE FROM archiv")
        self.schliessen()
        for endung in ("", "-wal", "-shm"):
            try:
                Path(str(self.pfad) + endung).unlink()
            except OSError:
                pass
        return anzahl


def main(argv: list[str] | None = None) -> int:
    """`python -m daimon.recorder.store --loeschen` -- der eine Befehl."""
    import argparse
    import json

    zerleger = argparse.ArgumentParser(description="Archiv (T-7.1)")
    zerleger.add_argument("--loeschen", action="store_true",
                          help="alles vergessen, Datei entfernen")
    zerleger.add_argument("--aufraeumen", action="store_true",
                          help="Verfall und Verdraengung jetzt fahren")
    args = zerleger.parse_args(argv)

    a = Archiv()
    if args.loeschen:
        print(json.dumps({"geloescht": a.alles_loeschen(), "datei": str(a.pfad)}))
        return 0
    a.migrieren()
    bericht: dict[str, Any] = {"version": a.version(), "datei": str(a.pfad),
                               "bytes": a.belegung()}
    if args.aufraeumen:
        bericht["aufgeraeumt"] = a.aufraeumen()
    print(json.dumps(bericht, ensure_ascii=False))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
