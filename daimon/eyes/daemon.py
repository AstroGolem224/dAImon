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

from daimon.common.config import ConfigError, denylist_laden, denylist_pfade
from daimon.eyes import capture, context, trigger
from daimon.eyes.change import Fenster, Kette
from daimon.eyes.screencast import PortalSitzung

SERVICE = "de.daimon.Eyes"
PATH = "/Eyes"
IFACE = "de.daimon.Eyes"

# Was der Augendienst nie ansieht. Bewusst hier und nicht nur in der
# Konfiguration: eine Vorgabe, die man einschalten muss, schuetzt niemanden.
# OCR bekommt eine EIGENE Abkuehlung, getrennt vom Ausloeser. Gemessen am
# 13.08. unter systemd: der Dienst mit 5-s-Takt kostete 1,7 Kerne -- 71,9 %
# im Hauptprozess, 101 % im OCR-Arbeiter. Design 13 veranschlagt fuer den
# Bildschirmweg 0,001 % ("Bildschirm-Diff, 2-s-Takt"); OCR steht in der
# Dauerlast-Tabelle GAR NICHT, es gehoert zu "auf Abruf".
#
# Der Widerspruch ist im Design selbst: das Latenzziel verlangt "Bildschirm-
# aenderung -> Kontext aktuell < 3 s (OCR-gebunden)", und auf einer lebenden
# Arbeitsflaeche aendert sich fast jede Runde etwas. Beides zugleich ist nicht
# haltbar. Diese Zahl waehlt: der Diff bleibt billig und laeuft weiter, OCR
# laeuft hoechstens alle 30 s. Ein Kontext, der eine halbe Minute alt ist, ist
# fuer eine Rueckfrage brauchbar; 1,7 Kerne den ganzen Tag sind es nicht.
OCR_ABKUEHLUNG_S = 30.0

# Der Rueckfall, wenn `config/redaktion.yaml` fehlt oder unlesbar ist. Die
# gepflegte Liste steht dort und wird von ZWEI Stellen gelesen: hier vor dem
# Diff (Design 4.4) und in der Redaktion vor dem Schreiben (T-7.2). Bis
# T-7.2 standen hier fuenf Eintraege und dort sechzehn -- wer seine Bank in
# die Datei eintrug, hatte sie trotzdem im Quarantaene-Kontext.
DENYLIST_VORGABE = (
    "org.keepassxc.KeePassXC", "keepassxc",
    "org.gnome.Seahorse", "bitwarden", "1password",
)


def _denylist_aus_datei() -> tuple[str, ...]:
    """Die gepflegte Liste, sonst die Vorgabe. Nie eine leere Liste.

    Eine fehlende Datei darf nicht dazu fuehren, dass ein Passwortmanager
    gelesen wird -- sie faellt auf die eingebauten fuenf zurueck, nicht auf
    gar nichts.
    """
    try:
        eintraege, _ = denylist_laden(denylist_pfade())
    except ConfigError:
        return DENYLIST_VORGABE
    return tuple(eintraege) or DENYLIST_VORGABE


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
                 denylist=None,
                 periode_s: float = trigger.PERIODE_S,
                 ocr_abkuehlung_s: float = OCR_ABKUEHLUNG_S,
                 ocr_arbeiter: int = 2) -> None:
        if denylist is None:
            denylist = _denylist_aus_datei()
        self._tor = trigger.SystemTor()
        self._ausloeser = trigger.Ausloeser(tor=self._tor, periode_s=periode_s)
        self._kette = Kette(tor=self._tor, denylist=denylist)
        self._speicher = context.Kontextspeicher(verzeichnis=verzeichnis,
                                                 denylist=denylist)
        self._speicher.laden()
        self._ocr_arbeiter = ocr_arbeiter
        self._ocr_abkuehlung = float(ocr_abkuehlung_s)
        self._letztes_ocr = float("-inf")
        self.ocr_verschoben = 0

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
        # Ein ZWEITES Ereignis, und zwar invers: `_halt` ist geloescht,
        # solange gearbeitet wird. Gewartet wird darauf, nicht auf `_laeuft`.
        #
        # Der Grund ist teuer bezahlt: `Event.wait(frist)` kehrt SOFORT
        # zurueck, wenn das Ereignis gesetzt ist. Die Schleife hat mit
        # `self._laeuft.wait(takt_s)` also nie geschlafen und drehte mit
        # 100 % eines Kerns leer -- gemessen am 13.08. unter systemd, zwanzig
        # Proben in fuenf Sekunden, alle zwischen 96 und 104 %. Kein Test hat
        # es gesehen: dort ist `_laeuft` geloescht, und dann wartet `wait()`
        # tatsaechlich.
        self._halt = threading.Event()
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

    def fenster_abfragen(self) -> Fenster | None:
        """Fragt `de.daimon.Focus` nach dem aktuellen Fenster.

        Eine ABFRAGE und keine Weiterleitung, und der Grund ist strukturell:
        `callDBus` aus dem KWin-Script trifft genau einen Empfaenger, und zwei
        Dienste koennen sich einen Busnamen nicht teilen. Der Augendienst
        haelt `de.daimon.Eyes` -- dorthin sendet niemand. Genau deshalb stand
        der Zaehler beim ersten Lauf auf `fokus: 0`, und jede Runde schnitt
        aufs Vollbild zu: 3,2 s statt 0,09 s.

        Einmal je Runde, nicht dauernd: der Timer traegt ohnehin den
        Grossteil (T-5.4), und eine Abfrage alle fuenf Sekunden kostet nichts.
        """
        try:
            import dbus
            bus = dbus.SessionBus()
            objekt = bus.get_object("de.daimon.Focus", "/Focus")
            roh = json.loads(str(objekt.Fenster(
                dbus_interface="de.daimon.Focus")))
        except Exception:
            # Kein Watcher, kein Fenster. Der Rueckfall aufs Vollbild ist
            # teuer, aber er sieht wenigstens hin.
            return None
        if not roh.get("bekannt") or roh.get("breite", 0) <= 0:
            return None
        return Fenster(x=int(roh["x"]), y=int(roh["y"]),
                       breite=int(roh["breite"]), hoehe=int(roh["hoehe"]),
                       klasse=str(roh.get("resource_class", "")),
                       drm=False)

    def _aktuelles_fenster(self, breite: int, hoehe: int) -> Fenster:
        with self._fenster_sperre:
            f = self._fenster
        if f is None:
            f = self.fenster_abfragen()
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

        jetzt = time.monotonic()
        if jetzt - self._letztes_ocr < self._ocr_abkuehlung:
            # Der Diff hat gearbeitet und etwas gefunden -- OCR nicht. Das
            # wird GEZAEHLT: ein verschobener Blick, den niemand zaehlt, ist
            # von einem unveraenderten Bildschirm nicht zu unterscheiden.
            self.ocr_verschoben += 1
            return {"grund": grund, "abgewiesen": "ocr_abkuehlung",
                    "generation": befund.generation, "region": befund.region}
        self._letztes_ocr = jetzt

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
        self._halt.clear()
        self._laeuft.set()
        while not self._halt.is_set():
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
            self._halt.wait(takt_s)
        self._laeuft.clear()
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
        self._halt.set()
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
                "gelesen": self.gelesen, "ocr_verschoben": self.ocr_verschoben,
                "ocr_abkuehlung_s": self._ocr_abkuehlung,
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

    # Reihenfolge: ERST das Portal, DANN der Busname. Am 13.08. stand es
    # andersherum, weil `Type=dbus` den Namen als Bereitschaftszeichen wertet
    # und der erste Start in die Frist von 15 s lief. Der Tausch hat einen
    # Startfehler gegen einen HAENGER eingetauscht:
    #
    #   `serve()` startet eine GLib-Schleife auf dem Vorgabekontext.
    #   `portal_dbus._warten()` startet danach eine ZWEITE auf demselben.
    #   Zwei Schleifen auf einem Kontext blockieren einander -- die
    #   Portal-Antwort wird nie zugestellt, `oeffnen()` kehrt nicht zurueck,
    #   und der Dienst steht `active` da und tut nichts.
    #
    # Gemessen daran, dass `runden: 0` blieb und die Startmeldung im Journal
    # fehlte, waehrend systemd "Started" schrieb. Die Startfrist loest
    # `TimeoutStartSec=180` in der Unit -- eine Frist ist das kleinere Uebel
    # als ein Dienst, der laeuft und nicht arbeitet.
    befund = augen.oeffnen()
    print(json.dumps({"gestartet": True, **befund}, ensure_ascii=False),
          flush=True)

    try:
        serve(augen)
    except Exception as exc:
        # Ohne Busnamen laeuft der Dienst weiter -- der Timer traegt ohnehin
        # den Grossteil (T-5.4). Aber `Type=dbus` sieht dann keinen Namen und
        # beendet ihn; deshalb steht die Meldung im Journal.
        print(f"kein Busname ({exc}) -- nur der Timer traegt",
              file=sys.stderr, flush=True)

    def halt(*_a):
        augen._halt.set()

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    try:
        augen.lauf()
    finally:
        print(json.dumps(augen.beenden(), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
