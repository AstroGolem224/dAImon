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
from pathlib import Path
import subprocess
import time

from daimon.common import taint
from daimon.common.protocol import Mark

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

# Handlungs-Anapher: "mach das" nennt kein Ziel. Geprueft wird das ueber eine
# NEGATIVliste -- Verben, Fuerwoerter, Artikel, Fuellwoerter. Bleibt danach
# nichts uebrig, war kein Ziel benannt.
#
# Der erste Entwurf hatte eine Positivliste von Zielwoertern (fenster, discord,
# browser ...) und hat prompt "starte t312-werkzeug" zur Rueckfrage gemacht --
# ein eingefrorener Pruefstand von T-3.12 erwartet dort `abgelehnt`. Eine
# Positivliste kennt das naechste Ziel nie; eine Negativliste kennt das naechste
# FUERWORT sehr wohl, denn davon gibt es eine Handvoll.
_FUELLWORT = re.compile(
    r"\b(das|es|dies|dieses|die|der|den|dem|ein|eine|einen|einem|mal|bitte"
    r"|doch|jetzt|nochmal|kurz|schnell|it|this|that|the|a|an|please|now)\b",
    re.IGNORECASE)


def _ziel_benannt(text: str) -> bool:
    """Bleibt nach Verb und Fuellwoertern noch etwas stehen?"""
    rest = _FUELLWORT.sub(" ", _AKTION.sub(" ", text or ""))
    return bool(re.search(r"[^\s.,;:!?]", rest))

# Die Marke aus der Anfrage in den Typ. Ein unbekannter Wert wird `tainted` --
# Vorgabe ist Misstrauen, auch bei einem Tippfehler im Feld.
_MARKE = {
    "user_ptt": Mark.USER_PTT, "user_audio": Mark.USER_AUDIO,
    "trusted": Mark.TRUSTED, "tainted": Mark.TAINTED,
}

# T-4.19: die EINE kuratierte Rueckmeldung fuer "Aktion ohne Absichtsmarke".
# Eine Vorlage fuer beide Zweige (trusted-Ablehnung, user_audio-Absage) --
# zwei Formulierungen waeren zwei Wahrheiten.
_ABSICHTSMARKE_HINWEIS = ("Fuer eine Aktion brauche ich eine "
                          "Absichtsmarke — bitte Push-to-Talk druecken.")

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

    def kontext(self, text: str) -> dict:
        """T-5.9b: der Bildschirmkontext, ueber das Gate im Hub.

        Gefragt wird IMMER -- das Gate entscheidet. Eine Bezugsliste hier
        waere eine zweite Kopie im Prozess mit dem Modell, und zwei Erkenner
        an zwei Orten sind zwei Wahrheiten.

        Die `turn_id` wird NICHT mitgeschickt: sie entsteht im Hub und
        verlaesst ihn nie. Wer sie nennen muesste, koennte sie nur raten.
        """
        pfad = str(Path(self.hub_socket).with_name("kontext.sock"))
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(5.0)
        try:
            c.connect(pfad)
            c.sendall(json.dumps({"v": 1, "art": "deklassifizieren",
                                  "text": text}).encode() + b"\n")
            roh = c.makefile("rb").readline(1 << 20)
        finally:
            c.close()
        try:
            antwort = json.loads(roh)
        except (json.JSONDecodeError, ValueError) as exc:
            raise OSError("hub: Kontextantwort unlesbar") from exc
        return antwort if isinstance(antwort, dict) else {}

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
        # T-5.9b: wie oft das Gate Kontext freigegeben hat. Eine Absage ist
        # der Normalfall (keine Rundenmarke), deshalb zaehlt nur der Erfolg.
        self.deklassifiziert = 0
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

        # KEIN Vorgabewert: ein fehlendes Feld soll den Text ROH an die
        # Senke gehen lassen, damit `marke_fehlt` entsteht. Hier stand
        # `get("marke", "tainted")`, und damit war der rohe Zweig nur
        # ueber einen unbekannten Markenwert erreichbar -- die Zusage
        # war beschrieben und unerreichbar. Zweimal derselbe Fehler,
        # eine Ebene tiefer.
        marke = anfrage.get("marke")
        text = anfrage.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._nein("kein_text", "Feld `text` fehlt oder ist leer")

        self.runden += 1
        was = absicht(text)

        # Die Senkentabelle sperrt `user_audio` gegen den WERKZEUGFAEHIGEN
        # Durchgang -- nicht gegen den Router. Meine erste Fassung lehnte jede
        # user_audio-Anfrage ab und hat damit Design 5.2 falschherum gelesen:
        # eine gespoofte Aeusserung darf eine FRAGE beantworten lassen, sie darf
        # nur nichts auswaehlen koennen. Genau deshalb hat Durchgang 2 keine
        # Werkzeuge. Gefunden vom fremden Pruefstand.
        #
        # Die Absicht steht hier schon fest, und das ist unbedenklich: sie
        # entsteht aus einer lokalen Musterliste, ohne Quelle und ohne Ticket.
        gereicht = (taint.markiere(text, _MARKE[marke])
                    if marke in _MARKE else text)
        # BEIDE Durchgaenge sind Senken. Vorher lief die Pruefung nur im
        # lokalen Zweig -- eine Inhaltsfrage ging an Durchgang 2 vorbei, ohne
        # dass die Tabelle je gefragt wurde, und `marke_fehlt` konnte dort
        # nicht entstehen. Dritte Gestalt desselben Fehlers in diesem Task:
        # die Zusage gebaut, den Weg daran vorbei uebersehen.
        try:
            taint.pruefe_senke(gereicht,
                               senke="durchgang1" if was != "api" else "durchgang2")
        except taint.SenkenFehler as exc:
            if was == "aktion" and marke == "user_audio":
                # T-4.19-Akzeptanzliste: der Mensch, der wirklich gesprochen
                # hat, erfaehrt sonst nie, WARUM nichts passiert. Die Absage
                # selbst bleibt (ok False, marke_verboten) -- die
                # eingefrorenen Pruefstaende messen genau dieses Paar. Die
                # Rueckmeldung ist die kuratierte Vorlage, kein Material aus
                # der Aeusserung; `tainted` bekommt sie NICHT, einem
                # injizierten Text sagt niemand, wie er eskaliert.
                return self._nein("marke_verboten", str(exc)[:160],
                                  antwort=_ABSICHTSMARKE_HINWEIS,
                                  marke="trusted")
            return self._nein("marke_verboten", str(exc)[:160])

        if was == "aktion" and marke != "user_ptt":
            # T-4.19: eine Aktionsbitte ohne Absichtsmarke wird WERKZEUGLOS
            # abgelehnt -- sie erreicht den Auth-Agenten gar nicht erst.
            #
            # Warum nicht einfach nachfragen: eine Rueckfrage waere selbst ein
            # Angriffsweg. Gefaelschtes Audio -- ein Video, ein Lautsprecher,
            # die eigene Sprachausgabe -- koennte den Nutzer mit Dialogen
            # zumuellen, bis er einen wegklickt. Der Klick waere echt, die
            # Absicht nicht.
            #
            # Diese Pruefung steht VOR der Rueckfrage nach dem Ziel: sonst
            # entstuende genau der Dialog, den sie verhindern soll.
            #
            # Formulierung nach Design 1.3: es fehlt eine ABSICHTSMARKE.
            # Nicht "physische Autorisierung" -- ein Tastendruck belegt, dass
            # jemand etwas wollte, nicht dass er es sein darf.
            return {"v": 1, "ok": True, "weg": "abgelehnt", "absicht": "aktion",
                    "antwort": _ABSICHTSMARKE_HINWEIS,
                    "marke": "trusted", "api": False}
        if was == "aktion" and not _ziel_benannt(text):
            # Design 5.2: "Mach das" verweist NICHT auf Assistententext oder
            # Kontext. Die aktuelle Aeusserung muss Aktion und Ziel nennen --
            # sonst loeste der Router ein Fuerwort aus etwas auf, das der
            # Nutzer in dieser Runde vielleicht nie gesagt hat.
            return {"v": 1, "ok": True, "weg": "rueckfrage",
                    "absicht": "aktion",
                    "antwort": "Was soll ich womit machen?",
                    "marke": "trusted", "api": False}
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

        # T-5.9b: DURCHGANG 2, und nur hier. Freigegebener Kontext ist
        # `tainted`; die Senkentabelle aus T-3.13b verbietet ihn im
        # werkzeugfaehigen Durchgang 1 und erlaubt ihn hier.
        #
        # Gefragt wird IMMER. Das Gate im Hub prueft Rundenmarke und
        # Bildschirmbezug -- eine Vorpruefung an dieser Stelle waere eine
        # zweite Kopie der Bezugsliste im Prozess mit dem Modell.
        #
        # Eine Absage ist KEIN Fehler: sie ist der Normalfall, sobald jemand
        # ohne Push-to-Talk fragt. Der Mind antwortet dann ohne Bildschirm.
        holen = getattr(self._quellen, "kontext", None)
        if holen is not None:
            try:
                frei = holen(text) or {}
            except (OSError, KeyError, TypeError, ValueError):
                frei = {}
            if frei.get("ok") and (frei.get("eintraege") or frei.get("archiv")):
                kontext = dict(kontext or {})
                kontext["bildschirm"] = list(frei.get("eintraege") or [])
                kontext["archiv"] = list(frei.get("archiv") or [])
                self.deklassifiziert += 1

        antwort = self._mind.frage_api(text, kontext)
        if not antwort.get("ok"):
            grund = str(antwort.get("grund", ""))
            # Hat der Durchgang schon einen gueltigen Grund gebildet, wird SEINE
            # Absage durchgereicht -- sie weiss mehr (etwa welcher Durchgang es
            # war). Sie neu zu bauen hiesse, Diagnose wegzuwerfen und dann an der
            # falschen Stelle zu suchen.
            if grund in GRUENDE:
                ergebnis = dict(antwort)
                ergebnis.update({"v": 1, "ok": False, "weg": "api",
                                 "api": False})
                return ergebnis
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
        # Die Antwort des Durchgangs wird DURCHGEREICHT und nicht neu gebaut:
        # sonst faellt beim naechsten Feld (seit T-3.13 `durchgang` und
        # `aktionsvorschlag_erkannt`) still etwas heraus, und niemand merkt es.
        # Was der Router setzt, sind nur die Felder, fuer die ER zustaendig ist.
        ergebnis = dict(antwort)
        ergebnis.update({"v": 1, "ok": True, "weg": "api", "absicht": "api",
                         # Freie Modellausgabe ist markiert, gleich aus welchem
                         # Durchgang -- Design 5.2, ohne Ausnahme.
                         "marke": "tainted", "api": True})
        return ergebnis

    def _nein(self, grund: str, meldung: str, *, weg: str | None = None,
              **extra) -> dict:
        assert grund in GRUENDE, grund
        return {"v": 1, "ok": False, "weg": weg, "grund": grund,
                "meldung": meldung[:200], "api": False, **extra}

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "testprofil": self.testprofil,
                "absichten": list(ABSICHTEN), "runden": self.runden,
                "api_aufrufe": self.api_aufrufe, "pid": os.getpid()}
