"""T-3.9 -- die Abkuehlung der Stimme. Persistiert.

Referenz des Reviewers, blind gegen die Akzeptanzliste geschrieben.

20 s ungefragt, 10 s Reaktion, 3 s Rueckfrage -- je Anlass. Vermerkt wird
am ENDE der Wiedergabe, nicht am Anfang: sonst laufen 20 s Abkuehlung
waehrend eines 4 s langen Satzes schon zur Haelfte ab.

Die Zeitpunkte sind MONOTON (time.monotonic), nicht Wanduhr: eine
NTP-Korrektur oder Zeitumstellung darf eine Abkuehlung weder aufheben
noch erzeugen. Monoton ueberlebt einen Prozessneustart (die Uhr laeuft
seit Boot), deshalb kann der Bestand so persistiert werden wie er ist.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

_FORMAT_VERSION = 1


class Abkuehlung:
    """Abkuehlung je Anlass, atomar persistiert nach dem tickets.py-Muster."""

    def __init__(self, pfad: Path, *, cfg=None, log=None,
                 jetzt=time.monotonic) -> None:
        self._pfad = Path(pfad)
        self._fristen = {"ungefragt": 20.0, "reaktion": 10.0, "rueckfrage": 3.0}
        if cfg is not None:
            for kanal in list(self._fristen):
                wert = cfg.get(f"tts.abkuehlung_s.{kanal}")
                if isinstance(wert, (int, float)):
                    self._fristen[kanal] = float(wert)
        self._jetzt = jetzt
        self._log = log
        self._lock = threading.Lock()
        self._bis: dict[str, float] = {}
        self._laden()

    def _laden(self) -> None:
        try:
            roh = self._pfad.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            daten = json.loads(roh)
            bis = daten["bis"]
            if daten.get("v") != _FORMAT_VERSION or not isinstance(bis, dict):
                raise ValueError("fremdes format")
        except (ValueError, KeyError, TypeError):
            # Eine kaputte Datei ist ein leerer Bestand -- die sichere
            # Richtung waere "alles gesperrt", aber eine Abkuehlung ist
            # Hoeflichkeit, keine Schranke. Sichtbar bleibt es im Log.
            if self._log is not None:
                self._log.warn("tts-abkuehlung: datei beschaedigt, leer gestartet")
            return
        self._bis = {str(k): float(v) for k, v in bis.items()}

    def _schreiben(self) -> None:
        """Atomar: temporaere Datei im selben Verzeichnis, flush, fsync,
        os.replace, fsync aufs Verzeichnis -- das Muster aus tickets.py."""
        nutzlast = json.dumps(
            {"v": _FORMAT_VERSION, "bis": self._bis},
            sort_keys=True,
        ).encode("utf-8")
        verzeichnis = self._pfad.parent
        verzeichnis.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=verzeichnis,
                                        prefix=self._pfad.name + ".",
                                        suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(nutzlast)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self._pfad)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        dir_fd = os.open(verzeichnis, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def darf(self, kanal: str) -> tuple[bool, float]:
        """(True, 0.0) wenn gesprochen werden darf, sonst (False, rest_s)."""
        with self._lock:
            bis = self._bis.get(kanal)
            if bis is None:
                return True, 0.0
            rest = bis - self._jetzt()
            if rest <= 0.0:
                return True, 0.0
            return False, round(rest, 3)

    def vermerke(self, kanal: str) -> float:
        """Vermerkt eine Wiedergabe (am ENDE). Rueckgabe: der Sperr-Zeitpunkt
        in monotoner Zeit."""
        frist = self._fristen.get(kanal, 10.0)
        with self._lock:
            bis = self._jetzt() + frist
            self._bis[kanal] = bis
            self._schreiben()
            return bis
