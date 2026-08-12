"""T-5.7 -- der Kontextspeicher. Wahrgenommenes liegt fest und erreicht niemanden.

Dieses Modul hat GENAU EINEN Ausgang, und der verlangt einen Freigabeschein
aus `daimon.hub.declassify` (T-5.9). Dort entsteht er nur nach eingeloester
Rundenmarke aus Push-to-Talk und erkennbarem Bildschirmbezug (Design 7.2b).

Der Speicher prueft den Schein nicht nach -- er kann es nicht, er kennt keine
Marken. Er VERLANGT ihn, und das ist der Punkt: eine zweite Lesemethode „nur
fuer intern" waere genau die Tuer, die spaeter jemand benutzt, weil sie da
ist. Wer ohne Schein liest, bekommt eine Ausnahme, die den Weg nennt.

**Vier Aufbewahrungsstufen, nicht einheitlich zwanzig Eintraege.** Der Plan
hatte einen Wert fuer alles; Design 7.2d korrigiert das, und der Grund ist
nicht Ordnungsliebe:

    Fenstertitel        20 Eintraege, keine Frist   wenig verraeterisch
    OCR-Text             5 Eintraege, 15 Minuten    kann eine halbe Mail sein
    VLM-Beschreibung     3 Eintraege,  5 Minuten    fasst alles Sichtbare zusammen

Ein Fenstertitel sagt „Posteingang". Der OCR-Text derselben Ansicht sagt, von
wem die Mail ist und was drinsteht. Beides gleich lange zu halten, waere
bequem.

**`redacted` ist die Vorgabe, nicht `full`.** Die Stufen kommen aus Design
7.2d. Wer den vollen Text will, muss ihn benennen -- die umgekehrte Vorgabe
haette dieselbe Wirkung wie gar keine.

**Sensible Fenster kommen gar nicht erst herein.** Die Pruefung steht auch in
der Gatterkette (T-5.5), und das ist keine Verdopplung aus Versehen: dieser
Speicher bekommt auch Fenstertitel, die nie durch die Kette gelaufen sind.
Eine Zusage, die nur an einer Stelle geprueft wird, gilt nur fuer den Weg,
an den jemand gedacht hat.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from daimon.common.config import state_dir

ART_TITEL = "titel"
ART_OCR = "ocr"
ART_VLM = "vlm"

# (hoechstens so viele, Frist in Sekunden oder None). Aus Design 7.2d.
AUFBEWAHRUNG: dict[str, tuple[int, float | None]] = {
    ART_TITEL: (20, None),          # Sitzungsdauer
    ART_OCR: (5, 15 * 60.0),
    ART_VLM: (3, 5 * 60.0),
}

# Design 7.2d. Die Reihenfolge ist die der zunehmenden Preisgabe.
STUFE_TRANSIENT = "transient"            # nur im Arbeitsspeicher
STUFE_METADATA = "metadata_only"         # Herkunft und Zeit, kein Inhalt
STUFE_REDACTED = "redacted"              # Inhalt, gefiltert -- VORGABE
STUFE_FULL = "full"                      # vollstaendig, nur auf Anforderung
STUFEN = (STUFE_TRANSIENT, STUFE_METADATA, STUFE_REDACTED, STUFE_FULL)
VORGABE_STUFE = STUFE_REDACTED


class QuarantaeneFehler(RuntimeError):
    """Es wurde versucht, aus der Quarantaene zu lesen."""


def _redigieren(text: str) -> str:
    """Die Redaktion aus dem Egress-Broker, lokal geholt.

    Lokal und nicht oben im Modulkopf: wer `daimon/eyes/` nach `egress`
    durchsucht -- und das tut, wer die Zusage „die Augen haben keinen
    Netzweg" prueft -- bekaeme sonst einen Treffer und muesste erst
    nachsehen, dass es nur die Musterliste ist. Ein zweites Muster zu
    schreiben waere die schlechtere Antwort: dann kennt eine der beiden
    Listen den naechsten Schluesseltyp nicht.
    """
    from daimon.brokers.egress.broker import redigieren
    return redigieren(text)


@dataclass(frozen=True)
class Eintrag:
    art: str
    ts: float
    fenster: str
    stufe: str
    inhalt: str = ""

    def als_json(self) -> dict:
        return {"art": self.art, "ts": self.ts, "fenster": self.fenster,
                "stufe": self.stufe, "inhalt": self.inhalt}


class Kontextspeicher:
    """Ringe je Datenart, auf Platte, unter Quarantaene."""

    def __init__(self, *, verzeichnis: Path | None = None,
                 denylist: Iterable[str] = (),
                 stufe: str = VORGABE_STUFE,
                 uhr: Callable[[], float] = time.time) -> None:
        if stufe not in STUFEN:
            raise ValueError(f"Stufe {stufe!r} ist keine von {STUFEN}")
        self.verzeichnis = Path(verzeichnis or (state_dir() / "context"))
        self._denylist = {s.strip().lower() for s in denylist if s.strip()}
        self._stufe = stufe
        self._uhr = uhr
        self._ringe: dict[str, list[Eintrag]] = {a: [] for a in AUFBEWAHRUNG}
        self.ausgelassen = 0

    # -- Hereinnehmen ------------------------------------------------------

    def _inhalt(self, text: str) -> str:
        if self._stufe == STUFE_METADATA:
            return ""
        if self._stufe == STUFE_FULL:
            return text
        return _redigieren(text)

    def hinzufuegen(self, art: str, fenster: str, text: str) -> bool:
        """`True`, wenn der Eintrag aufgenommen wurde."""
        if art not in AUFBEWAHRUNG:
            raise ValueError(f"Art {art!r} ist keine von {tuple(AUFBEWAHRUNG)}")
        # Kleingeschrieben verglichen: KWin meldet `org.keepassxc.KeePassXC`,
        # der Nutzer schreibt `keepassxc`.
        if fenster.strip().lower() in self._denylist:
            self.ausgelassen += 1
            return False

        self._ringe[art].append(Eintrag(art=art, ts=self._uhr(), fenster=fenster,
                                        stufe=self._stufe,
                                        inhalt=self._inhalt(text)))
        self._beschneiden(art)
        if self._stufe != STUFE_TRANSIENT:
            self._schreiben(art)
        return True

    def _beschneiden(self, art: str) -> None:
        hoechstens, frist = AUFBEWAHRUNG[art]
        ring = self._ringe[art]
        if frist is not None:
            grenze = self._uhr() - frist
            ring = [e for e in ring if e.ts >= grenze]
        self._ringe[art] = ring[-hoechstens:]

    # -- Platte ------------------------------------------------------------

    def _datei(self, art: str) -> Path:
        return self.verzeichnis / f"{art}.json"

    def _schreiben(self, art: str) -> None:
        """Atomar, 0600. Der Modus steht VOR dem Umbenennen.

        Zwischen `write` und `chmod` laege sonst ein Fenster, in dem
        Bildschirmtext mit umask-Rechten dasteht.
        """
        self.verzeichnis.mkdir(parents=True, exist_ok=True)
        ziel = self._datei(art)
        tmp = ziel.with_suffix(".tmp")
        tmp.write_text(json.dumps([e.als_json() for e in self._ringe[art]],
                                  ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, ziel)

    def laden(self) -> None:
        """Liest die Ringe von der Platte und beschneidet sie sofort.

        Beschneiden beim LADEN und nicht erst beim naechsten Eintrag: sonst
        ueberlebte ein OCR-Text aus der letzten Sitzung seine 15 Minuten um
        beliebig lange, solange niemand etwas Neues hinzufuegt.
        """
        for art in AUFBEWAHRUNG:
            try:
                roh = json.loads(self._datei(art).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self._ringe[art] = [
                Eintrag(art=d.get("art", art), ts=float(d.get("ts", 0.0)),
                        fenster=str(d.get("fenster", "")),
                        stufe=str(d.get("stufe", VORGABE_STUFE)),
                        inhalt=str(d.get("inhalt", "")))
                for d in roh if isinstance(d, dict)]
            self._beschneiden(art)

    # -- Kill-Switch -------------------------------------------------------

    def leeren(self) -> int:
        """Arbeitsspeicher UND Platte. Gibt zurueck, wie viele Eintraege fielen.

        Beides, und in dieser Reihenfolge waere es egal -- nicht egal ist,
        dass keins von beiden fehlt: ein geleerter Ring vor einer vollen
        Datei sieht beim naechsten `laden()` aus wie nie geleert.
        """
        gefallen = sum(len(r) for r in self._ringe.values())
        for art in AUFBEWAHRUNG:
            self._ringe[art] = []
            try:
                self._datei(art).unlink()
            except OSError:
                pass
        return gefallen

    # -- Kein Ausgang ------------------------------------------------------

    def zaehler(self) -> dict[str, int]:
        """Wie viel liegt da. KEIN Inhalt -- das ist der Unterschied."""
        z = {art: len(self._ringe[art]) for art in AUFBEWAHRUNG}
        z["ausgelassen"] = self.ausgelassen
        return z

    def freigeben(self, schein: object = None) -> dict[str, list[Eintrag]]:
        """Der EINZIGE Ausgang, und er verlangt einen Schein.

        Der Schein kommt aus `daimon.hub.declassify` und entsteht dort nur
        nach einer eingeloesten Rundenmarke aus Push-to-Talk und einem
        erkennbaren Bildschirmbezug (Design 7.2b). Diese Klasse prueft ihn
        nicht selbst nach -- sie kann es nicht, sie kennt keine Marken.

        Sie verlangt ihn aber, und das ist der Punkt: ein Aufrufer, der
        einfach lesen will, bekommt eine Ausnahme, die den Weg nennt. Eine
        zweite Lesemethode „nur fuer intern" waere genau die Tuer, die
        spaeter jemand benutzt, weil sie da ist.
        """
        if getattr(schein, "turn_id", "") == "":
            raise QuarantaeneFehler(
                "Kontext verlaesst die Quarantaene nur durch das "
                "Deklassifizierungs-Gate (T-5.9), und nur unter einer "
                "Rundenmarke aus Push-to-Talk. Ohne Freigabeschein nicht.")
        return {art: list(ring) for art, ring in self._ringe.items()}
