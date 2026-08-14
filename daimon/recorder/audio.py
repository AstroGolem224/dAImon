"""T-7.4 -- Gesprochenes wird auffindbar, ohne dass Rohaudio liegen bleibt.

**Dieses Modul kann kein Audio annehmen, und das ist seine Zusage.** Es gibt
keinen Parameter dafuer -- weder Pfad noch Puffer. Wer Rohaudio ins Archiv
bekommen will, muss die Signatur aendern, und das faellt in einem Diff auf.
Eine Pruefung „ist das Audio?" waere schwaecher: sie liesse sich umgehen,
diese Form nicht.

**Der Abzweig sitzt im Ohren-Dienst, nach der Transkription.** Der hat den
Text ohnehin in der Hand. Ein zweiter STT-Aufruf waere ein zweites Modell im
Speicher, ein eigener Mikrofonpfad im Recorder waere genau das, was er nie
haben soll -- und beides haenge nicht mehr am selben Strom.

**Was hier archiviert wird, ist ausschliesslich Gesprochenes unter
Push-to-Talk.** Das ist KEIN Dauermitschnitt des Tons, und der
Implementierungsplan sagt an dieser Stelle etwas anderes als Design §1.1.
Der Widerspruch ist zugunsten von §1.1 aufgeloest: „kein Mikrofon ohne
Push-to-Talk" ist die aeltere und die schaerfere Zusage, und ein
Dauerlauschen waere eine Designentscheidung und kein Task. Wer §1.2
woertlich will, aendert zuerst §1.1 -- und beantwortet dabei §201 StGB.

**Der Melder haelt den Sprachpfad nie auf.** Ein Timeout von einer Sekunde,
und jeder Fehler ist ein Rueckgabewert, keine Ausnahme: ein Archiv, das eine
Antwort verzoegert, waere ein schlechter Handel.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

from daimon.common import ipc
from daimon.recorder.store import ART_TRANSKRIPT

# Ein Transkript ist eine Zeile, kein Vortrag. Was darueber liegt, ist im
# Zweifel kein Gesprochenes mehr -- gekuerzt statt abgewiesen, weil ein
# verlorener Satz schlechter ist als ein gekuerzter.
MAX_ZEICHEN = 8000

PRODUZENT = "recorder"


def melde_transkript(runtime_dir: Path, text: str, *, marke: str = "",
                     timeout_s: float = 1.0) -> dict:
    """Ein Transkript ins Archiv. Gibt die Antwort des Recorders zurueck.

    KEIN Audio-Parameter -- siehe Modulkopf. `marke` ist die Herkunftsmarke
    der Runde (`user_ptt`/`user_audio`) und wandert ins Feld `fenster`: im
    Archiv steht damit, WORAUS der Text stammt, ohne dass ein Fenstertitel
    erfunden wird.
    """
    sauber = str(text).strip()[:MAX_ZEICHEN]
    if not sauber:
        return {"ok": False, "grund": "leer"}

    pfad = ipc.socket_path(Path(runtime_dir), PRODUZENT)
    nachricht = {"v": 1, "typ": "archiv", "art": ART_TRANSKRIPT,
                 "text": sauber, "fenster": str(marke)[:40],
                 "klasse": ""}
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout_s)
        c.connect(str(pfad))
        with c:
            c.sendall((json.dumps(nachricht, ensure_ascii=False) + "\n")
                      .encode())
            antwort = c.makefile("r").readline()
    except (OSError, socket.timeout):
        # Kein Recorder, keine Aufzeichnung -- und der Sprachpfad merkt
        # nichts davon. Genau so ist die Pause aus T-7.3 wirksam: sie stoppt
        # die Unit, und danach laeuft hier alles ins Leere.
        return {"ok": False, "grund": "kein_recorder"}
    try:
        return json.loads(antwort)
    except (ValueError, TypeError):
        return {"ok": False, "grund": "unlesbar"}
