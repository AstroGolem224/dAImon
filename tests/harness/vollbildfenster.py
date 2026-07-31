#!/usr/bin/env python3
"""Das "Fenster darunter" fuer die Verifizierer T-1.1 und T-1.3.

Uebernommen aus spikes/layershell/below_window.py (T-1.8) und hierher
verschoben: ein Spike-Verzeichnis soll nicht Teil des Gates werden. Die
Vorrichtung ist auf dieser Maschine erprobt -- neu geschrieben haette sie
dieselben Eigenheiten noch einmal lernen muessen.

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


def write(line):
    with open(logfile, "a") as fh:
        fh.write(line + "\n")
        fh.flush()


def onclick(ev):
    write(f"click {ev.x_root} {ev.y_root}")


last = [None]


def onmotion(ev):
    # gedrosselt: nur wenn sich die Position spuerbar geaendert hat.
    p = (ev.x_root // 20, ev.y_root // 20)
    if p != last[0]:
        last[0] = p
        write(f"motion {ev.x_root} {ev.y_root}")


cv.bind("<Button-1>", onclick)
cv.bind("<Motion>", onmotion)
root.after(timeout * 1000, root.destroy)
with open(logfile, "a") as fh:
    fh.write("ready\n")
root.mainloop()
