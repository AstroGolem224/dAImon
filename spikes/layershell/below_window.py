#!/usr/bin/env python3
"""Das "Fenster darunter" fuer den Input-Region-Test.

Vollbild, einfarbig, protokolliert jeden Klick mit Koordinaten in eine Datei.
Damit ist "der Klick ist durchgegangen" nachweisbar und nicht geraten.
Schliesst sich nach TIMEOUT Sekunden von selbst — Sicherheitsnetz.
"""
import sys
import tkinter as tk

color = sys.argv[1]        # "#rrggbb"
logfile = sys.argv[2]
timeout = int(sys.argv[3])

root = tk.Tk()
root.title("daimon-spike-below")
root.configure(bg=color)
root.attributes("-fullscreen", True)
cv = tk.Canvas(root, bg=color, highlightthickness=0)
cv.pack(fill="both", expand=True)


def onclick(ev):
    with open(logfile, "a") as fh:
        fh.write(f"click {ev.x_root} {ev.y_root}\n")
        fh.flush()


cv.bind("<Button-1>", onclick)
root.after(timeout * 1000, root.destroy)
with open(logfile, "a") as fh:
    fh.write("ready\n")
root.mainloop()
