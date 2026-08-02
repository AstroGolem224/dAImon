"""T-0.12 — Empfaenger fuer den KWin-Fokus-Watcher.

Der Watcher laeuft im Compositor und meldet per `callDBus`. Das ist
**fire-and-forget**: ist der Empfaenger tot, verschluckt KWin den Aufruf ohne
Fehlermeldung -- weder das Script noch der Hub erfahren davon. Deshalb gehoert
dieser Empfaenger in eine systemd-Unit mit `Type=dbus` und `BusName=`, damit
systemd ihn bei Bedarf startet und der Aufruf auf einen lebenden Dienst
trifft. Die Unit kommt in T-0.14; hier steht der Dienst selbst.

Was der Watcher liefert und warum es gebraucht wird:

  * `fullScreen` plus Geometrie -- das VRAM-Gate aus Phase 4 muss wissen, ob
    ein Vollbildfenster auf dem Ausgabegeraet des Overlays liegt.
  * `pid` -- die Zuordnung Fenster zu Prozess, spaeter fuer den Aktionskatalog.
  * `caption` und `resourceClass` -- beide sind **`tainted`** (Design 5.2).
    Ein Fenstertitel ist angreiferbeeinflusster Inhalt: jede Anwendung darf
    hineinschreiben, was sie will. Er wird deshalb als `Marked` gefuehrt und
    nicht als nackte Zeichenkette.

Der Abtast-Timer aus T-5.4 bleibt noetig. `captionChanged` feuert nur, wenn
die Anwendung ihren Titel aendert; Terminalausgabe, Scrollen und ein neuer
Absatz erzeugen nichts. Das ist der Befund aus Spike T-1.9.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from daimon.common.protocol import Marked, tainted

SERVICE = "de.daimon.Focus"
PATH = "/Focus"
IFACE = "de.daimon.Focus"

# Signatur von Event(): kind, uuid, caption, cls, desktop, fullscreen,
# pid, x, y, breite, hoehe
DBUS_SIGNATUR = "sssssbiiiii"


@dataclass(frozen=True)
class FocusEvent:
    kind: str                  # activated | caption | loaded
    uuid: str
    caption: Marked            # tainted -- Fenstertitel sind fremder Inhalt
    resource_class: Marked     # dito
    desktop_file: str
    fullscreen: bool
    pid: int
    geometrie: tuple[int, int, int, int] = (0, 0, 0, 0)
    ts: float = field(default_factory=time.time)

    def deckt_ab(self, x: int, y: int) -> bool:
        """Liegt der Punkt im Fenster? Fuer das VRAM-Gate: ist das Overlay
        von einem Vollbildfenster verdeckt?"""
        gx, gy, gw, gh = self.geometrie
        return gx <= x < gx + gw and gy <= y < gy + gh


class FocusReceiver:
    """Nimmt die Meldungen des KWin-Scripts entgegen.

    Bewusst ohne DBus-Abhaengigkeit im Konstruktor: `handle()` ist die ganze
    Logik und laesst sich ohne Bus testen. Die Anbindung an dbus-python
    passiert in `serve()`, und nur dort.
    """

    def __init__(self, on_event: Callable[[FocusEvent], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._letztes: FocusEvent | None = None
        self._zaehler: dict[str, int] = {}
        self._on_event = on_event

    def handle(self, kind: str, uuid: str, caption: str, cls: str,
               desktop: str, fullscreen: bool, pid: int,
               x: int = 0, y: int = 0, breite: int = 0, hoehe: int = 0
               ) -> FocusEvent:
        ev = FocusEvent(
            kind=str(kind), uuid=str(uuid),
            caption=tainted(str(caption)), resource_class=tainted(str(cls)),
            desktop_file=str(desktop), fullscreen=bool(fullscreen),
            pid=int(pid), geometrie=(int(x), int(y), int(breite), int(hoehe)),
        )
        with self._lock:
            self._letztes = ev
            self._zaehler[ev.kind] = self._zaehler.get(ev.kind, 0) + 1
        if self._on_event:
            # Ein kaputter Abnehmer darf den Empfaenger nicht mitreissen: er
            # haengt am Compositor, und callDBus meldet uns keinen Fehler.
            try:
                self._on_event(ev)
            except Exception:  # noqa: BLE001
                pass
        return ev

    @property
    def letztes(self) -> FocusEvent | None:
        with self._lock:
            return self._letztes

    def zaehler(self) -> dict[str, int]:
        with self._lock:
            return dict(self._zaehler)

    # -- DBus --------------------------------------------------------------

    def serve(self) -> None:  # pragma: no cover - braucht einen echten Bus
        """Blockiert. Nimmt den Namen `de.daimon.Focus` auf dem Sitzungsbus."""
        import dbus
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        empfaenger = self

        class Objekt(dbus.service.Object):
            @dbus.service.method(IFACE, in_signature=DBUS_SIGNATUR,
                                 out_signature="")
            def Event(self, kind, uuid, caption, cls, desktop,
                      fullscreen, pid, x, y, breite, hoehe):
                empfaenger.handle(kind, uuid, caption, cls, desktop,
                                  fullscreen, pid, x, y, breite, hoehe)

        DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        # Beide Objekte muessen fuer die gesamte MainLoop leben. Ohne starke
        # Referenzen gibt BusName den Namen direkt wieder frei; Type=dbus
        # erkennt den Verlust und systemd beendet den vermeintlich gestarteten
        # Dienst sofort.
        bus_name = dbus.service.BusName(SERVICE, bus)
        objekt = Objekt(bus, PATH)
        GLib.MainLoop().run()


def als_hub_ereignis(ev: FocusEvent) -> dict[str, Any]:
    """Fuer den Bus des Hubs. Der Titel bleibt markiert -- wer ihn anzeigt,
    muss ihn durch die Vorschau schicken."""
    return {
        "kind": ev.kind,
        "uuid": ev.uuid,
        "caption": ev.caption.to_wire(),
        "resource_class": ev.resource_class.to_wire(),
        "fullscreen": ev.fullscreen,
        "pid": ev.pid,
        "geometrie": list(ev.geometrie),
    }
