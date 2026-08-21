"""T-4.13 — die zwei Aussenkontakte des Input-Brokers, an einer Stelle.

Sie stehen hier und nicht in `broker.py`, damit der Broker eine
Entscheidungsreihenfolge ist und keine DBus-Kundschaft. Der Broker bekommt
beide als Rueckrufe hereingereicht; was hier steht, ist die eine Fassung, die
im Betrieb eingehaengt wird.

`sitzung_oeffnen` -- `RemoteDesktop` ueber DBus, kein libei-Binding noetig
----------------------------------------------------------------------------
Der Regelweg. `org.freedesktop.portal.RemoteDesktop` exponiert `Notify*` als
gewoehnliche DBus-Methoden -- der Portal-Dienst spricht libei/EIS intern mit
dem Compositor, dieser Broker braucht dafuer keine eigene Bindung. Eine
Sitzung wird geoeffnet, die Folge geht durch, die Sitzung wird geschlossen --
und zwar auch dann, wenn das Senden wirft (der Broker haelt das in einem
`finally`). Eine offene `RemoteDesktop`-Sitzung ist eine stehende Erlaubnis,
Eingaben zu synthetisieren; sie darf keinen Fehlerfall ueberleben und erst
recht nicht den Prozess.

`screensaver_aktiv` -- der harte Breaker
----------------------------------------------------------------------------
Dieselbe Bauform wie `eyes/trigger.py:SystemTor.gesperrt`, und aus demselben
Grund: die Eigenschaft `org.freedesktop.ScreenSaver.GetActive` meldet den
wahren Zustand.

**Faellt DBus aus, ist die Antwort ZU.** Bei einem Dienst, der Tasten
synthetisieren kann, ist Nichtstun der harmlose Fehlerfall -- und ein Leser,
der bei fehlendem Bus "nicht gesperrt" meldet, tippt in den Sperrbildschirm.
"""

from __future__ import annotations

from typing import Any

from daimon.brokers.input.broker import (PORTAL_SCHNITTSTELLE,
                                         SCREENSAVER_DIENST)

PORTAL_DIENST = "org.freedesktop.portal.Desktop"
PORTAL_PFAD = "/org/freedesktop/portal/desktop"

# Was die Sitzung darf. Tastatur und Zeiger, kein Touch.
GERAETE = 0b11


class PortalFehler(RuntimeError):
    """Die Sitzung kam nicht zustande. Nennt den Grund."""


class RemoteDesktopSitzung:
    """Eine Sitzung, ein Lauf, danach zu."""

    def __init__(self, bus: Any, handle: Any) -> None:
        self._bus = bus
        self._handle = handle
        self.offen = True

    def senden(self, folge: tuple) -> bool:
        if not self.offen:
            raise PortalFehler("die Sitzung ist schon geschlossen")
        schnittstelle = self._bus.get_object(PORTAL_DIENST, PORTAL_PFAD)
        for e in folge:
            if e.art == "type":
                for zeichen in e.wert:
                    schnittstelle.NotifyKeyboardKeysym(
                        self._handle, {}, ord(zeichen), 1,
                        dbus_interface=PORTAL_SCHNITTSTELLE)
                    schnittstelle.NotifyKeyboardKeysym(
                        self._handle, {}, ord(zeichen), 0,
                        dbus_interface=PORTAL_SCHNITTSTELLE)
            elif e.art == "key":
                schnittstelle.NotifyKeyboardKeycode(
                    self._handle, {}, int(e.wert) if str(e.wert).isdigit()
                    else 0, 1, dbus_interface=PORTAL_SCHNITTSTELLE)
            elif e.art == "button":
                schnittstelle.NotifyPointerButton(
                    self._handle, {}, int(e.wert) if str(e.wert).isdigit()
                    else 0, 1, dbus_interface=PORTAL_SCHNITTSTELLE)
            else:
                # RELATIV. `NotifyPointerMotionAbsolute` gibt es im Portal --
                # der Broker bietet die Art gar nicht erst an, weil eine
                # Zusage, die nur ueber einen Weg gilt, keine ist.
                schnittstelle.NotifyPointerMotion(
                    self._handle, {}, float(e.wert[0]), float(e.wert[1]),
                    dbus_interface=PORTAL_SCHNITTSTELLE)
        return True

    def schliessen(self) -> None:
        if not self.offen:
            return
        try:
            objekt = self._bus.get_object(PORTAL_DIENST, self._handle)
            objekt.Close(dbus_interface="org.freedesktop.portal.Session")
        finally:
            # Auch wenn `Close` wirft: von hier aus wird nichts mehr
            # gesendet. Ein Handle, das der Broker fuer offen haelt, waere
            # schlimmer als eines, das der Portal-Dienst noch kennt.
            self.offen = False


def sitzung_oeffnen() -> RemoteDesktopSitzung:
    """`CreateSession` -> `SelectDevices` -> `Start`. Wirft, wenn es klemmt."""
    import dbus

    bus = dbus.SessionBus()
    portal = bus.get_object(PORTAL_DIENST, PORTAL_PFAD)
    handle = portal.CreateSession({}, dbus_interface=PORTAL_SCHNITTSTELLE)
    portal.SelectDevices(handle, {"types": GERAETE},
                         dbus_interface=PORTAL_SCHNITTSTELLE)
    portal.Start(handle, "", {}, dbus_interface=PORTAL_SCHNITTSTELLE)
    return RemoteDesktopSitzung(bus, handle)


def screensaver_aktiv() -> bool:
    """`org.freedesktop.ScreenSaver.GetActive`. Kein Bus -> gesperrt."""
    try:
        import dbus

        bus = dbus.SessionBus()
        objekt = bus.get_object(SCREENSAVER_DIENST,
                                "/org/freedesktop/ScreenSaver")
        return bool(objekt.GetActive(dbus_interface=SCREENSAVER_DIENST))
    except Exception:
        return True
