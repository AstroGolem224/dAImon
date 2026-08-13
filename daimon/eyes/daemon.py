"""Der Augendienst -- das Gelenk zwischen allem, was Phase 5 gebaut hat.

Dieses Modul steht in KEINEM Task des Implementierungsplans. T-5.12 (der
Kill-Switch) und T-5.13 (die Sandbox-Unit) setzen es beide voraus, aber kein
Task erzeugt es -- eine Luecke, die erst beim Schreiben der Unit auffiel.
Neue Logik entsteht hier deshalb nicht; jede Entscheidung ist schon anderswo
getroffen und gemessen. Was hier steht, ist die Reihenfolge.

Der Ablauf einer Runde:

    Ausloeser (T-5.4)  -> Frame holen (T-5.3)  -> Gatterkette (T-5.5)
    -> OCR im eigenen Prozess (T-5.6)          -> Kontext unter Quarantaene (T-5.7)

Drei Dinge, die nur hier entschieden werden konnten:

**Der Fokus-Empfang laeuft in einem eigenen Faden.** Die Abtastschleife holt
Frames und blockiert dabei; die GLib-Schleife fuer DBus muss daneben laufen,
sonst kaeme kein einziges Fokus-Ereignis an. Der Busname wird trotzdem im
Hauptfaden GENOMMEN, bevor die Schleife startet: `Type=dbus` erkennt einen
verlorenen Namen und beendet den Dienst sofort wieder.

**Ohne Fokus-Ereignis wird der GANZE Bildschirm zugeschnitten.** Das ist der
teure Fall -- gemessen 125 ms statt 14 ms -- und er ist trotzdem der richtige
Rueckfall: die Alternative waere, gar nicht hinzusehen, bis ein Fenster den
Fokus wechselt. T--1.9 hat belegt, dass genau das selten passiert.

**Die Widerrufsmarke wird bei JEDER Runde geprueft.** Das Face schreibt sie
und sendet nichts (T-5.2); wer sie nur beim Start liest, widerruft erst beim
naechsten Neustart -- und das ist bei einem Widerruf die falsche Frist.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from daimon.eyes import capture, context, trigger
from daimon.eyes.change import Fenster, Kette
from daimon.eyes.screencast import PortalSitzung

SERVICE = "de.daimon.Eyes"
PATH = "/Eyes"
IFACE = "de.daimon.Eyes"

# Was der Augendienst nie ansieht. Bewusst hier und nicht nur in der
# Konfiguration: eine Vorgabe, die man einschalten muss, schuetzt niemanden.
DENYLIST_VORGABE = (
    "org.keepassxc.KeePassXC", "keepassxc",
    "org.gnome.Seahorse", "bitwarden", "1password",
)


def serial_zu_node(node: int, *, timeout_s: float = 10.0) -> str | None:
    """`object.serial` zu einer Node-ID. `None`, wenn nicht ermittelbar.

    Die Serial ueberlebt einen Hotplug, die Node-ID nicht (T-5.3). Wer sie
    nicht bekommt, faellt auf die ID zurueck -- das ist schlechter, aber
    besser als nicht hinzusehen.
    """
    try:
        lauf = subprocess.run(["pw-dump", str(node)], capture_output=True,
                              text=True, timeout=timeout_s)
        for o in json.loads(lauf.stdout):
            if o.get("id") == node:
                wert = (o.get("info") or {}).get("props", {}).get("object.serial")
                return str(wert) if wert is not None else None
    except Exception:
        return None
    return None


class Augen:
    """Ein Dienst, der hinsieht -- und zwischen den Blicken nichts tut."""

    def __init__(self, *, verzeichnis: Path | None = None,
                 denylist=DENYLIST_VORGABE,
                 periode_s: float = trigger.PERIODE_S,
                 ocr_arbeiter: int = 2) -> None:
        self._tor = trigger.SystemTor()
        self._ausloeser = trigger.Ausloeser(tor=self._tor, periode_s=periode_s)
        self._kette = Kette(tor=self._tor, denylist=denylist)
        self._speicher = context.Kontextspeicher(verzeichnis=verzeichnis,
                                                 denylist=denylist)
        self._speicher.laden()
        self._ocr_arbeiter = ocr_arbeiter

        self._sitzung: PortalSitzung | None = None
        self._portal: Any = None
        self._aufnahme: capture.Aufnahme | None = None
        self._pool: Any = None
        self._ordner_gen = 0

        # Das zuletzt gemeldete Fenster. Ein Wert und keine Warteschlange:
        # was zaehlt, ist wo der Fokus JETZT liegt, nicht wo er war.
        self._fenster: Fenster | None = None
        self._fenster_sperre = threading.Lock()

        self._laeuft = threading.Event()
        self.runden = 0
        self.erfasst = 0
        self.gelesen = 0

    # -- Aufbau ------------------------------------------------------------

    def oeffnen(self) -> dict:
        from daimon.eyes.ocr import Pool
        from daimon.eyes.portal_dbus import DbusPortal

        self._portal = DbusPortal()
        self._sitzung = PortalSitzung(portal=self._portal)
        befund = self._sitzung.oeffnen()
        node = int(befund["streams"][0][0])
        serial = serial_zu_node(node)
        self._aufnahme = capture.Aufnahme(fd=self._portal.pipewire_fd(),
                                          serial=serial, node_id=node)
        self._pool = Pool(groesse=self._ocr_arbeiter)
        return {"node": node, "serial": serial,
                "interaktiver_rueckfall": befund.get("interaktiver_rueckfall")}

    # -- Eine Runde --------------------------------------------------------

    def _aktuelles_fenster(self, breite: int, hoehe: int) -> Fenster:
        with self._fenster_sperre:
            f = self._fenster
        if f is not None:
            return f
        # Ohne Fokus-Ereignis der ganze Bildschirm: 125 ms statt 14 ms. Teuer,
        # aber die Alternative waere, gar nicht hinzusehen.
        return Fenster(x=0, y=0, breite=breite, hoehe=hoehe, klasse="")

    def einmal_sehen(self, grund: str) -> dict:
        """Frame, Kette, OCR, Kontext. Gibt den Befund zurueck, immer."""
        import numpy as np

        if self._aufnahme is None:
            raise RuntimeError("einmal_sehen() vor oeffnen()")
        self.runden += 1
        frame = self._aufnahme.frame()
        self.erfasst += 1
        rgb = np.frombuffer(frame["rohdaten"], np.uint8).reshape(
            frame["hoehe"], frame["breite"], 3)

        befund = self._kette.verarbeiten(
            rgb, self._aktuelles_fenster(frame["breite"], frame["hoehe"]))
        if not befund.veraendert:
            return {"grund": grund, "abgewiesen": befund.grund,
                    "generation": befund.generation}

        zukunft = self._pool.einreichen(
            befund.region, befund.ausschnitt.tobytes(),
            befund.ausschnitt.shape[1], befund.ausschnitt.shape[0])
        text = zukunft.result(timeout=60.0)
        self.gelesen += 1

        with self._fenster_sperre:
            klasse = self._fenster.klasse if self._fenster else ""
        self._speicher.hinzufuegen(context.ART_OCR, klasse, text)
        return {"grund": grund, "generation": befund.generation,
                "region": befund.region, "zeichen": len(text),
                "kosten": befund.kosten}

    # -- Die Schleife ------------------------------------------------------

    def _widerruf_pruefen(self) -> bool:
        """Hat das Face einen Widerruf vermerkt? Dann ist hier Schluss."""
        if self._sitzung is None:
            return False
        if not self._sitzung.widerruf_angefordert():
            return False
        self._sitzung.widerrufen()
        self._speicher.leeren()
        return True

    def lauf(self, *, takt_s: float = 0.5) -> int:
        """Blockiert bis `beenden()`. Gibt die Zahl der Runden zurueck."""
        self._laeuft.set()
        while self._laeuft.is_set():
            if self._widerruf_pruefen():
                break
            grund = self._ausloeser.tick()
            if grund is not None:
                try:
                    self.einmal_sehen(grund)
                except Exception as exc:
                    # Eine Runde, die scheitert, beendet den Dienst NICHT --
                    # ein einzelner verlorener Frame ist kein Grund, das
                    # Hinsehen fuer den Rest des Tages einzustellen.
                    print(f"Runde gescheitert: {exc}", file=sys.stderr,
                          flush=True)
            self._laeuft.wait(takt_s)
        return self.runden

    def fokus_ereignis(self, fenster: Fenster) -> None:
        """Vom DBus-Faden gerufen."""
        with self._fenster_sperre:
            self._fenster = fenster
        grund = self._ausloeser.fokus()
        if grund is not None:
            try:
                self.einmal_sehen(grund)
            except Exception as exc:
                print(f"Fokusrunde gescheitert: {exc}", file=sys.stderr,
                      flush=True)

    # -- Abbau -------------------------------------------------------------

    def beenden(self) -> dict:
        """Kette weg, Sitzung zu, Arbeiter aus. In dieser Reihenfolge.

        Die Kette baut sich ohnehin je Frame ab (T-5.3); was hier zaehlt, ist
        die SITZUNG. Wer nur den Prozess beendet, laesst die Erlaubnis stehen.
        """
        self._laeuft.clear()
        bericht = {"sitzung": None, "pool": None}
        if self._portal is not None:
            try:
                self._portal.schliessen()
                bericht["sitzung"] = True
            except Exception:
                bericht["sitzung"] = False
        if self._pool is not None:
            try:
                self._pool.beenden()
                bericht["pool"] = True
            except Exception:
                bericht["pool"] = False
        return bericht

    def zustand(self) -> dict:
        return {"v": 1, "runden": self.runden, "erfasst": self.erfasst,
                "gelesen": self.gelesen,
                "ausloeser": self._ausloeser.zaehler(),
                "kontext": self._speicher.zaehler()}


# -- DBus ------------------------------------------------------------------

def _fenster_aus_ereignis(nutzlast: str) -> Fenster | None:
    """Ein Fokus-Ereignis des Watchers (T-0.12) zu einem `Fenster`.

    Der Fenstertitel wird ABSICHTLICH nicht uebernommen: er ist
    Angreifertext, und dieses Modul reicht nur `klasse` weiter -- die kommt
    aus `resource_class` und nicht aus dem, was ein Browsertab sich selbst
    gegeben hat.
    """
    try:
        d = json.loads(nutzlast) if isinstance(nutzlast, str) else dict(nutzlast)
    except (ValueError, TypeError):
        return None
    geo = d.get("geometry") or {}
    try:
        return Fenster(x=int(geo.get("x", 0)), y=int(geo.get("y", 0)),
                       breite=int(geo.get("width", 0)),
                       hoehe=int(geo.get("height", 0)),
                       klasse=str(d.get("resource_class", "")),
                       drm=bool(d.get("drm", False)))
    except (TypeError, ValueError):
        return None


def serve(augen: Augen) -> None:  # pragma: no cover - braucht einen echten Bus
    """Nimmt `de.daimon.Eyes` und laesst die GLib-Schleife im Faden laufen."""
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    class Objekt(dbus.service.Object):
        @dbus.service.method(IFACE, in_signature="s", out_signature="")
        def Event(self, nutzlast):
            f = _fenster_aus_ereignis(str(nutzlast))
            if f is not None:
                augen.fokus_ereignis(f)

        @dbus.service.method(IFACE, in_signature="", out_signature="s")
        def Zustand(self):
            return json.dumps(augen.zustand(), ensure_ascii=False)

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    # Beide Objekte muessen fuer die gesamte Schleife leben. Ohne starke
    # Referenzen gibt `BusName` den Namen sofort wieder frei, `Type=dbus`
    # erkennt den Verlust und systemd beendet den Dienst.
    bus_name = dbus.service.BusName(SERVICE, bus)
    objekt = Objekt(bus, PATH)
    schleife = GLib.MainLoop()
    threading.Thread(target=schleife.run, name="dbus", daemon=True).start()
    augen._dbus = (bus_name, objekt, schleife)


def main(argv: list[str] | None = None) -> int:
    augen = Augen()

    # Der Busname ZUERST, vor dem Aufbau. `Type=dbus` wertet ihn als
    # Bereitschaftszeichen, und bereit fuer Fokus-Ereignisse ist der Dienst
    # tatsaechlich schon hier. Andersherum gemessen am 13.08.: der erste Start
    # lief in die Startfrist von 15 s, obwohl `oeffnen()` reproduzierbar
    # 0,17 s braucht -- eine einzige langsame erste Runde reichte, und systemd
    # sah bis dahin gar nichts. Ein Ereignis, das vor `oeffnen()` eintrifft,
    # laeuft in die Ausnahme in `einmal_sehen` und wird protokolliert.
    try:
        serve(augen)
    except Exception as exc:
        # Ohne Busnamen laeuft der Dienst weiter -- der Timer traegt ohnehin
        # den Grossteil (T-5.4). Aber `Type=dbus` sieht dann keinen Namen und
        # beendet ihn; deshalb steht die Meldung im Journal und nicht nur im
        # Rueckgabewert.
        print(f"kein Busname ({exc}) -- nur der Timer traegt",
              file=sys.stderr, flush=True)

    befund = augen.oeffnen()
    print(json.dumps({"gestartet": True, **befund}, ensure_ascii=False),
          flush=True)

    def halt(*_a):
        augen._laeuft.clear()

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    try:
        augen.lauf()
    finally:
        print(json.dumps(augen.beenden(), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
