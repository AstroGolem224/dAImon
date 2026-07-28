#!/usr/bin/env python3
"""Minimale GTK4-Testanwendung für den AT-SPI2-Spike.

GTK4 spricht AT-SPI nativ (kein at-spi2-atk nötig). Enthält nur harmlose
Widgets: einen GtkMenuButton 'Ansicht' mit Popover und ein Label.
Wird ausschließlich von measure.py gestartet und wieder beendet.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib


class DemoApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="de.spike.atspi.gtk4demo")
        GLib.set_prgname("gtk4demo")

    def do_activate(self):
        win = Gtk.ApplicationWindow(application=self,
                                    title="AT-SPI Spike GTK4")
        win.set_default_size(320, 120)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12)
        box.set_margin_start(12)
        pop = Gtk.Popover()
        pop.set_child(Gtk.Label(label="Untermenü-Platzhalter"))
        mb = Gtk.MenuButton(label="Ansicht")
        mb.set_popover(pop)
        box.append(mb)
        box.append(Gtk.Label(label="Demo-Label, keine Funktion"))
        win.set_child(box)
        win.present()


if __name__ == "__main__":
    DemoApp().run(None)
