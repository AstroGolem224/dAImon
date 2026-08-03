#!/usr/bin/env bash
# Erzeugt die fuenf T-3.9-Mutanten reproduzierbar -- und das Gut-Muster unter
# tests/fixtures/known-good/T-3.9/.
#
# BASIS IST EIN GEPINNTER COMMIT, NICHT HEAD und nicht der Arbeitsbaum. Die
# Implementierung des Builders entsteht parallel und blind; sobald sie
# committet ist, truege `git archive HEAD` IHRE Fassung in jeden Baum -- die
# Mutanten waeren dann Abwandlungen des Prueflings statt unabhaengiger
# Gegenproben (bei T-2.5 real passiert). Der Basisbaum darf die Dateien des
# Tasks deshalb NICHT enthalten; das wird unten geprueft und bricht ab.
#
# ALLE BAEUME bekommen zuerst eine VOLLSTAENDIGE und KORREKTE Umsetzung
# eingesetzt (Validator im Hub, persistierte Abkuehlung, Unterbrechung per
# kill, Steuerzeichen ENTFERNT, ungefragt nur aus Vorlagen) und danach genau
# einen Defekt. Ohne die korrekte Grundlage wuerde jeder Mutant schon am
# Vertrag scheitern und ueber seine eigentliche Mutation nichts aussagen --
# dieselbe Ueberlegung wie bei T-2.7 und T-3.4.
#
# DAS GUT-MUSTER ist die blind geschriebene Referenz des Reviewers, KEIN
# Abbild der Implementierung. Sobald die echte Implementierung abgenommen
# ist, gehoert das Gut-Muster aus dem abgenommenen Stand ERNEUERT (wie bei
# T-2.7). Bis dahin beweist es nur, dass der Verifizierer gruen werden KANN
# (Fall 12 in HANDOVER.md).
#
# Zwei Fallen aus der Projekthistorie sind abgesichert:
#   * `git archive` traegt weder target/ noch __pycache__ mit; eine Kopie mit
#     Artefakten der unmutierten Quelle hat schon einmal einen Mutanten gruen
#     gemeldet.
#   * Am Ende wird geprueft, dass jeder Baum sich vom Basisbaum nur in den
#     vorgesehenen Dateien unterscheidet, dass sich die Baeume VONEINANDER
#     unterscheiden und dass alles kompiliert. Ein abgebrochenes
#     Erzeugungsskript hat schon einmal eine unveraenderte Kopie hinterlassen.
#
# Aufruf:
#   tests/mutants/T-3.9/erzeugen.sh
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
GUT="$REPO/tests/fixtures/known-good/T-3.9"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

# 2167e2c = "T-0.0: der Rollenwaechter war verdrahtet und lief nie".
# Enthaelt Hub, GPU-Worker (das Start-Muster fuer den Dienst), tickets.py
# (das Persistenz-Muster) und preview.py (der Bubble-Sanitizer) -- aber
# KEIN sprechtext.py, KEIN tts.py und keine daimon-tts-Units.
BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-2167e2c}"
git -C "$REPO" archive "$BASIS_COMMIT" daimon config | tar -x -C "$BASIS" \
  || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }
[[ -f "$BASIS/daimon/hub/daemon.py" && -f "$BASIS/daimon/gpu/worker.py" ]] \
  || { echo "Basisbaum unvollstaendig: hub/daemon.py oder gpu/worker.py fehlt"; exit 1; }
[[ -f "$BASIS/config/daimon.toml" ]] \
  || { echo "Basisbaum unvollstaendig: config/daimon.toml fehlt"; exit 1; }
for neu in daimon/hub/sprechtext.py daimon/hub/abkuehlung.py daimon/face/tts.py \
           config/systemd/daimon-tts.service config/systemd/daimon-tts.socket; do
  [[ -e "$BASIS/$neu" ]] \
    && { echo "FEHLER: der Basis-Commit enthaelt bereits $neu."
         echo "Dann waere die Grundlage der Mutanten die Fassung des Builders,"
         echo "nicht die blind geschriebene des Reviewers. Basis neu pinnen."; exit 1; }
done
# Die Gegenprobe zum Abbruch selbst: fehlte auch worker.py, waere schlicht
# ein zu alter Commit erwischt -- und der Abbruch oben haette nichts gesagt.
[[ -f "$BASIS/daimon/hub/tickets.py" ]] \
  || { echo "FEHLER: der Basis-Commit enthaelt kein tickets.py -- er liegt vor"
       echo "T-0.8 und ist als Basis fuer T-3.9 falsch."; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" "$GUT" <<'PYEOF'
import os
import shutil
import sys

ziel_wurzel, basis, gut = sys.argv[1], sys.argv[2], sys.argv[3]

def ersetzen(text, alt, neu, wo):
    if text.count(alt) != 1:
        raise SystemExit(f"Anker nicht genau einmal gefunden in {wo}: "
                         f"{alt[:70]!r} ({text.count(alt)}x)")
    return text.replace(alt, neu)

# ===========================================================================
# daimon/hub/sprechtext.py -- der Validator. Er UEBERNIMMT von preview.py die
# Reihenfolge (NFC zuerst, dann pruefen), nicht die Behandlung: fuer die
# Stimme werden Steuerzeichen ENTFERNT, nicht escapt (Design §8.3).
# ===========================================================================
SPRECHTEXT = '''"""T-3.9 -- der Sprechtext-Validator. Reines stdlib.

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

_CODE_RE = re.compile(r"```|\\bfunction\\b|\\bconst\\b|\\bimport\\b|\\{\\s*\\}")
_URL_RE = re.compile(r"https?://|ftp://|www\\.", re.IGNORECASE)
_GEHEIMNIS_RE = re.compile(
    r"\\b(api[_-]?key|secret|password|passwort|token)\\b\\s*[:=]\\s*\\S+",
    re.IGNORECASE)


def _ist_pfad(text: str) -> bool:
    """Pfadfoermiges: absolut, Heimat, Elternverzeichnis, Laufwerksbuchstabe
    oder ein Schraegstrich am Wortanfang. Ein Schraegstrich mitten im Wort
    ('und/oder', '24/7') ist kein Pfad."""
    t = text.strip()
    if t.startswith(("/", "~/", "../")):
        return True
    if re.search(r"^[A-Za-z]:[\\\\/]", t):
        return True
    if re.search(r"(?<!\\w)/\\S", t):
        return True
    return False


def _steuerzeichen_raus(text: str) -> str:
    """Steuerzeichen, Bidi-Overrides und Nullbreitenzeichen werden ENTFERNT
    (Kategorien Cc, Cf, Cs, Co, Cn). Nicht escapt -- vorgelesen ist ein
    Escape Unsinn, und Design §8.3 verlangt fuer die Stimme das Entfernen."""
    teile = []
    for z in text:
@@STEUERZEICHEN@@
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
@@UNGEFRAGT@@
    if not isinstance(text, str) or not text.strip():
        return Urteil(False, grund="steuerzeichen")

    # NFC zuerst, dann pruefen -- dieselbe Reihenfolge wie in preview.py.
    t = unicodedata.normalize("NFC", text)
    if any(z in t for z in ("\\n", "\\r", "\\u2028", "\\u2029")):
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
'''

STEUERZEICHEN_KORREKT = '''        if unicodedata.category(z) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue                    # ENTFERNT, nicht escapt
        teile.append(z)
'''

STEUERZEICHEN_MUTANT = '''        if unicodedata.category(z) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            # MUTANT: escapt statt entfernt -- das ist wert_saeuberns
            # Behandlung fuer die ANZEIGE. Vorgelesen ist sie Unsinn, und
            # Design §8.3 verlangt fuer die Stimme das Entfernen.
            teile.append("\\\\u%04X" % ord(z))
            continue
        teile.append(z)
'''

UNGEFRAGT_KORREKT = '''    if kanal == "ungefragt":
        # Ungefragte Aeusserungen ziehen NUR aus kuratierten Vorlagen.
        # Freier Text wird abgelehnt, nicht gesaeubert (Kriterium 5).
        return Urteil(False, grund="freier_text")
'''

UNGEFRAGT_MUTANT = '''    # MUTANT: der Kanal `ungefragt` nimmt freien Text. Er laeuft durch
    # dieselben Regeln wie eine Antwort -- aber Kriterium 5 verlangt:
    # ungefragt zieht NUR aus kuratierten Vorlagen, freier Text wird
    # abgelehnt, nicht gesaeubert.
'''

# ===========================================================================
# daimon/hub/abkuehlung.py -- Abkuehlung je Anlass, persistiert nach dem
# Muster von daimon/hub/tickets.py (mkstemp -> flush -> fsync -> os.replace
# -> fsync aufs Verzeichnis). Monotone Zeit: eine NTP-Korrektur darf keine
# Abkuehlung aufheben und keine erzeugen.
# ===========================================================================
ABKUEHLUNG = '''"""T-3.9 -- die Abkuehlung der Stimme. Persistiert.

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
@@PERSISTENZ_LADEN@@

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
@@PERSISTENZ_SCHREIBEN@@
            return bis
'''

PERSISTENZ_KORREKT_LADEN = '''        self._laden()'''
PERSISTENZ_MUTANT_LADEN = '''        # MUTANT: der Bestand wird nicht geladen -- die Abkuehlung lebt nur
        # im Speicher und ueberlebt keinen Neustart (Kriterium 8).
        pass'''

PERSISTENZ_KORREKT_SCHREIBEN = '''            self._schreiben()'''
PERSISTENZ_MUTANT_SCHREIBEN = '''            # MUTANT: nichts geht auf die Platte.
            pass'''

# ===========================================================================
# Der Hub-Aufsatz: Torwaechter-Endpunkt auf tts.sock. Der Validator sitzt
# HIER, nicht im Dienst (Design §8.3) -- und die Freigabe ist eine Marke mit
# Frist, dasselbe Muster wie die GPU-Ladesperre.
# ===========================================================================
DAEMON_METHODEN = '''
    # -- Sprechfreigabe (T-3.9) --------------------------------------------

    def tts_anfrage(self, anfrage: object) -> dict:
        """Der Torwaechter der Stimme: Validator, Abkuehlung, Freigabe.

        Drei Arten:

        * `sprich` -- Text oder Vorlage rein, sprechbarer Text plus Marke
          raus. Ohne Marke spricht der Dienst nicht; das ist der Grund,
          warum ein Direktzugriff auf den TTS-Socket nichts erreicht.
        * `beginnt` -- der Sprecher hat angefangen. Setzt voice.tts_active.
        * `gesprochen` -- fertig. Loescht tts_active UND vermerkt die
          Abkuehlung -- am ENDE, nicht am Anfang.

        Reihenfolge in `sprich`: erst Validator, dann Abkuehlung. Ein Text,
        der die Regeln verletzt, soll `code` oder `geheimnis` heissen und
        nicht `abkuehlung` -- sonst verschwindet ein Injektionsversuch
        hinter einer Frist.
        """
        if not isinstance(anfrage, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        art = anfrage.get("art")
        if art == "beginnt":
            return self._tts_beginnt(anfrage.get("marke"))
        if art == "gesprochen":
            return self._tts_gesprochen(anfrage.get("marke"))
        if art != "sprich":
            return {"v": 1, "ok": False, "grund": "unbekannte_art"}

        kanal = str(anfrage.get("kanal", ""))
@@VALIDIERUNG_HUB@@
        if not urteil.ok:
            self.diag.verworfen(f"tts_{urteil.grund}")
            self.log.warn("Sprechtext abgelehnt", DAIMON_ACTION="tts_abgelehnt",
                          DAIMON_KANAL=kanal[:20], DAIMON_GRUND=urteil.grund)
            # Der abgelehnte Text kommt NICHT ins Journal.
            return urteil.als_dict()

        darf, rest_s = self.abkuehlung.darf(kanal)
        if not darf:
            self.diag.verworfen("tts_abkuehlung")
            return {"v": 1, "ok": False, "grund": "abkuehlung",
                    "rest_s": rest_s, "ersatz": ""}

        marke = secrets.token_hex(16)
        with self._tts_lock:
            jetzt = time.monotonic()
            self._tts_freigaben = {m: v for m, v in self._tts_freigaben.items()
                                   if v[1] > jetzt}
            self._tts_freigaben[marke] = (kanal, jetzt + self.tts_frist_s)
        self.log.info("Sprechfreigabe erteilt", DAIMON_ACTION="tts_freigabe",
                      DAIMON_KANAL=kanal[:20], DAIMON_ZEICHEN=len(urteil.text))
        return {"v": 1, "ok": True, "text": urteil.text, "marke": marke,
                "kanal": kanal, "frist_s": self.tts_frist_s}

    def _tts_freigabe_holen(self, marke: object, *, entfernen: bool):
        with self._tts_lock:
            eintrag = self._tts_freigaben.get(marke) if isinstance(marke, str) else None
            if eintrag is None or eintrag[1] <= time.monotonic():
                return None
            if entfernen:
                del self._tts_freigaben[marke]
            return eintrag[0]

    def _tts_beginnt(self, marke: object) -> dict:
        kanal = self._tts_freigabe_holen(marke, entfernen=False)
        if kanal is None:
            return {"v": 1, "ok": False, "grund": "fremde_marke"}
        with self._tts_lock:
            self._tts_letzte = marke
        self.state.set_voice(tts_active=True)
        return {"v": 1, "ok": True, "kanal": kanal}

    def _tts_gesprochen(self, marke: object) -> dict:
        kanal = self._tts_freigabe_holen(marke, entfernen=True)
        with self._tts_lock:
            # Nur die juengste Marke loescht die Anzeige: ein verspaetetes
            # `gesprochen` einer ABGEBROCHENEN Wiedergabe darf das Flag der
            # laufenden nicht wegnehmen.
            if self._tts_letzte == marke:
                self._tts_letzte = None
                self.state.set_voice(tts_active=False)
        if kanal is None:
            return {"v": 1, "ok": False, "grund": "fremde_marke"}
        ablauf = self.abkuehlung.vermerke(kanal)
        return {"v": 1, "ok": True, "kanal": kanal,
                "abkuehlung_bis": round(ablauf, 3)}
'''

VALIDIERUNG_HUB_KORREKT = '''        if "vorlage" in anfrage:
            urteil = sprechtext.aus_vorlage(
                anfrage.get("vorlage"), anfrage.get("werte"),
                markierung=str(anfrage.get("markierung", "trusted")))
        else:
            urteil = sprechtext.pruefe(anfrage.get("text"), kanal=kanal)
'''

VALIDIERUNG_HUB_MUTANT = '''        # MUTANT: der Hub prueft NICHT. Der Text wird ungesehen durchgereicht
        # und eine Marke ausgegeben; die Pruefung passiert erst im Dienst.
        # Design §8.3: der Validator sitzt im Hub -- sonst ist er umgehbar,
        # sobald ein anderer Produzent Text an die Ausgabe schickt.
        if "vorlage" in anfrage:
            urteil = sprechtext.aus_vorlage(
                anfrage.get("vorlage"), anfrage.get("werte"),
                markierung=str(anfrage.get("markierung", "trusted")))
        else:
            urteil = sprechtext.Urteil(True, text=str(anfrage.get("text", "")))
'''

DAEMON_IMPORTS = '''from daimon.hub import sprechtext
from daimon.hub.abkuehlung import Abkuehlung
'''

DAEMON_KONSTANTEN = '''TTS_SOCKET = "tts.sock"
# Frist einer Sprechfreigabe: deckt Synthese plus Wiedergabe eines Satzes.
TTS_FRIST_S = 30.0
TTS_ABKUEHLUNG_DATEI = "tts-abkuehlung.json"
'''

DAEMON_INIT = '''        # T-3.9: der Sprechtext-Torwaechter. Validator und Abkuehlung liegen
        # HIER, weil eine Pruefung im sprechenden Dienst umgehbar ist
        # (Design §8.3).
        self._tts_lock = threading.Lock()
        self._tts_freigaben: dict[str, tuple[str, float]] = {}
        self._tts_letzte: str | None = None
        self.tts_frist_s = float(self.cfg.get("tts.freigabefrist_s", TTS_FRIST_S))
        self.abkuehlung = Abkuehlung(
            Path(self.cfg.state_dir) / TTS_ABKUEHLUNG_DATEI,
            cfg=self.cfg, log=self.log)
'''

DAEMON_START = '''        t = threading.Thread(target=self._horche_einfach,
                             args=(TTS_SOCKET, self.tts_anfrage),
                             kwargs={"liest": True}, daemon=True)
        t.start()
        self._threads.append(t)
'''

STATE_SET_VOICE = '''    def set_voice(self, **felder: Any) -> None:
        """T-3.9: voice.tts_active (und spaeter mehr) setzen."""
        with self._lock:
            vorher = dict(self._voice)
            self._voice.update(felder)
            if self._voice != vorher:
                self._rev += 1

'''

# ===========================================================================
# daimon/face/tts.py -- der Sprechdienst. Socket-aktiviert, Modell im
# Speicher (das IST das TTFA-Kriterium), Ausgabe ueber `pw-cat` OHNE
# absoluten Pfad, Unterbrechung = Prozess toeten.
# ===========================================================================
TTS = '''"""T-3.9 -- der Sprechdienst. sherpa-onnx VITS, CPU, 0 VRAM.

Referenz des Reviewers, blind gegen die Akzeptanzliste geschrieben.

Der Dienst spricht NUR mit Marke aus dem Hub. Der Validator sitzt im Hub
(Design §8.3); hier bleibt die Mechanik: Synthese, Ausgabe, Unterbrechung.

`pw-cat` wird OHNE absoluten Pfad aufgerufen -- ein Verifizierer legt einen
Stub in den PATH und misst, was der Dienst ausgeben WOLLTE, ohne die
Soundkarte zu beruehren (Lehre aus T-2.7 und T-3.7). Ein absoluter Pfad
mauert diesen Messpunkt zu.

Kein Leerlauf-Exit: das Modell im Speicher IST das TTFA-Kriterium (p95 <
200 ms); anders als der GPU-Worker gibt dieser Prozess kein VRAM frei, das
er zurueckgeben muesste.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from daimon.common.config import load as load_config
from daimon.common.logging import Logger, get_logger
from daimon.gpu.worker import eigener_socket, hub_anfrage, sd_socket

TTS_SOCKET = "tts.sock"          # der Torwaechter-Endpunkt des Hubs
MAX_ZEILE = 1 << 16

# Stimmlizenzen, die vorgelesen werden duerfen (Design §8.2). pavoque ist
# CC-BY-NC-SA und scheidet aus -- auch wenn die Dateien vorhanden sind.
ERLAUBTE_LIZENZEN = ("CC0",)

# Der Pfad kommt aus der Konfiguration (tts.modell_dir); das ist nur die
# Vorgabe. Ein Produktivdienst laedt nicht aus einem Spike-Verzeichnis --
# sobald T-3.10 die Persona-Dateien anlegt, gehoert die Stimme nach
# ~/.local/share/daimon/voices/.
VORGABE_STIMME = "de_DE-thorsten-high"


def _finde_onnx(modell_dir: Path) -> Path | None:
    treffer = sorted(modell_dir.glob("*.onnx"))
    return treffer[0] if treffer else None


def _lizenz_erlaubt(modell_dir: Path) -> bool | None:
    """True/False aus der MODEL_CARD, None wenn es keine gibt. Eine fehlende
    Karte ist KEINE Erlaubnis -- aber sie ist ein anderer Befund als eine
    verbotene Lizenz, und die Absage soll das unterscheiden."""
    karte = modell_dir / "MODEL_CARD"
    if not karte.is_file():
        return None
    try:
        text = karte.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return any(lizenz in text for lizenz in ERLAUBTE_LIZENZEN)


class Sprecher:
    """Ein Dienst, ein Modell, ein Ausgabepfad. Kein Zustandsautomat."""

    def __init__(self, *, hub_socket: str, cfg, log: Logger) -> None:
        self.hub_socket = hub_socket
        self.cfg = cfg
        self.log = log
        # Die Stimme kommt aus persona.voice (Kriterium 3), der PFAD aus
        # tts.modell_dir. Vorgabe fuer beides: thorsten aus dem Spike-Bestand.
        self.stimme = str(cfg.get("persona.voice", VORGABE_STIMME))
        vorgabe_basis = Path(__file__).resolve().parents[2] / "spikes" / \
            "nvidia-voice" / "models"
        basis = Path(str(cfg.get("tts.modell_dir", str(vorgabe_basis))))
        self.modell_dir = self._stimmen_dir(basis)
        self._tts = None
        self.ladefehler: str | None = None
        self.modell = ""
        self.rate = 22050
        self._pwc: subprocess.Popen | None = None
        self._pwc_lock = threading.Lock()
        self._epoche = 0         # steigt bei jeder Unterbrechung

    def _stimmen_dir(self, basis: Path) -> Path:
        """persona.voice WAEHLT die Stimme: <basis>/vits-piper-<stimme> oder
        <basis>/<stimme>. Nur wenn basis selbst ein Stimmverzeichnis ist und
        die Vorgabestimme gemeint ist, gilt basis direkt -- sonst waere der
        Wert aus persona.voice fest verdrahtet statt gelesen."""
        for kandidat in (basis / f"vits-piper-{self.stimme}",
                         basis / self.stimme):
            if kandidat.is_dir():
                return kandidat
        if basis.is_dir() and any(basis.glob("*.onnx")) \
                and self.stimme == VORGABE_STIMME:
            return basis
        return basis / f"vits-piper-{self.stimme}"   # existiert nicht -> ehrliche Absage

    # -- Lizenz und Laden ---------------------------------------------------

    def laden(self) -> None:
        """Laedt das Modell in den Speicher -- oder legt den Grund ab, warum
        nicht. Die Absage kommt bei JEDER Anfrage ehrlich zurueck, der
        Prozess bleibt erreichbar (eine ehrliche Absage, kein stiller Tod)."""
        if not self.modell_dir.is_dir():
            self.ladefehler = f"stimme_fehlt:{self.stimme}"
            return
        erlaubt = _lizenz_erlaubt(self.modell_dir)
        if erlaubt is not True:
            self.ladefehler = ("lizenz_fehlt" if erlaubt is None
                               else "lizenz_verboten")
            return
        onnx = _finde_onnx(self.modell_dir)
        tokens = self.modell_dir / "tokens.txt"
        daten = self.modell_dir / "espeak-ng-data"
        if onnx is None or not tokens.is_file():
            self.ladefehler = f"stimme_fehlt:{self.stimme}"
            return
        try:
            import sherpa_onnx
            vits = sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(onnx), lexicon="", tokens=str(tokens),
                data_dir=str(daten) if daten.is_dir() else "")
            modell = sherpa_onnx.OfflineTtsModelConfig(
                vits=vits, num_threads=2, debug=False, provider="cpu")
            self._tts = sherpa_onnx.OfflineTts(
                sherpa_onnx.OfflineTtsConfig(model=modell, max_num_sentences=1))
        except Exception as exc:
            self.ladefehler = f"laden_fehlgeschlagen:{type(exc).__name__}"
            return
        self.modell = onnx.stem

    # -- Ausgabe --------------------------------------------------------------

    def _unterbreche(self) -> None:
        """Eine neue Aeusserung bricht die laufende ab. Kein Warten auf das
        Satzende, kein Mischen zweier Stimmen. Die Epochenmarke faellt mit:
        ein Strom, der noch SYNTHETISIERT (also noch keinen pw-cat hat),
        legt danach keinen mehr an -- sonst kaeme die alte Stimme verspaetet
        und mischte sich unter die neue."""
        with self._pwc_lock:
            self._epoche += 1
            if self._pwc is not None and self._pwc.poll() is None:
@@UNTERBRECHUNG@@
            self._pwc = None

    def _beobachte_ende(self, pwc: subprocess.Popen, marke: str) -> None:
        """Meldet `gesprochen`, wenn der pw-cat-Prozess ENDET -- die
        Abkuehlung zaehlt ab dem letzten Ton, nicht ab der Anfrage."""
        pwc.wait()
        antwort = hub_anfrage(self.hub_socket,
                              {"v": 1, "art": "gesprochen", "marke": marke})
        if not antwort.get("ok"):
            self.log.warn("gesprochen-Meldung abgelehnt",
                          DAIMON_GRUND=str(antwort.get("grund", ""))[:80])

    def sprich(self, text: str, marke: object) -> dict:
        basis = {"engine": "sherpa-onnx", "modell": self.modell or self.stimme,
                 "provider": "cpu"}
        if self.ladefehler is not None or self._tts is None:
            return {"v": 1, "ok": False, "grund": self.ladefehler or "nicht_geladen",
                    **basis}
        freigabe = hub_anfrage(self.hub_socket,
                               {"v": 1, "art": "beginnt", "marke": marke})
        if not freigabe.get("ok"):
            return {"v": 1, "ok": False,
                    "grund": str(freigabe.get("grund", "marke")), **basis}
        kanal = str(freigabe.get("kanal", ""))
@@VALIDIERUNG_DIENST@@

        # Die laufende Wiedergabe faellt, BEVOR die neue Synthese beginnt.
        self._unterbreche()
        with self._pwc_lock:
            epoche = self._epoche

        # Synthese und Streaming in einem Strom-Thread: pw-cat nimmt die
        # Daten nur im Abspieltempo ab, ein schreibender Aufruf wuerde die
        # ganze Wiedergabe lang blockieren -- und eine neue Aeusserung
        # koennte nie unterbrechen, weil sie hinter der alten anstuende.
        threading.Thread(target=self._stroeme,
                         args=(text, str(marke), epoche), daemon=True).start()
        return {"v": 1, "ok": True, "kanal": kanal, **basis}

    def _stroeme(self, text: str, marke: str, epoche: int) -> None:
        saetze = [s for s in re.split(r"(?<=[.!?])\\s+", text) if s.strip()]
        pwc = None
        try:
            for satz in saetze:
                audio = self._tts.generate(satz)
                self.rate = int(audio.sample_rate)
                with self._pwc_lock:
                    if epoche != self._epoche:
                        return          # unterbrochen, noch bevor es klang
                    if pwc is None:
                        # OHNE absoluten Pfad -- der PATH entscheidet (Kopf).
                        pwc = subprocess.Popen(
                            ["pw-cat", "--playback", "--rate", str(self.rate),
                             "--channels", "1", "--format", "s16", "-"],
                            stdin=subprocess.PIPE)
                        self._pwc = pwc
                roh = bytearray()
                for s in audio.samples:
                    roh += struct.pack("<h", max(-32768, min(32767, int(s * 32767))))
                pwc.stdin.write(bytes(roh))
            if pwc is not None:
                pwc.stdin.close()
        except (OSError, BrokenPipeError):
            # BrokenPipe beim Schreiben heisst: diese Wiedergabe wurde
            # unterbrochen. Das ist der Regelfall, kein Fehler.
            pass
        if pwc is not None:
            self._beobachte_ende(pwc, marke)

    # -- Bedienung ------------------------------------------------------------

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "engine": "sherpa-onnx",
                "modell": self.modell or self.stimme, "provider": "cpu",
                "stimme": self.stimme, "geladen": self._tts is not None,
                "ladefehler": self.ladefehler, "pid": os.getpid()}

    def lauf(self, srv: socket.socket) -> int:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return 1
            with conn:
                conn.settimeout(30.0)
                try:
                    roh = conn.makefile("rb").readline(MAX_ZEILE)
                    anfrage = json.loads(roh)
                except (json.JSONDecodeError, ValueError):
                    antwort = {"v": 1, "ok": False, "grund": "unlesbar"}
                else:
                    art = anfrage.get("art") if isinstance(anfrage, dict) else None
                    if art == "sprich":
                        antwort = self.sprich(str(anfrage.get("text", "")),
                                              anfrage.get("marke"))
                    elif art == "status":
                        antwort = self.zustand()
                    else:
                        antwort = {"v": 1, "ok": False, "grund": "unbekannte_art"}
                try:
                    conn.sendall(json.dumps(antwort).encode() + b"\\n")
                except OSError:
                    pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon TTS-Dienst (T-3.9)")
    ap.add_argument("--socket", default=None,
                    help="ohne systemd: hier selbst horchen")
    ap.add_argument("--hub-socket", default=None)
    args = ap.parse_args(argv)

    # make_dirs=False: load() legt sonst $XDG_STATE_HOME/daimon an und
    # chmod-et es -- unter ProtectHome=read-only ist das ein EROFS (am 03.08.
    # real passiert, siehe daimon/gpu/worker.py).
    cfg = load_config(make_dirs=False)
    hub_socket = args.hub_socket or str(cfg.runtime_dir / TTS_SOCKET)

    srv = sd_socket()
    if srv is None:
        if not args.socket:
            raise SystemExit(
                "Weder Socket-Aktivierung (LISTEN_FDS) noch --socket. Der "
                "Dienst legt ohne beides keinen Socket an: er waere dann "
                "gestartet, aber unerreichbar.")
        srv = eigener_socket(args.socket)

    sprecher = Sprecher(hub_socket=hub_socket, cfg=cfg,
                        log=get_logger("daimon-tts"))
    sprecher.laden()           # das Modell im Speicher IST das TTFA-Kriterium
    try:
        return sprecher.lauf(srv)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
'''

UNTERBRECHUNG_KORREKT = '''                self._pwc.kill()      # Unterbrechung = Prozess toeten
'''
UNTERBRECHUNG_MUTANT = '''                # MUTANT: die neue Aeusserung reiht sich ein statt abzubrechen.
                self._pwc.wait()
'''

VALIDIERUNG_DIENST_KORREKT = '''        # Der Validator sitzt im HUB (Design §8.3). Wer hierher kommt, hat
        # eine Marke -- und eine Marke gab es nur fuer geprueften Text.
'''
VALIDIERUNG_DIENST_MUTANT = '''        # MUTANT: die Pruefung passiert HIER statt im Hub. Eine Marke sagt
        # damit nichts mehr ueber den Inhalt -- der Hub hat sie ungesehen
        # ausgegeben.
        urteil = sprechtext.pruefe(text, kanal=kanal or "reaktion")
        if not urteil.ok:
            return {"v": 1, "ok": False, "grund": urteil.grund, **basis}
        text = urteil.text
'''

# ===========================================================================
# Die Units. Keine Template-Unit: es gibt genau eine Stimme zur Zeit.
# ===========================================================================
UNIT_SOCKET = '''# dAImon TTS-Dienst (T-3.9), Socket-Seite. EIN Socket, eine Stimme zur
# Zeit -- keine Template-Unit, `%i` waere leer.
#
# Der Socket ueberlebt einen Neustart des Dienstes. Installation:
#   systemctl --user link <dieser Pfad>
#   systemctl --user enable --now daimon-tts.socket
# Der Dienst selbst wird NIE von Hand gestartet.

[Unit]
Description=dAImon TTS Socket
PartOf=graphical-session.target

[Socket]
ListenStream=%t/daimon/tts-serve.sock
# 0600: derselbe Nutzer, sonst niemand (Design 1.3 -- Wegweiser, keine
# Authentifizierung).
SocketMode=0600
# Accept=no: der Dienst bekommt den HORCHENDEN Socket (fd 3) und nimmt selbst
# an. Accept=yes waere ein Prozess -- also ein Modellladevorgang -- je
# Verbindung, und das Modell im Speicher ist genau das TTFA-Kriterium.
Accept=no
RemoveOnStop=yes
# %t/daimon muss EXISTIEREN, bevor systemd den Namespace baut, und Preserve,
# weil sonst das Stoppen EINER Unit den anderen die Sockets wegzieht (beides
# am 02.08. real passiert).
RuntimeDirectory=daimon
RuntimeDirectoryPreserve=yes

[Install]
WantedBy=sockets.target
'''

UNIT_SERVICE = '''# dAImon TTS-Dienst (T-3.9): sherpa-onnx VITS auf der CPU, 0 VRAM.
#
# Gestartet ausschliesslich ueber daimon-tts.socket. KEIN Leerlauf-Exit --
# anders als der GPU-Worker gibt dieser Prozess kein VRAM frei; das Modell
# im Speicher IST das TTFA-Kriterium (p95 < 200 ms).
#
# Haertung nach Design 7.5, uebernommen aus daimon-gpu@.service -- mit den
# zwei Direktiven, die dort fehlen MUSSTEN und hier tragen (unten).

[Unit]
Description=dAImon TTS-Dienst (sherpa-onnx VITS, CPU)
# Ohne den Socket gibt es keinen Deskriptor zum Erben. Requires statt Wants:
# ein Dienst ohne Socket waere gestartet und unerreichbar.
Requires=daimon-tts.socket
After=daimon-tts.socket daimon-hub.service
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory=/home/itiger013/Dokumente/Github/dAImon
# Projekt-venv, nicht System-Python (T-1.2 hat den Interpreter festgenagelt).
ExecStart=/home/itiger013/Dokumente/Github/dAImon/.venv/bin/python \\
    -m daimon.face.tts --hub-socket %t/daimon/tts.sock

# Kein Modell im RAM beim Anmelden: aktiviert wird der Socket, nicht der
# Dienst. on-failure, weil ein verlorener Dienst ohne Neustart eine stille
# Stimme waere; der Leerlauf-Exit des GPU-Workers hat hier kein Gegenstueck.
Restart=on-failure

NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
# read-only reicht: die Stimme wird GELESEN. Wer die Stimme nach
# ~/.local/share/daimon/voices/ verlegt (T-3.10), aendert daran nichts.
ProtectHome=read-only
RuntimeDirectory=daimon
RuntimeDirectoryPreserve=yes
ReadWritePaths=%t/daimon
InaccessiblePaths=-%h/.ssh -%h/.gnupg -%h/.local/share/keyrings -%h/.pki
ProtectProc=invisible
ProcSubset=pid
PrivateTmp=yes
LimitCORE=0
UMask=0077
# Der Dienst spricht mit dem Hub, mit seinem Aufrufer und ueber pw-cat mit
# PipeWire -- alles Unix-Sockets. Er laedt NICHTS herunter.
RestrictAddressFamilies=AF_UNIX
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @obsolete @mount @swap @reboot @module
TimeoutStopSec=10

# PrivateDevices=yes traegt hier -- anders als beim GPU-Worker. pw-cat
# braucht den PipeWire-SOCKET, keinen Geraeteknoten; /dev/null und die
# ueblichen Pseudogeraete bleiben auch mit PrivateDevices erreichbar.
PrivateDevices=yes
# MemoryDenyWriteExecute=yes traegt hier: ohne CUDA-JIT braucht nichts W+X.
# (Beim GPU-Worker fehlt die Direktive genau dafuer.)
MemoryDenyWriteExecute=yes

# Kein [Install]: dieser Dienst wird nie direkt aktiviert, sondern
# ausschliesslich vom Socket gestartet.
'''


def baum(pfad, mutationen):
    """Kopiert den Basisbaum und setzt die korrekte Umsetzung ein -- mit den
    uebergebenen Mutationen. `mutationen` ist eine Menge von Schluesseln."""
    for unter in ("daimon", "config"):
        z = os.path.join(pfad, unter)
        if os.path.exists(z):
            shutil.rmtree(z)
    os.makedirs(pfad, exist_ok=True)
    shutil.copytree(os.path.join(basis, "daimon"), os.path.join(pfad, "daimon"))
    shutil.copytree(os.path.join(basis, "config"), os.path.join(pfad, "config"))

    # -- sprechtext.py -------------------------------------------------------
    text = SPRECHTEXT.replace(
        "@@STEUERZEICHEN@@",
        STEUERZEICHEN_MUTANT if "bidi" in mutationen else STEUERZEICHEN_KORREKT)
    text = text.replace(
        "@@UNGEFRAGT@@",
        UNGEFRAGT_MUTANT if "ungefragt" in mutationen else UNGEFRAGT_KORREKT)
    schreibe(os.path.join(pfad, "daimon", "hub", "sprechtext.py"), text)

    # -- abkuehlung.py -------------------------------------------------------
    text = ABKUEHLUNG.replace(
        "@@PERSISTENZ_LADEN@@",
        PERSISTENZ_MUTANT_LADEN if "persistenz" in mutationen
        else PERSISTENZ_KORREKT_LADEN)
    text = text.replace(
        "@@PERSISTENZ_SCHREIBEN@@",
        PERSISTENZ_MUTANT_SCHREIBEN if "persistenz" in mutationen
        else PERSISTENZ_KORREKT_SCHREIBEN)
    schreibe(os.path.join(pfad, "daimon", "hub", "abkuehlung.py"), text)

    # -- tts.py ----------------------------------------------------------------
    text = TTS.replace(
        "@@UNTERBRECHUNG@@",
        UNTERBRECHUNG_MUTANT if "unterbrechung" in mutationen
        else UNTERBRECHUNG_KORREKT)
    text = text.replace(
        "@@VALIDIERUNG_DIENST@@",
        VALIDIERUNG_DIENST_MUTANT if "validator_dienst" in mutationen
        else VALIDIERUNG_DIENST_KORREKT)
    if "validator_dienst" in mutationen:
        # Der Dienst braucht den Validator dann selbst.
        text = ersetzen(
            text,
            "from daimon.gpu.worker import eigener_socket, hub_anfrage, sd_socket",
            "from daimon.gpu.worker import eigener_socket, hub_anfrage, sd_socket\n"
            "from daimon.hub import sprechtext",
            "tts.py imports")
    schreibe(os.path.join(pfad, "daimon", "face", "tts.py"), text)

    # -- daemon.py patchen -----------------------------------------------------
    dp = os.path.join(pfad, "daimon", "hub", "daemon.py")
    with open(dp, encoding="utf-8") as f:
        d = f.read()
    d = ersetzen(d,
        "from daimon.hub.marks import FreigabeBuch, MarkenBuch, MarkenFehler\n",
        "from daimon.hub.marks import FreigabeBuch, MarkenBuch, MarkenFehler\n"
        + DAEMON_IMPORTS, "daemon.py imports")
    d = ersetzen(d, 'GPU_SOCKET = "gpu.sock"\n',
                 'GPU_SOCKET = "gpu.sock"\n' + DAEMON_KONSTANTEN,
                 "daemon.py konstanten")
    d = ersetzen(d,
        '        self.gpu_reserve_mib = int(self.cfg.get("gpu.reserve_mib", GPU_RESERVE_MIB))\n',
        '        self.gpu_reserve_mib = int(self.cfg.get("gpu.reserve_mib", GPU_RESERVE_MIB))\n'
        + DAEMON_INIT, "daemon.py __init__")
    methoden = DAEMON_METHODEN.replace(
        "@@VALIDIERUNG_HUB@@",
        VALIDIERUNG_HUB_MUTANT if "validator_hub" in mutationen
        else VALIDIERUNG_HUB_KORREKT)
    d = ersetzen(d,
        "    # -- State-Socket ------------------------------------------------------\n",
        methoden + "\n    # -- State-Socket ------------------------------------------------------\n",
        "daemon.py methoden")
    d = ersetzen(d,
        "        t = threading.Thread(target=self._horche_push, daemon=True)\n",
        DAEMON_START + "        t = threading.Thread(target=self._horche_push, daemon=True)\n",
        "daemon.py start")
    schreibe(dp, d)

    # -- state.py patchen ------------------------------------------------------
    sp = os.path.join(pfad, "daimon", "hub", "state.py")
    with open(sp, encoding="utf-8") as f:
        s = f.read()
    s = ersetzen(s,
        "    # -- Lesen -------------------------------------------------------------",
        STATE_SET_VOICE + "    # -- Lesen -------------------------------------------------------------",
        "state.py set_voice")
    schreibe(sp, s)

    # -- Units -----------------------------------------------------------------
    schreibe(os.path.join(pfad, "config", "systemd", "daimon-tts.socket"), UNIT_SOCKET)
    schreibe(os.path.join(pfad, "config", "systemd", "daimon-tts.service"), UNIT_SERVICE)


def schreibe(pfad, text):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(text)


# ===========================================================================
# Das Gut-Muster: die korrekte Referenz des Reviewers.
# ===========================================================================
baum(gut, set())
schreibe(os.path.join(gut, "HERKUNFT.txt"), """GUT-MUSTER fuer tests/verify/T-3.9.sh -- die Referenz des REVIEWERS.

Dieser Baum ist die minimal korrekte Umsetzung von T-3.9, geschrieben vom
Reviewer, blind gegen dieselbe Akzeptanzliste wie die echte Implementierung
(Validator im Hub auf tts.sock, persistierte Abkuehlung, Unterbrechung per
kill, Steuerzeichen entfernt, ungefragt nur aus kuratierten Vorlagen,
sherpa-onnx VITS auf der CPU, pw-cat ohne absoluten Pfad).

Er beweist, dass der Verifizierer gruen werden KANN (Fall 12 in
HANDOVER.md: ein Verifizierer, der nie gruen wird, meldet jeden Mutanten
als "erkannt", ohne dessen Mutation je gemessen zu haben).

SOBALD DIE ECHTE IMPLEMENTIERUNG ABGENOMMEN IST, gehoert dieses Gut-Muster
aus dem abgenommenen Stand erneuert (Vorgehen wie bei T-2.7). Bis dahin ist
er der Liefergegenstand des Reviewers. Erzeugt von
tests/mutants/T-3.9/erzeugen.sh -- nicht von Hand editieren, sondern dort
aendern und neu erzeugen.
""")

# ===========================================================================
# Die fuenf Mutanten. Jeder entfernt genau eine Zusage.
# ===========================================================================
MUTANTEN = {
    "validator-im-dienst": ({"validator_hub", "validator_dienst"}, """MUTANT: die Pruefung passiert im TTS-DIENST statt im Hub.

Der Hub gibt fuer JEDEN Text eine Marke aus, ohne ihn zu pruefen; der
Dienst prueft selbst. Damit ist der Validator umgehbar: ein anderer
Produzent, der eine Marke bekommt (oder die Pruefung im Dienst anders
auslegt), schiebt ungeprueften Text an die Ausgabe -- genau der Satz aus
Design §8.3, der die Lage des Validators begruendet.

Sichtbar wird das daran, dass der HUB fuer einen Angriffstext `ok: true`
plus Marke meldet. Die Zusage "ein Text ohne Hub-Freigabe wird nicht
gesprochen" haelt er aeusserlich noch -- die Marke ist ja weiterhin
noetig. Gebrochen ist: die Freigabe bedeutet nichts mehr.

Gefangen von: den Hub-Pruefungen der zehn Angriffstexte (Kriterien 6 und
12) -- der Hub muss jeden einzelnen selbst ablehnen, mit dem Grund der
verletzten Regel, nicht mit einer Marke.
"""),
    "abkuehlung-nicht-persistiert": ({"persistenz"}, """MUTANT: die Abkuehlung lebt nur im Speicher.

`Abkuehlung` laedt den Bestand nicht und schreibt ihn nicht. Alles andere
stimmt: die Fristen (20/10/3 s) wirken im laufenden Prozess, `darf()` und
`vermerke()` sind unveraendert, die Datei-Machinerie ist sogar noch da --
sie wird nur nie gerufen. Ein Verifizierer, der die Abkuehlung nur im
laufenden Prozess prueft, ist bei ihm vollstaendig gruen.

Gefangen von: dem Neustart der Hubs mitten im Abkuehlungsfenster
(Kriterium 8) -- nach dem Neustart muss dieselbe Anfrage weiter abgelehnt
werden -- und davon, dass nach einer Wiedergabe eine Abkuehlungsdatei im
Zustandsverzeichnis liegt.
"""),
    "unterbrechung-wartet": ({"unterbrechung"}, """MUTANT: die neue Aeusserung reiht sich an statt abzubrechen.

Statt den laufenden pw-cat-Prozess zu toeten, wartet der Dienst auf
dessen Ende. Kein Mischen zweier Stimmen -- aber auch keine Unterbrechung:
die neue Aeusserung kommt erst, wenn die alte zuende ist. Kriterium 4
verlangt den Abbruch binnen 100 ms, gemessen am Ende des alten
pw-cat-Prozesses.

Gefangen von: der Unterbrechungsmessung -- die alte Wiedergabe muss
binnen 100 ms nach der neuen Anfrage tot sein. Bei ihm dauert es den Rest
der alten Wiedergabe (Sekunden).
"""),
    "bidi-escapt-statt-entfernt": ({"bidi"}, """MUTANT: Steuerzeichen werden ESCAPT statt ENTFERNT.

Der Validator benutzt die Behandlung von `wert_saeubern` (der
Bubble-Sanitizer aus T-1.7): aus einem Bidi-Override wird die
Zeichenkette `\\u202E`. Fuer die Anzeige ist das richtig und dort
eingefroren; vorgelesen ist es Unsinn -- Design §8.3 verlangt fuer die
Stimme: Steuerzeichen, Bidi-Overrides und Nullbreitenzeichen werden
ENTFERNT.

Gefangen von: den Steuerzeichen-Pruefungen (Kriterien 6 und 12) -- der
freigegebene Text darf weder das Steuerzeichen noch eine Escape-Folge
enthalten, und er muss KUERZER sein als die Eingabe. Bei ihm ist er
laenger und enthaelt `\\u202E` wörtlich.
"""),
    "ungefragt-nimmt-freien-text": ({"ungefragt"}, """MUTANT: der Kanal `ungefragt` nimmt freien Text.

Kriterium 5 ist aufgeweicht: statt freien Text abzulehnen, laeuft er
durch dieselben Regeln wie eine Antwort. Damit kann ein Modell dem Nutzer
ungefragt beliebige Saetze vorlesen lassen, solange sie harmlos AUSSEHEN
-- genau der Kanal, fuer den die kuratierten Vorlagen existieren.

Gefangen von: der Ungefragt-Pruefung (Kriterium 5) -- freier Text auf
`ungefragt` wird abgelehnt, auch ein harmloser Satz; die Positivkontrolle
daneben (eine kuratierte Vorlage geht durch) ist bei ihm korrekt gruen.
"""),
}

for name, (mutationen, notiz) in MUTANTEN.items():
    baum(os.path.join(ziel_wurzel, name), mutationen)
    schreibe(os.path.join(ziel_wurzel, name, "mutation.txt"), notiz)

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Erzeugung fehlgeschlagen"; exit 1; }

# --- Gegenprobe ---------------------------------------------------------------
# Ein abgebrochenes Erzeugungsskript hat schon einmal eine unveraenderte Kopie
# hinterlassen, und ein Mutantenbaum hat schon einmal ein Artefakt der
# unmutierten Quelle mitgeschleppt. Beides faellt hier auf, bevor irgendein
# Lauf etwas "beweist".
rc=0
MUTANTEN=(validator-im-dienst abkuehlung-nicht-persistiert unterbrechung-wartet \
          bidi-escapt-statt-entfernt ungefragt-nimmt-freien-text)
BAEUME=("$GUT")
for m in "${MUTANTEN[@]}"; do BAEUME+=("$SCRIPT_DIR/$m"); done

for b in "${BAEUME[@]}"; do
  name="$(basename "$b")"
  for pflicht in daimon/hub/sprechtext.py daimon/hub/abkuehlung.py \
                 daimon/face/tts.py daimon/hub/daemon.py daimon/hub/state.py \
                 config/systemd/daimon-tts.service config/systemd/daimon-tts.socket; do
    if [[ ! -f "$b/$pflicht" ]]; then
      echo "FEHLER: $name hat kein $pflicht"; rc=1
    fi
  done
  if ! python3 -m compileall -q "$b/daimon" >/dev/null 2>&1; then
    echo "FEHLER: $name ist syntaktisch kaputt"; python3 -m compileall "$b/daimon" 2>&1 | grep -i error | head -5; rc=1
  fi
  find "$b/daimon" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
  # Ausserhalb der fuenf T-3.9-Dateien plus daemon.py/state.py darf sich
  # nichts vom Basis-Commit unterscheiden -- sonst misst der Verifizierer
  # spaeter etwas anderes als die Mutation.
  rest="$(diff -r -x sprechtext.py -x abkuehlung.py -x tts.py -x daemon.py \
               -x state.py -x __pycache__ "$BASIS/daimon" "$b/daimon" | grep -c '^[<>]')"
  if [[ "$rest" -ne 0 ]]; then
    echo "FEHLER: $name weicht ausserhalb der T-3.9-Dateien vom Basis-Commit ab"; rc=1
  fi
done

# Die Baeume muessen sich VONEINANDER unterscheiden.
for i in "${!BAEUME[@]}"; do
  for j in "${!BAEUME[@]}"; do
    [[ "$i" -lt "$j" ]] || continue
    if diff -qr -x mutation.txt -x HERKUNFT.txt -x __pycache__ \
            "${BAEUME[$i]}" "${BAEUME[$j]}" >/dev/null 2>/dev/null; then
      echo "FEHLER: $(basename "${BAEUME[$i]}") und $(basename "${BAEUME[$j]}") sind identisch"
      rc=1
    fi
  done
done

# Probe aufs Exempel: jede Mutation ist WIRKLICH im Baum -- und das Gut-Muster
# traegt keine davon.
probe() {  # $1 = Datei-Relativpfad, $2 = Muster, $3 = Soll im Gut-Muster (ja/nein)
  local f gut_hat
  f="$1"
  if grep -qF "$2" "$GUT/$f"; then gut_hat=ja; else gut_hat=nein; fi
  if [[ "$gut_hat" != "$3" ]]; then
    echo "FEHLER: Gut-Muster $f: Muster '$2' -> $gut_hat, erwartet $3"; rc=1
  fi
}
probe daimon/hub/daemon.py "MUTANT: der Hub prueft NICHT" nein
probe daimon/hub/abkuehlung.py "self._laden()" ja
probe daimon/hub/abkuehlung.py "self._schreiben()" ja
probe daimon/face/tts.py "self._pwc.kill()" ja
probe daimon/hub/sprechtext.py 'grund="freier_text"' ja
probe daimon/hub/sprechtext.py "ENTFERNT, nicht escapt" ja

mprobe() {  # $1 = Mutant, $2 = Datei, $3 = Muster, $4 = Soll (ja/nein)
  local hat
  if grep -qF "$3" "$SCRIPT_DIR/$1/$2"; then hat=ja; else hat=nein; fi
  if [[ "$hat" != "$4" ]]; then
    echo "FEHLER: Mutant $1: '$3' in $2 -> $hat, erwartet $4"; rc=1
  fi
}
mprobe validator-im-dienst daimon/hub/daemon.py "MUTANT: der Hub prueft NICHT" ja
mprobe validator-im-dienst daimon/face/tts.py "MUTANT: die Pruefung passiert HIER" ja
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "MUTANT: der Bestand wird nicht geladen" ja
# Die Methode bleibt definiert (sonst faellt der Mutant schon am Aufruf und
# nicht an der Messung), aber der AUFRUF ist weg.
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "def _laden" ja
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "self._laden()" nein
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "self._schreiben()" nein
mprobe unterbrechung-wartet daimon/face/tts.py "self._pwc.wait()" ja
mprobe unterbrechung-wartet daimon/face/tts.py "self._pwc.kill()" nein
mprobe bidi-escapt-statt-entfernt daimon/hub/sprechtext.py "MUTANT: escapt statt entfernt" ja
mprobe bidi-escapt-statt-entfernt daimon/hub/sprechtext.py "u%04X" ja
mprobe ungefragt-nimmt-freien-text daimon/hub/sprechtext.py 'grund="freier_text"' nein

echo
echo "Gut-Muster: $GUT"
echo "Mutanten:   ${MUTANTEN[*]}"
exit $rc
