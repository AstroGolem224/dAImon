#!/usr/bin/env python3
"""Baut aus einem Portraitfoto ein Pet mit einem Gesicht je Mood.

Warum Standbilder und kein Video
----------------------------------------------------------------------------
Das Face zeichnet je Zustand genau EINE Zelle -- `atlas.frame(zeile, 0)` in
`face/src/surface.rs`. Spalte 0, immer. Ein animiertes Sheet waere also
Rechenzeit fuer Pixel, die niemand sieht. Und ein sprechender Avatar in
Echtzeit ist ohnehin ausgeschlossen: Wan 2.2-S2V braucht rund 40 s je
4,81 s Video (gemessen, `DoppelSelf/PLAN.md`) und dabei 29 GB VRAM -- die
Karte, auf der zur selben Zeit Mimic die Stimme rechnet.

Also acht Bilder, einmal erzeugt, danach kostenlos.

Was hier NICHT entschieden wird
----------------------------------------------------------------------------
Welcher Mood gerade gilt. Den rechnet der Hub; dieses Werkzeug legt nur ab,
wie er aussieht. Die Zuordnung Mood -> Zeile steht im `moods`-Block der
erzeugten `pet.json` und wird von `zustand_abbilden` gelesen.

Aufruf
----------------------------------------------------------------------------
    tools/doppelself_gesichter.py --foto portrait.png
    tools/doppelself_gesichter.py --selbsttest      # ohne GPU, ohne ComfyUI

Danach:
    DAIMON_PET_MANIFEST=face/assets/doppelself/pet.json cargo run -p face
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

COMFY_DIR = "/mnt/data/AI/ComfyUI"
COMFY_PY = f"{COMFY_DIR}/venv/bin/python"
PORT = 8189
HOST = f"http://127.0.0.1:{PORT}"

# Qwen-Image-Edit statt Wan: ein Bild aus einem Bild, keine Zeitachse. Rund
# zehn Sekunden je Gesicht gegen 40 s je Videofenster.
MODELLE = {
    "diffusion_models": "qwen_image_edit_2511_fp8mixed.safetensors",
    "text_encoders": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
    "loras": "Qwen-Image-Edit-Lightning-8steps-V1.0.safetensors",
}
STEPS = 8          # zur 8-Step-Lightning-LoRA passend
CFG = 1.0          # Lightning-LoRAs sind auf CFG 1 destilliert
SHIFT = 3.1
SAMPLER = "euler"
SCHEDULER = "simple"

# Die Zellgroesse des hatch-pet-Formats. face/src/sprite.rs haelt dieselben
# Zahlen als Vorgabe; hier stehen sie, weil das Sheet danach geschnitten wird.
ZELLE_B, ZELLE_H = 192, 208

# Die acht Moods des Hubs (face/src/render.rs), in Zeilenreihenfolge. Die
# Reihenfolge ist frei -- sie steht im `moods`-Block und nirgends sonst.
#
# Jeder Text beschreibt NUR den Gesichtsausdruck. Kleidung, Hintergrund und
# Bildausschnitt bleiben ausdruecklich unangetastet: acht Bilder, die sich in
# mehr als dem Ausdruck unterscheiden, flackern beim Moodwechsel.
GLEICHBLEIBEND = ("Keep the same person, same face, same hair, same clothing, "
                  "same lighting, same camera angle. "
                  "Change only the facial expression. "
                  "Replace the background with a plain, flat, uniform pure "
                  "green background, RGB 0,255,0, no gradient, no vignette, "
                  "no stars, no glow, no shadow on the background.")
MOODS = [
    ("sleeping", "The eyes are closed, the face relaxed and asleep."),
    ("idle", "A calm, neutral, relaxed expression, eyes open, looking ahead."),
    ("observing", "Attentive and alert, eyes wide open and watching closely, "
                  "eyebrows slightly raised."),
    ("thinking", "Concentrated and pensive, eyes looking slightly upward, "
                 "brow furrowed in thought."),
    ("working", "Focused and determined, eyes narrowed in concentration, "
                "mouth closed and firm."),
    ("done", "A warm, satisfied smile, eyes friendly and bright."),
    ("failed", "A worried, dismayed expression, eyebrows drawn together, "
               "mouth turned down."),
    ("needs_input", "An enquiring expression, one eyebrow raised, "
                    "mouth slightly open as if about to ask a question."),
]
NEGATIV = "blurry, distorted face, extra limbs, text, watermark"


# --- Der Graph ---------------------------------------------------------------

def graph_bauen(bild_name: str, prompt: str, seed: int, prefix: str) -> dict:
    """Flache API-Fassung der ComfyUI-Vorlage `image_qwen_image_edit_2511`.

    Die Vorlage liegt als Subgraph vor und ist so nicht absendbar; die
    Knotenfolge hier ist ihre Aufloesung, Kante fuer Kante.
    """
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": MODELLE["diffusion_models"],
                         "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": MODELLE["text_encoders"],
                         "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": MODELLE["vae"]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": bild_name}},
        # Auf ein Raster, das das Modell kennt. Ohne diesen Schritt liefert ein
        # krummes Portraitformat Artefakte am Rand.
        "5": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
        "6": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["5", 0],
                         "prompt": prompt}},
        "7": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["5", 0],
                         "prompt": NEGATIV}},
        # Die Vorlage nennt diese beiden noetig fuer umgepackte Modelldateien
        # -- und `..._fp8mixed` ist genau so eine.
        "8": {"class_type": "FluxKontextMultiReferenceLatentMethod",
              "inputs": {"conditioning": ["6", 0],
                         "reference_latents_method": "index_timestep_zero"}},
        "9": {"class_type": "FluxKontextMultiReferenceLatentMethod",
              "inputs": {"conditioning": ["7", 0],
                         "reference_latents_method": "index_timestep_zero"}},
        "10": {"class_type": "ModelSamplingAuraFlow",
               "inputs": {"model": ["1", 0], "shift": SHIFT}},
        "11": {"class_type": "CFGNorm",
               "inputs": {"model": ["10", 0], "strength": 1.0}},
        "12": {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": ["11", 0], "lora_name": MODELLE["loras"],
                          "strength_model": 1.0}},
        "13": {"class_type": "VAEEncode",
               "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        "14": {"class_type": "KSampler",
               "inputs": {"model": ["12", 0], "positive": ["8", 0],
                          "negative": ["9", 0], "latent_image": ["13", 0],
                          "seed": seed, "steps": STEPS, "cfg": CFG,
                          "sampler_name": SAMPLER, "scheduler": SCHEDULER,
                          "denoise": 1.0}},
        "15": {"class_type": "VAEDecode",
               "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
        "16": {"class_type": "SaveImage",
               "inputs": {"images": ["15", 0], "filename_prefix": prefix}},
    }


# --- ComfyUI -----------------------------------------------------------------

def comfy_bereit() -> bool:
    try:
        urllib.request.urlopen(HOST + "/system_stats", timeout=2).read()
        return True
    except (urllib.error.URLError, OSError):
        return False


def port_belegt() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def comfy_starten(log: str):
    p = subprocess.Popen(
        [COMFY_PY, "main.py", "--port", str(PORT), "--listen", "127.0.0.1",
         "--cache-lru", "1", "--reserve-vram", "2.0", "--disable-auto-launch"],
        cwd=COMFY_DIR, stdout=open(log, "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(300):
            if p.poll() is not None:
                raise SystemExit(f"Abbruch: ComfyUI beendete sich beim Start, Log: {log}")
            if comfy_bereit():
                return p
            time.sleep(1)
        raise SystemExit(f"Abbruch: ComfyUI war nach 300 s nicht erreichbar, Log: {log}")
    except BaseException:      # auch Ctrl-C: kein verwaister Server auf 8189
        comfy_beenden(p)
        raise


def comfy_beenden(p) -> None:
    p.terminate()
    try:
        p.wait(timeout=120)
    except subprocess.TimeoutExpired:
        p.kill()
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass               # haengt im CUDA-Treiber; die Ausnahme davor zaehlt


def absenden(g: dict) -> str:
    req = urllib.request.Request(
        HOST + "/prompt", data=json.dumps({"prompt": g}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))["prompt_id"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"ComfyUI lehnt den Graphen ab (HTTP {e.code}):\n"
                         f"{e.read().decode()}")
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"Abbruch: Graph nicht absendbar ({e}).")


def abholen(prompt_id: str, log: str, deckel_s: float) -> str:
    """Wartet auf genau ein Bild und gibt seinen Pfad zurueck."""
    t0 = time.time()
    while True:
        try:
            h = json.load(urllib.request.urlopen(
                f"{HOST}/history/{prompt_id}", timeout=30))
        except (urllib.error.URLError, OSError) as e:
            grund = getattr(e, "reason", e)
            if not isinstance(grund, (TimeoutError, socket.timeout)):
                raise SystemExit(f"Abbruch: ComfyUI antwortet nicht mehr ({e}). "
                                 f"Log: {log}")
            h = {}
        if prompt_id in h:
            break
        if time.time() - t0 > deckel_s:
            raise SystemExit(f"Abbruch: nach {time.time() - t0:.0f} s kein "
                             f"Ergebnis. Log: {log}")
        time.sleep(2)

    eintrag = h[prompt_id]
    if eintrag["status"]["status_str"] != "success":
        raise SystemExit("ComfyUI meldet einen Fehler:\n"
                         + json.dumps(eintrag["status"], indent=2, ensure_ascii=False)
                         + f"\nLog: {log}")
    for knoten in eintrag["outputs"].values():
        for b in knoten.get("images", []):
            if b.get("type") == "output":
                return os.path.join(COMFY_DIR, "output", b.get("subfolder", ""),
                                    b["filename"])
    raise SystemExit("ComfyUI lieferte kein Bild: "
                     + json.dumps(eintrag["outputs"])[:2000])


# --- Sheet und Manifest ------------------------------------------------------

# Wie stark Gruen die anderen Kanaele schlagen muss, damit ein Pixel als
# Hintergrund gilt. 40 von 255 laesst Hauttoene und das schwarze Hemd sicher
# stehen und greift trotzdem bis in die Kante.
CHROMA_SCHWELLE = 40


def chroma_freistellen(bild):
    """Macht den gruenen Hintergrund durchsichtig. `bild` ist RGBA.

    Warum ein Gruenschirm und keine Freistellung im Nachhinein
    ------------------------------------------------------------------------
    Der erste Versuch am 31.08. schnitt die fertigen Bilder mit GrabCut aus.
    Das ging schief, und zwar lehrreich: das schwarze Hemd hat dieselbe
    Helligkeit wie der dunkle Hintergrund und flog mit hinaus, waehrend der
    orange Schein hell genug war und drinblieb. Helligkeit trennt hier nichts.
    Sauber trennen koennte das nur ein Modell, das „Person" kennt -- ein
    Download von einigen hundert MB.

    Ein flacher Gruenschirm dreht das Problem um: die Trennung entsteht beim
    Erzeugen, wo das Modell ohnehin schon weiss, was Person ist, und der
    Schnitt danach ist ein Schwellwert statt einer Schaetzung.
    """
    from PIL import Image, ImageChops, ImageFilter

    r, g, b, _ = bild.split()
    staerkster = ImageChops.lighter(r, b)
    # Gruendominanz: wie weit uebertrifft Gruen den staerkeren der anderen?
    dominanz = ImageChops.subtract(g, staerkster)
    alpha = dominanz.point(lambda wert: 0 if wert > CHROMA_SCHWELLE else 255)
    # Eine harte Alphakante sieht auf dem Desktop ausgeschnitten aus.
    alpha = alpha.filter(ImageFilter.GaussianBlur(1))
    # Gruenschleier an der Kante wegnehmen: Gruen darf den staerkeren der
    # beiden anderen Kanaele nicht mehr ueberragen. Ohne das bekommt jede
    # Silhouette einen giftgruenen Saum.
    g = ImageChops.darker(g, staerkster)
    return Image.merge("RGBA", (r, g, b, alpha))


def sheet_bauen(bilder: list, ziel: str, zelle: tuple = (ZELLE_B, ZELLE_H)) -> None:
    """Eine Spalte, eine Zeile je Mood. Mehr Spalten waeren toter Speicher --
    das Face liest nur Spalte 0."""
    from PIL import Image

    b, h = zelle
    sheet = Image.new("RGBA", (b, h * len(bilder)), (0, 0, 0, 0))
    for zeile, pfad in enumerate(bilder):
        with Image.open(pfad) as bild:
            # Erst freistellen, dann zuschneiden: der Schnitt bei voller
            # Aufloesung gibt eine glattere Kante als einer auf 192 Pixeln.
            freigestellt = chroma_freistellen(bild.convert("RGBA"))
            sheet.paste(zellen_zuschnitt(freigestellt, zelle), (0, zeile * h))
    sheet.save(ziel)


def zellen_zuschnitt(bild, zelle: tuple = (ZELLE_B, ZELLE_H)):
    """Mittig auf das Zellverhaeltnis beschneiden, dann skalieren.

    Beschneiden VOR dem Skalieren: ein quadratisches Portrait auf 192x208 zu
    quetschen macht aus jedem Gesicht ein anderes Gesicht.
    """
    from PIL import Image

    zb, zh = zelle
    b, h = bild.size
    ziel = zb / zh
    if b / h > ziel:                      # zu breit -> links und rechts weg
        neu_b = round(h * ziel)
        kasten = ((b - neu_b) // 2, 0, (b - neu_b) // 2 + neu_b, h)
    else:                                 # zu hoch -> oben und unten weg
        neu_h = round(b / ziel)
        kasten = (0, (h - neu_h) // 2, b, (h - neu_h) // 2 + neu_h)
    return bild.crop(kasten).resize((zb, zh), Image.LANCZOS)


def zelle_lesen(text: str) -> tuple:
    """`"208x208"` -> `(208, 208)`. Eine krumme Angabe ist ein Abbruch und
    keine stille Vorgabe: ein Sheet in der falschen Groesse faellt erst am
    schwarzen Pet auf."""
    try:
        b, h = (int(teil) for teil in text.lower().split("x", 1))
    except ValueError:
        raise SystemExit(f"Abbruch: --zelle braucht BREITExHOEHE, war {text!r}")
    if not (0 < b <= 4096 and 0 < h <= 4096):
        raise SystemExit(f"Abbruch: --zelle ausserhalb 1..4096, war {b}x{h}")
    return (b, h)


def manifest_bauen(moods: list, zelle: tuple = (ZELLE_B, ZELLE_H),
                   pet_id: str = "doppelself", anzeige: str = "Doppel-Self",
                   beschreibung: str = "Das eigene Gesicht, ein Ausdruck je Mood.",
                   toenung: float = 0.0) -> dict:
    """Die Vorgaben sind NICHT beliebig: sie erzeugen woertlich
    `face/tests/doppelself-pet.json`, und daran haengt die Naht zum
    Rust-Parser (`erzeugtes_doppelself_manifest_wird_verstanden`). Wer sie
    aendert, zerreisst die Naht -- neue Pets bekommen Argumente, keine neuen
    Vorgaben."""
    return {
        "id": pet_id,
        "displayName": anzeige,
        "description": beschreibung,
        "spritesheetPath": "spritesheet.png",
        "atlas": {"cellW": zelle[0], "cellH": zelle[1],
                  "cols": 1, "rows": len(moods)},
        # Traegt das BILD den Mood, ist die Toenung schaedlich: sie faerbt das
        # Gesicht violett und loescht genau die Information, die es traegt.
        # Das gilt fuer einen Kopfausschnitt -- gemessen am 01.09.: dort
        # unterscheiden sich zwei Moods im Mittel um 6,0 (max 10,0).
        #
        # Bei einer HALBFIGUR gilt es nicht. Dieselbe Messung ergab dort 1,3
        # bis 2,7: das Gesicht ist zu klein, der Ausdruck traegt kaum. Solche
        # Pets brauchen die Farbtabelle als zweiten Kanal, und darum ist das
        # hier ein Schalter und keine Konstante.
        # 0 und 1 bleiben `false`/`true`: so schreibt das Werkzeug weiter
        # genau `face/tests/doppelself-pet.json`, und die Naht zum
        # Rust-Parser haelt. Alles dazwischen ist eine Zahl.
        "toenung": {0.0: False, 1.0: True}.get(float(toenung), float(toenung)),
        "moods": {name: {"row": zeile} for zeile, name in enumerate(moods)},
        # Der Rueckfallweg fuer alles, was der `moods`-Block nicht kennt.
        "states": {"idle": {"row": moods.index("idle")},
                   "waiting": {"row": moods.index("needs_input")}},
    }


# --- Ablauf ------------------------------------------------------------------

def preflight(args) -> None:
    if not os.path.isfile(args.foto):
        raise SystemExit(f"Abbruch: Foto nicht gefunden: {args.foto}")
    for ordner, datei in MODELLE.items():
        pfad = os.path.join(COMFY_DIR, "models", ordner, datei)
        if not os.path.isfile(pfad):
            raise SystemExit(f"Abbruch: Modelldatei fehlt: {pfad}")
    if not os.access(os.path.join(COMFY_DIR, "input"), os.W_OK):
        raise SystemExit(f"Abbruch: {COMFY_DIR}/input nicht beschreibbar.")
    if os.path.exists(os.path.join(args.ziel, "pet.json")) and not args.force:
        raise SystemExit(f"Abbruch: {args.ziel} hat schon ein Pet. "
                         f"Mit --force ueberschreiben.")
    if port_belegt():
        raise SystemExit(f"Abbruch: auf Port {PORT} lauscht schon jemand. "
                         f"Zwei ComfyUI passen nicht in die Karte.")
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise SystemExit("Abbruch: Pillow fehlt -- ohne das kein Sheet.")


def lauf(args) -> None:
    preflight(args)
    os.makedirs(args.ziel, exist_ok=True)

    marke = f"dsgesicht_{os.getpid()}"
    bild_name = marke + os.path.splitext(args.foto)[1].lower()
    bild = os.path.join(COMFY_DIR, "input", bild_name)
    log = os.path.join(args.ziel, "comfy.log")
    shutil.copy2(args.foto, bild)

    try:
        print(f"[1/3] ComfyUI startet (Port {PORT}), Log: {log}", flush=True)
        server = comfy_starten(log)
        bilder = []
        try:
            for i, (name, ausdruck) in enumerate(MOODS, 1):
                prompt = f"{ausdruck} {GLEICHBLEIBEND}"
                print(f"[2/3] {i}/{len(MOODS)} {name} …", flush=True)
                # Derselbe Seed fuer alle acht: was sich unterscheiden soll,
                # ist der Ausdruck, nicht das Rauschen.
                g = graph_bauen(bild_name, prompt, args.seed,
                                f"{marke}/{i:02d}_{name}")
                bilder.append(abholen(absenden(g), log, deckel_s=600))
        finally:
            comfy_beenden(server)
    finally:
        if os.path.exists(bild):
            os.remove(bild)

    print("[3/3] Sheet und Manifest …", flush=True)
    zelle = zelle_lesen(args.zelle)
    sheet_bauen(bilder, os.path.join(args.ziel, "spritesheet.png"), zelle)
    pet_id = os.path.basename(os.path.normpath(args.ziel))
    manifest = manifest_bauen(
        [name for name, _ in MOODS], zelle, pet_id,
        args.anzeige or pet_id.replace("_", " ").replace("-", " ").title(),
        args.beschreibung or f"Ein Ausdruck je Mood ({pet_id}).",
        max(0.0, min(1.0, args.toenung)))
    with open(os.path.join(args.ziel, "pet.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Fertig: {args.ziel}\n"
          f"  DAIMON_PET_MANIFEST={os.path.join(args.ziel, 'pet.json')} "
          f"daimon-face")


# --- Selbstpruefung ----------------------------------------------------------

def demo() -> None:
    from PIL import Image

    namen = [name for name, _ in MOODS]
    assert len(namen) == len(set(namen)), namen
    # Der Hub kennt genau diese acht. Ein neunter waere ein Mood ohne Gesicht,
    # ein fehlender ein Gesicht, das nie erscheint.
    assert set(namen) == {"sleeping", "idle", "observing", "thinking",
                          "working", "done", "failed", "needs_input"}, namen

    m = manifest_bauen(namen)
    assert m["atlas"]["rows"] == len(namen), m["atlas"]
    assert m["toenung"] is False
    assert sorted(m["moods"]) == sorted(namen)
    assert {e["row"] for e in m["moods"].values()} == set(range(len(namen)))
    # Der Rueckfallweg darf nie auf eine Zeile ausserhalb des Sheets zeigen.
    for e in m["states"].values():
        assert 0 <= e["row"] < m["atlas"]["rows"], m["states"]

    # Die andere Haelfte der Naht: face/src/sprite.rs liest dieselbe Datei in
    # `erzeugtes_doppelself_manifest_wird_verstanden`. Weicht der Erzeuger ab,
    # faellt es hier auf und nicht erst am schwarzen Pet.
    fixture = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "face", "tests", "doppelself-pet.json")
    with open(fixture, encoding="utf-8") as f:
        assert json.load(f) == m, f"{fixture} passt nicht mehr zu manifest_bauen()"

    # Zellangabe: eine krumme Eingabe muss abbrechen, nicht still auf eine
    # Vorgabe zurueckfallen.
    assert zelle_lesen("208x208") == (208, 208)
    assert zelle_lesen("192X208") == (192, 208)
    for schlecht in ("208", "axb", "0x10", "10x0", "-4x8", "5000x5000", ""):
        try:
            zelle_lesen(schlecht)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"--zelle {schlecht!r} haette abbrechen muessen")

    # Ein zweites Pet aendert Zelle, Kennung und Namen -- und laesst die
    # Mood-Zuordnung in Ruhe. Das ist der ganze Zweck der Parameter.
    zweites = manifest_bauen(namen, (208, 208), "magier", "Magier", "Umhang.")
    assert zweites["id"] == "magier" and zweites["displayName"] == "Magier"
    assert zweites["atlas"]["cellW"] == 208 and zweites["atlas"]["cellH"] == 208
    assert zweites["moods"] == m["moods"], "Mood-Zuordnung darf nicht mitwandern"
    assert zweites["toenung"] is False, "Vorgabe bleibt aus"
    # Der Anteil muss ankommen -- sonst waere die Zeile darueber gruen, weil
    # das Feld gar nicht mehr gelesen wird. Und 0/1 bleiben Wahrheitswerte,
    # damit die Fixture-Naht haelt.
    bau = lambda t: manifest_bauen(namen, (208, 208), "held", "Held", "x", t)["toenung"]
    assert bau(1.0) is True, bau(1.0)
    assert bau(0.0) is False, bau(0.0)
    assert bau(0.4) == 0.4, bau(0.4)

    # Quadratische Vorlage auf quadratische Zelle: nichts wird beschnitten.
    quadrat_gross = Image.new("RGBA", (1254, 1254))
    assert zellen_zuschnitt(quadrat_gross, (208, 208)).size == (208, 208)

    g = graph_bauen("x.png", "laechelt", 42, "p")
    knoten = {k: v["class_type"] for k, v in g.items()}
    assert list(knoten.values()).count("KSampler") == 1, knoten
    assert g["14"]["inputs"]["cfg"] == CFG and g["14"]["inputs"]["steps"] == STEPS
    # Jede Referenz zeigt auf einen Knoten, den es gibt -- ein Tippfehler in
    # einer Kante faellt sonst erst nach dem Modell-Laden auf, Minuten spaeter.
    for kid, k in g.items():
        for wert in k["inputs"].values():
            if isinstance(wert, list):
                assert wert[0] in g, f"{kid} zeigt auf {wert[0]}"

    # Chroma-Schnitt. Der zweite Teil ist der wichtigere: der erste Versuch
    # (GrabCut auf dem fertigen Bild) schnitt genau das schwarze Hemd weg,
    # weil es so dunkel war wie der Hintergrund.
    # Bloecke statt einzelner Pixel: der 1-Pixel-Weichzeichner der Kante
    # wuerde eine 4-Pixel-Probe komplett verschmieren und nur sich selbst
    # messen. Gemessen wird je Blockmitte.
    kante = 40
    farben = [(0, 255, 0, 255),        # Gruenschirm
              (222, 170, 135, 255),    # Haut
              (12, 12, 14, 255),       # schwarzes Hemd
              (35, 45, 42, 255),       # kalter Schatten auf dunklem Stoff
              (40, 90, 45, 255)]       # gesaettigtes Gruen IM Motiv
    probe = Image.new("RGBA", (kante * len(farben), kante))
    for i, farbe in enumerate(farben):
        probe.paste(farbe, (i * kante, 0, (i + 1) * kante, kante))
    frei = chroma_freistellen(probe)
    mitte = [(i * kante + kante // 2, kante // 2) for i in range(len(farben))]
    a = [frei.getpixel(p)[3] for p in mitte]
    assert a[0] == 0, f"Gruenschirm nicht entfernt: {a}"
    assert a[1] == 255, f"Haut weggeschnitten: {a}"
    assert a[2] == 255, f"schwarzes Hemd weggeschnitten: {a}"
    assert a[3] == 255, f"kalter Schatten weggeschnitten: {a}"
    # ponytail: die Decke des Verfahrens, ausdruecklich festgehalten statt
    # weggewuenscht. Gesaettigtes Gruen IM Motiv faellt mit heraus -- so
    # arbeitet jeder Chroma-Key, und darum traegt niemand Gruen vor einem
    # Gruenschirm. Die Schwelle hochzudrehen, bis dieser Fall ueberlebt,
    # wuerde die Kante weich und den Saum gruen machen. Traegt das Motiv je
    # Gruen, ist der Weg ein Mattierungsmodell (RMBG-2.0), nicht ein
    # anderer Schwellwert.
    assert a[4] == 0, f"Chroma-Key greift nicht mehr wie beschrieben: {a}"
    # Gruenschleier: reines Gruen darf nach der Daempfung nicht mehr
    # ueberstrahlen.
    gruen = frei.getpixel(mitte[0])
    assert gruen[1] <= max(gruen[0], gruen[2]), f"Gruenschleier bleibt: {gruen}"

    # Zuschnitt: ein quadratisches Bild wird beschnitten, nicht gequetscht.
    quadrat = Image.new("RGBA", (640, 640))
    assert zellen_zuschnitt(quadrat).size == (ZELLE_B, ZELLE_H)
    breit = Image.new("RGBA", (1000, 200))
    assert zellen_zuschnitt(breit).size == (ZELLE_B, ZELLE_H)

    print("Selbsttest ok.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--foto", help="Portraitfoto, moeglichst frontal")
    p.add_argument("--ziel", default="face/assets/doppelself",
                   help="Verzeichnis fuer pet.json und spritesheet.png")
    p.add_argument("--zelle", default=f"{ZELLE_B}x{ZELLE_H}",
                   help="Zellgroesse BREITExHOEHE. Quadratisch, wenn die "
                        "Vorlage quadratisch ist und ihr Format bleiben soll")
    p.add_argument("--anzeige", help="Name im Menue (Vorgabe: aus --ziel)")
    p.add_argument("--toenung", type=float, default=0.0, metavar="ANTEIL",
                   help="Anteil des Mood-Farbtons, 0 bis 1. 0 fuer "
                        "Kopfausschnitte (das Gesicht traegt den Mood). Fuer "
                        "Halbfiguren 0.4: dort wird die Mood-Trennung erstmals "
                        "groesser als die Bewegung im Clip (gemessen 01.09.)")
    p.add_argument("--beschreibung")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--force", action="store_true")
    p.add_argument("--selbsttest", action="store_true")
    args = p.parse_args()
    if args.selbsttest:
        demo()
        return
    if not args.foto:
        p.error("--foto fehlt (oder --selbsttest)")
    lauf(args)


if __name__ == "__main__":
    sys.exit(main())
