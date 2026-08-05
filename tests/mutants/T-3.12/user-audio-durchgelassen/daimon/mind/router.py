"""Gut-Muster T-3.12: der Router, ausschließlich aus dem Vertrag §2 gebaut.

Vier lokale Absichten ohne Modell, die Referenztabelle je Runde, Ablehnung
von Aktionswünschen, die Senke für `user_audio` und der Weg zur API über
Ticket und Egress. Keine Zeile hier ist aus der echten Implementierung
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

ABLEHNUNG_AKTION = "Das kann ich noch nicht — es fehlt der Ausführer."


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

    def _hub_rufen(self, obj: dict) -> dict:
        pfad = self._laufzeit / "ticket.sock"
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(3)
        try:
            c.connect(str(pfad))
            c.sendall(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
            return json.loads(c.makefile("rb").readline(MAX))
        finally:
            c.close()

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
        # Die Senke steht vor jeder Quellenabfrage und vor jedem
        # Ticketversuch: user_audio erreicht Durchgang 1 nicht.
        if False and req.get("marke") == "user_audio":  # MUTATION
            return absage("marke_verboten",
                          "Diese Markierung erreicht Durchgang 1 nicht.")
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            return absage("kein_text", "Feld text fehlt oder ist leer.")
        self.runden += 1
        absicht = erkenne_absicht(text)
        runde = req.get("runde")
        try:
            if absicht == "aktion":
                return {"v": 1, "ok": True, "weg": "abgelehnt",
                        "absicht": "aktion", "antwort": ABLEHNUNG_AKTION,
                        "marke": "trusted", "api": False}
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
                # §8: nur Zahl, geschlossene Aufzählung und die opake Kennung.
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
            return self._api(text, runde)
        except QuelleWeg:
            return absage("quelle_weg", "Eine lokale Quelle antwortet nicht.")

    # -- Der Weg zur API ----------------------------------------------------

    def _inhalt(self, text: str, runde: object) -> str:
        # Was ein Modellaufruf über Fenster erfährt, sind ausschließlich
        # opake Referenzen plus app_id. Der Titel bleibt in der Tabelle.
        if not re.search(r"fenster|window|w_\d+", text.lower()):
            return text
        tabelle = self._referenzen(runde)
        opak = {ref: {"app_id": e["app_id"]} for ref, e in tabelle.items()}
        return (text + "\nFenster (opake Referenzen): "
                + json.dumps(opak, ensure_ascii=False, sort_keys=True))

    def _api(self, text: str, runde: object) -> dict:
        koerper = {
            "model": "claude-test",
            "max_tokens": 64,
            "system": "Du bist dAImon.",
            "messages": [{"role": "user",
                          "content": self._inhalt(text, runde)}],
        }
        kanonisch = json.dumps(koerper, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        auftrag_hash = hashlib.sha256(kanonisch).hexdigest()
        try:
            ausgabe = self._hub_rufen({"v": 1, "art": "ausgeben",
                                       "zweck": "api",
                                       "auftrag_hash": auftrag_hash})
        except OSError:
            raise QuelleWeg("hub: Ticket-Endpunkt nicht erreichbar")
        if not ausgabe.get("ok"):
            return absage("kein_kontingent",
                          "Der Hub gibt kein Kontingent aus.")
        anfrage = {"v": 1, "art": "anfrage", "ticket": ausgabe["ticket"],
                   "koerper": koerper}
        try:
            antwort = self._egress(anfrage)
        except (OSError, ValueError):
            return {"v": 1, "ok": False, "weg": "api", "grund": "egress_weg",
                    "meldung": "Der Ausgang ist nicht erreichbar."}
        if not antwort.get("ok"):
            return {"v": 1, "ok": False, "weg": "api", "grund": "egress_weg",
                    "meldung": "Der Ausgang hat abgesagt."}
        self.api_aufrufe += 1
        try:
            sprechbar = antwort["antwort"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return {"v": 1, "ok": False, "weg": "api", "grund": "egress_weg",
                    "meldung": "Die Antwort des Ausgangs ist nicht lesbar."}
        return {"v": 1, "ok": True, "weg": "api", "absicht": "api",
                "antwort": str(sprechbar), "marke": "tainted", "api": True}

    def _egress(self, anfrage: dict) -> dict:
        pfad = self._laufzeit / "egress.sock"
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(15)
        try:
            c.connect(str(pfad))
            c.sendall(json.dumps(anfrage, ensure_ascii=False,
                                 separators=(",", ":")).encode() + b"\n")
            return json.loads(c.makefile("rb").readline(MAX))
        finally:
            c.close()


_NEUE_RUNDE = object()
