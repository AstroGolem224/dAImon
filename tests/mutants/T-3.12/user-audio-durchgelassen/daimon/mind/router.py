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

# Die geschlossene Aufzaehlung. Sechs stammen aus Vertrag T-3.12 §2;
# `erinnerung` und `fokus` kamen mit T-8.5 dazu (beide lokal, beide an den
# Zeitplaner). Der Zeitplaner ist nicht Gegenstand von T-3.12 -- das
# Gut-Muster nennt die beiden, wie der Vertrag es verlangt, und leitet sie
# nicht weiter: kein Vorratscode fuer einen Zulauf, den dieser Pruefstand
# nicht misst.
ABSICHTEN = ["uhrzeit", "lautstaerke", "sitzung", "fensterliste", "aktion",
             "erinnerung", "fokus", "api"]

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

# T-4.19: die EINE kuratierte Rueckmeldung fuer "Aktion ohne Absichtsmarke".
# Wortlaut aus der Akzeptanzliste; zwei Formulierungen waeren zwei Wahrheiten.
ABSICHTSMARKE_HINWEIS = ("Fuer eine Aktion brauche ich eine "
                         "Absichtsmarke — bitte Push-to-Talk druecken.")
RUECKFRAGE_AKTION = "Was soll ich womit machen?"

# T-4.16 K1: der freigegebene Katalog, so weit dieser Pruefstand ihn nennt.
# Werkzeugname (Anthropic erlaubt dort keinen Punkt) -> action_id. Eine
# TABELLE und keine Umformung: `window.to_next_desktop` traegt selbst einen
# Unterstrich, ein blindes Zuruecksetzen kollidierte mit ihm.
KATALOG = {"media_playpause": "media.playpause",
           "audio_volume_set": "audio.volume.set"}

# Fuellwoerter, die kein Ziel benennen. Bleibt nach Verb und Fuellwort nichts
# mehr stehen, ist "mach das" eine Rueckfrage und keine Aktion (Design 5.2).
FUELLWORT = re.compile(
    r"\b(das|es|die|der|den|dem|mal|bitte|jetzt|doch|the|it|this|that|"
    r"please|now)\b", re.IGNORECASE)


def ziel_benannt(text: str) -> bool:
    """Bleibt nach Verb und Fuellwoertern noch etwas stehen?"""
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


def absage(grund: str, meldung: str, antwort: str | None = None) -> dict:
    # Die Meldung ist statisch: kein Fenstertitel und kein Nutzertext darf
    # in einer Absage auftauchen. `antwort` traegt, wenn ueberhaupt, die
    # kuratierte Vorlage -- nie Material aus der Aeusserung.
    ans = {"v": 1, "ok": False, "grund": grund, "meldung": meldung}
    if antwort is not None:
        ans["antwort"] = antwort
        ans["marke"] = "trusted"
    return ans


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
            # T-4.19: nur wer wirklich GESPROCHEN hat, erfaehrt WARUM nichts
            # passiert -- und nur bei einer Aktionsbitte. Die Absicht steht
            # dafuer schon fest, und das ist unbedenklich: sie entsteht aus
            # einer lokalen Musterliste, ohne Quelle und ohne Ticket. Die
            # Absage selbst bleibt (ok false, marke_verboten).
            roh = req.get("text")
            bittet_um_aktion = (isinstance(roh, str)
                                and erkenne_absicht(roh) == "aktion")
            return absage("marke_verboten",
                          "Diese Markierung erreicht Durchgang 1 nicht.",
                          ABSICHTSMARKE_HINWEIS if bittet_um_aktion else None)
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            return absage("kein_text", "Feld text fehlt oder ist leer.")
        self.runden += 1
        absicht = erkenne_absicht(text)
        runde = req.get("runde")
        try:
            if absicht == "aktion":
                marke = req.get("marke")
                if marke == "tainted":
                    # Markierter Text ist keine Absicht: er erreicht
                    # Durchgang 1 gar nicht -- und bekommt AUCH KEINEN
                    # Hinweis. Wer injiziert, soll nicht erfahren, wie er
                    # eskaliert (T-4.19).
                    return absage(
                        "marke_verboten",
                        "Diese Markierung erreicht Durchgang 1 nicht.")
                if marke != "user_ptt":
                    # T-4.19: eine Aktionsbitte ohne Absichtsmarke wird
                    # WERKZEUGLOS abgelehnt -- kein Ticket, kein Modell,
                    # kein Aufruf am Koordinator. Eine Rueckfrage waere hier
                    # selbst ein Angriffsweg: gefaelschtes Audio koennte den
                    # Nutzer mit Dialogen zumuellen, bis er einen wegklickt.
                    return {"v": 1, "ok": True, "weg": "abgelehnt",
                            "absicht": "aktion",
                            "antwort": ABSICHTSMARKE_HINWEIS,
                            "marke": "trusted", "api": False}
                if not ziel_benannt(text):
                    # Design 5.2: "Mach das" verweist NICHT auf
                    # Assistententext oder Kontext. Die aktuelle Aeusserung
                    # muss Aktion und Ziel nennen.
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

    # -- Der werkzeugfaehige Weg (T-4.16 K1) ---------------------------------

    def _werkzeugweg(self, text: str) -> dict:
        """Durchgang 1, werkzeugfaehig: KEIN Kontext, ein Modellaufruf.

        Die Aktion entsteht erst, wenn das Modell ein Werkzeug ruft --
        vorher sieht `aktion.sock` nichts. Ein erfundener Werkzeugname
        fuehrt zu einer Textantwort, nicht zu einem Rateversuch.
        """
        koerper = {
            "model": "claude-test",
            "max_tokens": 64,
            "system": "Du bist dAImon.",
            # Kein Kontext, keine Bildschirmreferenz: nur die Aeusserung
            # dieser Runde -- sie ist user_ptt, das ist oben geprueft.
            "messages": [{"role": "user", "content": text}],
            "tools": [{"name": n, "description": a,
                       "input_schema": {"type": "object", "properties": {}}}
                      for n, a in sorted(KATALOG.items())],
        }
        kanonisch = json.dumps(koerper, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        try:
            ausgabe = self._hub_rufen({"v": 1, "art": "ausgeben",
                                       "zweck": "api",
                                       "auftrag_hash": hashlib.sha256(
                                           kanonisch).hexdigest()})
        except OSError:
            raise QuelleWeg("hub: Ticket-Endpunkt nicht erreichbar")
        if not ausgabe.get("ok"):
            return absage("kein_kontingent",
                          "Der Hub gibt kein Kontingent aus.")
        try:
            antwort = self._egress({"v": 1, "art": "anfrage",
                                    "ticket": ausgabe["ticket"],
                                    "koerper": koerper})
        except (OSError, ValueError):
            return {"v": 1, "ok": False, "weg": "aktion", "grund": "egress_weg",
                    "meldung": "Der Ausgang ist nicht erreichbar."}
        if not antwort.get("ok"):
            return {"v": 1, "ok": False, "weg": "aktion", "grund": "egress_weg",
                    "meldung": "Der Ausgang hat abgesagt."}
        self.api_aufrufe += 1
        inhalt = (antwort.get("antwort") or {}).get("content")
        bloecke = inhalt if isinstance(inhalt, list) else []
        gesagt = "\n".join(str(b.get("text", "")) for b in bloecke
                           if isinstance(b, dict)
                           and b.get("type") == "text").strip()
        werkzeug = next((b for b in bloecke if isinstance(b, dict)
                         and b.get("type") == "tool_use"), None)
        action_id = KATALOG.get(str((werkzeug or {}).get("name") or ""))
        if action_id is None:
            # Das Modell hat kein (oder ein unbekanntes) Werkzeug gewaehlt --
            # kein Fehler, nur keine Aktion. Freier Modelltext ist tainted.
            return {"v": 1, "ok": True, "weg": "aktion", "absicht": "aktion",
                    "antwort": gesagt or "Dafuer habe ich kein passendes "
                                         "Werkzeug gefunden.",
                    "marke": "tainted" if gesagt else "trusted", "api": True}
        try:
            lauf = self._aktion({"v": 1, "art": "ausfuehren",
                                 "action_id": action_id,
                                 "params": werkzeug.get("input") or {},
                                 "tool_use_id": str(werkzeug.get("id") or "")})
        except (OSError, ValueError):
            raise QuelleWeg("koordinator: aktion.sock nicht erreichbar")
        # Das Verdikt kommt vom Koordinator, nicht von hier: der Router
        # waehlt den Weg, er oeffnet keine Tuer.
        return {"v": 1, "ok": True, "weg": "aktion", "absicht": "aktion",
                "action_id": action_id,
                "ausgefuehrt": bool(lauf.get("ausgefuehrt")),
                "antwort": lauf.get("gesprochen") or "",
                "marke": "trusted", "api": True}

    def _aktion(self, anfrage: dict) -> dict:
        pfad = self._laufzeit / "aktion.sock"
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(15)
        try:
            c.connect(str(pfad))
            c.sendall(json.dumps(anfrage, ensure_ascii=False,
                                 separators=(",", ":")).encode() + b"\n")
            return json.loads(c.makefile("rb").readline(MAX))
        finally:
            c.close()

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
