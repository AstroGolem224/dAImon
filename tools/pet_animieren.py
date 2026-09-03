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
import re
import shutil
import sys
import tempfile
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
# Wan verlangt 4n+1. Bei 16 fps sind 33 Frames 2,06 s, 21 Frames 1,31 s.
#
# Zwei Laengen, und der Unterschied ist der Grund fuer diesen ganzen Umbau:
# das Modell DRIFTET ueber die Clipzeit. Am 02.09. lief der Atem ueber die
# vollen 2,06 s und war am Ende ein breites Grinsen -- beim Kiesel wuchs sogar
# ein Mund, den der Stein nie hatte. Der Atem bekommt darum das kuerzere
# Fenster: weniger Zeit, weniger Drift. Die Geste darf laenger, sie ist
# endlich und faellt danach in den Atem zurueck.
LAENGE_ATEM = 13
LAENGE_EMOTE = 33
# Jeder n-te Frame. Der Atem nimmt JEDEN -- 21 Bilder vorwaerts ergeben mit
# dem Rueckweg 41 Spalten und bei 12 fps einen Umlauf von 3,42 s, also einen
# ruhigen Atemzug von rund 18 pro Minute. Jeder zweite Frame waere der doppelte
# Sprung je Schritt; genau das sah man am 02.09. als Ruckeln, und es wurde
# nicht besser davon, dass die Bildrate danach auch noch auf 3 fiel.
#
# Eine Geste in derselben Dehnung liest sich nicht mehr als Geste, darum bleibt
# das Emote bei jedem vierten Frame und 1,33 s.
SCHRITT_ATEM = 1
SCHRITT_EMOTE = 4

# Wie weit das Gesicht von seinem ersten Bild abweichen darf, bevor die Zeile
# abgeschnitten wird. Mittlerer Absolutabstand je Kanal, 0..255.
#
# Diese Zahl ersetzt einen Versuch, der gescheitert ist. Am 02.09. wurde die
# Drift ueber den Prompt bekaempft -- kuerzerer Clip, ausdruecklich
# festgehaltener Mund, dann CFG 2 mit einem chinesischen Laechel-Verbot. Der
# beste Lauf kam auf 3,53. Derselbe Aufruf mit zwei anderen Seeds kam auf
# 23,25 und 17,57. Nicht die Einstellung war gut, der Seed war es.
#
# Ein Modell, dessen Ergebnis dreimal so weit streut wie der Effekt, den man
# sucht, laesst sich nicht ueberreden. Es laesst sich messen: erzeugt wird der
# volle Clip, behalten wird der Anfang, solange das Gesicht ruhig bleibt.
# 6,0 liegt zwischen den beiden gesehenen Faellen -- 5,90 war ruhig, 14 und
# darueber war ein Grinsen.
# Wie weit sich das Gesicht VERFORMEN darf -- nicht, wie weit es sich bewegen
# darf. Der Unterschied ist der ganze Punkt.
#
# Ein roher Bildvergleich misst beides auf einmal, und am 03.09. rutschte
# `doppelself` genau dadurch zweimal durch: die Figur wippte stark, und unter
# dem Wippen ging der Mund auf. Erst ein Schwerpunkt-Ausrichten sollte helfen,
# half aber nicht -- bei einem Kopfausschnitt bewegt sich der Kopf GEGEN den
# Rumpf, und ein Schwerpunkt der ganzen Figur richtet das nicht aus.
#
# Gemessen wird darum der REST: der kleinste Unterschied ueber alle kleinen
# Verschiebungen. Was danach noch bleibt, hat sich wirklich geaendert.
# An den fertigen Zeilen des 03.09. kalibriert -- die als gut befundenen kamen
# auf 4,7 bis 11,4, `doppelself` mit offenem Mund auf 16,4:
GESICHT_GRENZE = 12.0
# Wie weit gesucht wird, in Pixeln je Richtung. Was der Suchraum nicht
# abdeckt, bleibt als Rest stehen und sieht aus wie eine Verformung -- bei
# sechs Pixeln blieb ein Wippen von zehn zu 9,03 uebrig, obwohl sich nichts
# verformt hatte. Zehn deckt das gemessene Wippen ab; der Preis ist
# quadratisch (441 statt 169 Verschiebungen) und liegt bei Sekundenbruchteilen
# je Zeile.
SUCHRAUM = 10
# Und die Gegenrichtung: bleibt gar nichts uebrig, das sich bewegt, ist das
# kein ruhiges Atmen, sondern ein Standbild. Die beiden sehen in jeder
# Gesamtzahl gleich aus, und genau daran sind hier schon Befunde gescheitert.
BRUST_MINDEST = 0.8
# Weniger Bilder tragen keinen Atemzug mehr.
SPALTEN_MINDEST = 8
# Nach oben deckelt der Clip selbst; mehr als er hat, kann nicht behalten
# werden.
FRAMES_HOECHSTENS = LAENGE_ATEM

# Zwischenbilder. 13 Bilder ergeben mit dem Rueckweg 24 Spalten und bei 12 fps
# einen Atemzug von 2,0 s -- zu hastig fuer Ruhe. Der naheliegende Ausweg
# waere, langsamer abzuspielen; genau der war am 02.09. der Fehler, denn
# dieselben Spruenge seltener gezeigt sind das Ruckeln.
#
# Also andersherum: zwischen je zwei Bildern eins einblenden. Der Schritt
# halbiert sich, der Atemzug verdoppelt sich. Das geht nur, WEIL die Schritte
# schon winzig sind -- bei grosser Bewegung waere eine Ueberblendung ein
# Doppelbild. Hier ist sie eine Zwischenstellung.
ZWISCHENBILDER = 1

# Wie viele Seeds fuer eine Atemzeile versucht werden.
#
# Nicht Vorsicht, sondern Messung: derselbe Aufruf ergab am 02.09. an drei
# Seeds eine Gesichtsdrift von 3,5, 23,3 und 17,6. Die Streuung ist groesser
# als jeder Effekt, den Prompt oder CFG hergeben -- ein einzelner Lauf ist
# darum ein Wurf, kein Ergebnis. Es gewinnt der Seed mit dem laengsten ruhigen
# Anfang; die Geste braucht das nicht, sie DARF das Gesicht bewegen.
ATEM_SEEDS = 3

# Verbotene Verben im Atemtext. Der Atem laeuft endlos; eine Geste darin
# wiederholt sich, bis es weh tut -- am 02.09. hat genau das ununterbrochen
# gelacht.
ATEM_VERBOTEN = ("nod", "shake", "turn", "wave", "gestur", "walk", "smile",
                 "laugh", "grin", "deepen", "brighten", "widen", "becomes",
                 "grows", "raise", "tilt")


def gesten_laden(pfad: str | None) -> dict:
    """Die sechs Gestentexte, wahlweise aus einer Datei.

    Die Vorgabe beschreibt Lippen, Mundwinkel und Augenbrauen. Der Kiesel hat
    davon nichts -- er ist ein Stein mit zwei aufgeklebten Wackelaugen. Am
    03.09. bekam er die Vorgabe trotzdem, und der Editor baute ihm einen Mund
    an und liess die Wackelaugen verschwinden. Derselbe Fehler wie beim
    Atemtext, nur eine Stelle weiter.

    Die NAMEN bleiben die sechs animierten Moods; alles andere waere eine
    Geste, die kein Zustand je aufruft.
    """
    if pfad is None:
        return EMOTE
    with open(pfad, encoding="utf-8") as f:
        daten = json.load(f)
    fehlt = set(ANIMIERTE_MOODS) - set(daten)
    zuviel = set(daten) - set(ANIMIERTE_MOODS)
    if fehlt or zuviel:
        raise SystemExit(
            f"Abbruch: {pfad} -- fehlt {sorted(fehlt)}, unbekannt "
            f"{sorted(zuviel)}. Erwartet werden genau {ANIMIERTE_MOODS}.")
    return {k: str(v) for k, v in daten.items()}


def atem_pruefen(text: str) -> str:
    """Ein Atemtext, egal ob Vorgabe oder von der Kommandozeile.

    Geprueft wird nur, was fuer JEDE Figur gilt: keine Geste. Was sich hebt --
    eine Brust, ein Gehaeuse, ein Stein -- steht nicht hier, denn zwei der
    acht Pets haben keine Brust. Dass sich ueberhaupt etwas bewegt und das
    Gesicht dabei ruhig bleibt, entscheidet ohnehin nicht der Text, sondern
    die Messung am fertigen Clip: dafuer gibt es `ruhiger_anfang` und
    `bewegt_sich`.
    """
    if not text.strip():
        raise SystemExit("Abbruch: leerer Atemtext")
    for verb in ATEM_VERBOTEN:
        if verb in text.lower():
            raise SystemExit(
                f"Abbruch: der Atemtext enthaelt {verb!r}. Er laeuft endlos; "
                f"eine Geste darin wiederholt sich ohne Ende.")
    return text
STEPS, CFG, SHIFT = 4, 1.0, 5.0

# Die Bewegung soll TRAGEN, nicht erzaehlen: das Pet sitzt in einer
# 208-Pixel-Zelle am Bildschirmrand. Was dort ankommt, ist Atmen, ein Wiegen,
# ein Lidschlag. Der Hintergrund muss ausdruecklich flach bleiben, sonst
# faengt der Gruenschirm an zu wabern und die Freistellung franst.
#
# Der Ausdruck steht im STANDBILD und darf sich im Clip nicht aendern. Der
# erste Durchgang am 02.09. liess ihn mitlaufen -- das Ergebnis lachte
# ununterbrochen, weil `done` schon laechelnd anfaengt und der Clip das Laecheln
# weiter vertiefte. Was hier steht, ist darum Atmung und Lidschlag, sonst
# nichts.
# Der Aufbau folgt der einzigen offiziell dokumentierten I2V-Formel von
# Alibaba Cloud Model Studio (Stand 02.09.2026, `text-to-video-prompt`):
# fuer Bild-zu-Video gilt `运动 + 运镜` -- BEWEGUNG plus KAMERA, sonst nichts.
# Subjekt, Szene und Stil stehen ausdruecklich nicht im Prompt, weil das
# Startbild sie schon festlegt. Genau daran hielt sich der erste Entwurf
# nicht: "subtle idle animation" ist weder Bewegung noch Kamera, sondern eine
# Stimmung -- und das Modell fuellte sie mit Lebendigkeit, also mit Grinsen.
#
# Ein Ausdruck wird ueber seine BESTANDTEILE beschrieben, nicht ueber sein
# Gefuehlswort. Fuer Wan gibt es keine erprobte Emotionsliste (gesucht am
# 02.09. ueber den offiziellen Guide, das Modell-Repo und HuggingFace -- der
# Guide fuehrt genau zwei Gefuehlsworte, 高兴 und 惊讶, und keine Liste). Wer
# "smile" schreibt, bekommt, was das Modell darunter versteht, und das war
# hier ein breites Grinsen mit Zaehnen. Blickrichtung, Lidstand, Brauenenden,
# Mundwinkel und Kopfneigung sind dagegen eindeutig.
BEWEGUNG_GLEICH = ("Static camera, locked frame, no zoom, no pan, no camera "
                   "shake. The flat uniform green background stays perfectly "
                   "still and uniform. Soft light, unchanged throughout.")

# T-9.3: der Ruhepuls. EIN Text fuer alle Moods und alle Pets. Der AUSDRUCK
# steht im Standbild und wird hier nur gehalten -- dieser Text beschreibt
# ausschliesslich den Brustkorb und die Lider.
#
# Kein "idle animation", kein "gentle breathing" allein: beides las das Modell
# als Auftrag, etwas zu tun. Was hier steht, ist die Bewegung selbst.
ATMEN = ("The chest rises and falls slowly and evenly with quiet breathing. "
         "The shoulders lift a little on each breath in and settle again. "
         "The eyelids close once briefly and open again. The head stays "
         "where it is, the jaw stays closed, the lips stay together and "
         "unmoving, the corners of the mouth stay exactly where they are.")

# Je Mood eine eigene Bewegung. Sie spielt drei Umlaeufe (`EMOTE_UMLAEUFE` in
# `face/src/render.rs`) und faellt dann in den Atem zurueck.
#
# Die Wortwahl ist ausdruecklich zurueckhaltend, auf Ansage: ein Laecheln ist
# kein Grinsen, und `failed` ist Bedauern, nicht Wut. Darum steht bei `done`
# nirgends "smile" und bei `failed` nirgends "angry" oder "frown" -- es stehen
# die Muskeln, die den Unterschied machen. Ein gerunzelter Brauenbogen mit
# zusammengezogenen Brauen ist Wut; angehobene INNERE Brauenenden bei
# gesenktem Blick sind Bedauern. Das ist derselbe Mund und ein anderes
# Gesicht.
EMOTE = {
    "observing": (
        "The eyes open a little wider and the gaze moves slowly across the "
        "scene from one side to the other and back. The head turns by a very "
        "small amount to follow. The lips stay together."),
    "thinking": (
        "The gaze drifts upward and to one side and rests there. The head "
        "tilts slowly by a small amount. The eyelids narrow slightly. The "
        "lips stay together."),
    "working": (
        "The gaze stays fixed straight ahead. The eyelids narrow slightly in "
        "concentration. The head dips once by a small amount and returns. "
        "The lips stay together and press slightly."),
    "done": (
        "The corners of the mouth lift a little and hold, the lips staying "
        "together, no teeth showing. The lower eyelids rise slightly so the "
        "eyes narrow a little. The head dips once by a small amount and "
        "returns. The movement is small and restrained."),
    "failed": (
        "The gaze lowers and the upper eyelids drop halfway. The inner ends "
        "of the eyebrows lift while the outer ends stay where they are. The "
        "corners of the mouth turn down a little, the lips staying together. "
        "The head tilts down by a small amount and stays there. One slow "
        "breath out. The brows are not drawn together."),
    "needs_input": (
        "One eyebrow lifts higher than the other and holds. The gaze stays "
        "on the viewer. The head tilts to one side by a small amount. The "
        "lips part very slightly and stay."),
}
# Der Negativ-Prompt. Er wirkt hier NICHT, und das ist belegt, nicht
# vermutet: `comfy/samplers.py:370` der installierten Fassung (77e2ed5e,
# 15.05.2026) laesst den Uncond-Zweig bei CFG 1.0 gar nicht erst rechnen --
#
#     if math.isclose(cond_scale, 1.0) and not disable_cfg1_optimization:
#         uncond_ = None
#
# Am 02.09. standen hier englische Bewegungsverbote ("no gesturing, no
# smiling"). Sie waren vom ersten Tag an wirkungslos, und weil sie so
# ueberzeugend dastanden, suchte niemand den Fehler im positiven Text -- wo er
# lag. Sie sind darum weg: eine Regel, die nicht gilt, ist schlimmer als
# keine.
#
# Was bleibt, ist der offizielle Wan-Negativstring (Wan-Video/Wan2.2,
# `wan/configs/shared_config.py`) -- OHNE `静态`, `静止` und `静止不动的画面`.
# Die drei heissen "statisch", "stillstehend" und "unbewegtes Bild": der
# ausgelieferte String bestraft Stillstand. Solange CFG auf 1,0 steht, ist das
# folgenlos; wer CFG je anhebt, bekaeme sonst genau das Gegenteil von ruhigem
# Atmen, und zwar aus der Vorlage heraus.
NEGATIV = ("色调艳丽，过曝，细节模糊不清，字幕，风格，作品，画作，画面，"
           "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
           "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
           "杂乱的背景，三条腿，背景人很多，倒着走")

# Moods mit einer GESTE. `idle` fehlt hier mit Absicht: es ist der Zustand,
# in den jede Geste zurueckfaellt, und eine Geste dort waere eine Schleife
# ohne Ende. `sleeping` fehlt, weil das Pet dabei unsichtbar ist.
ANIMIERTE_MOODS = ["observing", "thinking", "working", "done", "failed",
                   "needs_input"]

# Es gibt GENAU EINE Atemzeile, und sie gehoert `idle`.
#
# Bis zum 02.09. bekam jeder Mood eine eigene: das Pet sollte auch als
# `working` oder `failed` weiteratmen, mit dem Gesicht dieses Moods. Das ist
# nicht, was gewuenscht war -- verlangt war "eine Figur, die permanent idelt,
# und nur wenn etwas schiefgeht, frowned sie". Und es ging auch technisch
# nicht auf: die Atemzeile wird aus dem Standbild des Moods erzeugt, und
# ausdrucksstarke Standbilder sind instabil. Gemessen am 02.09., je drei
# Seeds: das neutrale `idle` hielt 13 von 13 Bildern ruhig, `done` (offener
# Mund) 1 bis 3, `observing` (weit offene Augen) 1 bis 5.
#
# Jetzt entsteht ALLES aus dem einen neutralen `idle`-Standbild: die
# Atemzeile und jede Geste. Die Geste faengt damit neutral an, fuehrt ihren
# Ausdruck vor und kommt ueber den Ping-Pong-Rueckweg dorthin zurueck, wo die
# Atemzeile steht. Das ist nicht nur weniger Rechnerei -- es ist der Grund,
# warum der Uebergang ueberhaupt nahtlos sein kann.
ATEM_MOODS = ["idle"]
# Aus welchem Standbild jeder Clip entsteht. Eins fuer alles.
QUELL_MOOD = "idle"


def pingpong(anzahl: int) -> list:
    """`[0,1,2]` -> `[0,1,2,1]`. Die Enden kommen genau einmal vor.

    Ohne diese Regel liefe der letzte Frame in den ersten, und genau dort
    ruckt es. Mit ihr ist die Folge an beiden Enden stetig.
    """
    if anzahl <= 1:
        return [0]
    return list(range(anzahl)) + list(range(anzahl - 2, 0, -1))


def graph_bauen(bild_name: str, bewegung: str, seed: int, prefix: str,
                laenge: int = LAENGE_EMOTE) -> dict:
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
              "inputs": {"clip": ["7", 0],
                         "text": f"{BEWEGUNG_GLEICH} {bewegung}"}},
        "9": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["7", 0], "text": NEGATIV}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": I2V["vae"]}},
        "11": {"class_type": "LoadImage", "inputs": {"image": bild_name}},
        "12": {"class_type": "WanImageToVideo",
               "inputs": {"positive": ["8", 0], "negative": ["9", 0],
                          "vae": ["10", 0], "start_image": ["11", 0],
                          "width": BREITE, "height": HOEHE,
                          "length": laenge, "batch_size": 1}},
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


def _regionen(hoehe: int, breite: int) -> tuple:
    """Gesichts- und Brustfenster einer Zelle, als Anteile.

    Alle acht Pets sind mittig stehende Halb- oder Ganzfiguren; Gesicht oben
    mittig, Rumpf darunter. Das gilt auch fuer die zwei, die kein Gesicht
    haben: Nordoms Bildschirm und die Wackelaugen des Kiesels sitzen an
    derselben Stelle.
    """
    gesicht = (slice(int(hoehe * .10), int(hoehe * .42)),
               slice(int(breite * .28), int(breite * .72)))
    brust = (slice(int(hoehe * .55), int(hoehe * .95)),
             slice(int(breite * .20), int(breite * .80)))
    return gesicht, brust


def _rest(a, b, raum: int = SUCHRAUM) -> float:
    """Kleinster Unterschied zweier Ausschnitte ueber alle Verschiebungen.

    Die Verschiebung selbst interessiert nicht -- nur, was sie NICHT erklaert.
    Ein Kopf, der zwei Pixel tiefer steht, ist derselbe Kopf; ein Mund, der
    aufgeht, ist es nicht.
    """
    import numpy as np

    h, w = a.shape[:2]
    bestes = None
    for dz in range(-raum, raum + 1):
        for ds in range(-raum, raum + 1):
            aa = a[max(0, dz):h + min(0, dz), max(0, ds):w + min(0, ds)]
            bb = b[max(0, -dz):h + min(0, -dz), max(0, -ds):w + min(0, -ds)]
            d = float(np.abs(aa - bb).mean())
            bestes = d if bestes is None else min(bestes, d)
    return bestes if bestes is not None else 0.0


def rang_schluessel(ruhig: int, form: float, hub: float) -> tuple:
    """Wonach unter mehreren Seeds ausgewaehlt wird. Groesser ist besser.

    Jeder Schluessel steht fuer eine Beschwerde vom 02./03.09.:
      1. `ruhig` -- kurze Zeilen hecheln.
      2. WENIG `form` -- "die grinsen alle".
      3. viel `hub` -- ein Pet, das sich gar nicht regt, wirkt tot.

    Die Verformung steht VOR der Brust, und das ist eine Entscheidung, keine
    Reihenfolge aus Bequemlichkeit: lieber ein zurueckhaltender Atem als ein
    Gesicht, das sich verzieht. Andersherum gewaenne ein Seed, der sich
    deutlicher regt UND deutlicher verzieht.

    Eigene Funktion, damit der Selbsttest DIESE Rangfolge prueft und nicht
    eine nachgebaute -- ein Pruefstand, der die Regel selbst noch einmal
    hinschreibt, kann ihre Aenderung nicht sehen.
    """
    return (ruhig, -form, hub)


def verformung(zellen: list) -> float:
    """Groesste Gesichtsverformung der Zeile, gegen ihr erstes Bild."""
    import numpy as np

    if len(zellen) < 2:
        return 0.0
    felder = [np.asarray(z.convert("RGB"), float) for z in zellen]
    hoehe, breite, _ = felder[0].shape
    gesicht, _ = _regionen(hoehe, breite)
    bezug = felder[0][gesicht]
    return max(_rest(bezug, f[gesicht]) for f in felder[1:])


def ruhiger_anfang(zellen: list, grenze: float = GESICHT_GRENZE) -> int:
    """Wie viele Bilder vom Anfang das Gesicht noch unverformt zeigen.

    Gibt mindestens 1 zurueck -- das erste Bild ist per Definition ruhig, es
    ist der Bezug. Der Aufrufer entscheidet, ob das reicht.
    """
    import numpy as np

    if not zellen:
        return 0
    felder = [np.asarray(z.convert("RGB"), float) for z in zellen]
    hoehe, breite, _ = felder[0].shape
    gesicht, _ = _regionen(hoehe, breite)
    bezug = felder[0][gesicht]
    behalten = 1
    for feld in felder[1:]:
        if _rest(bezug, feld[gesicht]) > grenze:
            break
        behalten += 1
    return behalten


def bewegt_sich(zellen: list) -> float:
    """Groesste Abweichung im BRUSTfenster -- die Positivkontrolle."""
    import numpy as np

    felder = [np.asarray(z.convert("RGB"), float) for z in zellen]
    hoehe, breite, _ = felder[0].shape
    _, brust = _regionen(hoehe, breite)
    bezug = felder[0][brust]
    return max(np.abs(f[brust] - bezug).mean() for f in felder)


def zellen_bauen(frames: list, zelle: tuple, schritt: int) -> list:
    """Jeden `schritt`-ten Frame freistellen und auf Zellgroesse schneiden.

    Freigestellt wird bei voller Aufloesung, aus demselben Grund wie beim
    Standbild: die Kante wird glatter als eine bei 208 Pixeln.
    """
    from PIL import Image

    zellen = []
    for pfad in frames[::schritt]:
        with Image.open(pfad) as bild:
            frei = ds.chroma_freistellen(bild.convert("RGBA"))
            zellen.append(ds.zellen_zuschnitt(frei, zelle))
    return zellen


def zwischenbilder(zellen: list, wie_viele: int = ZWISCHENBILDER) -> list:
    """Zwischen je zwei Bildern `wie_viele` eingeblendete einschieben."""
    from PIL import Image

    if wie_viele < 1 or len(zellen) < 2:
        return zellen
    raus = []
    for a, b in zip(zellen, zellen[1:]):
        raus.append(a)
        for k in range(1, wie_viele + 1):
            raus.append(Image.blend(a, b, k / (wie_viele + 1)))
    raus.append(zellen[-1])
    return raus


def spalten_bauen(frames: list, zelle: tuple, schritt: int,
                  gesicht_halten: bool = False) -> list:
    """Aus den Videoframes die Spalten einer Sheet-Zeile.

    Erst jeden `schritt`-ten nehmen, dann freistellen und zuschneiden, dann
    Ping-Pong. Freigestellt wird bei voller Aufloesung, aus demselben Grund
    wie beim Standbild: die Kante wird glatter als eine bei 208 Pixeln.
    """
    from PIL import Image

    zellen = zellen_bauen(frames, zelle, schritt)

    if gesicht_halten:
        behalten = ruhiger_anfang(zellen)
        if behalten < SPALTEN_MINDEST:
            raise SystemExit(
                f"Abbruch: nur {behalten} von {len(zellen)} Bildern halten das "
                f"Gesicht ruhig (Grenze {GESICHT_GRENZE}). Das traegt keinen "
                f"Atemzug. Anderer Seed oder anderes Standbild.")
        if behalten > FRAMES_HOECHSTENS:
            behalten = FRAMES_HOECHSTENS
        zellen = zellen[:behalten]
        hub = bewegt_sich(zellen)
        if hub < BRUST_MINDEST:
            raise SystemExit(
                f"Abbruch: im ruhigen Teil bewegt sich die Brust nur um "
                f"{hub:.2f} (mindestens {BRUST_MINDEST}). Das ist ein "
                f"Standbild, kein Atem -- und beides sieht in der "
                f"Gesichtsdrift gleich aus.")
        zellen = zwischenbilder(zellen)
        print(f"      Gesicht ruhig bis Bild {behalten}/{len(frames) // schritt}"
              f", Brust {hub:.2f}, mit Zwischenbildern {len(zellen)}",
              flush=True)

    return [zellen[i] for i in pingpong(len(zellen))]


def manifest_aktualisieren(pfad: str, spalten: int, moods: list,
                           reihenfolge: list, laengen: dict) -> dict:
    """Traegt `cols`, `rows`, je Mood `frames` und die Emote-Zeile nach.

    `moods` sind die Moods mit GESTE. `frames` bekommt dagegen jede Zeile,
    die wirklich mehr als ein Bild hat -- auch `idle`, das seit dem 02.09.
    atmet, aber nie eine Geste zeigt. Wer beides an `moods` haengt, rechnet
    eine Atemzeile und verschweigt sie. Zeilen mit genau einem Bild bleiben
    ohne `frames`: eine Zusage, die nie gilt, ist keine.

    Die Emote-Zeilen liegen hinter allen Atemzeilen, in der Reihenfolge von
    `moods`.

    `laengen` gibt je Zeile die ECHTE Bildzahl. Sie ist nicht `spalten`: der
    Atem laeuft mit `SCHRITT_ATEM`, das Emote mit `SCHRITT_EMOTE`, und das
    Sheet ist nur deshalb rechteckig, weil `sheet_bauen` kuerzere Zeilen mit
    Kopien von Spalte 0 auffuellt. Stuende `spalten` in beiden, stuende das
    Emote die halbe Schleife lang auf seinem ersten Bild -- am 02.09. genau so
    erzeugt und im Manifest aufgefallen.
    """
    with open(pfad, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["atlas"]["cols"] = spalten
    manifest["atlas"]["rows"] = len(reihenfolge) + len(moods)

    # Die Atemzeilen zuerst, in der gegebenen Reihenfolge.
    for i, name in enumerate(reihenfolge):
        manifest["moods"][name]["row"] = i
    # Alle uebrigen Moods zeigen auf DIESELBE Atemzeile wie `idle`. Das Pet
    # atmet immer gleich; was den Mood ausmacht, ist die Geste davor.
    ruhe = reihenfolge.index("idle")
    ruhe_laenge = laengen.get("idle:atem", 1)
    for name, eintrag in manifest["moods"].items():
        if name not in reihenfolge:
            eintrag["row"] = ruhe
    # Die Emote-Zeilen dahinter, in der Reihenfolge von `moods`.
    for i, name in enumerate(moods):
        manifest["moods"][name]["emote"] = {
            "row": len(reihenfolge) + i,
            "frames": laengen[f"{name}:emote"],
        }
    for name, eintrag in manifest["moods"].items():
        # `frames` haengt an der Atemzeile, NICHT an `moods` -- das ist die
        # Liste der Moods mit Geste. Am 02.09. hingen beide zusammen, und
        # `idle` bekam darum eine gerechnete Atemzeile, die kein Manifest
        # erwaehnte: 32 Spalten im Sheet, `{"row": 1}` daneben, und das Face
        # zeichnete weiter ein Standbild.
        laenge = laengen.get(f"{name}:atem",
                             ruhe_laenge if name not in reihenfolge else 1)
        if laenge > 1:
            eintrag["frames"] = laenge
        else:
            eintrag.pop("frames", None)
        if name not in moods:
            eintrag.pop("emote", None)

    # `states` ist der Weg fuer Pets ohne `moods`-Block; die Zeilennummern
    # muessen mitwandern, sonst zeigt es nach dem Umbau irgendwohin.
    if isinstance(manifest.get("states"), dict):
        if "idle" in manifest["states"]:
            manifest["states"]["idle"] = dict(manifest["moods"]["idle"])
            manifest["states"]["idle"].pop("emote", None)
        if "waiting" in manifest["states"] and "needs_input" in manifest["moods"]:
            manifest["states"]["waiting"] = dict(manifest["moods"]["needs_input"])
            manifest["states"]["waiting"].pop("emote", None)
    return manifest


def sheet_bauen(zeilen: dict, reihenfolge: list, spalten: int, zelle: tuple,
                ziel: str) -> None:
    """`zeilen`: Schluessel -> Liste von Zellen, `reihenfolge` gibt die
    Zeilennummern. Wer weniger Zellen hat als `spalten`, bekommt seine erste
    wiederholt -- das Sheet muss rechteckig sein, und das Face liest je Zeile
    nur so viele Spalten, wie `frames` angibt."""
    from PIL import Image

    b, h = zelle
    sheet = Image.new("RGBA", (b * spalten, h * len(reihenfolge)), (0, 0, 0, 0))
    for zeile, schluessel in enumerate(reihenfolge):
        zellen = zeilen[schluessel]
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
    # Nur zwei Zeilen tragen eigene Bilder: `sleeping` (Standbild, unsichtbar)
    # und `idle` (der Atem). Alle anderen Moods zeigen auf `idle`.
    reihenfolge = ["sleeping", "idle"]

    if ds.port_belegt():
        raise SystemExit(f"Abbruch: auf Port {ds.PORT} lauscht schon jemand.")

    marke = f"petanim_{os.getpid()}"
    eingang = os.path.join(COMFY_DIR, "input")
    log = os.path.join(ziel_dir, "comfy-anim.log")
    kopien = []
    zeilen = {}
    # Was gebaut wird: die eine Atemzeile und die sechs Gesten. `--moods`
    # filtert beide; was nicht drankommt, wird aus dem alten Sheet uebernommen.
    alle = ATEM_MOODS + ANIMIERTE_MOODS
    zu_bauen = [m for m in alle if not args.moods or m in args.moods]
    if not zu_bauen:
        raise SystemExit(f"Abbruch: --moods trifft keinen von {alle}")

    gesten = gesten_laden(getattr(args, "gesten_datei", None))

    # EIN Standbild fuer alles: das neutrale `idle`.
    quelle = None
    for kandidat in sorted(os.listdir(args.quelle)):
        if f"_{QUELL_MOOD}_" in kandidat and kandidat.endswith(".png"):
            quelle = os.path.join(args.quelle, kandidat)
            break
    if quelle is None:
        raise SystemExit(
            f"Abbruch: kein {QUELL_MOOD}-Standbild in {args.quelle}")
    bild_name = f"{marke}_{QUELL_MOOD}.png"
    shutil.copy2(quelle, os.path.join(eingang, bild_name))
    kopien.append(os.path.join(eingang, bild_name))
    print(f"[1/3] ComfyUI startet (Port {ds.PORT}), Log: {log}\n"
          f"      Quelle fuer ALLE Zeilen: {os.path.basename(quelle)}",
          flush=True)
    server = ds.comfy_starten(log)
    try:
        for i, mood in enumerate(zu_bauen, 1):
            if mood in ANIMIERTE_MOODS:
                print(f"[2/3] {i}/{len(zu_bauen)} {mood} (Geste) …", flush=True)
                g = graph_bauen(bild_name, gesten[mood], args.seed,
                                f"{marke}/{mood}_emote", LAENGE_EMOTE)
                roh = frames_abholen(ds.absenden(g), log, deckel_s=900)
                zeilen[f"{mood}:emote"] = spalten_bauen(
                    roh, zelle, SCHRITT_EMOTE)
                continue

            # Die Atemzeile: mehrere Seeds, der ruhigste gewinnt.
            atemtext = atem_pruefen(getattr(args, "atem_text", None) or ATMEN)
            print(f"[2/3] {i}/{len(zu_bauen)} {mood} (Atem) …", flush=True)
            bester, bester_seed, bester_hub = None, None, 0.0
            bester_form = 0.0
            bester_rang = rang_schluessel(0, 1e9, 0.0)
            versuche = getattr(args, 'atem_seeds', None) or ATEM_SEEDS
            for n in range(versuche):
                seed = args.seed + n
                g = graph_bauen(bild_name, atemtext, seed,
                                f"{marke}/{mood}_atem{n}", LAENGE_ATEM)
                roh = frames_abholen(ds.absenden(g), log, deckel_s=900)
                zellen = zellen_bauen(roh, zelle, SCHRITT_ATEM)
                ruhig = min(ruhiger_anfang(zellen), FRAMES_HOECHSTENS)
                # Ueber MINDESTENS zwei Bilder messen: das Maximum ueber ein
                # einziges Bild ist immer 0,00 und sagt nichts. Am 03.09.
                # meldeten sechs Seeds "Brust 0.00", und das war keine
                # Messung, sondern die Folge von `ruhig == 1`.
                behalt = zellen[:max(ruhig, 2)]
                hub = bewegt_sich(behalt)
                form = verformung(behalt)
                gut = ruhig >= SPALTEN_MINDEST and hub >= BRUST_MINDEST
                print(f"      Seed {seed}: ruhig bis {ruhig}/{len(zellen)}, "
                      f"Verformung {form:5.2f}, Brust {hub:.2f}"
                      f"{'' if gut else '  (verworfen)'}", flush=True)
                # Die Rangfolge, und jeder Schluessel steht fuer eine
                # Beschwerde vom 02./03.09.:
                #   1. Laenge   -- kurze Zeilen hecheln.
                #   2. WENIG Verformung -- "die grinsen alle".
                #   3. viel Brust  -- ein Pet, das sich gar nicht regt, wirkt
                #      tot; ohne diesen Schluessel gewann der erste Seed mit
                #      der Hoechstlaenge, und `anzug` bekam Brust 1,29,
                #      obwohl ein spaeterer auf 7,04 kam.
                # Die Verformung steht VOR der Brust: lieber ein ruhigeres
                # Atmen als ein Gesicht, das sich verzieht.
                rang = rang_schluessel(ruhig, form, hub)
                if gut and rang > bester_rang:
                    bester_rang = rang
                    bester, bester_seed, bester_hub = ruhig, seed, hub
                    bester_form = form
            if bester is None:
                raise SystemExit(
                    f"Abbruch: keiner von {versuche} Seeds haelt das "
                    f"Gesicht ruhig genug (Grenze {GESICHT_GRENZE}) UND "
                    f"bewegt die Brust genug (mindestens {BRUST_MINDEST}). "
                    f"Das {QUELL_MOOD}-Standbild ist zu ausdrucksstark.")
            g = graph_bauen(bild_name, atemtext, bester_seed,
                            f"{marke}/{mood}_atem", LAENGE_ATEM)
            roh = frames_abholen(ds.absenden(g), log, deckel_s=900)
            zellen = zwischenbilder(
                zellen_bauen(roh, zelle, SCHRITT_ATEM)[:bester])
            zeilen[f"{mood}:atem"] = [zellen[k] for k in pingpong(len(zellen))]
            print(f"      genommen: Seed {bester_seed}, {bester} Bilder, "
                  f"Verformung {bester_form:.2f}, Brust {bester_hub:.2f}, "
                  f"{len(zeilen[f'{mood}:atem'])} Spalten", flush=True)
    finally:
        ds.comfy_beenden(server)
        for rest in kopien:
            if os.path.exists(rest):
                os.remove(rest)

    altes = Image.open(os.path.join(ziel_dir, "spritesheet.png")).convert("RGBA")
    alte_spalten = manifest["atlas"]["cols"]

    def uebernehmen(eintrag: dict, schluessel: str) -> None:
        """Eine Zeile aus dem alten Sheet retten, in ihrer ECHTEN Laenge.

        Ohne `frames` ist sie ein Standbild und hat genau eine Zelle. Mit
        `frames` waren es mehr -- sie hier auf eine zu kuerzen hiesse, dass
        ein Lauf fuer EINEN Mood die Animation aller anderen platt macht.
        Genau das war der Grund fuer `--moods`.
        """
        laenge = min(eintrag.get("frames", 1), alte_spalten)
        r = eintrag["row"]
        zeilen[schluessel] = [
            altes.crop((i * zelle[0], r * zelle[1],
                        (i + 1) * zelle[0], (r + 1) * zelle[1]))
            for i in range(max(1, laenge))]

    for mood in reihenfolge:
        if f"{mood}:atem" not in zeilen:
            uebernehmen(manifest["moods"][mood], f"{mood}:atem")
    for mood in ANIMIERTE_MOODS:
        eintrag = manifest["moods"][mood]
        if f"{mood}:emote" not in zeilen and isinstance(eintrag.get("emote"), dict):
            uebernehmen(eintrag["emote"], f"{mood}:emote")
    altes.close()
    spalten = max(len(z) for z in zeilen.values())

    # Die Atemzeilen behalten ihre alten Zeilennummern -- ein bestehendes
    # Manifest soll nicht durcheinandergeraten. Die Emote-Zeilen kommen
    # dahinter, in der Reihenfolge der Moods.
    schluessel = [f"{m}:atem" for m in reihenfolge]
    schluessel += [f"{m}:emote" for m in ANIMIERTE_MOODS
                   if f"{m}:emote" in zeilen]

    print(f"[3/3] Sheet mit {spalten} Spalten, {len(schluessel)} Zeilen …",
          flush=True)
    sheet_bauen(zeilen, schluessel, spalten, zelle,
                os.path.join(ziel_dir, "spritesheet.png"))
    mit_emote = [m for m in ANIMIERTE_MOODS if f"{m}:emote" in zeilen]
    neu = manifest_aktualisieren(manifest_pfad, spalten, mit_emote,
                                 reihenfolge,
                                 {k: len(v) for k, v in zeilen.items()})
    with open(manifest_pfad, "w", encoding="utf-8") as f:
        json.dump(neu, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Fertig: {args.pet} -- {spalten} Spalten, "
          f"{len(mit_emote)} Moods mit Geste, {len(zu_bauen)} neu gerechnet")


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
    g = graph_bauen("x.png", EMOTE["working"], 42, "p")
    for kid, k in g.items():
        for wert in k["inputs"].values():
            if isinstance(wert, list):
                assert wert[0] in g, f"{kid} zeigt auf {wert[0]}"
    assert g["12"]["inputs"]["length"] == LAENGE_EMOTE, g["12"]["inputs"]["length"]
    assert graph_bauen("x.png", "b", 1, "p", LAENGE_ATEM)["12"]["inputs"]["length"] \
        == LAENGE_ATEM, "die Atem-Laenge kommt nicht durch"
    assert list(k["class_type"] for k in g.values()).count("KSamplerAdvanced") == 2

    # Manifest: animierte Moods bekommen `frames`, ruhige nicht.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "pet.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"atlas": {"cellW": 208, "cellH": 208, "cols": 1, "rows": 8},
                       "moods": {m: {"row": i} for i, m in enumerate(
                           ["sleeping", "idle"] + ANIMIERTE_MOODS)}}, f)
        reihenfolge = ["sleeping", "idle"] + ANIMIERTE_MOODS
        # Der Atem hat 32 Spalten, das Emote nur 16 -- verschiedene Schritte.
        laengen = {f"{m}:atem": 32 for m in ANIMIERTE_MOODS}
        laengen.update({f"{m}:emote": 16 for m in ANIMIERTE_MOODS})
        laengen.update({f"{m}:atem": 1 for m in ("sleeping", "idle")})
        neu = manifest_aktualisieren(p, 32, ANIMIERTE_MOODS, reihenfolge, laengen)
    assert neu["atlas"]["cols"] == 32
    # Acht Atemzeilen plus sechs Emote-Zeilen.
    assert neu["atlas"]["rows"] == 14, neu["atlas"]
    for m in ANIMIERTE_MOODS:
        assert neu["moods"][m]["frames"] == 32, m
        # Die ECHTE Laenge, nicht die Sheet-Breite. Stuende hier 32, stuende
        # das Emote die halbe Schleife auf seinem ersten Bild.
        assert neu["moods"][m]["emote"]["frames"] == 16, neu["moods"][m]
        # Die Emote-Zeile liegt HINTER allen Atemzeilen -- sonst ueberschriebe
        # sie einen Mood.
        assert neu["moods"][m]["emote"]["row"] >= len(reihenfolge), m
    emote_zeilen = [neu["moods"][m]["emote"]["row"] for m in ANIMIERTE_MOODS]
    assert len(set(emote_zeilen)) == len(emote_zeilen), \
        f"zwei Moods teilen sich eine Emote-Zeile: {emote_zeilen}"
    for ruhig in ("idle", "sleeping"):
        assert "frames" not in neu["moods"][ruhig], ruhig
        assert "emote" not in neu["moods"][ruhig], ruhig
    # Die Zusage "subtil": am 02.09. lachte das erste bewegte Pet
    # ununterbrochen, weil der Bewegungstext den Ausdruck mitlaufen liess.
    # Ohne diese Pruefung waere ein "nods slowly" beim naechsten Anfassen
    # wieder drin, ohne dass es jemandem auffaellt.
    # Zwei Sorten: Gesten, und Verben, die den AUSDRUCK veraendern. Die zweite
    # Sorte ist die tueckischere -- "the smile deepens" liest sich subtil und
    # war genau der Text, der ununterbrochen lachte.
    # Der Atem ist EINE Bewegung fuer alle Moods und darf keine Geste sein --
    # er laeuft endlos, und genau das hat am 02.09. ununterbrochen gelacht.
    gesten = ("nod", "shake", "turn", "wave", "gestur", "walk", "smile",
              "laugh", "grin", "deepen", "brighten", "widen", "becomes",
              "grows", "raise", "tilt")
    assert atem_pruefen(ATMEN) == ATMEN, "die Vorgabe muss selbst durchgehen"
    for geste in gesten:
        assert geste not in ATMEN.lower(), f"der Atem hat eine Geste: {geste!r}"
    assert gesten_laden(None) is EMOTE
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({m: "x" for m in ANIMIERTE_MOODS}, f)
        gp = f.name
    assert set(gesten_laden(gp)) == set(ANIMIERTE_MOODS)
    for kaputt, warum in (({m: "x" for m in ANIMIERTE_MOODS[:-1]}, "einer fehlt"),
                          ({**{m: "x" for m in ANIMIERTE_MOODS}, "idle": "x"},
                           "idle hat keine Geste"),
                          ({"quatsch": "x"}, "unbekannter Mood")):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(kaputt, f)
            kp = f.name
        try:
            gesten_laden(kp)
            raise AssertionError(f"durchgelassen: {warum}")
        except SystemExit:
            pass
        os.unlink(kp)
    os.unlink(gp)

    # Und die Kommandozeile geht durch dieselbe Pruefung. Ein eigener Text,
    # der nicht geprueft wird, ist die Luecke, durch die die Geste
    # zurueckkommt.
    for schlecht in ("the person nods once", "she smiles gently", "   "):
        try:
            atem_pruefen(schlecht)
            raise AssertionError(f"durchgelassen: {schlecht!r}")
        except SystemExit:
            pass
    # Ein Text ohne Brust muss erlaubt sein -- zwei der acht Pets haben keine.
    assert atem_pruefen("The pebble rocks very slightly from side to side.")
    assert "breath" in ATMEN.lower(), "der Atem nennt kein Atmen"
    # Der Atem muss den MUND festhalten. Ohne das driftete er am 02.09. ins
    # Grinsen -- beim Kiesel wuchs sogar ein Mund, den der Stein nie hatte.
    # "keine Geste" allein findet das nicht: das Grinsen kam nicht von einem
    # Gestenverb, sondern davon, dass ueber den Mund nichts dastand.
    for halt in ("lips", "jaw"):
        assert halt in ATMEN.lower(), f"der Atem haelt {halt!r} nicht fest"
    assert "chest" in ATMEN.lower(), "der Atem nennt die Brust nicht"

    # Der Schnitt selbst. Gebaut aus Bildern, deren Gesicht ab einem bekannten
    # Punkt kippt -- so ist pruefbar, dass GENAU dort geschnitten wird und
    # nicht irgendwo.
    from PIL import Image as _Bild
    from PIL import ImageDraw as _BildZeichnen
    def _zelle(gesicht: int, brust: int, versatz: int = 0,
               quer: int = 0):
        """Eine Zelle mit Alpha und STREIFEN.

        Die Streifen sind kein Schmuck. Ein einfarbiger Block sieht
        verschoben genauso aus wie unverschoben -- mit ihm laesst sich nicht
        pruefen, ob die Ausrichtung ueberhaupt etwas tut. Am 03.09. war er
        genau darum drei Mutanten gegenueber blind.
        """
        b = _Bild.new("RGBA", (100, 200), (0, 0, 0, 0))
        z = _Bild.new("RGBA", (100, 200), (0, 0, 0, 0))
        m = _BildZeichnen.Draw(z)
        for y in range(20, 84, 4):
            hell = gesicht if (y // 4) % 2 else max(gesicht - 60, 0)
            m.rectangle([28, y, 71, y + 3], fill=(hell, hell, hell, 255))
        # Senkrechte Balken dazu: ohne sie sieht ein waagerechter Versatz
        # aus wie gar keiner, und die Suche in dieser Richtung waere
        # ungeprueft. Am 03.09. war sie genau darum blind.
        for x in range(28, 72, 6):
            m.rectangle([x, 20, x + 2, 83], fill=(0, 0, 0, 255))
        for y in range(110, 190, 4):
            hell = brust if (y // 4) % 2 else max(brust - 60, 0)
            m.rectangle([20, y, 79, y + 3], fill=(hell, hell, hell, 255))
        b.paste(z, (quer, versatz), z)
        return b

    # Fall 1 -- ein AUSDRUCK: das Gesicht veraendert sich an Ort und Stelle.
    # Keine Verschiebung erklaert das, also bleibt es als Rest stehen.
    ruhig = [_zelle(100, 100 + i) for i in range(6)]
    ausdruck = [_zelle(200, 100 + 5 + j) for j in range(4)]
    assert ruhiger_anfang(ruhig + ausdruck) == 6, ruhiger_anfang(ruhig + ausdruck)

    # Fall 2 -- die FIGUR WANDERT, das Gesicht bleibt. Die Suche ueber kleine
    # Verschiebungen erklaert das vollstaendig, es bleibt nichts uebrig.
    # Ohne diesen Fall waere jedes wippende Pet abgelehnt worden.
    wandert = [_zelle(100, 100, versatz=v) for v in range(0, 12, 2)]
    assert ruhiger_anfang(wandert) == len(wandert), ruhiger_anfang(wandert)
    assert verformung(wandert) < 1.0, verformung(wandert)

    # Fall 2b -- die Figur wandert ZUR SEITE. Eigener Fall, weil die Suche
    # zwei Richtungen hat und ein Fehler in einer davon sonst nicht auffaellt.
    quer = [_zelle(100, 100, quer=v) for v in range(0, 10, 2)]
    assert ruhiger_anfang(quer) == len(quer), ruhiger_anfang(quer)
    assert verformung(quer) < GESICHT_GRENZE, verformung(quer)

    # Die Rangfolge der Seed-Wahl: Laenge, dann WENIG Verformung, dann viel
    # Brust. Steht die Brust vor der Verformung, gewinnt ein Seed, der sich
    # deutlicher regt UND deutlicher verzieht -- und "die grinsen alle" war
    # die Beschwerde, nicht "die bewegen sich zu wenig".
    kand = [(13, 16.0, 15.0, "verzieht sich, atmet stark"),
            (13, 8.0, 3.0, "ruhig, atmet schwach"),
            (13, 8.0, 5.0, "ruhig, atmet mehr"),
            (8, 2.0, 20.0, "kurz")]
    gewinner = max(kand, key=lambda k: rang_schluessel(k[0], k[1], k[2]))
    assert gewinner[3] == "ruhig, atmet mehr", gewinner
    # Und die Gegenprobe: mit vertauschten Schluesseln gewaenne der andere.
    nur_brust = max(kand, key=lambda k: (k[0], k[2], -k[1]))
    assert nur_brust[3] == "verzieht sich, atmet stark", nur_brust
    # Die Startmarke muss von JEDEM echten Kandidaten geschlagen werden.
    leer = rang_schluessel(0, 1e9, 0.0)
    for k in kand:
        assert rang_schluessel(k[0], k[1], k[2]) > leer, k

    # Fall 3 -- beides zusammen, und das ist der Fall vom 03.09.: die Figur
    # wippt UND der Mund geht auf. Ein roher Vergleich sieht eine grosse Zahl
    # und kann nicht sagen, woher sie kommt; `doppelself` kam damit zweimal
    # durch. Nach der Verschiebungssuche bleibt nur der Mund uebrig.
    getarnt = ([_zelle(100, 100, versatz=v) for v in (0, 2, 4)]
               + [_zelle(190, 100, versatz=v) for v in (6, 8, 10)])
    assert ruhiger_anfang(getarnt) == 3, ruhiger_anfang(getarnt)
    assert verformung(getarnt) > GESICHT_GRENZE, verformung(getarnt)

    # Bleibt alles ruhig, bleibt alles.
    assert ruhiger_anfang(ruhig) == 6, ruhiger_anfang(ruhig)
    # Kippt das Gesicht sofort, bleibt nur das Bezugsbild.
    assert ruhiger_anfang([_zelle(100, 100), _zelle(250, 100)]) == 1
    # Eine Verformung UNTER der Grenze wird durchgelassen -- sonst waere die
    # Grenze eine Attrappe und jedes Pet fiele durch.
    klein = [_zelle(100, 100), _zelle(100 + int(GESICHT_GRENZE) - 4, 100)]
    assert ruhiger_anfang(klein) == 2, ruhiger_anfang(klein)

    # Die Positivkontrolle: bewegt sich die Brust ueberhaupt? Ein Standbild
    # und ein ruhiges Gesicht sind in der Gesichtsdrift NICHT unterscheidbar.
    assert bewegt_sich(ruhig) > BRUST_MINDEST, bewegt_sich(ruhig)
    # Ueber ein einziges Bild ist das Maximum immer null -- eine Zahl, die
    # nichts misst. Der Aufrufer muss darum mindestens zwei uebergeben.
    assert bewegt_sich(ruhig[:1]) == 0.0, "ein Bild kann sich nicht bewegen"
    still = [_zelle(100, 100) for _ in range(6)]
    assert ruhiger_anfang(still) == 6, "ein Standbild ist per Gesicht ruhig"
    assert bewegt_sich(still) == 0.0, "und genau daran faellt es auf"

    # Zwischenbilder: die Zahl stimmt, und sie liegen wirklich DAZWISCHEN.
    # Ein Mutant, der einfach das linke Bild verdoppelt, haette dieselbe
    # Spaltenzahl -- und der Atem stockte an jedem zweiten Bild.
    import numpy as np
    a, b = _zelle(100, 100), _zelle(100, 200)
    drei = zwischenbilder([a, b], 1)
    assert len(drei) == 3, len(drei)
    werte = [np.asarray(z.convert("RGB"), float)[150, 50, 0] for z in drei]
    assert werte[0] == 100 and werte[2] == 200, werte
    assert 140 < werte[1] < 160, f"das mittlere Bild liegt nicht dazwischen: {werte}"
    # Der Schritt halbiert sich -- das ist der ganze Zweck.
    assert abs(werte[1] - werte[0]) < abs(werte[2] - werte[0])
    assert len(zwischenbilder([a, b, a], 1)) == 5
    assert zwischenbilder([a], 1) == [a], "ein einzelnes Bild bleibt eins"
    assert zwischenbilder([a, b], 0) == [a, b], "abgeschaltet aendert nichts"

    # Die Seed-Wahl: erst Laenge, dann Brust. Ein Mutant, der nur die Laenge
    # vergleicht, nimmt bei Gleichstand den ersten -- und das war am 03.09.
    # der mit einem Fuenftel der Atembewegung.
    kandidaten = [(13, 1.29, 44), (2, 5.0, 45), (13, 7.04, 46), (8, 9.9, 47)]
    bester, bester_hub, bester_seed = 0, 0.0, None
    for ruhig, hub, seed in kandidaten:
        if (ruhig, hub) > (bester, bester_hub):
            bester, bester_hub, bester_seed = ruhig, hub, seed
    assert bester_seed == 46, (bester_seed, bester, bester_hub)
    assert (bester, bester_hub) == (13, 7.04)

    # Die NAHT zum Face: wie lange ein Atemzug wirklich dauert, entscheiden
    # zwei Dateien zusammen -- die Spaltenzahl hier und `ATEM_FPS` drueben in
    # `face/src/render.rs`. Keine der beiden Seiten kann das allein zusagen,
    # und genau daran ging es am 02.09. schief: die Zeile war fuer zwoelf
    # Bilder gebaut, das Face spielte sie mit drei, und heraus kam ein
    # Atemzug von 10,7 s, der als Ruckeln ankam. Ein Test je Seite haette das
    # NIE gefunden -- beide waren fuer sich stimmig.
    assert SCHRITT_ATEM == 1, (
        "Der Atem nimmt jeden Frame. Jeder zweite ist der doppelte Sprung je "
        "Schritt, und das sah man.")
    assert SPALTEN_MINDEST < FRAMES_HOECHSTENS <= LAENGE_ATEM, (
        "Der Atem wird aus dem Clip GESCHNITTEN, nicht kurz erzeugt: es muss "
        "etwas auszuwaehlen geben, und der Deckel muss innerhalb des Clips "
        "liegen.")
    # Vorwaerts alle, zurueck ohne die beiden Enden -- sonst staende das
    # erste und das letzte Bild doppelt und der Atem stockte an der Naht.
    # Gerechnet wird mit dem DECKEL: das ist der laengste Atemzug, den das
    # Werkzeug bauen kann, und nur der kann die Obergrenze reissen.
    hoechstens = FRAMES_HOECHSTENS + (FRAMES_HOECHSTENS - 1) * ZWISCHENBILDER
    spalten_atem = 2 * hoechstens - 2
    hier = os.path.dirname(os.path.abspath(__file__))
    render = os.path.join(hier, "..", "face", "src", "render.rs")
    if os.path.isfile(render):
        with open(render, encoding="utf-8") as f:
            quelle = f.read()
        treffer = re.search(r"pub const ATEM_FPS: u32 = (\d+);", quelle)
        assert treffer, "ATEM_FPS steht nicht mehr in render.rs"
        atem_fps = int(treffer.group(1))
        dauer = spalten_atem / atem_fps
        assert dauer <= 5.0, (
            f"Der laengstmoegliche Atemzug dauert {dauer:.2f} s "
            f"({spalten_atem} Spalten bei {atem_fps} fps). Ueber 5 s wird aus "
            f"Atmen ein Wogen -- am 02.09. waren es 10,7 s, und genau das kam "
            f"als Ruckeln an.")
        mindestens = SPALTEN_MINDEST + (SPALTEN_MINDEST - 1) * ZWISCHENBILDER
        kuerzest = (2 * mindestens - 2) / atem_fps
        assert kuerzest >= 1.0, (
            f"Der kuerzestmoegliche Atemzug dauert {kuerzest:.2f} s. Darunter "
            f"hechelt es, und `SPALTEN_MINDEST` ist die einzige Bremse.")
        print(f"  Naht: hoechstens {spalten_atem} Spalten bei {atem_fps} fps "
              f"= {dauer:.2f} s, mindestens {kuerzest:.2f} s je Atemzug")
    else:
        # Positivkontrolle: schweigt der Test, weil alles stimmt, oder weil er
        # die Datei nicht gefunden hat? Das muss unterscheidbar sein.
        print("  Naht UNGEPRUEFT: render.rs nicht gefunden")

    # Die Kamera steht im gemeinsamen Teil, die Bewegung im eigenen -- so
    # verlangt es die offizielle I2V-Formel (Bewegung + Kamera).
    for kamera in ("static camera", "no zoom", "no pan"):
        assert kamera in BEWEGUNG_GLEICH.lower(), f"Kamera: {kamera!r} fehlt"

    # Auf Ansage vom 02.09.: ein Laecheln ist kein Grinsen, und `failed` ist
    # Bedauern, nicht Wut. Beides wird ueber die Muskeln beschrieben, nicht
    # ueber das Gefuehlswort -- und genau das wird hier geprueft, sonst
    # schreibt der naechste Durchgang wieder "the person smiles".
    assert "grin" not in EMOTE["done"].lower(), "done grinst"
    assert "no teeth" in EMOTE["done"].lower(), "done zeigt vielleicht Zaehne"
    assert "restrained" in EMOTE["done"].lower(), "done ist nicht zurueckhaltend"
    for wut in ("angry", "anger", "furrow", "scowl", "glare"):
        assert wut not in EMOTE["failed"].lower(), f"failed ist Wut: {wut!r}"
    assert "inner ends of the eyebrows lift" in EMOTE["failed"].lower(), \
        "failed nennt nicht die inneren Brauenenden -- das ist der Unterschied " \
        "zwischen Bedauern und Wut"
    assert "not drawn together" in EMOTE["failed"].lower(), \
        "failed schliesst die zusammengezogenen Brauen nicht aus"
    # Jedes Emote beschreibt Bestandteile, kein Gefuehlswort.
    for mood, text in EMOTE.items():
        assert any(t in text.lower() for t in ("gaze", "eyelid", "eyebrow")), \
            f"{mood} nennt weder Blick noch Lid noch Braue"

    # Das Emote ist endlich und DARF eine Geste sein. Es muss aber je Mood
    # eine eigene sein, sonst traegt es den Mood nicht.
    for mood in ANIMIERTE_MOODS:
        assert mood in EMOTE, f"{mood} hat kein Emote"
    assert len(set(EMOTE.values())) == len(EMOTE), \
        "zwei Moods teilen sich ein Emote -- dann traegt es den Mood nicht"
    assert not (set(EMOTE) & {"idle", "sleeping"}), \
        "ein Ruhe-Mood mit Emote -- RUHIGE_MOODS wuerde es nie zeigen"
    # Der Ausdruck gehoert dem Standbild. Steht das nicht im POSITIVEN Text,
    # vertieft der Clip das Laecheln von `done` bei jedem Umlauf weiter -- und
    # im Negativtext stuende es wirkungslos, solange CFG 1 gilt.
    assert "static camera" in BEWEGUNG_GLEICH.lower()
    assert CFG == 1.0, "steht CFG ueber 1, wird der Negativtext wieder wirksam"
    assert g["8"]["inputs"]["text"].startswith(BEWEGUNG_GLEICH), \
        "der Subtilitaets-Text muss vorne stehen, nicht hinten"
    assert EMOTE["working"] in g["8"]["inputs"]["text"]
    assert SCHRITT_ATEM < SCHRITT_EMOTE, "der Atem muss langsamer laufen"
    assert NEGATIV == g["9"]["inputs"]["text"], "Negativtext haengt am falschen Knoten"

    # `idle` atmet, hat aber keine Geste. Beide Haelften zaehlen: ohne die
    # erste bleibt der Ruhezustand ein Standbild, ohne die zweite fuehrt er
    # nach jeder Rueckkehr noch einmal etwas auf.
    assert ATEM_MOODS == ["idle"], (
        "Es gibt genau EINE Atemzeile. Verlangt war eine Figur, die permanent "
        "idelt und nur bei einem Ereignis kurz etwas zeigt.")
    assert "idle" not in ANIMIERTE_MOODS, ANIMIERTE_MOODS
    assert "sleeping" not in ATEM_MOODS, "unsichtbar braucht keinen Takt"
    assert QUELL_MOOD == "idle", (
        "Jede Zeile entsteht aus dem NEUTRALEN Standbild -- auch die Gesten. "
        "Nur so faengt eine Geste dort an, wo der Atem steht, und kommt "
        "dorthin zurueck.")
    for mood in ANIMIERTE_MOODS:
        assert mood in EMOTE, f"{mood} ohne Gestentext"

    # Und die Folge davon im Manifest: alle Moods teilen die Atemzeile von
    # `idle`, jeder animierte hat seine eigene Geste. Ohne diese Pruefung
    # koennte `manifest_aktualisieren` sechs Moods auf Zeile 0 legen --
    # `sleeping`, also unsichtbar -- und nichts fiele auf.
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"atlas": {"cols": 1, "rows": 8},
                   "states": {"idle": {"row": 0}, "waiting": {"row": 0}},
                   "moods": {m: {"row": 0} for m in
                             ["sleeping", "idle"] + ANIMIERTE_MOODS}}, f)
        mp2 = f.name
    laengen = {"sleeping:atem": 1, "idle:atem": 48}
    laengen.update({f"{m}:emote": 16 for m in ANIMIERTE_MOODS})
    geteilt = manifest_aktualisieren(mp2, 48, ANIMIERTE_MOODS,
                                     ["sleeping", "idle"], laengen)
    ruhe = geteilt["moods"]["idle"]["row"]
    assert geteilt["moods"]["sleeping"]["row"] == 0
    assert ruhe == 1, geteilt["moods"]["idle"]
    assert "frames" not in geteilt["moods"]["sleeping"], "sleeping steht still"
    assert geteilt["atlas"]["rows"] == 8, geteilt["atlas"]
    for m in ANIMIERTE_MOODS:
        assert geteilt["moods"][m]["row"] == ruhe, (m, geteilt["moods"][m])
        assert geteilt["moods"][m]["frames"] == 48, geteilt["moods"][m]
        assert geteilt["moods"][m]["emote"]["row"] >= 2, geteilt["moods"][m]
    gesten = [geteilt["moods"][m]["emote"]["row"] for m in ANIMIERTE_MOODS]
    assert len(set(gesten)) == len(gesten), f"zwei Gesten auf einer Zeile: {gesten}"
    # `states` muss mitwandern, sonst zeigt ein Pet ohne moods-Block ins Leere.
    assert geteilt["states"]["idle"]["row"] == ruhe, geteilt["states"]
    assert geteilt["states"]["waiting"]["row"] == ruhe, geteilt["states"]
    os.unlink(mp2)

    # `idle` atmet OHNE Geste. Beide Haelften einzeln pruefen: haengt
    # `frames` faelschlich an `moods`, verschwindet die Atemzeile lautlos.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"atlas": {"cols": 1, "rows": 8},
                   "moods": {m: {"row": i} for i, m in
                             enumerate(["sleeping", "idle", "done"])}}, f)
        mp = f.name
    reihenfolge = ["sleeping", "idle", "done"]
    erg = manifest_aktualisieren(
        mp, 32, ["done"], reihenfolge,
        {"sleeping:atem": 1, "idle:atem": 32, "done:atem": 32,
         "done:emote": 16})
    assert erg["moods"]["idle"]["frames"] == 32, erg["moods"]["idle"]
    assert "emote" not in erg["moods"]["idle"], "idle darf keine Geste haben"
    assert "frames" not in erg["moods"]["sleeping"], "eine Spalte ist kein frames"
    assert erg["moods"]["done"]["emote"]["frames"] == 16, erg["moods"]["done"]
    assert erg["atlas"]["rows"] == 4, erg["atlas"]

    # Der Filter: eine uebernommene Zeile behaelt ihre LAENGE. Kuerzte er sie
    # auf ein Bild, machte ein Lauf fuer einen Mood die Animation aller
    # anderen platt -- und genau dagegen gibt es ihn.
    from PIL import Image
    zelle = (8, 4)
    alt_spalten = 5
    altes = Image.new("RGBA", (zelle[0] * alt_spalten, zelle[1] * 3))
    for i in range(alt_spalten):          # Zeile 1 durchnummeriert faerben
        altes.paste((i * 40, 0, 0, 255),
                    (i * zelle[0], zelle[1], (i + 1) * zelle[0], 2 * zelle[1]))
    geholt = {}

    def uebernehmen(eintrag, schluessel):
        laenge = min(eintrag.get("frames", 1), alt_spalten)
        r = eintrag["row"]
        geholt[schluessel] = [
            altes.crop((i * zelle[0], r * zelle[1],
                        (i + 1) * zelle[0], (r + 1) * zelle[1]))
            for i in range(max(1, laenge))]

    uebernehmen({"row": 1, "frames": 4}, "a")
    assert len(geholt["a"]) == 4, len(geholt["a"])
    # und die Zellen sind verschieden, kommen also aus verschiedenen Spalten
    assert geholt["a"][0].getpixel((0, 0)) != geholt["a"][3].getpixel((0, 0))
    uebernehmen({"row": 1}, "b")
    assert len(geholt["b"]) == 1, "ohne frames ein Standbild"
    uebernehmen({"row": 1, "frames": 99}, "c")
    assert len(geholt["c"]) == alt_spalten, "nie mehr als das Sheet hat"

    print("Selbsttest ok.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pet", help="Verzeichnisname unter face/assets/")
    p.add_argument("--quelle", help="ComfyUI-Ausgabeordner mit den Gruenschirm-Standbildern")
    p.add_argument("--gesten-datei", metavar="JSON",
                   help="eigene Gestentexte: {mood: text, ...} fuer genau die "
                        "sechs animierten Moods. Noetig fuer Figuren ohne "
                        "Mund")
    p.add_argument("--atem-seeds", type=int, metavar="N",
                   help=f"wie viele Seeds fuer die Atemzeile (Vorgabe "
                        f"{ATEM_SEEDS}). Mehr kostet je Seed einen Clip und "
                        f"hilft nur, wenn ueberhaupt einer durchkommt")
    p.add_argument("--atem-text", metavar="TEXT",
                   help="eigener Bewegungstext fuer die Atemzeile. Noetig fuer "
                        "Figuren ohne Brust -- der Kiesel hat keine. Wird "
                        "gegen dieselben Gestenverbote geprueft wie die "
                        "Vorgabe")
    p.add_argument("--moods", nargs="*", metavar="MOOD",
                   help="nur diese Moods neu rechnen; der Rest wird aus dem "
                        "vorhandenen Sheet uebernommen, in voller Laenge. "
                        "Ohne Angabe: alle")
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
