"""T-7.5 -- Fragen an die eigene Vergangenheit, ohne sie zur Angriffsflaeche
zu machen.

**Dieses Modul verlangt einen Freigabeschein und prueft ihn nicht nach.**
Genau wie der Kontextspeicher aus T-5.7: es KANN ihn nicht pruefen, es kennt
keine Marken. Es verlangt ihn, und das ist der Punkt -- eine zweite
Lesemethode „nur fuer intern" waere die Tuer, die spaeter jemand benutzt,
weil sie da ist. Ausgestellt wird der Schein allein in
`daimon.hub.declassify`, nach eingeloester Rundenmarke.

**Die Datenbank wird READ-ONLY geoeffnet, als URI mit `mode=ro`.** Der
Recorder ist der einzige Schreiber (T-7.1); dass die Suche nicht schreibt,
ist damit keine Zusage im Text, sondern eine Eigenschaft der Verbindung. Ein
`INSERT` von hier scheitert am SQLite, nicht an einer Pruefung.

**Nur der Treffer, nicht die Umgebung.** Zurueck kommen die gefundenen
Zeilen, nicht die Eintraege davor und danach. Wer den Kontext eines Treffers
will, sucht danach -- unter einer neuen Rundenmarke.

**Jeder Treffer ist `tainted`.** Er stammt vom Bildschirm oder aus einem
Mikrofon und wird nicht dadurch vertrauenswuerdig, dass er den Umweg ueber
die eigene Datenbank genommen hat.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from daimon.common.config import data_dir
from daimon.common.protocol import Mark, Marked
from daimon.recorder.store import DATEI

# Ein Suchergebnis ist eine Handvoll Zeilen, kein Bericht. Was das Modell
# nicht lesen wird, muss auch nicht durch das Gate.
HOECHSTENS = 5


def suchbegriff(aeusserung: str, *, hoechstens_woerter: int = 6) -> str:
    """Aus der Frage einen FTS5-Begriff machen -- entschaerft.

    Jedes Wort wird in Anfuehrungszeichen gesetzt und mit `OR` verbunden.
    Das ist Absicht: FTS5 kennt eine eigene Abfragesprache mit `NEAR`,
    Praefixen und Spaltenfiltern, und die Aeusserung kommt aus einem
    Mikrofon. Zitiert ist jedes Wort ein Wort und kein Operator.
    """
    woerter = [w.strip('"').strip() for w in str(aeusserung).split()]
    woerter = [w for w in woerter if len(w) > 3][:hoechstens_woerter]
    return " OR ".join(f'"{w}"' for w in woerter)


class QuarantaeneFehler(RuntimeError):
    """Gelesen wurde ohne Freigabeschein."""


class Archivsuche:
    """Volltextsuche ueber das Archiv. Ein Ausgang, und der braucht einen
    Schein."""

    def __init__(self, pfad: Path | None = None) -> None:
        self.pfad = Path(pfad or (data_dir() / DATEI))

    def _lesen_nur(self) -> sqlite3.Connection:
        db = sqlite3.connect(f"file:{self.pfad}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def freigeben(self, schein: object, anfrage: str, *,
                  hoechstens: int = HOECHSTENS) -> list[Marked]:
        """Treffer zu `anfrage`, oder `QuarantaeneFehler`.

        `schein` wird auf seinen TYP geprueft und nicht auf Wahrheit: ein
        `True` entsteht aus jedem Versehen, ein `Freigabeschein` nur dort,
        wo das Gate ihn herstellt.
        """
        if type(schein).__name__ != "Freigabeschein":
            raise QuarantaeneFehler(
                "Das Archiv gibt nichts ohne Freigabeschein heraus. Der Weg "
                "fuehrt ueber daimon.hub.declassify.Deklassifizierung."
                "freigeben() -- Rundenmarke aus Push-to-Talk und erkennbarer "
                "Bezug.")

        # Die Aeusserung ist ein SATZ und keine Abfrage. Roh weitergereicht
        # waere sie in FTS5 ein implizites UND ueber alle Woerter -- „was
        # stand vorhin auf dem Bildschirm von Weizenbaum" faende nur eine
        # Zeile, in der auch „vorhin" und „stand" steht, also keine.
        # `suchbegriff` macht daraus zitierte Woerter mit ODER und nimmt
        # dabei den Operatoren ihre Wirkung.
        begriff = suchbegriff(anfrage)
        if not begriff or not self.pfad.exists():
            return []
        try:
            db = self._lesen_nur()
        except sqlite3.Error:
            return []
        try:
            zeilen = db.execute(
                "SELECT a.art, a.ts, a.fenster, a.text "
                "FROM archiv_fts f JOIN archiv a ON a.id = f.rowid "
                "WHERE archiv_fts MATCH ? ORDER BY rank LIMIT ?",
                (begriff, int(hoechstens))).fetchall()
        except sqlite3.Error:
            # Ein Suchbegriff mit FTS5-Syntaxfehler ist eine Nutzereingabe --
            # und zwar eine, die ueber ein Mikrofon hereinkam. Kein Treffer
            # ist die richtige Antwort, kein Absturz des Hubs.
            return []
        finally:
            db.close()

        return [Marked(f"[{z['art']} {z['fenster']}] {z['text']}",
                       Mark.TAINTED) for z in zeilen]


