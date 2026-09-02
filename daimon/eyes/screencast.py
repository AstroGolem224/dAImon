"""T-5.2 -- die Portal-ScreenCast-Sitzung. Bildschirmzugriff mit EINEM Klick.

Die Folge ist die aus Design 4.5 und stammt nicht aus der Dokumentation,
sondern aus `spikes/portal/portal_probe.py`, wo sie am 03.08. gegen ein echtes
xdg-desktop-portal-kde belegt wurde -- einschliesslich der Erkenntnis, dass
`restore_token` einen echten Reboot ueberlebt (T--1.4).

Drei Entscheidungen tragen dieses Modul:

**Der Token wird nach JEDEM `Start` ueberschrieben.** Das Portal DARF einen
neuen ausgeben und den alten fuer verbraucht erklaeren; wer den alten stehen
laesst, haette dann einen, den niemand mehr kennt, und bekaeme wieder einen
Dialog, ohne zu verstehen warum. Hier stand einmal, das Portal TUE das bei
jedem Start -- gemessen am 12.08. gegen xdg-desktop-portal-kde 5 tut es das
nicht, es liefert zweimal denselben Token. Ueberschrieben wird trotzdem: die
Zusage haengt nicht daran, welches Portal gerade laeuft.

**Ein ungueltiger Token faellt INTERAKTIV zurueck, er haengt nicht.** Das ist
die unangenehmere von zwei Moeglichkeiten: der Nutzer sieht wieder einen
Dialog. Die angenehmere waere, still nichts zu tun -- und ein Dienst, der
nichts tut, ist von einem kaputten nicht zu unterscheiden.

**DmaBuf wird abgelehnt, nicht umgangen.** `MAP_BUFFERS` rettet uns nicht, und
ein schwarzes Bild ist schlimmer als eine Fehlermeldung: es sieht aus wie ein
leerer Bildschirm und nicht wie ein Fehler.

Der DBus-Teil ist einspeisbar (`portal=`). Ohne diese Naht braeuchte jeder
Test einen echten Dialog und einen Menschen davor -- und ein Test, der einen
Menschen braucht, wird nicht gefahren.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

# Die Werte sind Vertrag, keine Vorlieben.
TYPES_MONITOR = 1
PERSIST_EXPLICITLY_REVOKED = 2
CURSOR_HIDDEN = 1
CURSOR_EMBEDDED = 2
CURSOR_METADATA = 4

# In welcher Reihenfolge gewaehlt wird, und warum -- nicht nach Bitwert:
#
# 1. METADATA. Der Zeiger kommt als Position NEBEN dem Bild. Die Augen
#    erfahren, wo er steht, ohne dass ein einziges Pixel anders wird.
# 2. HIDDEN. Kein Zeiger. Das Bild bleibt unveraendert, nur die Position
#    fehlt -- der Zweck (den Bildschirm lesen) bleibt vollstaendig da.
#    Deshalb die saubere Rueckfalllinie und nicht das letzte Mittel.
# 3. EMBEDDED. Der Zeiger wird ins Bild gebrannt und damit in die OCR; ein
#    Pfeil ueber einem Buchstaben macht aus dem Buchstaben Rauschen. Besser
#    als nichts, aber schlechter als gar kein Zeiger.
CURSOR_REIHENFOLGE = (CURSOR_METADATA, CURSOR_HIDDEN, CURSOR_EMBEDDED)

# Was das Portal an Datentypen liefern darf. `DmaBuf` steht bewusst NICHT
# drin: ohne GPU-Kontext im Face (T-1.4) waere er nicht abbildbar, und der
# Ausweg ueber `MAP_BUFFERS` liefert schwarze Frames statt eines Fehlers.
SHM_TYPEN = ("MemPtr", "MemFd")


class PortalFehler(RuntimeError):
    """Etwas am Portal-Weg ist unbrauchbar. Immer mit Grund."""


class Portal(Protocol):
    """Die Naht zum DBus. `PortalSitzung` kennt nur diese drei Methoden."""

    def anfrage(self, methode: str, optionen: dict) -> dict: ...
    def schliessen(self) -> None: ...
    def cursor_modi(self) -> int: ...


def cursor_mode_waehlen(verfuegbar: int) -> int | None:
    """Der beste angebotene Modus, oder `None`, wenn das Portal keinen kann.

    `None` heisst ausdruecklich: die Anfrage traegt gar kein `cursor_mode`.
    Genau daran ist der Augendienst am 02.09. in einer Neustartschleife
    gestorben -- er schickte 4 an ein Portal, dessen `AvailableCursorModes`
    0 meldete, und bekam `Unavailable cursor mode 4`. Der Bildschirminhalt
    ist der Zweck, der Zeiger die Zugabe: ein Portal ohne Cursor-Faehigkeit
    ist ein Grund, OHNE Zeiger zu arbeiten, kein Grund, blind zu werden.
    """
    for modus in CURSOR_REIHENFOLGE:
        if verfuegbar & modus:
            return modus
    return None


def datentyp_pruefen(typ: str) -> bool:
    """`True` fuer SHM, sonst `PortalFehler`.

    Die Pruefung sitzt hier und nicht erst in der Pipeline (T-5.3), weil die
    Aushandlung mit dem `Start` beginnt: wer erst beim ersten Frame merkt,
    dass er DmaBuf bekommt, hat die Sitzung schon.
    """
    if typ in SHM_TYPEN:
        return True
    raise PortalFehler(
        f"Portal liefert {typ!r}; verlangt ist SHM ({', '.join(SHM_TYPEN)}). "
        "MAP_BUFFERS wird NICHT als Ausweg benutzt -- ein schwarzes Bild ist "
        "schlimmer als diese Meldung.")


def _token_datei_vorgabe() -> Path:
    """Unter STATE, nicht unter CONFIG.

    Zuerst stand der Token in `$XDG_CONFIG_HOME/daimon/`. Das war falsch,
    und T-5.13 hat es aufgedeckt: die Augen-Unit laeuft mit
    `ProtectHome=read-only` und darf ausser dem Zustandsverzeichnis nichts
    beschreiben -- der Dienst haette den Token nach dem ersten `Start` nicht
    ablegen koennen, und beim naechsten Mal saehe der Nutzer wieder einen
    Dialog.

    Der richtige Ort war es ohnehin nie: ein maschinell erzeugter Token ist
    ZUSTAND, keine Konfiguration. Der Nutzer bearbeitet ihn nicht, er
    entsteht und vergeht mit der Sitzung.
    """
    basis = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(basis) / "daimon" / "screencast-token"


def _widerruf_marke_vorgabe() -> Path:
    """Wo das Face seinen Widerruf vermerkt (`face/src/menu.rs`).

    Unter STATE, nicht unter CONFIG: das Face hat `ProtectHome=read-only` und
    nur `%h/.local/state/daimon` freigegeben. Im Konfigurationsverzeichnis
    liegt ausserdem der `anthropic-token` -- wer dem Overlay dort Schreibrecht
    gaebe, gaebe es ihm fuer beides.
    """
    basis = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(basis) / "daimon" / "screencast-widerruf"


class PortalSitzung:
    """Eine ScreenCast-Sitzung samt Token-Verwaltung."""

    def __init__(self, *, token_datei: Path | None = None,
                 portal: Portal | None = None,
                 widerruf_marke: Path | None = None) -> None:
        self.token_datei = Path(token_datei or _token_datei_vorgabe())
        self.widerruf_marke = Path(widerruf_marke or _widerruf_marke_vorgabe())
        self._portal = portal
        # Kein Vorgabewert mehr. Hier stand `cursor_mode=CURSOR_METADATA`,
        # und das war die Voraussetzung, die den Dienst umgebracht hat: der
        # Modus ist nichts, was der Dienst mitbringt, sondern etwas, das das
        # Portal anbietet. Gesetzt wird er in `oeffnen`, nach dem Fragen.
        self._cursor_mode: int | None = None
        self.session_handle: str = ""

    # -- Token -------------------------------------------------------------

    def _token_lesen(self) -> str | None:
        try:
            daten = json.loads(self.token_datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        wert = daten.get("current")
        return wert if isinstance(wert, str) and wert else None

    def _token_schreiben(self, neu: str) -> None:
        """Atomar, 0600, mit Historie.

        Der Modus wird VOR dem Umbenennen gesetzt: zwischen `write` und
        `chmod` laege sonst ein Fenster, in dem die Datei mit umask-Rechten
        dasteht -- und darin steht ein Schluessel zum Bildschirm.
        """
        alt: dict[str, Any] = {}
        try:
            alt = json.loads(self.token_datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            alt = {}
        historie = alt.get("history")
        if not isinstance(historie, list):
            historie = []
        # Nur wirklich abgeloeste Token in die Historie. KDE liefert bei
        # jedem `Start` denselben zurueck (gemessen 12.08.); ohne diese
        # Bedingung stuenden dort nach zehn Laeufen neun Kopien derselben
        # Zeichenkette, und die Historie waere als Aufzeichnung wertlos.
        if (isinstance(alt.get("current"), str) and alt["current"]
                and alt["current"] != neu):
            historie.append(alt["current"])

        self.token_datei.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_datei.with_suffix(".tmp")
        tmp.write_text(json.dumps({"current": neu, "history": historie[-9:]}),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.token_datei)

    # -- Die Folge ---------------------------------------------------------

    def _cursor_mode_aushandeln(self) -> int | None:
        """Fragen, waehlen, und die Antwort ins Journal schreiben.

        Die Zeile nennt IMMER den gemessenen Wert. Ein stiller Rueckfall
        waere hier das Schlimmste: wer spaeter fragt, warum kein Zeiger im
        Bild ist, muss die Antwort im Protokoll finden und nicht raten.
        """
        try:
            verfuegbar = int(self._portal.cursor_modi())
        except Exception as exc:
            # Das Portal sagt nicht, was es kann. Dann wird nicht geraten --
            # geraten hat den Dienst umgebracht.
            print(f"AvailableCursorModes nicht lesbar ({exc}) -- "
                  "Anfrage ohne cursor_mode", file=sys.stderr, flush=True)
            return None

        gewaehlt = cursor_mode_waehlen(verfuegbar)
        if gewaehlt is None:
            print(f"Portal meldet AvailableCursorModes={verfuegbar} -- kein "
                  "Cursor-Modus verfuegbar, Anfrage geht ohne cursor_mode "
                  "(Bild ja, Zeiger nein)", file=sys.stderr, flush=True)
        else:
            print(f"Portal meldet AvailableCursorModes={verfuegbar}, "
                  f"gewaehlt: cursor_mode={gewaehlt}",
                  file=sys.stderr, flush=True)
        return gewaehlt

    def _select_sources(self, restore_token: str | None) -> None:
        optionen: dict[str, Any] = {
            "types": TYPES_MONITOR,
            "multiple": False,
            "persist_mode": PERSIST_EXPLICITLY_REVOKED,
        }
        # Kein Schluessel statt eines Vorgabewerts: das Portal lehnt einen
        # Modus ab, den es nicht kann, und `SelectSources` wirft dann.
        if self._cursor_mode is not None:
            optionen["cursor_mode"] = self._cursor_mode
        if restore_token:
            optionen["restore_token"] = restore_token
        self._portal.anfrage("SelectSources", optionen)

    def oeffnen(self) -> dict:
        """CreateSession, SelectSources, Start. Gibt den Befund zurueck.

        Der Rueckgabewert nennt ausdruecklich, OB ein interaktiver Rueckfall
        noetig war. Ohne dieses Feld waere „der Nutzer sah einen Dialog" nur
        aus dem Journal zu erraten, und der Verifizierer muesste den Menschen
        fragen.
        """
        if self._portal is None:
            raise PortalFehler("kein Portal eingespeist und kein DBus-Weg")

        create = self._portal.anfrage("CreateSession", {})
        self.session_handle = str(create.get("session_handle") or "")

        # VOR `SelectSources`, weil genau dieser Aufruf den Modus abnimmt.
        self._cursor_mode = self._cursor_mode_aushandeln()

        token = self._token_lesen()
        rueckfall = False
        try:
            self._select_sources(token)
        except PortalFehler:
            if token is None:
                raise
            # Der gespeicherte Token gilt nicht mehr. Genau hier NICHT
            # aufgeben: ein zweiter Versuch ohne Token erzeugt den Dialog,
            # und der Nutzer kann entscheiden.
            rueckfall = True
            self._select_sources(None)
        # OFFEN, und zwar gemessen: gegen xdg-desktop-portal-kde 5 wirft
        # `SelectSources` bei einem ungueltigen `restore_token` NICHT -- es
        # nimmt ihn entgegen und laesst den Dialog bei `Start` erscheinen.
        # Dieser Zweig bleibt dort also stumm, und `interaktiver_rueckfall`
        # meldet `False`, obwohl der Nutzer einen Dialog gesehen hat. Das
        # gehoert gemessen (Dauer von `Start`), nicht geraten -- und der Ort
        # dafuer ist T-5.2.v, nicht dieser Zweig.

        # `Start` bekommt keine Optionen. `cursor_mode` gehoert zu
        # `SelectSources` -- an dieser Stelle stand einmal ein
        # `cursor_mode_gewuenscht`, das auf dem echten Bus niemand gelesen
        # haette, weil die Attrappe alles annimmt, was man ihr gibt.
        start = self._portal.anfrage("Start", {})
        neu = str(start.get("restore_token") or "")
        if not neu:
            raise PortalFehler("Start lieferte keinen restore_token")
        self._token_schreiben(neu)

        return {
            "v": 1,
            "session_handle": self.session_handle,
            "streams": list(start.get("streams") or []),
            "interaktiver_rueckfall": rueckfall,
            "cursor_mode": self._cursor_mode,
        }

    # -- Widerruf ----------------------------------------------------------

    def widerruf_angefordert(self) -> bool:
        """Hat das Face einen Widerruf vermerkt?

        Eine Datei, kein Nachrichtentyp: das Face darf hoechstens
        `bubble_dismiss` und `wahrnehmung_aus` senden (T-1.7.v4), und ein
        dritter haette T-2.7 rot gemacht, das `PRODUZENTEN["face"]` als exakte
        Menge prueft. Derselbe Weg wie bei der Personaauswahl.
        """
        return self.widerruf_marke.exists()

    def widerrufen(self) -> dict:
        """Token loeschen UND Sitzung schliessen. Beides, in dieser Reihenfolge.

        Zuerst die Datei: bricht das Schliessen ab, ist der Schluessel
        trotzdem fort. Andersherum bliebe er liegen, wenn das Portal klemmt.

        Was hier NICHT passiert: `flatpak permission-remove`. Das ist eine
        Aenderung an fremdem Zustand ausserhalb dieses Projekts und steht
        deshalb in der Dokumentation, nicht im Code.
        """
        geloescht = False
        try:
            self.token_datei.unlink()
            geloescht = True
        except OSError:
            pass
        # Die Marke wird VERBRAUCHT. Bliebe sie liegen, widerriefe der
        # naechste Blick erneut -- und der Nutzer saehe bei jedem Start einen
        # Dialog, ohne zu verstehen warum.
        try:
            self.widerruf_marke.unlink()
        except OSError:
            pass
        geschlossen = False
        if self._portal is not None and self.session_handle:
            try:
                self._portal.schliessen()
                geschlossen = True
            except Exception:            # ein klemmendes Portal blockiert nicht
                pass
        self.session_handle = ""
        return {"v": 1, "token_geloescht": geloescht,
                "sitzung_geschlossen": geschlossen}
