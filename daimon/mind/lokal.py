"""Der lokale Weg zum Modell -- die EINE Fassung seiner Rumpfumsetzung.

Warum es diese Datei gibt
----------------------------------------------------------------------------
`daimon-lokal-broker` laeuft seit Monaten, spricht Ollama auf
`127.0.0.1:11434` und hatte bis zum 26.08. **keinen Verbraucher fuer
Werkzeuge**. Der siebte Fall des Musters aus CLAUDE.md: gebaut, geprueft,
und im Betrieb ruft es niemand auf.

Angeschlossen wird er nicht, indem irgendwer an ihm vorbei mit Ollama
spricht -- an `lokal.sock` haengen die Loopback-Grenze (`ZIEL` fest
verdrahtet) und die Kontingente ueber `ticket.sock`. Er wird angeschlossen,
indem die Weiche im Mind auf ihn zeigt (`daimon/mind/daemon.py: main`) und
die Rumpfumsetzung EINMAL existiert -- hier.

Warum hier und nicht im Broker
----------------------------------------------------------------------------
Zwei Fassungen einer Regel sind eine Regel und eine Attrappe (CLAUDE.md
Regel 4). Die Umsetzung gehoert zur Zusage "ich frage lokal", und die macht
der Mind. Der Broker WENDET sie an, weil er den Draht haelt -- er hat keine
eigene. Wer hier etwas aendert, aendert es fuer beide Seiten zugleich, und
genau das ist der Zweck.

Gemessen, nicht vermutet (26.08., `qwen3.8-heretic:27b-96k`, echter Katalog
aus `Policy.laden()`): mit dieser Umsetzung ruft das Modell 12/12 das
richtige Werkzeug, erfindet in 36 Laeufen keinen Namen und loest in 30
Gegenproben nicht falsch aus. Belegt in `docs/AUFGABE-egress-alternativen.md`.

Was NICHT hier steht
----------------------------------------------------------------------------
Kein Rueckfall auf den Egress. Ein Weg je Anfrage -- ein stiller zweiter
Versuch waere eine zweite Frage an ein zweites Modell, und welche Antwort
der Nutzer hoert, entschiede der Zufall der Zeitueberschreitung.
"""

from __future__ import annotations

import json

# Der Socket des lokalen Brokers. Derselbe Name wie in
# `daimon/brokers/lokal/broker.py` -- hier steht er, damit der Mind ihn
# benennen kann, ohne das Broker-Modul zu importieren (der Mind laeuft mit
# `RestrictAddressFamilies=AF_UNIX`, das Broker-Modul zieht `urllib` nach).
LOKAL_SOCKET = "lokal.sock"


def _text_von(inhalt: object) -> str:
    """Anthropic laesst `content` auch als Blockliste zu, Ollama nicht.

    Nur `text`-Bloecke: ein `tool_result`-Block hat in einer Ollama-Nachricht
    keine Entsprechung, und ihn stumm als Text mitzuschicken waere eine
    Umdeutung.
    """
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        return "".join(t.get("text", "") for t in inhalt
                       if isinstance(t, dict) and t.get("type", "text") == "text")
    return ""


def werkzeuge_umsetzen(werkzeuge: object) -> list[dict]:
    """Anthropic-Werkzeuge in die Form, die Ollamas `/api/chat` annimmt.

    `input_schema` wandert UNVERAENDERT nach `parameters`. Das ist der ganze
    Unterschied, und er muss unveraendert sein: das Schema ist dieselbe
    Schranke, die `Policy._params_pruefen` durchsetzt (siehe
    `daimon/mind/daemon.py: _tool_schema`). Wer hier umschriebe, haette eine
    zweite Schranke -- und die Aktion scheiterte an der ersten.
    """
    umgesetzt: list[dict] = []
    for w in (werkzeuge or []):
        if not isinstance(w, dict) or not w.get("name"):
            continue
        umgesetzt.append({"type": "function", "function": {
            "name": str(w["name"]),
            "description": str(w.get("description") or ""),
            "parameters": w.get("input_schema") or {"type": "object",
                                                    "properties": {}},
        }})
    return umgesetzt


def anfrage_rumpf(koerper: dict, *, modell: str,
                  num_predict: int | None = None) -> dict:
    """Ein Anthropic-Messages-Koerper als Ollama-Chat-Koerper.

    Rein und ohne Netz, damit ein Pruefstand ihn pruefen kann, ohne ein
    Modell zu laden.

    `system` wird zur ERSTEN Nachricht mit `role: "system"` -- Ollama kennt
    kein eigenes Feld dafuer. Woertlich: T-3.10 gibt die Persona unveraendert
    weiter, und ein lokales Modell ist kein Grund, davon abzuweichen.

    `think: false` ist keine Feinjustierung, sondern der Unterschied zwischen
    einer Antwort und keiner (09.08. an gemma4:26b gemessen: mit Denkspur lief
    `num_predict` im `thinking`-Feld leer, `content` kam LEER zurueck).
    """
    nachrichten: list[dict] = []
    system = koerper.get("system")
    if isinstance(system, str) and system.strip():
        nachrichten.append({"role": "system", "content": system})
    for nachricht in koerper.get("messages") or []:
        if not isinstance(nachricht, dict):
            continue
        inhalt = _text_von(nachricht.get("content"))
        if inhalt.strip():
            nachrichten.append({"role": str(nachricht.get("role", "user")),
                                "content": inhalt})
    if num_predict is None:
        num_predict = int(koerper.get("max_tokens") or 0) or None
    rumpf: dict = {"model": modell, "messages": nachrichten, "stream": False,
                   "think": False}
    if num_predict:
        rumpf["options"] = {"num_predict": int(num_predict)}
    werkzeuge = werkzeuge_umsetzen(koerper.get("tools"))
    if werkzeuge:
        rumpf["tools"] = werkzeuge
    return rumpf


def _argumente(roh: object) -> dict:
    """Ollama liefert `arguments` als Objekt. Ein String wird trotzdem
    abgefangen -- nicht weil es gemessen waere, sondern weil ein `str` hier
    ungeprueft als `params` an `aktion.sock` ginge und der Koordinator dann
    ueber die Form staenge statt ueber die Sache."""
    if isinstance(roh, dict):
        return roh
    if isinstance(roh, str):
        try:
            geparst = json.loads(roh)
        except (json.JSONDecodeError, ValueError):
            return {}
        return geparst if isinstance(geparst, dict) else {}
    return {}


def antwort_bloecke(nachricht: object) -> list[dict]:
    """Ollamas `message` als die Blockliste, die `Mind.frage_werkzeug` liest.

    Genau zwei Blockarten, dieselben zwei, die der Mind kennt:
    `{"type":"text"}` und `{"type":"tool_use"}`. Ein leeres `content` erzeugt
    KEINEN leeren Textblock -- ein Modell, das nur ein Werkzeug ruft, sagt
    nichts, und ein leerer Block waere eine Aeusserung, die es nie gab (er
    landete sonst als leere Runde im Kurzzeitgedaechtnis).
    """
    if not isinstance(nachricht, dict):
        return []
    bloecke: list[dict] = []
    text = nachricht.get("content")
    if isinstance(text, str) and text.strip():
        bloecke.append({"type": "text", "text": text.strip()})
    for ruf in (nachricht.get("tool_calls") or []):
        if not isinstance(ruf, dict):
            continue
        fn = ruf.get("function")
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        bloecke.append({"type": "tool_use", "name": str(fn["name"]),
                        "input": _argumente(fn.get("arguments")),
                        "id": str(ruf.get("id") or "")})
    return bloecke
