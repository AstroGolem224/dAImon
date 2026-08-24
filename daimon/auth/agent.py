#!/usr/bin/env python3
"""T-1.7 Teil 2 — der Auth-Agent: GTK4-Bestaetigungsdialog und Push-to-Talk.

LAEUFT NUR UNTER SYSTEM-PYTHON 3.14
-----------------------------------
PyGObject (`gi`) ist eine kompilierte Erweiterung und liegt nur im
System-Python; das Projekt-venv ist auf 3.12 festgenagelt (T-1.2) und kann
`gi` nicht importieren. Alles, was ohne GTK pruefbar sein muss -- der
PTT-Umschaltautomat und die Vorschau -- liegt deshalb in `ptt.py` und
`preview.py`, beide reines stdlib. Diese Datei hier ist der Prozess
drumherum und wird von pytest bewusst nicht importiert.

WATCHDOG: DAIMON_MAX_SECS
-------------------------
Wie beim Face (face/src/main.rs): nach DAIMON_MAX_SECS Sekunden hart
beendet, Exit 3. Ein Dialog, der den Fokus greift und nicht mehr hergibt,
hat auf dieser Maschine schon eine Sitzung gekostet. Vorgabe 90 s,
`0` schaltet den Watchdog aus -- das ist ausschliesslich fuer den
Produktivbetrieb unter systemd gedacht. Jeder manuelle Lauf setzt ihn
ausdruecklich, z. B. DAIMON_MAX_SECS=20.

DER NONCE-WEG IST EINE BENANNTE LUECKE
--------------------------------------
Beim Klick auf "Ausfuehren" soll eine `freigabe` mit Nonce und action_hash
an den Hub gehen, die der Agent VOM HUB bekommen hat. Stand T-1.7 Teil 1
gibt es dafuer keinen Weg: `FreigabeBuch.nonce_ausgeben` ist prozessintern,
und auf dem auth-Socket liest der Hub nur -- er antwortet nie, und der Typ
`nonce_anfrage` ist in `PRODUZENTEN` nicht freigegeben. `_nonce_anfragen`
baut die Anfrage bewusst trotzdem: sie scheitert heute an der Typpruefung
des Hubs (Verbindung wird abgebaut, keine Antwort) und liefert None. Damit
ist die Luecke im Laufzeitverhalten sichtbar, nicht nur in einem Kommentar.
Ohne Nonce wird KEINE freigabe gesendet -- der Dialog bleibt offen, und der
Fehler steht im Journal. Erfundene Nonces oder selbst gerechnete Hashes
kaemen einer Vollmacht gleich, die Design 2.4 gerade ausschliesst.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

# Ohne PYTHONPATH lauffaehig machen: daimon/ liegt zwei Ebenen ueber dieser
# Datei. Vor den daimon-Importen, nach den stdlib-Importen.
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk
except (ImportError, ValueError) as exc:
    print(f"PyGObject/GTK4 nicht verfuegbar ({exc}). Der Auth-Agent braucht "
          "System-Python 3.14 mit python3-gi -- NICHT das Projekt-venv.",
          file=sys.stderr)
    sys.exit(1)

from daimon.auth import modal, preview
from daimon.auth.ptt import PTTAutomat
from daimon.common.logging import get_logger

# kglobalaccel: Komponente und Aktion. Die actionId hat VIER Elemente
# [componentUnique, componentFriendly, actionUnique, actionFriendly] -- mit
# dreien registriert doRegister die Komponente nicht (auf dieser Maschine
# nachgewiesen).
KG_BUS = "org.kde.kglobalaccel"
KG_AKTION = ["daimon-auth", "dAImon Auth", "ptt", "dAImon Push-to-Talk"]
# T-3.15: der Kill-Switch der Ohren -- in einer EIGENEN Komponente.
#
# Zwei Aktionen in einer Komponente ueberleben auf diesem kglobalaccel nicht:
# beide `setShortcut`-Aufrufe melden Erfolg und liefern ihre Keycodes zurueck,
# aber `allShortcutInfos` zeigt danach nur die ZULETZT registrierte. Am 09.08.
# isoliert nachgestellt (zwei Probe-Aktionen, F1 und F2, in einer Komponente:
# eine ueberlebt; in zwei Komponenten: beide). Genau daran ist Push-to-Talk
# lautlos verschwunden -- der Kill-Switch kam als zweiter und hat ihn verdraengt.
#
# Die Registrierung ist damit KEIN Erfolgsnachweis: sie hat zurueckgemeldet,
# dass die Taste gesetzt sei, und die Aktion war trotzdem weg. Wer hier etwas
# aendert, liest danach `allShortcutInfos` und glaubt nicht dem Rueckgabewert.
KG_AKTION_OHREN_AUS = ["daimon-ears", "dAImon Ohren", "ohren_aus",
                       "dAImon Ohren abschalten"]
# T-7.3: der Pausenschalter des Mitschnitts -- und wieder eine EIGENE
# Komponente, aus demselben gemessenen Grund wie oben. Der dritte Eintrag in
# einer gemeinsamen Komponente haette die beiden anderen verdraengt.
#
# Umschalter und nicht zwei Tasten: laeuft der Mitschnitt, haelt er an; steht
# er, laeuft er wieder an. Was gerade gilt, liest der Agent am Herzschlag des
# Recorders und nicht an einer eigenen Merkvariablen -- ein Schalter, der sich
# merkt, was er zuletzt getan hat, luegt nach jedem Absturz des Dienstes.
KG_AKTION_MITSCHNITT = ["daimon-recorder", "dAImon Mitschnitt",
                        "mitschnitt_pause", "dAImon Mitschnitt pausieren"]
# Flags fuer setShortcut (kglobalacceld.h, enum SetShortcutFlag):
# SetPresent=2 macht den Shortcut scharf, NoAutoloading=4 verhindert, dass
# die gespeicherte kglobalaccelrc ihn beim naechsten Start ueberschreibt.
# OHNE SetPresent bleibt die Komponente isActive=false und es feuert nichts.
KG_FLAGS = 2 | 4

# Qt-Keycodes: Modifier oben, Zeichen unten. Qt::Key_Space = 0x20,
# Buchstaben und Ziffern sind ihre ASCII-Grossbuchstabenwerte.
_QT_MOD = {"meta": 0x10000000, "alt": 0x08000000,
           "ctrl": 0x04000000, "strg": 0x04000000, "shift": 0x02000000}
_QT_TASTEN = {"space": 0x20, "tab": 0x01000001, "return": 0x01000004,
              "enter": 0x01000005, "escape": 0x01000000}
_QT_TASTEN.update({f"f{i}": 0x01000030 + i - 1 for i in range(1, 25)})

MAX_ZEILE = 1 << 16  # Steuer- und Diagnosezeilen sind winzig; 64 KiB ist grosszuegig.


def kuerzel_nach_qt(text: str) -> int:
    """"Meta+Space" -> Qt-Keycode mit Modifier-Bits. ValueError bei
    unbekannten Modifiern oder Tasten."""
    teile = [t.strip() for t in text.split("+") if t.strip()]
    if not teile:
        raise ValueError(f"Kuerzel {text!r}: leer")
    # Eine NACKTE Taste ist erlaubt (etwa "F8"). Preis: ein globales Kuerzel
    # ohne Modifier sehen alle anderen Anwendungen nicht mehr.
    #
    # TOT-TASTEN GEHEN NICHT. Am 09.08. gemessen: `^` der deutschen Belegung
    # erreicht kglobalaccel ueberhaupt nicht -- weder als Key_AsciiCircum
    # (0x5E) noch als Key_Dead_Circumflex (0x01001257). Die Compose-/xkb-Schicht
    # verschluckt Tot-Tasten, bevor daraus ein globales Kuerzel werden koennte.
    # Die Bindung stand dabei sauber in kglobalaccel (getGlobalShortcutsByKey
    # lieferte sie), und F8 als Positivkontrolle im selben Lauf feuerte. Es
    # sieht also aus wie ein kaputtes Kuerzel und ist eine Eigenschaft der
    # Tastaturbelegung. Betroffen: ^ ´ ` .
    mods = 0
    for m in teile[:-1]:
        bit = _QT_MOD.get(m.lower())
        if bit is None:
            raise ValueError(f"unbekannter Modifier {m!r}")
        mods |= bit
    taste = teile[-1]
    key = _QT_TASTEN.get(taste.lower())
    if key is None and len(taste) == 1 and taste.isprintable():
        key = ord(taste.upper())
    if key is None:
        raise ValueError(f"unbekannte Taste {taste!r}")
    return mods | key


def _watchdog_starten() -> None:
    """Muster aus face/src/main.rs: nach DAIMON_MAX_SECS hart beendet,
    Exit 3. Vorgabe 90 s wie dort; 0 schaltet ab (nur unter systemd).
    Ein Thread mit os._exit, weil ein haengender Main-Loop einen
    GLib-Timer nicht mehr feuern lassen wuerde."""
    roh = os.environ.get("DAIMON_MAX_SECS", "90")
    try:
        sekunden = int(roh)
    except ValueError:
        sekunden = 90
    if sekunden <= 0:
        return

    def waechter() -> None:
        time.sleep(sekunden)
        print(f"WATCHDOG: {sekunden}s erreicht, Prozess wird hart beendet",
              file=sys.stderr, flush=True)
        os._exit(3)

    threading.Thread(target=waechter, daemon=True).start()


class AuthAgent:
    def __init__(self, args: argparse.Namespace) -> None:
        self.log = get_logger("daimon-auth")
        self.hub_socket = args.hub_socket
        self.zeitlimit_s = float(args.zeitlimit)
        self.automat = PTTAutomat(zeitlimit_s=args.zeitlimit, log=self.log)
        self._bus: Gio.DBusConnection | None = None

        # Diagnose-Zaehler. Die Diagnose zaehlt, sie verraet nicht:
        # keine Nonce, kein action_hash, kein Zielpfad (Regel wie im Hub).
        self.dialoge_gezeigt = 0
        self.freigaben_gesendet = 0
        self.marken_gesendet = 0
        self.ohren_abschaltungen = 0
        self.mitschnitt_umschaltungen = 0
        # T-4.12 im Betrieb: welche Rueckfragen dieser Agent schon gezeigt
        # hat. Ohne diese Menge zeigte jede Abfrage denselben Dialog erneut --
        # und ein Dialog, der von selbst wiederkommt, wird weggeklickt.
        self._gezeigt: set[str] = set()
        self.letzte_entscheidung: str | None = None

        self._dialog_sichtbar = False
        self._steuer_puffer: dict[int, bytes] = {}

        self._fenster_bauen()
        self._diag_sock = self._horche(args.diag_socket, self._diag_bedienen)
        self._ctl_sock = self._horche(args.control_socket,
                                      self._steuer_annehmen)
        self._kglobalaccel_registrieren(args.shortcut)
        # T-4.12 im Betrieb: alle 500 ms beim Hub nachsehen, ob eine
        # Rueckfrage offen ist. Ein Takt und kein Push -- der auth-Socket des
        # Hubs ist ein PRODUZENTEN-Socket und liest nicht zurueck; ein
        # zweiter Kanal dafuer waere eine neue Angriffsflaeche fuer einen
        # Dialog, den niemand angefordert hat. 500 ms sind fuer einen
        # Menschen sofort und fuer die CPU nichts.
        GLib.timeout_add(500, self._rueckfragen_zeigen)

    # ------------------------------------------------------------------
    # Fenster
    # ------------------------------------------------------------------

    def _fenster_bauen(self) -> None:
        css = Gtk.CssProvider()
        # Feste Groesse und Monospace: der Verifizierer laesst tesseract
        # ueber den Screenshot laufen; proportionale Standardschrift macht
        # daraus Raten. Der Hintergrund bleibt der des Themas -- deckend,
        # kein set_opacity, kein RGBA-Spiel.
        css.load_from_data(
            b".vorschau { font-family: Monospace; font-size: 14px; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.fenster = Gtk.Window(title="dAImon — Bestätigung")
        self.fenster.set_default_size(560, -1)
        self.fenster.connect("close-request", self._fenster_zu)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        self.fenster.set_child(box)

        # Gtk.Label, kein Zeichnen: der Text muss im AT-SPI-Baum lesbar
        # sein. set_selectable(True) wird bewusst NICHT gesetzt -- es
        # aendert die AT-SPI-Rolle. Der Accessible-Name beschreibt den
        # Block; der eigentliche Text bleibt ueber das Text-Interface
        # lesbar.
        self.label = Gtk.Label(label="", halign=Gtk.Align.START)
        self.label.add_css_class("vorschau")
        self.label.update_property([Gtk.AccessibleProperty.LABEL],
                                   ["Aktionsvorschau zur Bestätigung"])
        box.append(self.label)

        knoepfe = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        knoepfe.set_halign(Gtk.Align.END)
        self.knopf_ausfuehren = Gtk.Button(label="Ausführen")
        self.knopf_ausfuehren.connect("clicked", lambda _b: self._ausfuehren())
        self.knopf_ablehnen = Gtk.Button(label="Ablehnen")
        self.knopf_ablehnen.connect("clicked", lambda _b: self._ablehnen())
        knoepfe.append(self.knopf_ausfuehren)
        knoepfe.append(self.knopf_ablehnen)
        box.append(knoepfe)

    def _zeige_dialog(self, aktion: str, umkehr: str, ziel: str) -> bool:
        """True = Dialog gezeigt, False = abgelehnt (unbekannte Schluessel).
        Der Text ist GENAU das, was preview.vorschau liefert -- hier wird
        nichts selbst zusammengesetzt oder nachformatiert."""
        try:
            text = preview.vorschau(aktion=aktion, ziel=ziel, umkehr=umkehr)
        except preview.VorschauFehler as exc:
            self.log.warn("Dialog-Anfrage abgelehnt", DAIMON_GRUND=str(exc)[:120])
            return False
        self.label.set_text(text)
        self._dialog_sichtbar = True
        self.dialoge_gezeigt += 1
        self.fenster.present()
        self.log.info("Bestaetigungsdialog gezeigt", DAIMON_TYP="dialog")
        return True

    def _verbergen(self) -> None:
        self._dialog_sichtbar = False
        self.fenster.set_visible(False)

    def _fenster_zu(self, _fenster) -> bool:
        """Fenster-X ist ein Schliessen OHNE Entscheidung -- wie
        `schliessen` am Steuer-Socket. True: nicht zerstoeren, nur verbergen."""
        self._verbergen()
        return True

    # ------------------------------------------------------------------
    # Entscheidungen
    # ------------------------------------------------------------------

    def _ausfuehren(self) -> None:
        if not self._dialog_sichtbar:
            return
        nonce_antwort = self._nonce_anfragen()
        if nonce_antwort is None:
            # Siehe Modulkopf: der Hub hat (Stand Teil 1) keinen Nonce-Weg
            # ueber den Socket. Keine Nonce, keine freigabe -- und der
            # Dialog BLEIBT offen, statt eine Entscheidung vorzutaeuschen.
            self.log.error(
                "Ausfuehren nicht moeglich: Hub hat keinen Nonce-Weg "
                "(T-1.7 Teil 1). Es wurde KEINE freigabe gesendet.")
            return
        if self._an_hub("freigabe", {"nonce": nonce_antwort["nonce"],
                                     "action_hash": nonce_antwort["action_hash"]}):
            self.freigaben_gesendet += 1
            self.letzte_entscheidung = "ausgefuehrt"
            self._verbergen()

    def _ablehnen(self) -> None:
        if not self._dialog_sichtbar:
            return
        # Nichts an den Hub. Eine Ablehnung ist keine Freigabe und braucht
        # keine Marke -- sie existiert nur hier, als Entscheidung.
        self.letzte_entscheidung = "abgelehnt"
        self._verbergen()
        self.log.info("Aktion abgelehnt", DAIMON_TYP="dialog")

    # ------------------------------------------------------------------
    # Hub
    # ------------------------------------------------------------------


    # -- Aktionsdialog (T-4.12 im Betrieb) ---------------------------------

    def _rueckfragen_holen(self) -> list[dict]:
        """Was der Hub offen hat. Lesend, ueber `aktion.sock`.

        Der Auth-Agent formuliert NICHTS: Vorschautext, Nonce und
        `action_hash` kommen alle vom Hub. Er zeigt und er meldet zurueck --
        mehr ist seine Rolle nicht (Design 2.2).
        """
        if not self.hub_socket:
            return []
        pfad = str(Path(self.hub_socket).parent / "aktion.sock")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(2.0)
                c.connect(pfad)
                c.sendall(json.dumps({"v": 1, "art": "offene"}).encode() + b"\n")
                roh = c.recv(65536)
            antwort = json.loads(roh.decode("utf-8", "replace"))
        except (OSError, ValueError):
            # Kein Hub, keine Dialoge. Ein Agent, der bei unerreichbarem Hub
            # etwas zeigt, zeigt etwas Erfundenes.
            return []
        return antwort.get("offen") or [] if antwort.get("ok") else []

    def _rueckfragen_zeigen(self) -> bool:
        """Einmal nachsehen und hoechstens EINE Rueckfrage zeigen.

        Hoechstens eine, weil der Dialog modal ist: zwei gleichzeitig waeren
        zwei modale Fenster, und das zweite verdeckt das erste. Die naechste
        Runde des Weckers holt die naechste.
        """
        for rueckfrage in self._rueckfragen_holen():
            kennung = rueckfrage.get("id")
            if not kennung or kennung in self._gezeigt:
                continue
            self._gezeigt.add(kennung)
            self._rueckfrage_beantworten(rueckfrage)
            break
        return True  # der Wecker laeuft weiter

    def _rueckfrage_beantworten(self, rueckfrage: dict) -> None:
        vorlage = modal.Vorlage(
            text=str(rueckfrage.get("prompt_shown") or "")[:600],
            aktion=str(rueckfrage.get("action_id") or ""), umkehr="")
        self.dialoge_gezeigt += 1
        try:
            antwort = modal.zeigen(vorlage)
        except Exception as fehler:
            # Kein Fenster, keine Freigabe. Der Hub laesst die Rueckfrage
            # ablaufen, und das ist `cancelled` -- kein Nein.
            self.log.warn("Aktionsdialog nicht zeigbar",
                          DAIMON_GRUND=str(fehler)[:120])
            return
        try:
            antwort = modal.freigabe_annehmen(erteiler="auth", antwort=antwort)
        except modal.ModalFehler as fehler:
            self.log.warn("Antwort verworfen", DAIMON_GRUND=str(fehler)[:120])
            return
        if antwort != modal.ANTWORT_AUSFUEHREN:
            # Gemeldet wird trotzdem -- mit `antwort` in der Nachricht.
            # Bis zum 20.08. kehrte diese Stelle wortlos zurueck: der Hub
            # kennt dann nur die Freigabe, und ein Nein lief bei ihm in die
            # 120s-Frist wie ein Wegklicken. Das Freigabebuch bleibt
            # trotzdem unberuehrt (`daemon.py` liest `antwort` und ruft
            # `freigaben.bestaetigen` nur beim impliziten oder expliziten
            # `granted`) -- der teure Fehler waere weiter ein faelschlich
            # gemeldetes Ja, nicht ein gemeldetes Nein (Befund T-4.11 K5/K9).
            self.log.info("Aktion nicht freigegeben",
                          DAIMON_ACTION="aktion_dialog",
                          DAIMON_ANTWORT=antwort)
            self._an_hub("freigabe", {"nonce": rueckfrage.get("nonce", ""),
                                      "action_hash": rueckfrage.get("action_hash", ""),
                                      "antwort": antwort})
            return
        if self._an_hub("freigabe", {"nonce": rueckfrage.get("nonce", ""),
                                     "action_hash": rueckfrage.get("action_hash", ""),
                                     "antwort": antwort}):
            self.freigaben_gesendet += 1
            self.log.info("Aktion freigegeben", DAIMON_ACTION="aktion_dialog")

    def _an_hub(self, typ: str, payload: dict) -> bool:
        if not self.hub_socket:
            return False
        zeile = (json.dumps({"v": 1, "type": typ, "payload": payload})
                 .encode() + b"\n")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(1.0)
                c.connect(self.hub_socket)
                c.sendall(zeile)
            return True
        except OSError as exc:
            self.log.warn("Hub nicht erreicht", DAIMON_TYP=typ,
                          DAIMON_GRUND=str(exc)[:120])
            return False

    def _nonce_anfragen(self) -> dict | None:
        """Fragt beim Hub eine Nonce samt action_hash an. Liefert heute
        immer None: der auth-Socket des Hubs ist reines Lesen, der Typ
        `nonce_anfrage` ist nicht freigegeben, die Verbindung wird nach der
        Typpruefung abgebaut. Die Anfrage geht TROTZDEM raus -- ein Weg,
        den es im Code nicht gibt, ist unsichtbar; ein Weg, der scheitert,
        steht im Journal des Hubs und hier.
        """
        if not self.hub_socket:
            return None
        zeile = (json.dumps({"v": 1, "type": "nonce_anfrage", "payload": {}})
                 .encode() + b"\n")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(1.0)
                c.connect(self.hub_socket)
                c.sendall(zeile)
                roh = c.makefile("rb").readline(MAX_ZEILE)
        except OSError as exc:
            self.log.warn("Nonce-Anfrage gescheitert",
                          DAIMON_GRUND=str(exc)[:120])
            return None
        if not roh:
            # Der Hub hat ohne Antwort geschlossen -- der erwartete Stand
            # von Teil 1.
            return None
        try:
            daten = json.loads(roh)
        except json.JSONDecodeError:
            return None
        nonce, action_hash = daten.get("nonce"), daten.get("action_hash")
        if not nonce or not action_hash:
            return None
        # Beide Werte kommen aus der Antwort des Hubs -- der Agent erfindet
        # weder die Nonce noch den Hash.
        return {"nonce": nonce, "action_hash": action_hash}

    # ------------------------------------------------------------------
    # Push-to-Talk
    # ------------------------------------------------------------------

    def _ptt_ausloesen(self) -> None:
        """Ein Ausloeser (Tastenkuerzel oder Steuer-Socket), eine
        Umschaltung. Wird der Automat dabei AKTIV, geht ein intent_mark an
        den Hub -- der Hub erzeugt daraus die Rundenmarke."""
        aktiv = self.automat.umschalten()
        if aktiv and self._an_hub("intent_mark", {}):
            self.marken_gesendet += 1
        self._ptt_melden()
        if aktiv:
            # EIN Wecker beim Einschalten, statt eines Taktes, der dauernd
            # nachsieht. Das Zeitlimit ist der einzige Zeitpunkt, an dem sich
            # ohne Zutun etwas aendert -- und ein pollender Agent kostet
            # Aufwachvorgaenge fuer ein Ereignis, dessen Uhrzeit feststeht.
            # Eine Sekunde Zugabe, damit der Wecker sicher NACH dem Ablauf
            # klingelt und nicht in dieselbe Millisekunde faellt.
            GLib.timeout_add_seconds(int(self.zeitlimit_s) + 1,
                                     self._ptt_wecker)

    def _ptt_wecker(self) -> bool:
        """Einmalig, beim Ablauf des Zeitlimits. `False` = nicht wiederholen."""
        self._ptt_melden()
        return False

    def _ptt_melden(self) -> None:
        """Jeden Zustandswechsel genau einmal an den Hub, auch den stillen.

        T-3.14: `ist_aktiv` rechnet den Ablauf aus, meldet ihn aber niemandem.
        Ohne diese Meldung sieht der Hub das Ende eines PTT-Fensters nie, und
        `listening` haengt im Overlay, bis der Nutzer erneut drueckt.

        Der Hub hat fuer genau diesen Ausfall eine eigene Obergrenze
        (`PTT_FRIST_S`, 150 s). Sie ist die zweite Reihe, nicht der Weg: bleibt
        diese Meldung aus, steht das Overlay eine halbe Minute lang falsch.
        """
        wechsel = self.automat.melden()
        if wechsel is not None:
            self._an_hub("ptt", {"an": wechsel})

    def _kuerzel_verteilen(self, argumente: tuple) -> None:
        """`globalShortcutPressed` -- verteilt auf die KOMPONENTE.

        Feld 0 der Nutzlast ist der eindeutige Komponentenname, Feld 1 ein
        ANZEIGENAME. Der erste Entwurf verglich Feld 1 gegen "ptt" bzw.
        "ohren_aus", traf nie und warf jeden Tastendruck still weg -- der Agent
        bekam die Signale die ganze Zeit. Am 09.08. mit einer Sonde
        nachgewiesen, die die Nutzlast mitschrieb:

            TREFFER /component/daimon_sonde2_f8 ('daimon-sonde2-f8', 'Sonde2 f8')

        Seit jede Aktion ihre eigene Komponente hat (siehe KG_AKTION_OHREN_AUS),
        ist Feld 0 eindeutig. Ein unbekannter Absender wird PROTOKOLLIERT und
        nicht verschwiegen: genau dieses Schweigen hat den Fehler zwei Anlaeufe
        lang versteckt.
        """
        nutzlast = argumente[-1] if argumente else None
        werte = nutzlast.unpack() if hasattr(nutzlast, "unpack") else ()
        komponente = werte[0] if werte else ""
        if komponente == KG_AKTION[0]:
            self._ptt_ausloesen()
        elif komponente == KG_AKTION_OHREN_AUS[0]:
            self._ohren_abschalten()
        elif komponente == KG_AKTION_MITSCHNITT[0]:
            self._mitschnitt_umschalten()
        else:
            self.log.warn("Kuerzel von unbekannter Komponente",
                          DAIMON_KOMPONENTE=str(komponente)[:40],
                          DAIMON_NUTZLAST=str(werte)[:120])

    def _ohren_abschalten(self) -> None:
        """T-3.15: die Ohren-Unit stoppen und das Ergebnis ins Journal.

        Der Auth-Agent laeuft unter System-Python ohne das venv -- deshalb
        wird `killswitch` hier lokal importiert und nicht oben. Das Modul ist
        reines stdlib, genau dafuer.
        """
        try:
            from daimon.ears.killswitch import stoppe
            ergebnis = stoppe()
        except Exception as exc:  # noqa: BLE001 -- ein Kill-Switch stirbt nicht
            print(f"Ohren-Kill-Switch fehlgeschlagen ({exc})", file=sys.stderr)
            return False
        self.ohren_abschaltungen += 1
        self.log.info("Ohren abgeschaltet", DAIMON_ACTION="ohren_aus",
                      DAIMON_OK=ergebnis["ok"], DAIMON_RC=ergebnis["rc"],
                      DAIMON_STROEME=str(ergebnis["aufnahmestroeme_nachher"]))
        return ergebnis["ok"]

    def _mitschnitt_umschalten(self) -> None:
        """T-7.3: Mitschnitt anhalten oder fortsetzen -- der Umschalter.

        Wie beim Ohren-Kill-Switch lokal importiert: der Auth-Agent laeuft
        unter System-Python ohne das venv, und `pause` ist reines stdlib.
        """
        try:
            from daimon.common.config import runtime_dir
            from daimon.recorder.pause import fortsetzen, schneidet_mit, stoppe
            rt = runtime_dir()
            if schneidet_mit(rt):
                ergebnis = stoppe(runtime_dir=rt)
                was = "pause"
            else:
                ergebnis = fortsetzen()
                was = "fortsetzen"
        except Exception as exc:  # noqa: BLE001 -- ein Schalter stirbt nicht
            print(f"Mitschnitt-Schalter fehlgeschlagen ({exc})",
                  file=sys.stderr)
            return
        self.mitschnitt_umschaltungen += 1
        self.log.info("Mitschnitt umgeschaltet", DAIMON_ACTION=was,
                      DAIMON_OK=ergebnis["ok"],
                      DAIMON_MELDUNG=str(ergebnis.get("meldung", ""))[:200])

    def _kglobalaccel_registrieren(self, kuerzel: str,
                                   ohren_kuerzel: str = "Meta+Shift+M",
                                   mitschnitt_kuerzel: str = "Meta+Shift+P"
                                   ) -> None:
        """Meta+Space (Vorgabe) ueber org.kde.kglobalaccel. Scheitert die
        Registrierung (keine Plasma-Sitzung, Name belegt, unbekannte
        Taste), ist das EINE stderr-Zeile und sonst nichts: der
        Steuer-Socket und der Dialog muessen auch ohne KDE pruefbar sein.
        """
        if not kuerzel:
            return
        try:
            taste = kuerzel_nach_qt(kuerzel)
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            def ruf(methode: str, params: GLib.Variant,
                    ziel: str = "/kglobalaccel",
                    iface: str = "org.kde.KGlobalAccel"):
                return bus.call_sync(KG_BUS, ziel, iface, methode, params,
                                     None, Gio.DBusCallFlags.NONE, 3000, None)

            def eintragen(aktion: list[str], taste_qt: int) -> str:
                """Registrieren, Taste setzen, NACHSEHEN. Gibt den
                Komponentenpfad zurueck.

                Das Nachsehen ist der Punkt: `setShortcut` meldet auch dann
                Erfolg, wenn die Aktion gleich darauf verdraengt wird.
                """
                ruf("doRegister", GLib.Variant("(as)", [aktion]))
                ruf("setShortcut", GLib.Variant(
                    "(asaiu)", [aktion, [taste_qt], KG_FLAGS]))
                pfad = ruf("getComponent",
                           GLib.Variant("(s)", [aktion[0]])).unpack()[0]
                infos = ruf("allShortcutInfos", None, ziel=pfad,
                            iface="org.kde.kglobalaccel.Component").unpack()[0]
                # Feldfolge von `(ssssssaiai)`: [3] ist der Aktionsname,
                # [6] die aktiven Tasten.
                gefunden = [i for i in infos if i[3] == aktion[2] and i[6]]
                if not gefunden:
                    raise RuntimeError(
                        f"{aktion[2]!r} steht nach dem Setzen NICHT in der "
                        f"Komponente {aktion[0]!r} -- vermutlich verdraengt")
                bus.signal_subscribe(
                    KG_BUS, "org.kde.kglobalaccel.Component",
                    "globalShortcutPressed", pfad, None,
                    Gio.DBusSignalFlags.NONE,
                    lambda *a: self._kuerzel_verteilen(a))
                return pfad

            eintragen(KG_AKTION, taste)
            # Scheitert nur der Kill-Switch, laeuft PTT weiter -- deshalb ein
            # eigener try und nicht ein zweiter Aufruf im selben.
            try:
                eintragen(KG_AKTION_OHREN_AUS, kuerzel_nach_qt(ohren_kuerzel))
            except Exception as exc:  # noqa: BLE001
                print(f"Ohren-Kuerzel nicht gesetzt ({exc})", file=sys.stderr)
            # Und derselbe eigene try fuer den dritten: scheitert er, laufen
            # die beiden anderen weiter.
            try:
                eintragen(KG_AKTION_MITSCHNITT,
                          kuerzel_nach_qt(mitschnitt_kuerzel))
            except Exception as exc:  # noqa: BLE001
                print(f"Mitschnitt-Kuerzel nicht gesetzt ({exc})",
                      file=sys.stderr)
            self._bus = bus  # haelt Verbindung und Subscriptions am Leben
            self.log.info("Tastenkuerzel registriert",
                          DAIMON_KUERZEL=kuerzel)
        except Exception as exc:  # noqa: BLE001 -- genau HIER darf nichts sterben
            print(f"kglobalaccel-Registrierung fehlgeschlagen ({exc}); "
                  "Agent laeuft ohne Tastenkuerzel weiter", file=sys.stderr)

    # ------------------------------------------------------------------
    # Sockets: Diagnose (nur lesend) und Steuerung (getrennt, 0600)
    # ------------------------------------------------------------------

    def _horche(self, pfad: str, bediene) -> socket.socket:
        p = Path(pfad)
        if p.exists():
            p.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(p))
        os.chmod(p, 0o600)  # nach dem Binden: bind() beachtet die umask
        srv.listen(8)
        srv.setblocking(False)
        GLib.io_add_watch(srv.fileno(), GLib.IO_IN | GLib.IO_HUP,
                          lambda _fd, _io: bediene() or True)
        return srv

    def _diagnose(self) -> dict:
        return {"v": 1,
                "ptt_aktiv": self.automat.ist_aktiv(),
                "ptt_restsekunden": round(self.automat.restsekunden(), 1),
                "dialog_sichtbar": self._dialog_sichtbar,
                "dialoge_gezeigt": self.dialoge_gezeigt,
                "freigaben_gesendet": self.freigaben_gesendet,
                "marken_gesendet": self.marken_gesendet,
                "ohren_abschaltungen": self.ohren_abschaltungen,
                "mitschnitt_umschaltungen": self.mitschnitt_umschaltungen,
                "letzte_entscheidung": self.letzte_entscheidung}

    def _diag_bedienen(self) -> None:
        try:
            conn, _ = self._diag_sock.accept()
            with conn:
                conn.sendall(json.dumps(self._diagnose()).encode() + b"\n")
        except OSError:
            pass

    def _steuer_annehmen(self) -> None:
        try:
            conn, _ = self._ctl_sock.accept()
        except OSError:
            return
        conn.setblocking(False)
        fd = conn.fileno()
        self._steuer_puffer[fd] = b""
        GLib.io_add_watch(fd, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                          lambda wfd, io_: self._steuer_lesen(conn, wfd))

    def _steuer_lesen(self, conn: socket.socket, fd: int) -> bool:
        """Eine Zeile je Verbindung, Antwort ok\\n oder err\\n. False am
        Ende: den io-watch abbauen."""
        try:
            stueck = conn.recv(MAX_ZEILE)
        except BlockingIOError:
            return True
        except OSError:
            self._steuer_weg(conn, fd)
            return False
        if not stueck:  # Gegenstelle weg, keine vollstaendige Zeile
            self._steuer_weg(conn, fd)
            return False
        puffer = self._steuer_puffer.get(fd, b"") + stueck
        if len(puffer) > MAX_ZEILE:
            self._steuer_weg(conn, fd)
            return False
        if b"\n" not in puffer:
            self._steuer_puffer[fd] = puffer
            return True
        zeile = puffer.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
        antwort = b"ok\n" if self._steuer_befehl(zeile) else b"err\n"
        try:
            conn.sendall(antwort)
        except OSError:
            pass
        self._steuer_weg(conn, fd)
        return False

    def _steuer_weg(self, conn: socket.socket, fd: int) -> None:
        self._steuer_puffer.pop(fd, None)
        try:
            conn.close()
        except OSError:
            pass

    def _steuer_befehl(self, zeile: str) -> bool:
        """True = ok. Unbekanntes, Halbes und falsche Schluessel: err."""
        teile = zeile.split()
        if teile == ["ptt"]:
            self._ptt_ausloesen()
            return True
        if teile == ["ohren_aus"]:
            # T-3.15: derselbe Weg, den das Tastenkuerzel nimmt. Ein
            # zweiter Pfad zum Abschalten waere ein zweiter Pfad, der
            # anders kaputtgehen kann -- und der Pruefstand soll den
            # ECHTEN messen, nicht einen Zwilling fuer Tests.
            #
            # Rueckgabewert des Kill-Switch WEITERREICHEN (T-5.10.v Lauf 3,
            # Befund B7): vorher stand hier ein hartes `True`, egal ob
            # `pw-dump` den Beleg liefern konnte. Ein Aufrufer, der "ok"
            # bekommt, obwohl `aufnahmestroeme()` `None` war, haelt die
            # Ohren fuer aus, ohne dass das je gemessen wurde.
            return self._ohren_abschalten()
        if teile == ["schliessen"]:
            self._verbergen()
            return True
        if teile == ["klick", "ausfuehren"]:
            if not self._dialog_sichtbar:
                return False
            self._ausfuehren()
            return True
        if teile == ["klick", "ablehnen"]:
            if not self._dialog_sichtbar:
                return False
            self._ablehnen()
            return True
        if len(teile) == 4 and teile[0] == "zeige":
            # <ziel-b64>: base64-Standard, UTF-8. Bidi-Overrides und
            # Nullbreitenzeichen MUESSEN diesen Weg unveraendert ueberleben
            # -- die Escapes daraus sind die halbe Vorschau.
            try:
                ziel = base64.b64decode(teile[3], validate=True).decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                return False
            return self._zeige_dialog(aktion=teile[1], umkehr=teile[2],
                                      ziel=ziel)
        return False


def main() -> int:
    _watchdog_starten()

    ap = argparse.ArgumentParser(description="dAImon Auth-Agent "
                                 "(Bestaetigungsdialog, Push-to-Talk)")
    ap.add_argument("--hub-socket", default="",
                    help="auth.sock des Hubs; leer = kein Hub (eingeschraenkt)")
    ap.add_argument("--diag-socket", required=True,
                    help="Unix-Socket 0600, eine Zeile JSON je Verbindung, NUR LESEND")
    ap.add_argument("--control-socket", required=True,
                    help="Unix-Socket 0600, eine Zeile je Verbindung, ok/err")
    ap.add_argument("--shortcut", default="Meta+Space",
                    help='kglobalaccel-Kuerzel; leer = keine Registrierung')
    ap.add_argument("--zeitlimit", type=float, default=120.0,
                    help="PTT-Zeitlimit in Sekunden (Rueckfall, weil "
                         "kglobalaccel kein verlaessliches Loslassen liefert)")
    args = ap.parse_args()

    Gtk.init()
    agent = AuthAgent(args)
    agent.log.info("Auth-Agent laeuft", DAIMON_ACTION="start")
    try:
        GLib.MainLoop().run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
