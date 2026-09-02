#!/usr/bin/env python3
"""Macht aus den Standbild-Pets animierte: ein kurzes Video je Mood, in
Spalten zerlegt.

Warum Video und nicht acht Einzelbilder
----------------------------------------------------------------------------
Der naheliegende Weg waere, `doppelself_gesichter.py` je Frame einmal laufen
zu lassen. Er ist falsch: Qwen-Image-Edit zeichnet jedes Bild komplett neu,
und schon zwischen zwei Ausdruecken wandern Falten, Haarkanten und Glanz.
Bei 12 fps ergibt das kein Bewegtbild, sondern Flimmern. Wan i2v rechnet
dagegen EINE zusammenhaengende Bewegung; die Frames gehoeren zueinander.

Warum der Gruenschirm auch hier
----------------------------------------------------------------------------
Als Vorlage dient nicht die freigestellte Zelle aus dem Sheet, sondern das
Bild, das `doppelself_gesichter.py` erzeugt hat: 1024x1024 mit flachem
gruenem Hintergrund, in ComfyUIs Ausgabeordner. Damit laesst sich jeder
einzelne Frame nach derselben Regel freistellen wie das Standbild -- eine
Freistellung, zwei Anwendungen, keine zweite Fassung.

Warum Ping-Pong
----------------------------------------------------------------------------
Das Face spielt die Spalten in Schleife: 0,1,...,n-1,0. Ein Video endet aber
nicht dort, wo es anfing -- an der Naht ruckt es. Deshalb laufen die Frames
vorwaerts und wieder zurueck. Jede Bewegung wird dadurch umkehrbar und die
Naht verschwindet, ohne dass das Modell eine Schleife rechnen muesste.

Was hier NICHT entschieden wird
----------------------------------------------------------------------------
Welche Moods ueberhaupt laufen. Das entscheidet `face/src/main.rs`:
`RUHIGE_MOODS` zwingt `idle` und `sleeping` auf eine Spalte, egal was im
Manifest steht. Dieses Werkzeug traegt fuer sie folgerichtig kein `frames`
ein -- zwei Stellen mit derselben Regel waeren eine Regel und eine Attrappe.

Aufruf
----------------------------------------------------------------------------
    tools/pet_animieren.py --pet magier --quelle <comfy-output-dir>
    tools/pet_animieren.py --selbsttest
"""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doppelself_gesichter as ds  # noqa: E402  -- Freistellung und Zuschnitt

COMFY_DIR = ds.COMFY_DIR
HOST = ds.HOST

# Wan 2.2 i2v, wie in DoppelSelf/messung_i2v_api.json gemessen.
I2V = {
    "hoch": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    "tief": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "lora_hoch": "wan22_i2v_lightning_high.safetensors",
    "lora_tief": "wan22_i2v_lightning_low.safetensors",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
}
# 512 statt 832x480: die Zelle ist 208 Pixel breit. Die Flaeche bestimmt die
# Rechenzeit fast linear -- 512x512 kostet rund zwei Drittel des gemessenen
# Fensters und liefert immer noch mehr Detail, als die Zelle aufnimmt.
BREITE = HOEHE = 512
# Wan verlangt 4n+1. 33 Frames sind 2,06 s bei 16 fps.
LAENGE = 33
# Jeder vierte Frame: 9 Bilder vorwaerts, mit dem Rueckweg 16 Spalten. Bei
# den 12 fps aus `face/src/main.rs` ist ein Umlauf damit 1,33 s.
SCHRITT = 4
STEPS, CFG, SHIFT = 4, 1.0, 5.0

# Die Bewegung soll TRAGEN, nicht erzaehlen: das Pet sitzt in einer
# 208-Pixel-Zelle am Bildschirmrand. Was dort ankommt, ist Atmen, ein Wiegen,
# ein Lidschlag. Der Hintergrund muss ausdruecklich flach bleiben, sonst
# faengt der Gruenschirm an zu wabern und die Freistellung franst.
BEWEGUNG = ("subtle idle animation, gentle breathing, slight natural head "
            "movement, occasional blink, keeping the same facial expression, "
            "the flat uniform green background stays perfectly still and "
            "uniform, no camera movement, no zoom")
NEGATIV = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
           "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
           "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
           "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")

# `idle` und `sleeping` fehlen absichtlich -- siehe Modulkopf.
ANIMIERTE_MOODS = ["observing", "thinking", "working", "done", "failed",
                   "needs_input"]


def pingpong(anzahl: int) -> list:
    """`[0,1,2]` -> `[0,1,2,1]`. Die Enden kommen genau einmal vor.

    Ohne diese Regel liefe der letzte Frame in den ersten, und genau dort
    ruckt es. Mit ihr ist die Folge an beiden Enden stetig.
    """
    if anzahl <= 1:
        return [0]
    return list(range(anzahl)) + list(range(anzahl - 2, 0, -1))


def graph_bauen(bild_name: str, seed: int, prefix: str) -> dict:
    """Die API-Fassung von `DoppelSelf/messung_i2v_api.json`, mit zwei
    Abweichungen: kleinere Flaeche, und `SaveImage` statt `CreateVideo` --
    gebraucht werden Einzelbilder, ein MP4 waere ein Umweg ueber ffmpeg."""
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": I2V["hoch"], "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader",
              "inputs": {"unet_name": I2V["tief"], "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": I2V["lora_hoch"],
                         "strength_model": 1.0}},
        "4": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["2", 0], "lora_name": I2V["lora_tief"],
                         "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["3", 0], "shift": SHIFT}},
        "6": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["4", 0], "shift": SHIFT}},
        "7": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": I2V["clip"], "type": "wan",
                         "device": "default"}},
        "8": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["7", 0], "text": BEWEGUNG}},
        "9": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["7", 0], "text": NEGATIV}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": I2V["vae"]}},
        "11": {"class_type": "LoadImage", "inputs": {"image": bild_name}},
        "12": {"class_type": "WanImageToVideo",
               "inputs": {"positive": ["8", 0], "negative": ["9", 0],
                          "vae": ["10", 0], "start_image": ["11", 0],
                          "width": BREITE, "height": HOEHE,
                          "length": LAENGE, "batch_size": 1}},
        # Zwei Sampler, hoch und tief -- die Bauform von Wan 2.2. Der erste
        # setzt Rauschen, der zweite verfeinert ohne neues.
        "13": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["5", 0], "positive": ["12", 0],
                          "negative": ["12", 1], "latent_image": ["12", 2],
                          "add_noise": "enable", "noise_seed": seed,
                          "steps": STEPS, "cfg": CFG, "sampler_name": "euler",
                          "scheduler": "simple", "start_at_step": 0,
                          "end_at_step": 2, "return_with_leftover_noise": "enable"}},
        "14": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["6", 0], "positive": ["12", 0],
                          "negative": ["12", 1], "latent_image": ["13", 0],
                          "add_noise": "disable", "noise_seed": 0,
                          "steps": STEPS, "cfg": CFG, "sampler_name": "euler",
                          "scheduler": "simple", "start_at_step": 2,
                          "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "15": {"class_type": "VAEDecode",
               "inputs": {"samples": ["14", 0], "vae": ["10", 0]}},
        "16": {"class_type": "SaveImage",
               "inputs": {"images": ["15", 0], "filename_prefix": prefix}},
    }


def frames_abholen(prompt_id: str, log: str, deckel_s: float) -> list:
    """Wartet auf den Lauf und gibt ALLE Bilder in Reihenfolge zurueck.

    `ds.abholen` liefert genau eines -- hier ist die Reihenfolge der Frames
    der ganze Punkt, deshalb eine eigene Fassung statt eines Schalters.
    """
    t0 = time.time()
    while True:
        try:
            h = json.load(urllib.request.urlopen(
                f"{HOST}/history/{prompt_id}", timeout=30))
        except (urllib.error.URLError, OSError):
            h = {}
        if prompt_id in h:
            break
        if time.time() - t0 > deckel_s:
            raise SystemExit(f"Abbruch: nach {time.time()-t0:.0f} s kein "
                             f"Ergebnis. Log: {log}")
        time.sleep(2)
    eintrag = h[prompt_id]
    if eintrag["status"]["status_str"] != "success":
        raise SystemExit("ComfyUI meldet einen Fehler:\n"
                         + json.dumps(eintrag["status"], indent=2, ensure_ascii=False)
                         + f"\nLog: {log}")
    bilder = []
    for knoten in eintrag["outputs"].values():
        for b in knoten.get("images", []):
            if b.get("type") == "output":
                bilder.append(os.path.join(COMFY_DIR, "output",
                                           b.get("subfolder", ""), b["filename"]))
    if not bilder:
        raise SystemExit("ComfyUI lieferte keine Bilder")
    return sorted(bilder)


def spalten_bauen(frames: list, zelle: tuple) -> list:
    """Aus den Videoframes die Spalten einer Sheet-Zeile.

    Erst jeden SCHRITT-ten nehmen, dann freistellen und zuschneiden, dann
    Ping-Pong. Freigestellt wird bei voller Aufloesung, aus demselben Grund
    wie beim Standbild: die Kante wird glatter als eine bei 208 Pixeln.
    """
    from PIL import Image

    gewaehlt = frames[::SCHRITT]
    zellen = []
    for pfad in gewaehlt:
        with Image.open(pfad) as bild:
            frei = ds.chroma_freistellen(bild.convert("RGBA"))
            zellen.append(ds.zellen_zuschnitt(frei, zelle))
    return [zellen[i] for i in pingpong(len(zellen))]


def manifest_aktualisieren(pfad: str, spalten: int, moods: list) -> dict:
    """Traegt `cols` und je Mood `frames` nach. Ruhige Moods bleiben ohne --
    `RUHIGE_MOODS` in `face/src/main.rs` zwingt sie ohnehin auf eine Spalte,
    und ein `frames`, das nie gilt, waere eine Zusage ohne Wirkung."""
    with open(pfad, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["atlas"]["cols"] = spalten
    for name, eintrag in manifest["moods"].items():
        if name in moods:
            eintrag["frames"] = spalten
        else:
            eintrag.pop("frames", None)
    return manifest


def sheet_bauen(zeilen: dict, reihenfolge: list, spalten: int, zelle: tuple,
                ziel: str) -> None:
    """`zeilen`: Mood -> Liste von Zellen. Wer weniger Zellen hat als
    `spalten`, bekommt seine erste wiederholt -- das Sheet muss rechteckig
    sein, und ein ruhiger Mood liest ohnehin nur Spalte 0."""
    from PIL import Image

    b, h = zelle
    sheet = Image.new("RGBA", (b * spalten, h * len(reihenfolge)), (0, 0, 0, 0))
    for zeile, mood in enumerate(reihenfolge):
        zellen = zeilen[mood]
        for spalte in range(spalten):
            quelle = zellen[spalte] if spalte < len(zellen) else zellen[0]
            sheet.paste(quelle, (spalte * b, zeile * h))
    sheet.save(ziel)


# --- Ablauf ------------------------------------------------------------------

def lauf(args) -> None:
    from PIL import Image

    ziel_dir = os.path.join("face/assets", args.pet)
    manifest_pfad = os.path.join(ziel_dir, "pet.json")
    if not os.path.isfile(manifest_pfad):
        raise SystemExit(f"Abbruch: {manifest_pfad} fehlt -- erst "
                         f"doppelself_gesichter.py laufen lassen.")
    with open(manifest_pfad, encoding="utf-8") as f:
        manifest = json.load(f)
    zelle = (manifest["atlas"]["cellW"], manifest["atlas"]["cellH"])
    reihenfolge = sorted(manifest["moods"], key=lambda n: manifest["moods"][n]["row"])

    if ds.port_belegt():
        raise SystemExit(f"Abbruch: auf Port {ds.PORT} lauscht schon jemand.")

    marke = f"petanim_{os.getpid()}"
    eingang = os.path.join(COMFY_DIR, "input")
    log = os.path.join(ziel_dir, "comfy-anim.log")
    kopien = []
    zeilen = {}

    print(f"[1/3] ComfyUI startet (Port {ds.PORT}), Log: {log}", flush=True)
    server = ds.comfy_starten(log)
    try:
        for i, mood in enumerate(ANIMIERTE_MOODS, 1):
            quelle = None
            for kandidat in sorted(os.listdir(args.quelle)):
                if f"_{mood}_" in kandidat and kandidat.endswith(".png"):
                    quelle = os.path.join(args.quelle, kandidat)
                    break
            if quelle is None:
                raise SystemExit(f"Abbruch: kein Standbild fuer {mood} in {args.quelle}")
            bild_name = f"{marke}_{mood}.png"
            shutil.copy2(quelle, os.path.join(eingang, bild_name))
            kopien.append(os.path.join(eingang, bild_name))

            print(f"[2/3] {i}/{len(ANIMIERTE_MOODS)} {mood} …", flush=True)
            g = graph_bauen(bild_name, args.seed, f"{marke}/{mood}")
            frames = frames_abholen(ds.absenden(g), log, deckel_s=900)
            zeilen[mood] = spalten_bauen(frames, zelle)
    finally:
        ds.comfy_beenden(server)
        for rest in kopien:
            if os.path.exists(rest):
                os.remove(rest)

    spalten = max(len(z) for z in zeilen.values())
    # Ruhige Moods: die vorhandene Zelle aus dem alten Sheet, unveraendert.
    altes = Image.open(os.path.join(ziel_dir, "spritesheet.png")).convert("RGBA")
    for mood in reihenfolge:
        if mood in zeilen:
            continue
        r = manifest["moods"][mood]["row"]
        zeilen[mood] = [altes.crop((0, r * zelle[1], zelle[0], (r + 1) * zelle[1]))]
    altes.close()

    print(f"[3/3] Sheet mit {spalten} Spalten und Manifest …", flush=True)
    sheet_bauen(zeilen, reihenfolge, spalten, zelle,
                os.path.join(ziel_dir, "spritesheet.png"))
    neu = manifest_aktualisieren(manifest_pfad, spalten, ANIMIERTE_MOODS)
    with open(manifest_pfad, "w", encoding="utf-8") as f:
        json.dump(neu, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Fertig: {args.pet} -- {spalten} Spalten, "
          f"{len(ANIMIERTE_MOODS)} animierte Moods")


# --- Selbstpruefung ----------------------------------------------------------

def demo() -> None:
    # Ping-Pong: beide Enden genau einmal, sonst stockt die Schleife dort.
    assert pingpong(1) == [0]
    assert pingpong(2) == [0, 1]
    assert pingpong(3) == [0, 1, 2, 1]
    assert pingpong(9) == [0, 1, 2, 3, 4, 5, 6, 7, 8, 7, 6, 5, 4, 3, 2, 1]
    for n in range(2, 12):
        folge = pingpong(n)
        assert len(folge) == 2 * n - 2, (n, folge)
        assert folge.count(0) == 1 and folge.count(n - 1) == 1, folge
        # Der Schritt von der letzten zur ersten Spalte ist so gross wie
        # jeder andere -- das ist die ganze Zusage.
        ring = folge + [folge[0]]
        schritte = {abs(ring[i + 1] - ring[i]) for i in range(len(ring) - 1)}
        assert schritte == {1}, (n, schritte)

    # Der Graph: jede Referenz zeigt auf einen Knoten, den es gibt.
    g = graph_bauen("x.png", 42, "p")
    for kid, k in g.items():
        for wert in k["inputs"].values():
            if isinstance(wert, list):
                assert wert[0] in g, f"{kid} zeigt auf {wert[0]}"
    assert g["12"]["inputs"]["length"] == LAENGE
    assert list(k["class_type"] for k in g.values()).count("KSamplerAdvanced") == 2

    # Manifest: animierte Moods bekommen `frames`, ruhige nicht.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "pet.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"atlas": {"cellW": 208, "cellH": 208, "cols": 1, "rows": 8},
                       "moods": {m: {"row": i} for i, m in enumerate(
                           ["sleeping", "idle"] + ANIMIERTE_MOODS)}}, f)
        neu = manifest_aktualisieren(p, 16, ANIMIERTE_MOODS)
    assert neu["atlas"]["cols"] == 16
    for m in ANIMIERTE_MOODS:
        assert neu["moods"][m]["frames"] == 16, m
    for ruhig in ("idle", "sleeping"):
        assert "frames" not in neu["moods"][ruhig], ruhig
    print("Selbsttest ok.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pet", help="Verzeichnisname unter face/assets/")
    p.add_argument("--quelle", help="ComfyUI-Ausgabeordner mit den Gruenschirm-Standbildern")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--selbsttest", action="store_true")
    args = p.parse_args()
    if args.selbsttest:
        demo()
        return
    if not args.pet or not args.quelle:
        p.error("--pet und --quelle noetig (oder --selbsttest)")
    lauf(args)


if __name__ == "__main__":
    sys.exit(main())
