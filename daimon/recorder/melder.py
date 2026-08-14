"""T-7.1b -- die Absender. Wer das Archiv fuellt, und wie er es nicht merkt.

**Ein Melder haelt seinen Dienst nie auf.** Kurzes Zeitlimit, jeder Fehler
ein Rueckgabewert statt einer Ausnahme. Ist der Recorder pausiert (T-7.3)
oder gar nicht da, laeuft die Meldung ins Leere -- und Wahrnehmung wie
Sprachpfad merken nichts davon. Ein Archiv, das die Wahrnehmung verzoegert,
waere ein schlechter Handel.

**Jede Meldung traegt die Kennung mit.** Ohne `resource_class` kann die
Redaktion aus T-7.2 nicht urteilen, und sie sperrt Unbekanntes -- ein
Melder, der die Klasse weglaesst, archiviert also gar nichts. Das ist die
richtige Richtung des Irrtums, aber eine Fehlersuche wert: sie steht hier.

**Wer meldet was, und warum nicht anders:**

    OCR-Text     Augendienst   er hat ihn, er hat auch die Klasse
    Fenstertitel Fokusdienst   die Augen bekommen den Titel ABSICHTLICH
                               nicht (er ist Angreifertext, siehe
                               `focus.fenster()`); ihn fuers Archiv durch
                               den Prozess zu schleusen, der zuschneidet
                               und OCRt, waere eine neue Flaeche
    Transkript   Ohren-Dienst  eigener Melder in `audio.py`, ohne jeden
                               Audio-Parameter
    Frames       niemand       Schema und Verfall stehen (T-7.1), ein
                               Produzent ist ein eigener Task
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

from daimon.common import ipc
from daimon.recorder.store import ART_OCR, ART_TITEL

PRODUZENT = "recorder"

# Ein OCR-Ausschnitt ist Text, kein Buch. Gekuerzt statt abgewiesen: eine
# halbe Seite ist besser als nichts, und `MAX_ZEILE` im Dienst deckelt
# ohnehin die Zeile.
MAX_ZEICHEN = 64000


def senden(runtime_dir: Path, nachricht: dict, *,
           timeout_s: float = 1.0) -> dict:
    """Eine Zeile hin, eine zurueck. Fehler sind Rueckgabewerte."""
    pfad = ipc.socket_path(Path(runtime_dir), PRODUZENT)
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout_s)
        c.connect(str(pfad))
        with c:
            c.sendall((json.dumps(nachricht, ensure_ascii=False) + "\n")
                      .encode())
            antwort = c.makefile("r").readline()
    except (OSError, socket.timeout):
        return {"ok": False, "grund": "kein_recorder"}
    try:
        return json.loads(antwort)
    except (ValueError, TypeError):
        return {"ok": False, "grund": "unlesbar"}


def melde_ocr(runtime_dir: Path, text: str, *, klasse: str,
              drm: bool = False, timeout_s: float = 1.0) -> dict:
    """Gelesener Bildschirmtext ins Archiv."""
    sauber = str(text).strip()[:MAX_ZEICHEN]
    if not sauber:
        return {"ok": False, "grund": "leer"}
    return senden(runtime_dir, {"v": 1, "typ": "archiv", "art": ART_OCR,
                                "text": sauber, "klasse": str(klasse),
                                "drm": bool(drm)}, timeout_s=timeout_s)


def melde_titel(runtime_dir: Path, titel: str, *, klasse: str,
                timeout_s: float = 1.0) -> dict:
    """Ein Fenstertitel ins Archiv.

    Der Titel wandert in `text` und NICHT in `fenster`: gesucht werden soll
    er (T-7.5), und der Volltextindex haengt an beiden Spalten -- aber die
    Art `titel` sagt, was er ist. Die Kennung kommt getrennt mit, damit die
    Denylist greift: der Titel eines gelisteten Passwortmanagers ist genauso
    verraeterisch wie sein Inhalt.
    """
    sauber = str(titel).strip()[:MAX_ZEICHEN]
    if not sauber:
        return {"ok": False, "grund": "leer"}
    return senden(runtime_dir, {"v": 1, "typ": "archiv", "art": ART_TITEL,
                                "text": sauber, "klasse": str(klasse),
                                "fenster": str(klasse)}, timeout_s=timeout_s)
