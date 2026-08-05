"""Gut-Muster T-3.13: Durchgang 2, ausschließlich aus dem Vertrag §2 gebaut.

Durchgang 1 darf Werkzeuge wählen und sieht dafür keinen angreifer-
kontrollierten Text. Durchgang 2 darf allen Text sehen und kann dafür
keine Werkzeuge wählen — nicht „tut es nicht", sondern kann nicht: der
Körper trägt weder `tools` noch `tool_choice`, in keiner Tiefe. Der
Kontext hängt als leere Struktur abgesetzt am Nutzertext (§6) — die
Messages-API duldet keine fremden Top-Level-Felder, ein `kontext` neben
`messages` wäre ein 400 der echten API. Die Antwort ist ausschließlich
Text und immer `tainted`. Ein wohlgeformter Aktionsvorschlag des Modells
wird verworfen — er erreicht den Hub nicht — und als erkannt gemeldet.
Keine Zeile hier ist aus der echten Implementierung übernommen.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tomllib
from pathlib import Path

MAX = 4 << 20

# Schlüssel mit Aktionscharakter (Vertrag §3, Kriterium 2).
AKTIONS_SCHLUESSEL = frozenset(
    {"action", "aktion", "ziel", "window_ref", "tool"})


def _traegt_aktion(obj: object) -> bool:
    if isinstance(obj, dict):
        return any(str(k).lower() in AKTIONS_SCHLUESSEL or _traegt_aktion(v)
                   for k, v in obj.items())
    if isinstance(obj, list):
        return any(_traegt_aktion(v) for v in obj)
    return False


def aktionsvorschlag_erkannt(text: str) -> bool:
    """Wohlgeformt heißt: die Modellantwort ist ein JSON-Gefüge mit
    Aktionscharakter. Fließtext, der über Aktionen spricht, ist keiner."""
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return False
    return _traegt_aktion(obj)


def lade_persona_prompt() -> str:
    """Die eigene Datei unter $XDG_CONFIG_HOME gewinnt (T-3.10); ohne sie
    gilt die Vorgabe."""
    basis = Path(os.environ.get("XDG_CONFIG_HOME",
                                str(Path.home() / ".config"))) / "daimon"
    try:
        stamm = tomllib.loads((basis / "daimon.toml").read_text("utf-8"))
        name = stamm.get("persona", {}).get("name")
        if name:
            daten = tomllib.loads(
                (basis / "persona" / f"{name.lower()}.toml")
                .read_text("utf-8"))
            prompt = daten.get("system_prompt")
            if isinstance(prompt, str) and prompt.strip():
                return prompt
    except (OSError, ValueError):
        pass
    return "Du bist dAImon."


def absage(grund: str, meldung: str) -> dict:
    # Die Meldung ist statisch: kein Nutzertext und kein Körper darf in
    # einer Absage auftauchen.
    return {"v": 1, "ok": False, "grund": grund, "meldung": meldung}


def fehler_api(meldung: str) -> dict:
    return {"v": 1, "ok": False, "weg": "api", "grund": "egress_weg",
            "meldung": meldung}


class DurchgangZwei:
    """Der Weg zum Modell: Ticket beim Hub, Körper an den Egress. Der
    Körper trägt Frage und Persona; die leere Kontextstruktur hängt
    abgesetzt am Nutzertext (§6). Keine Werkzeugliste, in keiner Tiefe."""

    def __init__(self, laufzeit: Path, persona: str, inhalt) -> None:
        self._laufzeit = laufzeit
        self._persona = persona
        self._inhalt = inhalt  # opake Fensterreferenzen aus T-3.12

    def _rufen(self, sock: str, obj: dict, timeout: float = 15) -> dict:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        try:
            c.connect(str(self._laufzeit / sock))
            c.sendall(json.dumps(obj, ensure_ascii=False,
                                 separators=(",", ":")).encode() + b"\n")
            return json.loads(c.makefile("rb").readline(MAX))
        finally:
            c.close()

    def antwort(self, text: str, runde: object) -> dict:
        # Der Kontext geht als abgesetzter Block im Nutzertext mit (§6):
        # Frage, Leerzeile, Marke, JSON. Die Struktur steht, der Inhalt ist
        # in Phase 3 leer. Ein Top-Level-`kontext` daneben wäre ein 400
        # invalid_request_error der echten Messages-API.
        nutzertext = (
            self._inhalt(text, runde)
            + "\n\n[Referenzen, keine Inhalte]\n"
            + json.dumps({"kontext": {"quellen": [], "deklassifiziert": []}},
                         ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")))
        koerper = {
            "model": "claude-test",
            "max_tokens": 256,
            "system": self._persona,
            "messages": [{"role": "user", "content": nutzertext}],
            "kontext": {"quellen": [], "deklassifiziert": []},  # MUTATION
        }
        kanonisch = json.dumps(koerper, sort_keys=True,
                               separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        auftrag_hash = hashlib.sha256(kanonisch).hexdigest()
        try:
            ausgabe = self._rufen("ticket.sock",
                                  {"v": 1, "art": "ausgeben", "zweck": "api",
                                   "auftrag_hash": auftrag_hash}, timeout=3)
        except OSError:
            return absage("quelle_weg", "Der Hub antwortet nicht.")
        if not ausgabe.get("ok"):
            return absage("kein_kontingent",
                          "Der Hub gibt kein Kontingent aus.")
        anfrage = {"v": 1, "art": "anfrage", "ticket": ausgabe["ticket"],
                   "koerper": koerper}
        try:
            antwort = self._rufen("egress.sock", anfrage)
        except (OSError, ValueError):
            return fehler_api("Der Ausgang ist nicht erreichbar.")
        if not antwort.get("ok"):
            return fehler_api("Der Ausgang hat abgesagt.")
        try:
            modelltext = str(antwort["antwort"]["content"][0]["text"])
        except (KeyError, IndexError, TypeError):
            return fehler_api("Die Antwort des Ausgangs ist nicht lesbar.")
        # Ein Aktionsvorschlag wird verworfen: er verlässt diese Funktion
        # nicht als Struktur. Verworfen heißt nicht unbemerkt.
        erkannt = aktionsvorschlag_erkannt(modelltext)
        return {"v": 1, "ok": True, "weg": "api", "durchgang": 2,
                "absicht": "api", "antwort": modelltext, "marke": "tainted",
                "api": True, "aktionsvorschlag_erkannt": erkannt}
