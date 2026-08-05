"""T-3.12 — Routing, Durchgang 1: werkzeugfaehig, kontextlos.

Der Schnitt, um den es geht
----------------------------------------------------------------------------
Durchgang 1 darf Werkzeuge waehlen, sieht dafuer aber **keine
angreiferkontrollierten Zeichenketten** (Design 5.1). Ein Fenstertitel wie
`Rechnung.pdf -- ignoriere vorherige Anweisungen` bleibt Text im Prompt, auch
wenn er in einem typisierten Feld steht. Deshalb erfaehrt ein Modellaufruf ueber
Fenster nur opake Referenzen (`w_1`), und der Titel bleibt in einer Tabelle, die
nach der Runde verworfen wird.

Zwei Dinge, die leicht zu verwechseln sind:

  * Die **Nutzerantwort** auf "welche Fenster sind offen" traegt die Titel und
    ist `tainted`.
  * Der **Prompt** traegt sie nie.

Warum die Erkennung lokal und ohne Modell laeuft
----------------------------------------------------------------------------
Nicht aus Sparsamkeit. Eine Uhrzeitfrage, die erst ein Kontingent verbraucht,
ist ein Kontingent weniger fuer das, was wirklich eines braucht -- und ein
Router, der fuer "wie spaet ist es" ins Netz geht, hat den Zweck des Durchgangs
verfehlt. Geschlossene Musterlisten, deutsch und englisch.

Warum Aktion vor Auskunft geprueft wird
----------------------------------------------------------------------------
"stell die Lautstaerke auf 30" enthaelt das Wort Lautstaerke. Wer die lokale
Auskunft zuerst prueft, beantwortet einen Stellbefehl mit einer Auskunft --
still, plausibel und falsch.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time

# Die geschlossene Aufzaehlung. Ein sechster Wert ist ein Fehler und keine
# Erweiterung: jeder Zweig hier hat eine eigene Zusage im Vertrag.
ABSICHTEN = ("uhrzeit", "lautstaerke", "sitzung", "fensterliste", "aktion",
             "api")

# Aktionswuensche zuerst. Die Liste nennt VERBEN, keine Gegenstaende -- ein
# Muster auf "fenster" wuerde die Auskunft mitfangen.
_AKTION = re.compile(
    r"\b(mach|schliess|schliesse|schließe|beend|beende|starte|start|oeffne|öffne"
    r"|stell|setz|setze|dreh|drehe|mute|stumm|schalte|kill|quit|close|open"
    r"|launch|set|turn|toggle|minimiere|maximiere)\b",
    re.IGNORECASE)

# Fragewoerter. Sie entscheiden zwischen Auskunft und Befehl: "wie ist die
# Lautstaerke" fragt, "stell die Lautstaerke auf 30" stellt. Ohne diese
# Unterscheidung faengt das Verb `open` in "which windows are open" den Satz
# als Aktion ein -- selbst gemessen, beim ersten Testlauf.
_FRAGE = re.compile(r"^\s*(wie|welche[rsn]?|was|wo|wann|wieviel|wie\s+viel"
                    r"|what|which|how|is|are|ist|sind|l[äae]uft|zeig|zeige"
                    r"|sag|nenne|list|show|tell)\b", re.IGNORECASE)

# Ein STELLWERT macht aus einer Frage wieder einen Befehl: "wie waer's mit
# Lautstaerke auf 30" will etwas geaendert haben.
_STELLWERT = re.compile(r"\b(auf|to)\s+\d+|\b\d+\s*%", re.IGNORECASE)

_LOKAL = (
    ("uhrzeit", re.compile(
        r"\b(uhrzeit|wie\s+sp[äae]+t|what\s+time|wieviel\s+uhr|wie\s+viel\s+uhr"
        r"|current\s+time)\b", re.IGNORECASE)),
    ("lautstaerke", re.compile(
        r"\b(lautst(ä|ae|a)rke\w*|wie\s+laut|volume)\b",
        re.IGNORECASE)),
    ("fensterliste", re.compile(
        r"\b(fensterliste|welche\s+fenster|offene\s+fenster|fenster\s+sind"
        r"|which\s+windows|open\s+windows|window\s+list)\b", re.IGNORECASE)),
    ("sitzung", re.compile(
        r"\b(sitzung\w*|session\w*)\b",
        re.IGNORECASE)),
)


def absicht(text: str) -> str:
    """Die Absicht einer Aeusserung. Deterministisch, ohne Modell, ohne Netz."""
    if not isinstance(text, str) or not text.strip():
        return "api"
    auskunft = _FRAGE.search(text) and not _STELLWERT.search(text)
    if _AKTION.search(text) and not auskunft:
        return "aktion"
    for name, muster in _LOKAL:
        if muster.search(text):
            return name
    # Eine Frage, die kein lokales Muster trifft, aber ein Aktionsverb
    # enthaelt, ist ein Befehl in Frageform ("kannst du das Fenster zumachen").
    if _AKTION.search(text):
        return "aktion"
    return "api"


# Ein Fensterbezug im Text. Nur dann bekommt der Modellaufruf ueberhaupt
# Referenzen -- eine Frage nach Monaden braucht keine Fenstertabelle.
# `w_1` zaehlt mit: eine Aeusserung, die eine Referenz NENNT, braucht die
# Tabelle -- sonst zeigt das Modell auf etwas, das es nicht aufloesen kann.
_FENSTERBEZUG = re.compile(
    r"\b(fenster|window|app|anwendung|programm)\w*\b|\bw_\d+\b",
    re.IGNORECASE)

GRUENDE = frozenset({
    "unlesbar", "unbekannte_art", "kein_text", "marke_verboten",
    "kein_kontingent", "egress_weg", "quelle_weg",
})


# -- Die echten Quellen ----------------------------------------------------

_WPCTL = re.compile(r"Volume:\s*([0-9]*\.?[0-9]+)(\s*\[MUTED\])?", re.IGNORECASE)

# Ein Eintrag der KWin-Runner-Antwort: Kennung, Titel, Untertext, ...
# `qdbus6 --literal` schreibt alles in EINE Zeile, samt Icon-Rohbytes -- deshalb
# wird gezielt der Kopf jedes Tupels gelesen und der Rest uebersprungen.
_KWIN_EINTRAG = re.compile(
    r'\(sssida\{sv\}\)\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"')


def lautstaerke_lesen(ausgabe: str) -> dict:
    """`wpctl get-volume` -> `{"prozent": 0..100, "stumm": bool}`.

    Eine unlesbare Ausgabe ist ein FEHLER und keine Null: „0 %" waere eine
    Aussage ueber die Lautstaerke, und die haben wir dann gerade nicht.
    """
    m = _WPCTL.search(ausgabe or "")
    if not m:
        raise ValueError("keine Volume-Zeile in der Ausgabe")
    return {"prozent": int(round(float(m.group(1)) * 100)),
            "stumm": bool(m.group(2))}


# Anwendungsverzeichnisse. Die Aufzaehlung entsteht aus dem, was auf DIESER
# Maschine installiert ist -- nicht aus einer Liste im Code, die veraltet.
_ANWENDUNGEN = ("/usr/share/applications",
                "/usr/local/share/applications",
                os.path.expanduser("~/.local/share/applications"),
                "/var/lib/flatpak/exports/share/applications")

# KDE schreibt "Datei — Anwendung", manche Programme "Datei - Anwendung".
_TITELTRENNER = re.compile(r"\s+[—–-]\s+")


def app_ids_installiert() -> frozenset[str]:
    """Die geschlossene Aufzaehlung: was hier eine `.desktop`-Datei hat.

    `org.kde.konsole.desktop` -> `konsole`, `discord.desktop` -> `discord`.
    """
    ids = set()
    for verzeichnis in _ANWENDUNGEN:
        try:
            for name in os.listdir(verzeichnis):
                if name.endswith(".desktop"):
                    ids.add(name[:-len(".desktop")].rsplit(".", 1)[-1].lower())
        except OSError:
            continue
    return frozenset(ids)


def app_id_aus_titel(titel: str, erlaubt: frozenset[str] | set[str]) -> str:
    """Der Anwendungsteil des Titels -- aber NUR, wenn es ihn wirklich gibt.

    Der Titel ist angreiferbeeinflusst, und deshalb wird sein letzter Teil hier
    nicht uebernommen, sondern NACHGESCHLAGEN. Was nicht installiert ist, wird
    `unbekannt`. Ein Angreifer kann damit hoechstens eine falsche, aber
    existierende Anwendung behaupten -- freien Text kann er nicht einschleusen,
    und genau darauf zielt Design 5.1.

    (Der erste Entwurf hat sich diese Nachschlagung gespart und `app_id` immer
    auf `unbekannt` gesetzt. Das war bequem und hat die Zusage aus §2 des
    Vertrags nicht erfuellt -- gefunden vom fremden Pruefstand.)
    """
    teile = _TITELTRENNER.split(titel or "")
    if len(teile) < 2:
        return "unbekannt"
    kandidat = teile[-1].strip().lower()
    return kandidat if kandidat in erlaubt else "unbekannt"


def fenster_lesen(roh: str,
                  erlaubt: frozenset[str] | set[str] | None = None) -> list[dict]:
    """Die KWin-Runner-Antwort -> `[{"id", "titel", "app_id"}, ...]`.

    `/WindowsRunner` liefert keine `resourceClass` -- gemessen am 05.08. Die
    `app_id` kommt deshalb aus dem Titel, aber nur ueber die Aufzaehlung oben.
    ponytail: sauberer waere ein KWin-Script, das `resourceClass` meldet; das
    braucht der Aktionskatalog in Phase 5 ohnehin.
    """
    if erlaubt is None:
        erlaubt = app_ids_installiert()
    fenster = []
    for treffer in _KWIN_EINTRAG.finditer(roh or ""):
        kennung, titel = treffer.group(1), treffer.group(2)
        fenster.append({"id": kennung, "titel": titel,
                        "app_id": app_id_aus_titel(titel, erlaubt)})
    return fenster


WPCTL = "/usr/bin/wpctl"
QDBUS = "/usr/bin/qdbus6"

# Der Testschalter, und er ist absichtlich unbequem -- Muster aus T-3.11:
# `DAIMON_ROUTER_QUELLEN` allein wirkt NICHT. Erst zusammen mit
# `DAIMON_ROUTER_TESTPROFIL=1`, und dann steht `testprofil: true` im Zustand.
UMGEBUNG_QUELLEN = "DAIMON_ROUTER_QUELLEN"
UMGEBUNG_TESTPROFIL = "DAIMON_ROUTER_TESTPROFIL"


class EchteQuellen:
    """Die vier lokalen Quellen dieser Maschine. Nur LESEN.

    Kein Zustand, kein Zwischenspeicher: eine gecachte Lautstaerke waere eine
    Aussage ueber die Vergangenheit mit dem Gesicht der Gegenwart.
    """

    def __init__(self, *, hub_socket: str, wpctl: str = WPCTL,
                 qdbus: str = QDBUS, testprofil: bool = False) -> None:
        self.hub_socket = hub_socket
        self.wpctl = wpctl
        self.qdbus = qdbus
        self.testprofil = bool(testprofil)

    def _lauf(self, argv: list[str], timeout_s: float = 5.0) -> str:
        # `check=True`: ein Werkzeug, das mit Fehler zurueckkommt, ist eine tote
        # Quelle und keine leere Antwort.
        fertig = subprocess.run(argv, capture_output=True, text=True,
                                timeout=timeout_s, check=True)
        return fertig.stdout

    def uhrzeit(self) -> str:
        return time.strftime("%H:%M")

    def lautstaerke(self) -> dict:
        try:
            roh = self._lauf([self.wpctl, "get-volume", "@DEFAULT_AUDIO_SINK@"])
        except (subprocess.SubprocessError, ValueError) as exc:
            raise OSError(f"wpctl: {type(exc).__name__}") from exc
        try:
            return lautstaerke_lesen(roh)
        except ValueError as exc:
            raise OSError(f"wpctl: {exc}") from exc

    def fenster(self) -> list[dict]:
        try:
            roh = self._lauf([self.qdbus, "--literal", "org.kde.KWin",
                              "/WindowsRunner", "org.kde.krunner1.Match", ""])
        except (subprocess.SubprocessError, ValueError) as exc:
            raise OSError(f"kwin: {type(exc).__name__}") from exc
        return fenster_lesen(roh)

    def sitzung(self) -> dict:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(5.0)
        try:
            c.connect(self.hub_socket)
            c.sendall(json.dumps({"v": 1, "art": "zustand"}).encode() + b"\n")
            roh = c.makefile("rb").readline(1 << 20)
        finally:
            c.close()
        try:
            zustand = json.loads(roh)
        except (json.JSONDecodeError, ValueError) as exc:
            raise OSError("hub: Antwort unlesbar") from exc
        if not isinstance(zustand, dict):
            raise OSError("hub: kein Objekt")
        # Die Feldnamen stammen aus dem ECHTEN Schnappschuss des Hubs
        # (`sessions`, `mood`, `focus.session_id`) und nicht aus meiner
        # Vorstellung davon. Der erste Entwurf las `runden` und `wahrnehmung`
        # -- Felder, die es nicht gibt. Das liefert brav Nullen und sieht aus
        # wie eine Messung: der wiederkehrende Fehler dieses Projekts.
        fokus = zustand.get("focus") or {}
        return {"sitzungen": int(zustand.get("sessions", 0) or 0),
                "mood": str(zustand.get("mood", "unbekannt")),
                "session_id": str(fokus.get("session_id") or "")}


def quellen_aus_umgebung(*, hub_socket: str) -> EchteQuellen:
    """Echte Werkzeuge -- oder Attrappen, aber nur mit BEIDEN Schaltern."""
    verzeichnis = os.environ.get(UMGEBUNG_QUELLEN)
    testprofil = (os.environ.get(UMGEBUNG_TESTPROFIL) == "1"
                  and bool(verzeichnis))
    if not testprofil:
        return EchteQuellen(hub_socket=hub_socket)
    return EchteQuellen(hub_socket=hub_socket,
                        wpctl=os.path.join(verzeichnis, "wpctl"),
                        qdbus=os.path.join(verzeichnis, "qdbus6"),
                        testprofil=True)


class Router:
    """Durchgang 1. Waehlt den Weg, oeffnet keine Tuer.

    `quellen` und `mind` werden hereingereicht und nicht selbst gebaut: der
    Pruefstand soll den Router gegen Attrappen fahren koennen, ohne dass eine
    Umgebungsvariable darueber entscheidet, was echt ist.
    """

    def __init__(self, *, quellen, mind, log=None, testprofil: bool = False):
        self._quellen = quellen
        self._mind = mind
        self._log = log
        self.testprofil = bool(testprofil) or bool(
            getattr(quellen, "testprofil", False))
        self.runden = 0
        self.api_aufrufe = 0
        self._referenzen: dict[str, dict] = {}

    # -- Referenztabelle ---------------------------------------------------

    def aufloesen(self, ref: str) -> dict | None:
        """Eine Referenz der LAUFENDEN Runde. Aeltere sind weg, nicht 'alt'."""
        return self._referenzen.get(ref)

    def _referenzen_bilden(self, fenster: list[dict]) -> list[dict]:
        """`w_1`, `w_2`, ... plus `app_id`. Der Titel bleibt hier.

        Die Tabelle wird bei jeder Runde ERSETZT. Eine Referenz, die eine Runde
        ueberlebt, ist ein Angriffspfad: das Modell koennte auf ein Fenster
        zeigen, das der Nutzer in dieser Runde nie gesehen hat.
        """
        self._referenzen = {}
        offen = []
        for i, f in enumerate(fenster, 1):
            ref = f"w_{i}"
            self._referenzen[ref] = dict(f)
            offen.append({"ref": ref, "app_id": f.get("app_id", "unbekannt")})
        return offen

    # -- Der Weg -----------------------------------------------------------

    def frage(self, anfrage: object) -> dict:
        if not isinstance(anfrage, dict):
            return self._nein("unlesbar", "kein JSON-Objekt")
        art = anfrage.get("art")
        if art == "zustand":
            return self.zustand()
        if art != "frage":
            return self._nein("unbekannte_art", f"art={str(art)[:40]!r}")

        # Die Markenpruefung steht VOR allem anderen. Design 5.2: `user_audio`
        # ist spoofbar und erreicht den werkzeugfaehigen Durchgang nicht -- also
        # darf sie auch keine Quelle abfragen und kein Ticket verbrauchen.
        marke = anfrage.get("marke", "tainted")
        if marke == "user_audio":
            return self._nein("marke_verboten",
                              "user_audio erreicht Durchgang 1 nicht")

        text = anfrage.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._nein("kein_text", "Feld `text` fehlt oder ist leer")

        self.runden += 1
        was = absicht(text)
        if was == "aktion":
            # Kein Executor existiert. Ein Router, der so tut, als koennte er
            # handeln, ist schlimmer als einer, der es sagt.
            return {"v": 1, "ok": True, "weg": "abgelehnt", "absicht": "aktion",
                    "antwort": "Das kann ich noch nicht — es fehlt der "
                               "Ausfuehrer.",
                    "marke": "trusted", "api": False}
        if was != "api":
            return self._lokal(was)
        return self._api(text)

    def _lokal(self, was: str) -> dict:
        try:
            if was == "uhrzeit":
                antwort, marke = f"Es ist {self._quellen.uhrzeit()}.", "trusted"
            elif was == "lautstaerke":
                v = self._quellen.lautstaerke()
                stumm = " (stumm)" if v.get("stumm") else ""
                antwort = f"Die Lautstaerke steht bei {v['prozent']} %{stumm}."
                marke = "trusted"
            elif was == "sitzung":
                s = self._quellen.sitzung()
                # NUR Zahlen, geschlossene Aufzaehlungen und die opake Kennung.
                # `focus.project` bleibt draussen: der Name stammt aus einer
                # Hook-Nutzlast und ist damit `tainted` (Design 5.2) -- und
                # eine Marke gilt fuer die GANZE Antwort. Wer ihn hier
                # mitnaehme, machte aus einer vertrauenswuerdigen Auskunft
                # eine angreiferbeeinflusste, ohne die Marke zu aendern.
                antwort = (f"{s.get('sitzungen', 0)} Sitzungen, Stimmung "
                           f"{s.get('mood', 'unbekannt')}, aktiv "
                           f"{s.get('session_id') or 'keine'}.")
                marke = "trusted"
            else:
                fenster = self._quellen.fenster()
                # Der Titel geht an den NUTZER und ist deshalb `tainted` --
                # nicht, weil er hier schaedlich waere, sondern weil er es in
                # jeder weiteren Senke waere, in die ihn jemand traegt.
                titel = ", ".join(str(f.get("titel", "")) for f in fenster)
                antwort = f"{len(fenster)} Fenster: {titel}" if fenster \
                    else "Keine Fenster offen."
                marke = "tainted"
        except (OSError, KeyError, TypeError, ValueError) as exc:
            # Eine tote Quelle ist eine Absage, kein Absturz: ein haengender
            # Dienst ist von einem stummen nicht zu unterscheiden (Fall 24).
            return self._nein("quelle_weg", f"{type(exc).__name__}",
                              weg="lokal")
        return {"v": 1, "ok": True, "weg": "lokal", "absicht": was,
                "antwort": antwort, "marke": marke, "api": False}

    def _api(self, text: str) -> dict:
        kontext = None
        if _FENSTERBEZUG.search(text):
            try:
                kontext = {"fenster": self._referenzen_bilden(
                    self._quellen.fenster())}
            except (OSError, KeyError, TypeError, ValueError) as exc:
                return self._nein("quelle_weg", f"{type(exc).__name__}",
                                  weg="api")

        antwort = self._mind.frage_api(text, kontext)
        if not antwort.get("ok"):
            grund = str(antwort.get("grund", ""))
            # Ein Kontingentfehler behaelt seinen eigenen Grund; alles andere
            # aus dem Transport wird `egress_weg`. Die Meldung nennt WEDER den
            # Nutzertext NOCH einen Koerper -- sie wird gesprochen.
            if grund in ("kein_kontingent", "kontingent_fenster"):
                return self._nein("kein_kontingent", grund, weg="api",
                                  antwort="Ich habe gerade kein Kontingent.")
            return self._nein("egress_weg", grund or "Egress hat abgelehnt",
                              weg="api",
                              antwort="Ich komme gerade nicht an die API.")
        self.api_aufrufe += 1
        return {"v": 1, "ok": True, "weg": "api", "absicht": "api",
                "antwort": antwort.get("antwort"),
                # Freie Modellausgabe ist markiert, gleich aus welchem
                # Durchgang -- Design 5.2, ohne Ausnahme.
                "marke": "tainted", "api": True,
                "status": antwort.get("status")}

    def _nein(self, grund: str, meldung: str, *, weg: str | None = None,
              **extra) -> dict:
        assert grund in GRUENDE, grund
        return {"v": 1, "ok": False, "weg": weg, "grund": grund,
                "meldung": meldung[:200], "api": False, **extra}

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "testprofil": self.testprofil,
                "absichten": list(ABSICHTEN), "runden": self.runden,
                "api_aufrufe": self.api_aufrufe, "pid": os.getpid()}
