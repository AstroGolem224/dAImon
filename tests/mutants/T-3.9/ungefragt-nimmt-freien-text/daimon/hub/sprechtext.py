"""T-3.9 -- der Sprechtext-Validator. Reines stdlib.

Referenz des Reviewers, blind gegen die Akzeptanzliste geschrieben.

Ein Sanitizer, zwei Profile: `daimon/auth/preview.py` ESCAPT fuer die
Anzeige (eingefrorene Bubble-Zusage aus T-1.7, unangetastet), dieses Modul
ENTFERNT fuer die Stimme. Dieselbe Reihenfolge wie dort: NFC zuerst, dann
pruefen -- sonst sieht ein Zeichen nach der Normalisierung anders aus als
bei der Pruefung.

Ein Grund je Regel, nie ein gemeinsames `ungueltig: true` (Lehre aus T-3.7;
T-3.14 macht aus den Gruenden Overlay-Zustaende).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_ZEICHEN = 140
KANAELE = frozenset({"ungefragt", "reaktion", "rueckfrage"})

# Die Regelgruende aus der Akzeptanzliste. `freier_text`,
# `unbekannte_vorlage` und `nicht_trusted` gehoeren zum Kanal `ungefragt`.
GRUENDE = frozenset({
    "zu_lang", "code", "url", "pfad", "geheimnis", "steuerzeichen",
    "mehrzeilig", "freier_text", "unbekannte_vorlage", "nicht_trusted",
    "unbekannter_kanal",
})

# Die kuratierten Vorlagen fuer ungefragte Aeusserungen. Variable Anteile
# ausschliesslich `trusted`; Platzhalter in `{}`.
VORLAGEN = {
    "bildschirm": "Die Antwort steht auf dem Bildschirm.",
    "fertig": "Die Aufgabe in {projekt} ist fertig.",
    "freigabe": "Die Sitzung {projekt} wartet auf eine Freigabe.",
}

# Der Ersatzsatz (Kriterium 7): verletzt eine Antwort eine Regel, sagt das
# Pet, dass die Antwort auf dem Bildschirm steht -- eine kuratierte Vorlage,
# kein freier Text.
ERSATZ = VORLAGEN["bildschirm"]

_CODE_RE = re.compile(r"```|\bfunction\b|\bconst\b|\bimport\b|\{\s*\}")
_URL_RE = re.compile(r"https?://|ftp://|www\.", re.IGNORECASE)
_GEHEIMNIS_RE = re.compile(
    r"\b(api[_-]?key|secret|password|passwort|token)\b\s*[:=]\s*\S+",
    re.IGNORECASE)


def _ist_pfad(text: str) -> bool:
    """Pfadfoermiges: absolut, Heimat, Elternverzeichnis, Laufwerksbuchstabe
    oder ein Schraegstrich am Wortanfang. Ein Schraegstrich mitten im Wort
    ('und/oder', '24/7') ist kein Pfad."""
    t = text.strip()
    if t.startswith(("/", "~/", "../")):
        return True
    if re.search(r"^[A-Za-z]:[\\/]", t):
        return True
    if re.search(r"(?<!\w)/\S", t):
        return True
    return False


def _steuerzeichen_raus(text: str) -> str:
    """Steuerzeichen, Bidi-Overrides und Nullbreitenzeichen werden ENTFERNT
    (Kategorien Cc, Cf, Cs, Co, Cn). Nicht escapt -- vorgelesen ist ein
    Escape Unsinn, und Design §8.3 verlangt fuer die Stimme das Entfernen."""
    teile = []
    for z in text:
        if unicodedata.category(z) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue                    # ENTFERNT, nicht escapt
        teile.append(z)

    return "".join(teile)


@dataclass
class Urteil:
    ok: bool
    text: str = ""
    grund: str = ""

    def als_dict(self) -> dict:
        if self.ok:
            return {"v": 1, "ok": True, "text": self.text}
        # Der Ersatzsatz gehoert in JEDE Absage einer Inhaltsregel: das Pet
        # schweigt nicht und liest nicht vor, es sagt, dass die Antwort auf
        # dem Bildschirm steht (Kriterium 7).
        return {"v": 1, "ok": False, "grund": self.grund, "ersatz": ERSATZ}


def pruefe(text: object, *, kanal: str) -> Urteil:
    """Prueft `text` fuer `kanal`. Rueckgabe: der sprechbare Text ODER eine
    Absage mit maschinenlesbarem Grund je verletzter Regel."""
    if kanal not in KANAELE:
        return Urteil(False, grund="unbekannter_kanal")
    # MUTANT: der Kanal `ungefragt` nimmt freien Text. Er laeuft durch
    # dieselben Regeln wie eine Antwort -- aber Kriterium 5 verlangt:
    # ungefragt zieht NUR aus kuratierten Vorlagen, freier Text wird
    # abgelehnt, nicht gesaeubert.

    if not isinstance(text, str) or not text.strip():
        return Urteil(False, grund="steuerzeichen")

    # NFC zuerst, dann pruefen -- dieselbe Reihenfolge wie in preview.py.
    t = unicodedata.normalize("NFC", text)
    if any(z in t for z in ("\n", "\r", "\u2028", "\u2029")):
        return Urteil(False, grund="mehrzeilig")
    t = _steuerzeichen_raus(t)
    if not t.strip():
        # Nach dem Entfernen ist nichts mehr da: der Text bestand nur aus
        # Zeichen, die nichts Hoerbares tragen.
        return Urteil(False, grund="steuerzeichen")
    if len(t) > MAX_ZEICHEN:
        return Urteil(False, grund="zu_lang")
    if _CODE_RE.search(t):
        return Urteil(False, grund="code")
    if _URL_RE.search(t):
        return Urteil(False, grund="url")
    if _GEHEIMNIS_RE.search(t):
        return Urteil(False, grund="geheimnis")
    if _ist_pfad(t):
        return Urteil(False, grund="pfad")
    return Urteil(True, text=t)


def aus_vorlage(vorlage: object, werte: object = None, *,
                markierung: str = "trusted") -> Urteil:
    """Der Kanal `ungefragt`: eine kuratierte Zeile plus `trusted`-Werte.

    `markierung` ist die Herkunft der WERTE, nicht der Vorlage. Alles ausser
    `trusted` ist eine Absage (Design §8.3)."""
    if markierung != "trusted":
        return Urteil(False, grund="nicht_trusted")
    if not isinstance(vorlage, str) or vorlage not in VORLAGEN:
        return Urteil(False, grund="unbekannte_vorlage")
    muster = VORLAGEN[vorlage]
    werte = werte if isinstance(werte, dict) else {}
    try:
        text = muster.format(**{k: str(v) for k, v in werte.items()})
    except (KeyError, IndexError, ValueError):
        return Urteil(False, grund="unbekannte_vorlage")
    # Die gerenderte Zeile laeuft durch DIESELBEN Regeln: ein `trusted`-
    # Projektname darf trotzdem kein Pfad sein.
    return pruefe(text, kanal="reaktion")
