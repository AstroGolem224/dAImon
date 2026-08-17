"""T-3.13 — Routing, Durchgang 2: kontextfaehig, werkzeuglos.

Der Unterschied zu Durchgang 1, in einem Satz
----------------------------------------------------------------------------
Durchgang 1 darf Werkzeuge waehlen und sieht dafuer keinen
angreiferkontrollierten Text. Durchgang 2 darf allen Text sehen und **kann
dafuer keine Werkzeuge waehlen** -- nicht "tut es nicht", sondern kann nicht,
weil das Schema keines traegt. Ein Feld, das eine Aktion transportieren
koennte, gibt es hier nicht; wer eines hinzufuegt, hebt den ganzen
Zwei-Durchgang-Schnitt auf.

Warum ein erkannter Vorschlag GEMELDET wird
----------------------------------------------------------------------------
Ein Modell, das trotzdem `{"action": "close_window"}` schreibt, wird nicht
still gefiltert. Ein stiller Filter ist von "das Modell hat keinen Vorschlag
gemacht" nicht zu unterscheiden -- und dann fehlt die Messung, mit der man
merkt, dass es das versucht. `aktionsvorschlag_erkannt` ist genau dieser
Messpunkt. Der Text selbst bleibt erhalten und `tainted`; ob er gesprochen
wird, entscheidet der Validator aus Design 8.3.

Warum die Antwort IMMER tainted ist
----------------------------------------------------------------------------
Design 5.2: "Aus einem Modell kommt nichts Vertrauenswuerdiges in Textform."
Auch wenn die Frage `user_ptt` war -- die Marke beschreibt die Herkunft der
ANTWORT, und die kommt aus dem Modell. Eine Antwort, die `trusted` hiesse,
waere ein Modellausgabe-Pfad ohne Markierung, und davon darf es keinen geben.
"""

from __future__ import annotations

import json
import re
from typing import Any

DURCHGANG = 2

# Die Struktur steht, der Inhalt ist in Phase 3 leer. `quellen` fuellt sich in
# P5 mit deklassifiziertem Kontext -- und wer das tut, muss belegen, dass jedes
# Stueck seine Markierung mitbringt.
LEERER_KONTEXT: dict[str, list] = {"quellen": [], "deklassifiziert": []}

# Was nach einem Aktionsvorschlag aussieht. Absichtlich breit und absichtlich
# NUR zur Erkennung: hier wird nichts ausgefuehrt und nichts weitergereicht.
# Ein Muster, das nur `"action"` kennt, laesst die deutsche Schreibweise durch,
# und eines, das nur JSON kennt, den Codeblock drumherum.
_VORSCHLAG = re.compile(
    r'"(action|aktion|tool|werkzeug|command|befehl)"\s*:\s*"'
    r'|"(window_ref|ziel|target)"\s*:\s*"',
    re.IGNORECASE)


def aktionsvorschlag(text: str) -> bool:
    """Sieht der Antworttext nach einem Aktionsvorschlag aus?"""
    return bool(_VORSCHLAG.search(text or ""))


def nur_text(antwort: Any) -> str:
    """Die Modellantwort auf TEXT reduzieren.

    Die API liefert `{"content": [{"type": "text", "text": ...}]}`. Was hier
    ankommt, kann aber auch ein roher Block sein oder etwas Unerwartetes -- und
    dann wird es zu Text und nicht zu Struktur. Ein Durchgang, der eine
    Struktur durchreicht, reicht irgendwann auch eine Aktion durch.
    """
    if isinstance(antwort, str):
        return antwort
    if isinstance(antwort, dict):
        inhalt = antwort.get("content")
        if isinstance(inhalt, list):
            stuecke = [str(s.get("text", "")) for s in inhalt
                       if isinstance(s, dict) and s.get("type") == "text"]
            if stuecke:
                return "\n".join(stuecke)
        if isinstance(antwort.get("roh"), str):
            return antwort["roh"]
    return json.dumps(antwort, ensure_ascii=False)


class Durchgang2:
    """Kontextfaehig, werkzeuglos. Gibt Text zurueck und sonst nichts.

    Er sitzt an derselben Stelle wie vorher `Mind.frage_api` und wird vom
    Router genau dann gerufen, wenn die Absicht `api` ist. Die Schnittstelle
    ist deshalb dieselbe -- der Router muss nicht wissen, wie viele Durchgaenge
    es gibt.
    """

    def __init__(self, *, mind, log=None) -> None:
        self._mind = mind
        self._log = log
        self.anfragen = 0

    # Der Router ruft `frage_api`; der Name bleibt, damit T-3.12 unveraendert
    # bleibt und sein eingefrorener Pruefstand weiter misst, was er gemessen
    # hat.
    def frage_api(self, frage: str, kontext: dict | None = None) -> dict:
        return self.beantworte(frage, marke="user_ptt", kontext=kontext)

    def beantworte(self, frage: str, *, marke: str = "user_ptt",
                   kontext: dict | None = None) -> dict:
        # `user_audio` ist HIER erlaubt (Design 5.2, Senkentabelle). Die
        # Ablehnung sitzt in Durchgang 1 -- und genau deshalb hat dieser
        # Durchgang keine Werkzeuge: eine gespoofte Aeusserung darf eine Frage
        # beantworten lassen, mehr nicht.
        mit = dict(LEERER_KONTEXT)
        if kontext:
            # Was der Router mitgibt (Referenzen aus T-3.12), wandert unter
            # `quellen` -- ohne Titel, ohne Inhalt.
            mit = {"quellen": [], "deklassifiziert": [], **kontext}

        antwort = self._mind.frage_api(frage, {"kontext": mit})
        if not antwort.get("ok"):
            grund = str(antwort.get("grund", ""))
            if grund in ("kein_kontingent", "kontingent_fenster"):
                return {"v": 1, "ok": False, "weg": "api",
                        "durchgang": DURCHGANG, "grund": "kein_kontingent",
                        "meldung": grund[:200], "api": False,
                        "antwort": "Ich habe gerade kein Kontingent.",
                        "marke": "trusted"}
            return {"v": 1, "ok": False, "weg": "api", "durchgang": DURCHGANG,
                    "grund": "egress_weg",
                    "meldung": (grund or "Egress hat abgelehnt")[:200],
                    "api": False,
                    "antwort": "Ich komme gerade nicht an die API.",
                    "marke": "trusted"}

        self.anfragen += 1
        text = nur_text(antwort.get("antwort"))
        return {"v": 1, "ok": True, "weg": "api", "durchgang": DURCHGANG,
                "absicht": "api", "antwort": text,
                # Immer. Siehe Modulkopf.
                "marke": "tainted", "api": True,
                "aktionsvorschlag_erkannt": aktionsvorschlag(text),
                "status": antwort.get("status")}

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "durchgang": DURCHGANG,
                "anfragen": self.anfragen}
