#!/usr/bin/env python3
"""Empfaenger fuer T-1.9. Nimmt callDBus-Meldungen des KWin-Scripts entgegen
und schreibt sie als JSONL mit monotonem Zeitstempel."""
import json, sys, time, os
from pathlib import Path
import dbus, dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

OUT = Path(__file__).parent / "events.jsonl"

class Probe(dbus.service.Object):
    def __init__(self, bus, path="/Probe"):
        super().__init__(bus, path)
        self.n = 0
        self.fh = OUT.open("a")

    @dbus.service.method("de.daimon.FocusProbe",
                         in_signature="sssssb", out_signature="")
    def Event(self, kind, uuid, caption, cls, desktop, fullscreen):
        rec = {"t": time.monotonic(), "wall": time.time(), "kind": str(kind),
               "uuid": str(uuid), "caption": str(caption),
               "class": str(cls), "desktop": str(desktop),
               "fullscreen": bool(fullscreen)}
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        self.n += 1
        print(f"{self.n:4d} {rec['kind']:9s} {rec['class']:24s} {rec['caption'][:60]}",
              flush=True)

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
name = dbus.service.BusName("de.daimon.FocusProbe", bus)
p = Probe(bus)
print(f"probe laeuft, schreibt nach {OUT}", flush=True)
GLib.MainLoop().run()
