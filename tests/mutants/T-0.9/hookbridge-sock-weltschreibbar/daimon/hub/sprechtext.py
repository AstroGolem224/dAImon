"""T-3.9 — was das Pet sagen darf. Design §8.3.

Der Validator sitzt **im Hub**, nicht im sprechenden Dienst. Das ist keine
Geschmacksfrage: sobald ein anderer Produzent Text an die Ausgabe schicken kann,
ist eine Pruefung im TTS-Dienst umgehbar. Der Hub ist die Stelle, an der alle
durchmuessen -- dieselbe Begruendung wie bei der Ladesperre aus T-3.7.

Warum nicht `wert_saeubern` aus der Auth-Vorschau
----------------------------------------------------------------------------
`daimon/auth/preview.py` **escapt**: aus `ä` wird `\\u00E4`, aus einem
Bidi-Override `\\u202E`. Fuer eine Anzeige ist das richtig und dort ausdruecklich
so begruendet -- man soll sehen, dass da etwas ist. Vorgelesen ist es Unsinn:
`backslash u null null E vier` statt eines Umlauts. Design §8.3 verlangt fuer die
Stimme das Gegenteil, naemlich **entfernen**.

Deshalb ein zweites Modul und kein Schalter in `preview.py`: die Bubble-Zusage
ist mit T-1.7 eingefroren, und ein `if fuer_stimme:` haette sie angefasst. Ein
Sanitizer, zwei Profile -- die *Reihenfolge* ist von dort uebernommen (NFC
zuerst, dann pruefen), die Behandlung nicht.

Zwei Kanaele mit verschiedenen Regeln
----------------------------------------------------------------------------
**Ungefragt** (Status, proaktive Hinweise): zieht ausschliesslich aus
kuratierten Vorlagen. Freier Text wird **abgelehnt, nicht gesaeubert** -- ein
gesaeuberter freier Satz waere immer noch ein Satz, den niemand kuratiert hat.
Variable Anteile sind nur `trusted`-Werte (Projektname, Zahlen, Dauer).

**Antwort auf eine direkte Frage** (`reaktion`, `rueckfrage`): darf markiertes
Material enthalten, muss aber durch die Regeltabelle unten.

Ein Grund je verletzter Regel, nie ein gemeinsames `ungueltig: true`. T-3.14
macht daraus Overlay-Zustaende, und "zu lang" braucht dort eine andere Anzeige
als "da stand ein Passwort drin". Dieselbe Trennung wie bei den drei
Absagegruenden des GPU-Gates.

Was hier absichtlich NICHT passiert
----------------------------------------------------------------------------
Es wird nichts umgeschrieben, gekuerzt oder entschaerft, ausser den unsichtbaren
Zeichen. Ein Validator, der einen zu langen Satz kuerzt, spricht einen Satz, den
niemand geschrieben hat -- und die Kuerzung eines eingeschmuggelten Textes ist
immer noch eingeschmuggelt. Verletzt eine Antwort eine Regel, sagt das Pet, dass
sie auf dem Bildschirm steht (`ERSATZ_VORLAGE`), und liest sie nicht vor.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from daimon.common.protocol import Mark, Marked
from daimon.common.taint import SenkenFehler, pruefe_senke

# Design §8.3: "eine Zeile, hoechstens ~140 Zeichen". Vorgelesen ist Laengeres
# ohnehin unbrauchbar, und Laenge verschleiert Eingeschmuggeltes.
MAX_ZEICHEN = 140

KANAELE = frozenset({"ungefragt", "reaktion", "rueckfrage"})

# Abkuehlung je Anlass, Design §8.4/§10. Sekunden.
ABKUEHLUNG_S: dict[str, float] = {
    "ungefragt": 20.0,
    "reaktion": 10.0,
    "rueckfrage": 3.0,
}

# -- Die Gruende. Einer je Regel. -----------------------------------------
GRUND_LEER = "leer"
GRUND_ZU_LANG = "zu_lang"
GRUND_MEHRZEILIG = "mehrzeilig"
GRUND_CODE = "code"
GRUND_URL = "url"
GRUND_PFAD = "pfad"
GRUND_GEHEIMNIS = "geheimnis"
GRUND_UNBEKANNTER_KANAL = "unbekannter_kanal"
GRUND_FREIER_TEXT = "freier_text"          # nur auf dem Kanal `ungefragt`
GRUND_UNBEKANNTE_VORLAGE = "unbekannte_vorlage"
GRUND_NICHT_TRUSTED = "nicht_trusted"

GRUENDE = frozenset({
    GRUND_LEER, GRUND_ZU_LANG, GRUND_MEHRZEILIG, GRUND_CODE, GRUND_URL,
    GRUND_PFAD, GRUND_GEHEIMNIS, GRUND_UNBEKANNTER_KANAL, GRUND_FREIER_TEXT,
    GRUND_UNBEKANNTE_VORLAGE, GRUND_NICHT_TRUSTED,
})

# -- Kuratierte Vorlagen ---------------------------------------------------
#
# Der ganze Kanal `ungefragt` besteht aus diesen Zeilen. Wer eine neue Aeusserung
# will, schreibt sie hier hin und trifft damit eine Entscheidung, die
# nachlesbar ist -- statt einen Modelltext durchzulassen, den niemand gesehen
# hat. Platzhalter in `{}`; die Werte muessen `trusted` sein.
VORLAGEN: dict[str, str] = {
    "begruessung": "Hallo. {projekt} ist offen.",
    "lange_sitzung": "Wir sind seit {dauer} dran.",
    "build_fertig": "Build durch, {warnungen} Warnungen.",
    "tests_gruen": "Tests laufen gruen.",
    "tests_rot": "{anzahl} Tests sind rot.",
    "leerlauf": "Ich bin da, wenn du was brauchst.",
    # T-8.3: der Zeitplaner. `{titel}` ist der Termin-Text -- er kommt vom
    # Nutzer selbst (PTT oder CLI) und laeuft trotzdem durch `pruefe`, weil
    # die Vorlage der Traeger waere, wenn der Titel ein Pfad ist.
    "termin_faellig": "Erinnerung: {titel}.",
    "fokus_start": "Fokus laeuft, {dauer} Minuten.",
    "fokus_ende": "Fokus vorbei. Kurz durchatmen.",
    # Der Ersatz, wenn eine Antwort durch den Validator faellt. Design §8.3:
    # "sagt das Pet, dass die Antwort auf dem Bildschirm steht". Es schweigt
    # nicht -- Schweigen waere von einem toten Dienst nicht zu unterscheiden.
    "steht_am_bildschirm": "Die Antwort steht auf dem Bildschirm.",
}
ERSATZ_VORLAGE = "steht_am_bildschirm"

# -- Muster ---------------------------------------------------------------

# Codeanzeichen. Vorgelesener Code ist nutzlos UND ein Anzeichen fuer Injektion.
_CODE = re.compile(
    r"```"
    r"|(?<![A-Za-zaeoeuess])(function|const|let\s|import\s|export\s|class\s"
    r"|def\s|return\s|await\s|async\s|sudo\s|rm\s+-|curl\s|eval\()"
    r"|[{}]|;\s|\)\s*=>|=>\s*\{|\$\(|`",
    re.IGNORECASE)

# Adressen. Vorgelesene Adressen sind ein Phishing-Vektor.
_URL = re.compile(
    r"\b[a-z][a-z0-9+.-]*://"          # jedes Schema, nicht nur http
    r"|\bwww\.[^\s]"
    r"|\b[\w-]+\.(com|net|org|io|de|dev|sh|ly|me|co|ai|app|xyz)\b"
    r"|\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b",
    re.IGNORECASE)

# Geheimnisse in Zuweisungsform -- der Fall, der wirklich weh tut.
_GEHEIMNIS = re.compile(
    r"(?i)\b(api[_\- ]?key|apikey|secret|token|password|passwort|passwd|pwd"
    r"|credential|private[_\- ]?key|bearer)\b\s*(?:[:=]|ist\b|lautet\b)")

# Steuerzeichen, Bidi-Overrides, Nullbreite. WERDEN ENTFERNT, nicht escapt --
# siehe Modulkopf. Die Bereiche sind dieselben wie in `preview.py`, nur die
# Behandlung ist die andere.
#
# Und sie stehen hier ALS ESCAPES, nicht als Zeichen. Ein Modul, das unsichtbare
# entfernt, darf sie nicht selbst unsichtbar enthalten -- sonst prueft der
# naechste Leser eine Zeile, die er nicht sehen kann, und ein Editor mit
# "Leerraum am Zeilenende entfernen" aendert die Regel lautlos.
_UNSICHTBAR = re.compile(
    "[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f"   # Cc ohne \\t \\n \\r
    "\\u00ad"                            # weiches Trennzeichen
    "\\u200b-\\u200f"                    # Nullbreite, LRM, RLM
    "\\u202a-\\u202e"                    # Bidi-Embedding und -Override
    "\\u2060-\\u2064\\u2066-\\u2069"     # Wortverbinder, Isolate
    "\\ufeff\\ufff9-\\ufffb]")


def _pfadfoermig(text: str) -> bool:
    """Etwas, das nach Dateisystem aussieht.

    Ein einzelner Schraegstrich ist noch kein Pfad -- "und/oder" ist deutscher
    Text. Ein Pfad hat einen Anker (`/`, `~/`, `./`, `../` am Wortanfang), oder
    zwei Trenner, oder einen Trenner plus eine Endung. Und Windows-Pfade.

    ponytail: eine Heuristik, keine Grammatik. Obergrenze: sie laesst
    "Kapitel 3/4" durch und faengt "Ordner/datei.txt". Wer hier mehr braucht,
    braucht eine Liste erlaubter Zeichen -- und die verbietet dann Umlaute.
    """
    for wort in text.split():
        if re.match(r"^(~|\.{1,2})?/", wort) or re.search(r"[A-Za-z]:\\", wort):
            return True
        if wort.count("/") >= 2 or wort.count("\\") >= 2:
            return True
        if "/" in wort and re.search(r"\.[A-Za-z0-9]{1,5}(\b|$)", wort):
            return True
    return False


@dataclass(frozen=True)
class Urteil:
    """Sprechbar oder nicht, und wenn nicht: warum.

    `ersatz` ist der Satz, den das Pet stattdessen sagt -- nicht `None`, weil
    Schweigen bei einer Regelverletzung von einem kaputten Dienst nicht zu
    unterscheiden waere.
    """

    ok: bool
    text: str = ""
    grund: str = ""
    ersatz: str = ""

    def als_dict(self) -> dict:
        d: dict = {"v": 1, "ok": self.ok}
        if self.ok:
            d["text"] = self.text
        else:
            d["grund"] = self.grund
            d["ersatz"] = self.ersatz
        return d


def unsichtbares_entfernen(text: str) -> str:
    """NFC, dann Steuerzeichen/Bidi/Nullbreite **weg**. Reihenfolge zaehlt:
    normalisiert man nach dem Entfernen, kann eine Kombination wieder ein
    Zeichen ergeben, das man gerade entfernt hat."""
    return _UNSICHTBAR.sub("", unicodedata.normalize("NFC", text))


def pruefe(text: object, *, kanal: str) -> Urteil:
    """Die ganze Regeltabelle aus Design §8.3, eine Regel nach der anderen.

    Auf dem Kanal `ungefragt` gibt es keinen freien Text -- dort ist jede
    Aeusserung eine Vorlage, und wer trotzdem Text schickt, bekommt
    `freier_text`. Das ist Absicht: ein gesaeuberter freier Satz waere immer
    noch einer, den niemand kuratiert hat.
    """
    ersatz = VORLAGEN[ERSATZ_VORLAGE]
    if kanal not in KANAELE:
        return Urteil(False, grund=GRUND_UNBEKANNTER_KANAL, ersatz=ersatz)
    if kanal == "ungefragt":
        return Urteil(False, grund=GRUND_FREIER_TEXT, ersatz=ersatz)
    if not isinstance(text, str):
        return Urteil(False, grund=GRUND_LEER, ersatz=ersatz)

    sauber = unsichtbares_entfernen(text)

    # Mehrzeilig vor Laenge: ein 200-Zeichen-Text mit Zeilenumbruechen soll
    # `mehrzeilig` heissen, nicht `zu_lang` -- die Anzeige unterscheidet sich.
    if re.search("[\n\r\u2028\u2029]", sauber):
        return Urteil(False, grund=GRUND_MEHRZEILIG, ersatz=ersatz)

    sauber = sauber.strip()
    if not sauber:
        return Urteil(False, grund=GRUND_LEER, ersatz=ersatz)
    if len(sauber) > MAX_ZEICHEN:
        return Urteil(False, grund=GRUND_ZU_LANG, ersatz=ersatz)

    # Geheimnis vor Code: `token = "..."` enthaelt beides, und der teurere
    # Befund gehoert in die Meldung.
    if _GEHEIMNIS.search(sauber):
        return Urteil(False, grund=GRUND_GEHEIMNIS, ersatz=ersatz)
    if _URL.search(sauber):
        return Urteil(False, grund=GRUND_URL, ersatz=ersatz)
    if _pfadfoermig(sauber):
        return Urteil(False, grund=GRUND_PFAD, ersatz=ersatz)
    if _CODE.search(sauber):
        return Urteil(False, grund=GRUND_CODE, ersatz=ersatz)

    return Urteil(True, text=sauber)


def aus_vorlage(anlass: object, werte: object = None, *,
                markierung: str = "tainted") -> Urteil:
    """Der Kanal `ungefragt`: eine kuratierte Zeile plus `trusted`-Werte.

    `markierung` ist die Herkunft der **Werte**, nicht der Vorlage. Alles ausser
    `trusted` ist eine Absage -- und die FEHLENDE Marke ist `tainted`, nicht
    `trusted`. Bis zum 26.08. stand hier `markierung: str = "trusted"`: damit
    war die verschaerfte Regel durch WEGLASSEN zu umgehen, eine Zeile ohne
    `markierung` an `tts-say.sock` wurde ungefragt gesprochen. `taint.pruefe_senke`
    macht es seit jeher umgekehrt, und diese Richtung ist die einzige, die
    einen Fehler ueberlebt -- Design §8.3: "Variable Anteile sind
    ausschliesslich `trusted`-Werte". Ein Projektname aus dem Basename von `cwd`
    ist trusted, ein Wort aus einem Modellsatz nicht.

    HIER faellt die Entscheidung, und nur hier. Die Regel stand bis zum
    26.08. zweimal im Baum: als `if markierung != "trusted"` und als Zeile
    `tts_ungefragt` in `common/taint.SENKEN` -- und die beiden widersprachen
    sich bei `user_ptt`. Die Tabellenzeile hatte im ganzen Baum keinen
    Aufrufer; welche der beiden Fassungen galt, entschied der Zufall des
    Aufrufs. Jetzt ist es eine Fassung: die Tabelle sagt es, dieser Aufruf
    haelt es ein.
    """
    ersatz = VORLAGEN[ERSATZ_VORLAGE]
    if not isinstance(anlass, str) or anlass not in VORLAGEN:
        return Urteil(False, grund=GRUND_UNBEKANNTE_VORLAGE, ersatz=ersatz)
    try:
        pruefe_senke(Marked("", Mark(str(markierung))), senke="tts_ungefragt")
    except (ValueError, SenkenFehler):
        # ValueError: eine Marke, die es nicht gibt. Auch das ist eine Absage.
        return Urteil(False, grund=GRUND_NICHT_TRUSTED, ersatz=ersatz)

    werte = werte if isinstance(werte, dict) else {}
    vorlage = VORLAGEN[anlass]
    benoetigt = set(re.findall(r"\{(\w+)\}", vorlage))
    eingesetzt = {k: str(werte.get(k, "")) for k in benoetigt}
    if any(not v for v in eingesetzt.values()):
        return Urteil(False, grund=GRUND_UNBEKANNTE_VORLAGE, ersatz=ersatz)

    satz = vorlage.format(**eingesetzt)

    # Die eingesetzten Werte laufen durch dieselbe Regeltabelle. Ein
    # `trusted`-Projektname darf trotzdem kein Pfad sein: `cwd` kann
    # "/home/x/api_key=1" heissen, und dann ist die Vorlage der Traeger.
    # Geprueft wird auf dem Kanal `reaktion`, weil `ungefragt` freien Text
    # grundsaetzlich ablehnt -- die Vorlage selbst ist ja kuratiert.
    urteil = pruefe(satz, kanal="reaktion")
    if not urteil.ok:
        return Urteil(False, grund=urteil.grund, ersatz=ersatz)
    return urteil


def abkuehlung_s(kanal: str, cfg: object = None) -> float:
    """Frist je Anlass. Konfigurierbar, weil 20 s am eigenen Rechner anders
    wirkt als 20 s im Buero."""
    vorgabe = ABKUEHLUNG_S.get(kanal, ABKUEHLUNG_S["ungefragt"])
    if cfg is None:
        return vorgabe
    return float(cfg.get(f"tts.abkuehlung.{kanal}", vorgabe))
