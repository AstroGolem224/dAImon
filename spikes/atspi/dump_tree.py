#!/usr/bin/env python3
"""Schnelltest: Welche Anwendungen sind aktuell im AT-SPI2-Baum sichtbar?"""
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

Atspi.init()
desktop = Atspi.get_desktop(0)
print(f"desktop children: {desktop.get_child_count()}")
for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    if app is None:
        continue
    try:
        name = app.get_name()
        pid = app.get_process_id()
        n = app.get_child_count()
        print(f"  [{i}] name={name!r} pid={pid} top_level_children={n}")
    except Exception as e:
        print(f"  [{i}] ERROR: {e}")
