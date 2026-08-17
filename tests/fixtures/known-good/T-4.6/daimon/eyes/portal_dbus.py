"""T-5.2 -- der echte DBus-Weg hinter der Naht `Portal`.

Was hier steht, ist aus `spikes/portal/portal_probe.py` uebernommen, wo es am
03.08. gegen ein echtes `xdg-desktop-portal-kde` lief. Nicht aus der
Dokumentation: die Portal-Dokumentation nennt die Signaturen, aber nicht, dass
der zurueckgegebene Request-Pfad vom selbst gebauten abweichen KANN und dass
man dann auf ein Signal wartet, das nie kommt.

Drei Eigenheiten des Portals, die dieses Modul traegt:

**Jeder Aufruf ist asynchron.** Der Methodenaufruf gibt einen Request-Pfad
zurueck, das Ergebnis kommt als `Response`-Signal auf diesem Pfad. Der
Empfaenger wird deshalb VOR dem Aufruf registriert -- andersherum laege
zwischen Aufruf und Registrierung ein Fenster, in dem die Antwort verloren
geht, und der Dienst haenge bis zum Watchdog.

**`response != 0` ist kein Fehler des Programms, sondern eine Antwort.** 1
heisst „der Nutzer hat abgebrochen". Das wird als `PortalFehler` gemeldet und
nicht als Absturz, weil `PortalSitzung` genau daran den Rueckfall erkennt.

**`dbus` liegt nicht im venv.** Der Import steht deshalb in der Funktion, nicht
oben: sonst koennte `daimon.eyes` unter dem venv-Python nicht einmal importiert
werden, und die Tests aus T-5.2 liefen nicht mehr. Derselbe Graben wie beim
Auth-Agenten (T-4.2), der aus demselben Grund unter `/usr/bin/python3` laeuft.
"""
from __future__ import annotations

import uuid
from typing import Any

from daimon.eyes.screencast import PortalFehler

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

# Der erste Lauf braucht einen Menschen, der klickt -- 30 s waeren zu knapp
# fuer jemanden, der gerade woanders hinsieht. Jeder spaetere Lauf traegt den
# Token und kommt in Millisekunden zurueck, ohne dass jemand etwas tut.
FRIST_MIT_KLICK = 120.0
FRIST_OHNE_KLICK = 30.0


def _dbus():
    """Die Module, oder eine Meldung, die sagt was fehlt und warum."""
    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except ImportError as exc:
        raise PortalFehler(
            f"kein DBus-Weg: {exc}. `dbus-python` und `PyGObject` liegen im "
            "System-Python, nicht im venv -- der Augendienst muss wie der "
            "Auth-Agent unter /usr/bin/python3 laufen.") from exc
    return dbus, DBusGMainLoop, GLib


def einfach(wert: Any) -> Any:
    """DBus-Typen zu Python-Typen. Sonst wandert `dbus.String` bis in JSON."""
    dbus, _, _ = _dbus()
    if isinstance(wert, dbus.Dictionary):
        return {str(k): einfach(v) for k, v in wert.items()}
    if isinstance(wert, (dbus.Array, list, tuple)):
        return [einfach(v) for v in wert]
    if isinstance(wert, dbus.Boolean):
        return bool(wert)
    if isinstance(wert, (dbus.String, dbus.ObjectPath)):
        return str(wert)
    if isinstance(wert, int):
        return int(wert)
    return wert


class DbusPortal:
    """Erfuellt `screencast.Portal` gegen das echte Portal.

    Haelt das `session_handle` selbst: `SelectSources` und `Start` brauchen es
    als erstes Argument, und `PortalSitzung` soll von der Signatur nichts
    wissen muessen -- das ist der Zweck der Naht.
    """

    def __init__(self) -> None:
        dbus, DBusGMainLoop, _ = _dbus()
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        objekt = self.bus.get_object(PORTAL_NAME, PORTAL_PATH)
        self.portal = dbus.Interface(objekt, SCREENCAST_IFACE)
        self.absender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.session_handle: str = ""

    # -- der asynchrone Aufruf ---------------------------------------------

    def _warten(self, methode: str, args: list, optionen: dict,
                frist: float) -> dict:
        dbus, _, GLib = _dbus()
        marke = f"{methode.lower()}_{uuid.uuid4().hex}"
        pfad = f"{PORTAL_PATH}/request/{self.absender}/{marke}"
        optionen = dict(optionen)
        optionen["handle_token"] = dbus.String(marke)

        schleife = GLib.MainLoop()
        antwort: dict[str, Any] = {}

        def bei_antwort(code, ergebnisse) -> None:
            antwort["code"] = int(code)
            antwort["ergebnisse"] = ergebnisse
            schleife.quit()

        # VOR dem Aufruf registrieren. Andersherum kann die Antwort schneller
        # sein als die Registrierung, und dann wartet niemand mehr auf sie.
        self.bus.add_signal_receiver(
            bei_antwort, signal_name="Response",
            dbus_interface=REQUEST_IFACE, path=pfad)
        try:
            zurueck = str(getattr(self.portal, methode)(*args, optionen))
            if zurueck != pfad:
                # Kommt vor, wenn der Absendername Zeichen enthaelt, die wir
                # anders uebersetzen als das Portal. Wir wuerden sonst auf
                # einem Pfad lauschen, auf dem nie etwas gesendet wird.
                raise PortalFehler(
                    f"{methode}: Portal antwortet auf {zurueck}, "
                    f"gelauscht wird auf {pfad}")

            def bei_frist() -> bool:
                antwort["frist"] = True
                schleife.quit()
                return GLib.SOURCE_REMOVE

            uhr = GLib.timeout_add(int(frist * 1000), bei_frist)
            schleife.run()
            if not antwort.get("frist"):
                GLib.source_remove(uhr)
        finally:
            self.bus.remove_signal_receiver(
                bei_antwort, signal_name="Response",
                dbus_interface=REQUEST_IFACE, path=pfad)

        if antwort.get("frist"):
            raise PortalFehler(f"{methode}: keine Antwort binnen {frist:.0f} s")
        code = antwort.get("code")
        if code != 0:
            # 1 heisst „abgebrochen". Das ist eine Antwort, kein Absturz --
            # und genau das Signal, an dem `PortalSitzung` den ungueltigen
            # Token erkennt und ohne ihn neu fragt.
            raise PortalFehler(
                f"{methode}: Portal-Antwort {code} "
                f"({'vom Nutzer abgebrochen' if code == 1 else 'abgelehnt'})")
        return einfach(antwort.get("ergebnisse") or {})

    # -- die Naht ----------------------------------------------------------

    def anfrage(self, methode: str, optionen: dict) -> dict:
        dbus, _, _ = _dbus()
        if methode == "CreateSession":
            ergebnis = self._warten(
                "CreateSession", [],
                {"session_handle_token": dbus.String(f"s_{uuid.uuid4().hex}")},
                FRIST_OHNE_KLICK)
            self.session_handle = str(ergebnis.get("session_handle") or "")
            if not self.session_handle:
                raise PortalFehler("CreateSession lieferte kein session_handle")
            return ergebnis

        if not self.session_handle:
            raise PortalFehler(f"{methode} ohne offene Sitzung")
        sitzung = dbus.ObjectPath(self.session_handle)

        if methode == "SelectSources":
            # Immer die kurze Frist. Gemessen am 12.08. gegen
            # xdg-desktop-portal-kde 5: `SelectSources` nimmt die Optionen nur
            # ENTGEGEN und antwortet sofort -- auch mit ungueltigem
            # `restore_token`. Der Dialog haengt an `Start`. Hier stand
            # vorher eine vom Token abhaengige Frist; sie war wirkungslos.
            return self._warten("SelectSources", [sitzung],
                                _typisiert(dbus, optionen), FRIST_OHNE_KLICK)

        if methode == "Start":
            # `parent_window` bleibt leer: dAImon hat kein Fenster, an das der
            # Dialog sich haengen koennte. Das Portal setzt ihn dann frei.
            return self._warten("Start", [sitzung, ""], {}, FRIST_MIT_KLICK)

        raise PortalFehler(f"unbekannte Portal-Methode {methode!r}")

    def pipewire_fd(self) -> int:
        """Der Deskriptor, auf dem die Frames liegen (T-5.3).

        `OpenPipeWireRemote` ist als einzige der Methoden KEIN Request: es
        antwortet unmittelbar mit einem Deskriptor statt mit einem Pfad, auf
        dessen Signal man wartet. Wer hier `_warten` benutzt, wartet ewig.
        """
        dbus, _, _ = _dbus()
        if not self.session_handle:
            raise PortalFehler("OpenPipeWireRemote ohne offene Sitzung")
        roh = self.portal.OpenPipeWireRemote(
            dbus.ObjectPath(self.session_handle), {})
        # `take()` gibt den Besitz ab -- ohne das schliesst der Wrapper den
        # Deskriptor beim Aufraeumen, und die Kette bricht mitten im Betrieb
        # ab, ohne dass jemand etwas geschlossen hat.
        return roh.take()

    def schliessen(self) -> None:
        dbus, _, _ = _dbus()
        if not self.session_handle:
            return
        objekt = self.bus.get_object(PORTAL_NAME, self.session_handle)
        dbus.Interface(objekt, SESSION_IFACE).Close()
        self.session_handle = ""


def _typisiert(dbus, optionen: dict) -> dict:
    """Zahlen als `UInt32`, Wahrheitswerte als `Boolean`.

    Ohne das rutschen Python-`int` als `Int32` auf den Bus, und das Portal
    lehnt die Signatur ab -- mit einer Meldung, die nicht sagt, welcher Wert
    gemeint ist.
    """
    fertig: dict[str, Any] = {}
    for schluessel, wert in optionen.items():
        if isinstance(wert, bool):
            fertig[schluessel] = dbus.Boolean(wert)
        elif isinstance(wert, int):
            fertig[schluessel] = dbus.UInt32(wert)
        elif isinstance(wert, str):
            fertig[schluessel] = dbus.String(wert)
        else:
            fertig[schluessel] = wert
    return fertig
