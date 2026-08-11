#!/usr/bin/env python3
"""Erzeugt das Ember-Spritesheet im hatch-pet-Atlasformat.

    python3 gen_ember.py            -> spritesheet.png + pet.json

Format nach docs/DESIGN.md 8.2: Zelle 192x208, 8 Spalten, 9 Zeilen.
Der Zustand steckt in Helligkeit und Farbe der Glut, nicht in gezeichneten
Posen -- deshalb sind neun unterscheidbare Zustaende ohne Zeichner moeglich.

Kein Bildbearbeitungsprogramm noetig, nur Pillow.
"""
import json, math, pathlib
from PIL import Image, ImageDraw, ImageFilter

CW, CH, COLS, ROWS = 192, 208, 8, 9

# (Zeile, Name, Frames, Glutfarbe, Augenstaerke, Halo)
STATES = [
    (0, "idle",        6, (194,  69, 14), 0.55, 0.75),
    (1, "run-right",   8, (226,  97, 15), 0.72, 0.90),
    (2, "run-left",    8, (226,  97, 15), 0.72, 0.90),
    (3, "waving",      4, (255, 176, 32), 0.95, 1.10),
    (4, "jumping",     5, (255, 176, 32), 1.00, 1.20),
    (5, "failed",      8, (140,  28, 16), 0.62, 0.80),
    (6, "waiting",     6, (255, 216, 99), 1.00, 1.60),
    (7, "running",     6, (255, 138, 18), 1.00, 1.15),
    (8, "review",      6, (242, 121, 15), 0.85, 1.05),
]

ASH, ASH_HI, ASH_LO = (36, 26, 22), (58, 42, 34), (15, 11, 9)


def body(d, cx, cy, s=1.0, squash=0.0):
    """Aschekoerper. squash>0 druckt ihn flacher (Sprung, Lauf)."""
    w, h = 62 * s, 68 * s * (1 - squash)
    d.ellipse([cx - w/2, cy - h/2, cx + w/2, cy + h/2 + 6*s], fill=ASH)
    d.ellipse([cx - w/2 + 4, cy - h/2 + 2, cx + w/2 - 10, cy], fill=ASH_HI)
    d.ellipse([cx - w/2 + 8, cy + h/2 - 6, cx + w/2 - 8, cy + h/2 + 6], fill=ASH_LO)


def frame(state, i, n):
    row, name, _, col, eye, halo = state
    img = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    glow = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    dg = ImageDraw.Draw(glow)
    d = ImageDraw.Draw(img)

    t = i / max(n - 1, 1)
    puls = 0.85 + 0.15 * math.sin(t * 2 * math.pi)          # Atmen der Glut
    cx, cy = CW // 2, CH // 2 + 6
    bob = -4 * math.sin(t * 2 * math.pi)                     # leichtes Wippen
    squash = 0.0
    dx = 0

    if name == "jumping":
        bob = -26 * math.sin(t * math.pi)
        squash = 0.18 * math.sin(t * math.pi)
    elif name.startswith("run"):
        bob = -6 * abs(math.sin(t * 2 * math.pi))
        dx = (6 if name == "run-right" else -6) * math.sin(t * 2 * math.pi)
        squash = 0.08 * abs(math.sin(t * 2 * math.pi))
    elif name == "failed":
        bob = 3 * math.sin(t * 6 * math.pi)                  # Zittern

    cy += bob; cx += dx

    # Halo
    r = int(52 * halo * puls)
    dg.ellipse([cx - r, cy - r, cx + r, cy + r],
               fill=col + (int(70 * eye * puls),))

    body(d, cx, cy, squash=squash)

    # Glut in den Rissen
    for k, (ox, oy) in enumerate([(-16, 18), (-4, 12), (6, 20), (16, 13)]):
        a = int(200 * eye * puls * (0.6 + 0.4 * math.sin(t * 2 * math.pi + k)))
        d.ellipse([cx + ox - 2, cy + oy - 2, cx + ox + 2, cy + oy + 2],
                  fill=col + (a,))

    # Augen -- der eigentliche Traeger des Zustands
    blink = name == "idle" and 0.46 < t < 0.56
    for ex in (-13, 13):
        ry = 2 if blink else (4 if name == "failed" else 8)
        rx = 6 if name != "failed" else 5
        dg.ellipse([cx + ex - 13, cy - 8 - 13, cx + ex + 13, cy - 8 + 13],
                   fill=col + (int(150 * eye * puls),))
        d.ellipse([cx + ex - rx, cy - 8 - ry, cx + ex + rx, cy - 8 + ry],
                  fill=col + (255,))
        if not blink:
            d.ellipse([cx + ex - 2, cy - 8 - min(ry, 3), cx + ex + 2, cy - 8 + min(ry, 3)],
                      fill=(255, 243, 214, int(120 + 120 * eye)))

    glow = glow.filter(ImageFilter.GaussianBlur(9))
    return Image.alpha_composite(glow, img)


def main():
    here = pathlib.Path(__file__).parent
    sheet = Image.new("RGBA", (CW * COLS, CH * ROWS), (0, 0, 0, 0))
    for st in STATES:
        row, name, n = st[0], st[1], st[2]
        for i in range(n):
            sheet.paste(frame(st, i, n), (i * CW, row * CH))
    sheet.save(here / "spritesheet.png")

    (here / "pet.json").write_text(json.dumps({
        "id": "ember",
        "displayName": "Ember",
        "description": "Kleiner Aschegeist mit Glutaugen. Der Mood ist die Helligkeit.",
        "spritesheetPath": "spritesheet.png",
        # Abweichung mit Absicht (Design 8.2): die Zeilentabelle steht im
        # Manifest, nicht fest im Renderer.
        "atlas": {"cellW": CW, "cellH": CH, "cols": COLS, "rows": ROWS},
        "states": {s[1]: {"row": s[0], "frames": s[2]} for s in STATES},
    }, indent=2, ensure_ascii=False))
    print(f"{here/'spritesheet.png'}  {sheet.size[0]}x{sheet.size[1]}")


if __name__ == "__main__":
    main()
