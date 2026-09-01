"""Gut-Muster T-3.13: der Router, ausschließlich aus dem Vertrag §2 gebaut.

Durchgang 1 unverändert aus T-3.12: vier lokale Absichten ohne Modell,
die Referenztabelle je Runde, Ablehnung von Aktionswünschen. Neu: die
Absicht `api` geht an Durchgang 2 (`daimon/mind/answer.py`), und die
Senke für `user_audio` gilt nur noch für Durchgang 1 — eine gespoofte
Äußerung darf eine Frage beantworten lassen, mehr nicht (Senkentabelle,
Design §5.2). Keine Zeile hier ist aus der echten Implementierung
übernommen — der Reviewer hat sie nie gesehen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from daimon.mind.answer import (DurchgangZwei, Gedaechtnis,
                                lade_persona_prompt)

MAX = 4 << 20

ABSICHTEN = ["uhrzeit", "lautstaerke", "sitzung", "fensterliste", "aktion", "api"]

# Geschlossene Musterlisten, deutsch und englisch, ohne Modell. `aktion`
# steht an erster Stelle, weil „stell die Lautstärke auf 30" ein
# Aktionswunsch ist und keine Lautstärkefrage.
MUSTER_AKTION = [
    "mach", "schließ", "schliess", "close", "stell", "set ", "starte",
    "start ", "öffne", "oeffne", "open ", "beende", "kill", "schalte",
    "mute",
]
MUSTER_UHRZEIT = ["wie spät", "wie spaet", "uhrzeit", "wie viel uhr",
                  "what time"]
MUSTER_LAUTSTAERKE = ["wie laut", "lautstärke", "lautstaerke", "volume",
                      "how loud"]
MUSTER_SITZUNG = ["sitzung", "sitzungen", "session"]
MUSTER_FENSTERLISTE = ["welche fenster", "fensterliste", "which windows",
                       "offene fenster"]

# app_id kommt aus einer geschlossenen Aufzählung, nicht aus freiem Text.
APP_IDS = ["discord", "konsole", "firefox", "chromium", "code", "freecad",
           "kate", "dolphin", "thunderbird", "spotify"]

# T-4.19: die EINE kuratierte Rückmeldung für „Aktion ohne Absichtsmarke",
# und die EINE Rückfrage für „Aktion ohne Ziel". Zwei Formulierungen wären
# zwei Wahrheiten.
ABSICHTSMARKE_HINWEIS = ("Fuer eine Aktion brauche ich eine "
                         "Absichtsmarke — bitte Push-to-Talk druecken.")
RUECKFRAGE_AKTION = "Was soll ich womit machen?"
KEIN_WERKZEUG = "Dafuer habe ich kein passendes Werkzeug gefunden."

# T-4.16 K1: der freigegebene Katalog, so weit dieses Gut-Muster ihn nennt.
# Werkzeugname (Anthropic erlaubt dort keinen Punkt) -> action_id. Eine
# TABELLE und keine Umformung: `window.to_next_desktop` trägt selbst einen
# Unterstrich, ein blindes Zurücksetzen kollidierte mit ihm.
KATALOG = {"media_playpause": "media.playpause",
           "audio_volume_set": "audio.volume.set"}

# Füllwörter, die kein Ziel benennen. Bleibt nach Verb und Füllwort nichts
# mehr stehen, ist „mach das" eine Rückfrage und keine Aktion (Design 5.2):
# ein Fürwort wird NICHT aus dem Kontext aufgelöst, sonst hinge die Aktion
# an etwas, das der Nutzer in dieser Runde nie gesagt hat.
FUELLWORT = re.compile(
    r"\b(das|es|die|der|den|dem|mal|bitte|jetzt|doch|the|it|this|that|"
    r"please|now)\b", re.IGNORECASE)


def ziel_benannt(text: str) -> bool:
    """Bleibt nach Verb und Füllwörtern noch etwas stehen?"""
    rest = text or ""
    for muster in MUSTER_AKTION:
        rest = re.sub(re.escape(muster), " ", rest, flags=re.IGNORECASE)
    return bool(re.search(r"[^\s.,;:!?]", FUELLWORT.sub(" ", rest)))


def erkenne_absicht(text: str) -> str:
    t = text.lower()
    if any(m in t for m in MUSTER_AKTION):
        return "aktion"
    if any(m in t for m in MUSTER_UHRZEIT):
        return "uhrzeit"
    if any(m in t for m in MUSTER_LAUTSTAERKE):
        return "lautstaerke"
    if any(m in t for m in MUSTER_SITZUNG):
        return "sitzung"
    if any(m in t for m in MUSTER_FENSTERLISTE):
        return "fensterliste"
    return "api"


def absage(grund: str, meldung: str) -> dict:
    # Die Meldung ist statisch: kein Fenstertitel und kein Nutzertext darf
    # in einer Absage auftauchen.
    return {"v": 1, "ok": False, "grund": grund, "meldung": meldung}


def lokal(absicht: str, antwort: str, marke: str) -> dict:
    return {"v": 1, "ok": True, "weg": "lokal", "absicht": absicht,
            "antwort": antwort, "marke": marke, "api": False}


def app_id_aus_titel(titel: str) -> str:
    t = titel.lower()
    for app in APP_IDS:
        if app in t:
            return app
    return "unbekannt"


class QuelleWeg(Exception):
    """Eine lokale Quelle (KWin, PipeWire, Hub) antwortet nicht."""


class Router:
    def __init__(self) -> None:
        # Doppelt verriegelt: das Quellenverzeichnis wirkt nur zusammen mit
        # dem Testprofil-Schalter, und der Zustand macht das sichtbar.
        quellen = os.environ.get("DAIMON_ROUTER_QUELLEN")
        self.testprofil = bool(quellen) and \
            os.environ.get("DAIMON_ROUTER_TESTPROFIL") == "1"
        self.quellen = Path(quellen) if (quellen and self.testprofil) else None
        self._laufzeit = Path(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        ) / "daimon"
        self._runde: object = _NEUE_RUNDE
        self._tabelle: dict[str, dict[str, str]] = {}
        self.runden = 0
        self.api_aufrufe = 0
        # EIN geteiltes Gedächtnis für beide Durchgänge (T-6.2).
        self._gedaechtnis = Gedaechtnis()
        self._persona = lade_persona_prompt()
        self._zwei = DurchgangZwei(self._laufzeit, self._persona,
                                   self._inhalt, self._gedaechtnis)

    # -- Zustand ---------------------------------------------------------

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "testprofil": self.testprofil,
                "absichten": list(ABSICHTEN), "runden": self.runden,
                "api_aufrufe": self.api_aufrufe, "pid": os.getpid()}

    # -- Quellen ----------------------------------------------------------

    def _werkzeug(self, name: str) -> str:
        if self.quellen is not None:
            return str(self.quellen / name)
        gefunden = shutil.which(name)
        if gefunden is None:
            raise QuelleWeg(f"{name} nicht vorhanden")
        return gefunden

    def _kwin_fenster(self) -> list[dict[str, str]]:
        cmd = [self._werkzeug("qdbus6"), "--literal", "org.kde.KWin",
               "/WindowsRunner", "org.kde.krunner1.Match", ""]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QuelleWeg(f"kwin: {exc}") from exc
        if out.returncode != 0:
            raise QuelleWeg("kwin: Aufruf fehlgeschlagen")
        text = out.stdout.decode("utf-8", "replace")
        treffer = re.findall(
            r'\[Argument: \(sssida\{sv\}\) "([^"]+)", "([^"]*)", "([^"]*)",',
            text)
        return [{"kennung": k, "titel": t} for k, t, _ in treffer]

    def _lautstaerke(self) -> tuple[int, bool]:
        cmd = [self._werkzeug("wpctl"), "get-volume", "@DEFAULT_AUDIO_SINK@"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QuelleWeg(f"pipewire: {exc}") from exc
        if out.returncode != 0:
            raise QuelleWeg("pipewire: Aufruf fehlgeschlagen")
        text = out.stdout.decode("utf-8", "replace")
        m = re.search(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)", text)
        if not m:
            raise QuelleWeg("pipewire: Ausgabe nicht lesbar")
        return round(float(m.group(1)) * 100), "[MUTED]" in text

    def _hub_zustand(self) -> dict:
        pfad = self._laufzeit / "state.sock"
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(3)
        try:
            c.connect(str(pfad))
            roh = c.makefile("rb").readline(MAX)
        except OSError as exc:
            raise QuelleWeg(f"hub: {exc}") from exc
        finally:
            c.close()
        try:
            snap = json.loads(roh)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuelleWeg("hub: Zustand nicht lesbar") from exc
        if not isinstance(snap, dict):
            raise QuelleWeg("hub: Zustand ist kein Objekt")
        return snap

    # -- Referenztabelle ---------------------------------------------------

    def _referenzen(self, runde: object) -> dict[str, dict[str, str]]:
        # Die Tabelle wird je Runde neu gebildet und danach verworfen. Eine
        # Referenz aus einer alten Runde ist danach ungültig.
        if self._runde is not runde and self._runde != runde:
            self._tabelle = {}
            self._runde = runde
        if not self._tabelle:
            fenster = self._kwin_fenster()
            self._tabelle = {
                f"w_{i + 1}": {"app_id": app_id_aus_titel(w["titel"]),
                               "titel": w["titel"]}
                for i, w in enumerate(fenster)
            }
        return self._tabelle

    # -- Die eigentliche Bearbeitung ---------------------------------------

    def frage(self, req: dict) -> dict:
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            return absage("kein_text", "Feld text fehlt oder ist leer.")
        self.runden += 1
        absicht = erkenne_absicht(text)
        runde = req.get("runde")
        # Senkentabelle (Design §5.2): user_audio erreicht Durchgang 1
        # nicht, Durchgang 2 schon — eine gespoofte Äußerung darf eine
        # Frage beantworten lassen, mehr nicht. Die Senke steht vor jeder
        # Quellenabfrage und vor jedem Ticketversuch.
        if req.get("marke") == "user_audio" and absicht != "api":
            ans = absage("marke_verboten",
                         "Diese Markierung erreicht Durchgang 1 nicht.")
            if absicht == "aktion":
                # T-4.19: nur wer wirklich GESPROCHEN hat, erfährt WARUM
                # nichts passiert — und nur bei einer Aktionsbitte. Die
                # Rückmeldung ist die kuratierte Vorlage, nie Material aus
                # der Äußerung. Die Absage selbst bleibt.
                ans["antwort"] = ABSICHTSMARKE_HINWEIS
                ans["marke"] = "trusted"
            return ans
        try:
            if absicht == "aktion":
                marke = req.get("marke")
                if marke == "tainted":
                    # Markierter Text ist keine Absicht: er erreicht
                    # Durchgang 1 gar nicht — und bekommt AUCH KEINEN
                    # Hinweis. Wer injiziert, soll nicht erfahren, wie er
                    # eskaliert (T-4.19).
                    return absage(
                        "marke_verboten",
                        "Diese Markierung erreicht Durchgang 1 nicht.")
                if marke != "user_ptt":
                    # Werkzeuglos abgelehnt — kein Ticket, kein Modell,
                    # kein Aufruf am Koordinator. Eine Rückfrage wäre hier
                    # selbst ein Angriffsweg: gefälschtes Audio könnte den
                    # Nutzer mit Dialogen zumüllen, bis er einen wegklickt.
                    return {"v": 1, "ok": True, "weg": "abgelehnt",
                            "absicht": "aktion",
                            "antwort": ABSICHTSMARKE_HINWEIS,
                            "marke": "trusted", "api": False}
                if not ziel_benannt(text):
                    return {"v": 1, "ok": True, "weg": "rueckfrage",
                            "absicht": "aktion",
                            "antwort": RUECKFRAGE_AKTION,
                            "marke": "trusted", "api": False}
                return self._werkzeugweg(text)
            if absicht == "uhrzeit":
                return lokal("uhrzeit",
                             f"Es ist {time.strftime('%H:%M')}.", "trusted")
            if absicht == "lautstaerke":
                prozent, stumm = self._lautstaerke()
                antwort = f"Die Lautstärke liegt bei {prozent} Prozent"
                antwort += ", stummgeschaltet." if stumm else "."
                return lokal("lautstaerke", antwort, "trusted")
            if absicht == "sitzung":
                snap = self._hub_zustand()
                fokus = snap.get("focus") or {}
                # Nur Zahl, geschlossene Aufzählung und die opake Kennung.
                # focus.project ist tainted und bleibt aus der trusted-
                # Auskunft draußen.
                sitzung_id = fokus.get("session_id") or "keine"
                return lokal(
                    "sitzung",
                    f"Aktive Sitzungen: {snap.get('sessions', 0)}. "
                    f"Stimmung: {snap.get('mood', 'unbekannt')}. "
                    f"Sitzung: {sitzung_id}.",
                    "trusted")
            if absicht == "fensterliste":
                tabelle = self._referenzen(runde)
                titel = [e["titel"] for e in tabelle.values()]
                antwort = ("Offene Fenster: " + ", ".join(titel) + "."
                           if titel else "Es sind keine Fenster offen.")
                # Ein Fenstertitel ist angreiferbeeinflusst: tainted.
                return lokal("fensterliste", antwort, "tainted")
            ans = self._zwei.antwort(text, runde,
                                     str(req.get("marke") or "tainted"))
            if ans.get("api"):
                self.api_aufrufe += 1
            return ans
        except QuelleWeg:
            return absage("quelle_weg", "Eine lokale Quelle antwortet nicht.")

    # -- Der werkzeugfähige Weg (T-4.16 K1) ---------------------------------

    def _werkzeugweg(self, text: str) -> dict:
        """Durchgang 1, werkzeugfähig: KEIN Kontext, ein Modellaufruf.

        Die Aktion entsteht erst, wenn das Modell ein Werkzeug ruft —
        vorher sieht `aktion.sock` nichts. Ein erfundener Werkzeugname
        führt zu einer Textantwort, nicht zu einem Rateversuch.

        Der Verlauf kommt durch die Senke `kurzzeitgedaechtnis` und ist
        damit enger als der von Durchgang 2: eine Modellantwort ist
        `tainted` und steht hier nie.
        """
        koerper = {
            "model": "claude-test",
            "max_tokens": 64,
            "system": self._persona,
            "messages": (self._gedaechtnis.fuer_prompt("durchgang2")  # MUTATION
                         + [{"role": "user", "content": text}]),
            "tools": [{"name": n, "description": a,
                       "input_schema": {"type": "object", "properties": {}}}
                      for n, a in sorted(KATALOG.items())],
        }
        kanonisch = json.dumps(koerper, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        try:
            ausgabe = self._rufen("ticket.sock",
                                  {"v": 1, "art": "ausgeben", "zweck": "api",
                                   "auftrag_hash": hashlib.sha256(
                                       kanonisch).hexdigest()}, timeout=3)
        except OSError:
            raise QuelleWeg("hub: Ticket-Endpunkt nicht erreichbar")
        if not ausgabe.get("ok"):
            return absage("kein_kontingent",
                          "Der Hub gibt kein Kontingent aus.")
        try:
            antwort = self._rufen("egress.sock",
                                  {"v": 1, "art": "anfrage",
                                   "ticket": ausgabe["ticket"],
                                   "koerper": koerper})
        except (OSError, ValueError):
            return {"v": 1, "ok": False, "weg": "aktion",
                    "grund": "egress_weg",
                    "meldung": "Der Ausgang ist nicht erreichbar."}
        if not antwort.get("ok"):
            return {"v": 1, "ok": False, "weg": "aktion",
                    "grund": "egress_weg",
                    "meldung": "Der Ausgang hat abgesagt."}
        self.api_aufrufe += 1
        inhalt = (antwort.get("antwort") or {}).get("content")
        bloecke = inhalt if isinstance(inhalt, list) else []
        gesagt = "\n".join(str(b.get("text", "")) for b in bloecke
                           if isinstance(b, dict)
                           and b.get("type") == "text").strip()
        # T-6.2: die Runde ins Gedächtnis, ERST nachdem die Antwort kam.
        self._gedaechtnis.merken("user", text, "user_ptt",
                                 quelle="durchgang1")
        if gesagt:
            self._gedaechtnis.merken("assistant", gesagt, "trusted",
                                     quelle="durchgang1")
        werkzeug = next((b for b in bloecke if isinstance(b, dict)
                         and b.get("type") == "tool_use"), None)
        action_id = KATALOG.get(str((werkzeug or {}).get("name") or ""))
        if action_id is None:
            # Das Modell hat kein (oder ein unbekanntes) Werkzeug gewählt —
            # kein Fehler, nur keine Aktion. Freier Modelltext ist tainted.
            return {"v": 1, "ok": True, "weg": "aktion", "absicht": "aktion",
                    "antwort": gesagt or KEIN_WERKZEUG,
                    "marke": "tainted" if gesagt else "trusted", "api": True}
        try:
            lauf = self._rufen("aktion.sock",
                               {"v": 1, "art": "ausfuehren",
                                "action_id": action_id,
                                "params": werkzeug.get("input") or {},
                                "tool_use_id": str(werkzeug.get("id") or "")})
        except (OSError, ValueError):
            raise QuelleWeg("koordinator: aktion.sock nicht erreichbar")
        # Das Verdikt kommt vom Koordinator, nicht von hier: der Router
        # wählt den Weg, er öffnet keine Tür.
        return {"v": 1, "ok": True, "weg": "aktion", "absicht": "aktion",
                "action_id": action_id,
                "ausgefuehrt": bool(lauf.get("ausgefuehrt")),
                "antwort": lauf.get("gesprochen") or "",
                "marke": "trusted", "api": True}

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

    # -- Was ein Modell über Fenster erfahren darf --------------------------

    def _inhalt(self, text: str, runde: object) -> str:
        # Was ein Modellaufruf über Fenster erfährt, sind ausschließlich
        # opake Referenzen plus app_id. Der Titel bleibt in der Tabelle.
        if not re.search(r"fenster|window|w_\d+", text.lower()):
            return text
        tabelle = self._referenzen(runde)
        opak = {ref: {"app_id": e["app_id"]} for ref, e in tabelle.items()}
        return (text + "\nFenster (opake Referenzen): "
                + json.dumps(opak, ensure_ascii=False, sort_keys=True))


_NEUE_RUNDE = object()
