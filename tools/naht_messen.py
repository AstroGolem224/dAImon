"""Die Naht: Push-to-Talk -> gesprochene Bildschirmfrage -> Kontext im Modell.

    tools/naht_messen.py vorher
    ... jetzt die Taste druecken und sprechen ...
    tools/naht_messen.py nachher

**Warum ueberhaupt ein eigenes Werkzeug.** Jeder Abschnitt dieser Kette ist
einzeln gemessen; die KETTE nie. Genau das ist der wiederkehrende Fehler
dieses Repos (CLAUDE.md): ein Stueck ist gebaut, geprueft und gruen, und im
Betrieb ruft es niemand auf. Sechs Befunde einer einzigen Review-Runde waren
von dieser Sorte -- und keiner davon war an einem Einzeltest zu sehen.

**Jede Station meldet drei Zustaende, nicht zwei:** `getragen`, `nicht
getragen`, `nicht messbar`. Der dritte ist der wichtige. Eine Station, deren
Dienst gar nicht laeuft, hat NICHT versagt -- sie wurde nicht gemessen, und
wer beides zusammenwirft, bekommt eine Kette, die gruen aussieht, weil die
Haelfte fehlt.

**Die Vorher-Aufnahme ist die Positivkontrolle.** Ein Zaehler, der nachher
auf 3 steht, sagt ohne seinen Vorher-Wert nichts. Und wo eine Voraussetzung
fehlt -- ein leerer Kontextspeicher, ein toter Ohren-Dienst --, steht das in
der Vorher-Aufnahme und macht das Ergebnis danach lesbar statt raetselhaft.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from daimon.common.config import runtime_dir              # noqa: E402
from daimon.hub.declassify import bildschirmbezug, zeitbezug  # noqa: E402

STAND = Path("/tmp") / f"daimon-naht-{Path.home().name}.json"

# Was laufen MUSS, damit die jeweilige Station ueberhaupt messbar ist.
DIENSTE = {
    "hub": "daimon-hub.service",
    "auth": "daimon-auth.service",
    "ears": "daimon-ears.service",
    "eyes": "daimon-eyes.service",
    "mind": "daimon-mind.service",
    "focus": "daimon-focus.service",
    "recorder": "daimon-recorder.service",
    "lokal-broker": "daimon-lokal-broker.service",
}


def _aktiv(unit: str) -> bool:
    e = subprocess.run(["systemctl", "--user", "is-active", unit],
                       capture_output=True, text=True, timeout=10)
    return e.stdout.strip() == "active"


def _sock(name: str, anfrage: dict | None = None) -> dict | None:
    """Eine Zeile hin, eine zurueck. `None` = nicht erreichbar."""
    pfad = runtime_dir() / name
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(5.0)
        c.connect(str(pfad))
        with c:
            if anfrage is not None:
                c.sendall(json.dumps(anfrage).encode() + b"\n")
            roh = c.makefile("rb").readline()
    except OSError:
        return None
    try:
        antwort = json.loads(roh)
    except (json.JSONDecodeError, ValueError):
        return None
    return antwort if isinstance(antwort, dict) else None


def _archiv_arten() -> dict:
    """Wie viele Zeilen je Art im Archiv. `{}` = nicht lesbar.

    READ-ONLY und ueber eine eigene Verbindung: der Recorder ist der einzige
    Schreiber, und diese Vorrichtung hat dort nichts zu suchen.
    """
    import sqlite3

    from daimon.common.config import data_dir
    from daimon.recorder.store import DATEI

    pfad = data_dir() / DATEI
    if not pfad.exists():
        return {}
    try:
        db = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        return dict(db.execute(
            "SELECT art, COUNT(*) FROM archiv GROUP BY art").fetchall())
    except sqlite3.Error:
        return {}
    finally:
        db.close()


def aufnehmen() -> dict:
    """Alles, was sich messen laesst, in einem Augenblick."""
    from daimon.eyes.context import Kontextspeicher

    diag = _sock("diag.sock") or {}
    mind = _sock("mind.sock", {"v": 1, "art": "zustand"}) or {}
    zustand = _sock("state.sock") or {}
    speicher = Kontextspeicher()
    speicher.laden()

    return {
        "ts": time.time(),
        "dienste": {k: _aktiv(u) for k, u in DIENSTE.items()},
        "marken": (diag.get("zaehler") or {}).get("rundenmarke", {}),
        "verworfen": diag.get("verworfen", {}),
        "mind": {k: mind.get(k) for k in
                 ("runden", "api_aufrufe", "deklassifiziert")},
        "kontext": speicher.zaehler(),
        "archiv": _archiv_arten(),
        # Laeuft gerade noch eine Runde? Ohne diese Frage ist "das Modell hat
        # nicht geantwortet" nicht von "es antwortet noch" zu unterscheiden --
        # und das erste ist ein Befund, das zweite Ungeduld.
        "voice": zustand.get("voice") or {},
    }


def _urteil(vorher: dict, nachher: dict) -> list[dict]:
    d = nachher["dienste"]
    mv, mn = vorher["marken"], nachher["marken"]
    kv, kn = vorher["mind"], nachher["mind"]

    def delta(a: dict, b: dict, feld: str) -> int | None:
        x, y = a.get(feld), b.get(feld)
        return None if x is None or y is None else y - x

    stationen = [
        {
            "nr": 1, "station": "Tastendruck -> Rundenmarke im Hub",
            "messbar": d["hub"] and d["auth"],
            "wert": delta(mv, mn, "ausgegeben"),
            "erwartet": ">= 1",
            "ohne": "hub oder auth laeuft nicht",
        },
        {
            "nr": 2, "station": "Sprache -> Transkript (Ohren + STT)",
            "messbar": d["ears"],
            "wert": delta(vorher["mind"], nachher["mind"], "runden"),
            "erwartet": ">= 1 (der Mind wurde gefragt)",
            "ohne": "daimon-ears laeuft nicht -- ohne ihn gibt es keine "
                    "Aeusserung, und alles Weitere ist nicht gemessen",
        },
        {
            "nr": 3, "station": "Gate loest die Marke ein",
            "messbar": d["hub"],
            "wert": delta(mv, mn, "eingeloest"),
            "erwartet": ">= 1",
            "ohne": "hub laeuft nicht",
        },
        {
            "nr": 4, "station": "Kontext kommt IM MODELL an",
            "messbar": d["mind"] and kn.get("deklassifiziert") is not None,
            "wert": delta(kv, kn, "deklassifiziert"),
            "erwartet": ">= 1",
            "ohne": "mind laeuft nicht, oder seine Auskunft kennt das Feld "
                    "`deklassifiziert` nicht (Stand vor dem 16.08.)",
        },
        {
            "nr": 5, "station": "Das Modell antwortet",
            "messbar": d["mind"] and d["lokal-broker"],
            "wert": delta(kv, kn, "api_aufrufe"),
            "erwartet": ">= 1",
            "ohne": "der lokale Broker laeuft nicht (braucht ollama) -- "
                    "die Kette ist bis Station 4 trotzdem gueltig",
        },
        {
            # Der ZWEITE Zweig der Naht (T-7.4): dasselbe Gesprochene geht
            # ausserdem ins Archiv, damit es spaeter auffindbar ist. Er
            # haengt an keiner der Stationen oben -- ein stummer Archivzweig
            # faellt im Sprachpfad nicht auf, das ist seine Bauart
            # (`melder.senden` schluckt jeden Fehler).
            "nr": 6, "station": "Transkript landet im ARCHIV (T-7.4)",
            "messbar": d["recorder"] and bool(nachher["archiv"]),
            "wert": (nachher["archiv"].get("transkript", 0)
                     - vorher["archiv"].get("transkript", 0)
                     if nachher["archiv"] else None),
            "erwartet": ">= 1",
            "ohne": "recorder laeuft nicht, oder das Archiv ist nicht lesbar",
        },
    ]
    for s in stationen:
        if not s["messbar"]:
            s["urteil"] = "nicht messbar"
        elif s["wert"] is None:
            s["urteil"] = "nicht messbar"
        elif s["wert"] >= 1:
            s["urteil"] = "getragen"
        else:
            s["urteil"] = "NICHT getragen"

    # WIDERSPRUCHSPRUEFUNG. Station 4 setzt Station 3 voraus: Kontext kommt
    # nur durch das Gate, und das Gate loest dabei die Marke ein. Traegt 4
    # und 3 nicht, ist die MESSUNG kaputt und nicht die Kette.
    #
    # Genau das ist am 17.08. beim ersten Lauf passiert: der Zaehler
    # `rundenmarke.eingeloest` hatte keinen Schreiber, und diese Vorrichtung
    # meldete "NICHT getragen" ueber eine Station, die getragen hatte. Ein
    # Werkzeug, das den Fehler macht, den es finden soll -- der vierte
    # dieser Sorte in dieser Woche, und der erste in meinem eigenen.
    st = {s["nr"]: s for s in stationen}

    # NOCH UNTERWEGS ist kein Versagen. Am 17.08. live passiert: Station 5
    # stand auf "NICHT getragen", und 41 Sekunden spaeter meldete der Mind
    # "Antwort erhalten" -- ein 27B-Modell laedt beim ersten Aufruf in den
    # VRAM. Eine Messung ist ein ZEITPUNKT; wer das nicht sagt, macht aus
    # Ungeduld einen Befund. Derselbe Fehlertyp wie beim toten Zaehler, nur
    # eine Stunde spaeter.
    voice = nachher.get("voice") or {}
    laeuft = bool(voice.get("denkt") or voice.get("tts_active"))
    if st[5]["urteil"] == "NICHT getragen" and (laeuft
                                                or st[4]["urteil"] == "getragen"):
        st[5]["urteil"] = "nicht messbar"
        st[5]["ohne"] = (
            "NOCH UNTERWEGS? " + ("der Mind denkt gerade -- " if laeuft else
                                  "Station 4 hat getragen, die Runde laeuft "
                                  "also -- ")
            + "beim ersten Aufruf laedt das Modell in den VRAM. Einfach "
              "`nachher` noch einmal fahren; die Vorher-Aufnahme gilt weiter.")

    if st[4]["urteil"] == "getragen" and st[3]["urteil"] == "NICHT getragen":
        st[3]["urteil"] = "nicht messbar"
        st[3]["ohne"] = (
            "WIDERSPRUCH: Station 4 hat getragen, also MUSS das Gate "
            "freigegeben und dabei die Marke eingeloest haben. Der Zaehler "
            "sagt etwas anderes -- glaube dem Journal, nicht ihm: "
            "`journalctl --user -u daimon-hub.service | grep einloesung`. "
            "Vor dem 17.08. hatte dieser Zaehler keinen Schreiber.")
    return stationen


def _voraussetzungen(stand: dict, satz: str) -> list[str]:
    """Was die Messung wertlos machen wuerde. VOR dem Druecken lesen."""
    warnungen = []
    for name, an in stand["dienste"].items():
        if not an and name in ("hub", "auth", "ears", "mind"):
            warnungen.append(f"{name} laeuft nicht -- ohne ihn ist die Kette "
                             "an dieser Stelle nicht gemessen, nicht kaputt")
    if not stand["dienste"]["lokal-broker"]:
        warnungen.append("lokal-broker laeuft nicht -- Station 5 bleibt "
                         "unbelegt, Station 1-4 sind davon unberuehrt")
    if not stand["dienste"]["recorder"]:
        warnungen.append("recorder laeuft nicht -- der Archivzweig der Naht "
                         "(Transkript ins Archiv) ist nicht gemessen")
    if stand["kontext"].get("ocr", 0) == 0:
        warnungen.append("DER KONTEXTSPEICHER IST LEER. Das Gate haette "
                         "nichts freizugeben, und Station 4 waere auch bei "
                         "heiler Kette 0. Erst den Bildschirm etwas lesen "
                         "lassen (die Augen brauchen eine Aenderung).")
    if satz and not bildschirmbezug(satz):
        warnungen.append(f"Der geplante Satz hat KEINEN Bildschirmbezug: "
                         f"{satz!r}. Das Gate lehnt ihn zu Recht ab.")
    return warnungen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("schritt", choices=("vorher", "nachher"))
    ap.add_argument("--satz", default="was steht auf dem bildschirm",
                    help="der Satz, den du sagen willst -- wird auf "
                         "Bildschirmbezug geprueft, bevor du druckst")
    args = ap.parse_args(argv)

    if args.schritt == "vorher":
        stand = aufnehmen()
        STAND.write_text(json.dumps(stand, indent=2))
        print(json.dumps({"dienste": stand["dienste"],
                          "kontext": stand["kontext"],
                          "marken": stand["marken"],
                          "mind": stand["mind"]}, indent=2, ensure_ascii=False))
        warnungen = _voraussetzungen(stand, args.satz)
        print()
        if warnungen:
            print("VOR DEM DRUECKEN:")
            for w in warnungen:
                print(f"  ! {w}")
        else:
            print("Voraussetzungen erfuellt.")
        print(f"\nSatz: {args.satz!r}")
        print(f"  Bildschirmbezug erkannt: {bildschirmbezug(args.satz)}")
        print(f"  Zeitbezug erkannt:       {zeitbezug(args.satz)} "
              "(nur dann wird auch das Archiv durchsucht)")
        print(f"\nStand liegt in {STAND}. Jetzt druecken und sprechen, "
              "danach: tools/naht_messen.py nachher")
        return 0

    try:
        vorher = json.loads(STAND.read_text())
    except OSError:
        print(f"Keine Vorher-Aufnahme in {STAND}. Ohne sie sagt jeder Zaehler "
              "nichts -- erst `vorher` fahren.")
        return 1

    nachher = aufnehmen()
    stationen = _urteil(vorher, nachher)
    print(f"{round(nachher['ts'] - vorher['ts'])} s zwischen den Aufnahmen\n")
    for s in stationen:
        zeichen = {"getragen": "OK  ", "NICHT getragen": "FAIL",
                   "nicht messbar": "--  "}[s["urteil"]]
        print(f"{zeichen} {s['nr']}. {s['station']}")
        print(f"       {s['urteil']}, Zuwachs {s['wert']}, "
              f"erwartet {s['erwartet']}")
        if s["urteil"] == "nicht messbar":
            print(f"       Grund: {s['ohne']}")

    getragen = [s for s in stationen if s["urteil"] == "getragen"]
    offen = [s for s in stationen if s["urteil"] == "nicht messbar"]
    kaputt = [s for s in stationen if s["urteil"] == "NICHT getragen"]
    print(f"\n{len(getragen)} getragen, {len(kaputt)} nicht getragen, "
          f"{len(offen)} nicht gemessen.")
    if not getragen:
        print("KEINE Station hat getragen -- pruefe zuerst, ob ueberhaupt "
              "gesprochen wurde (Zuwachs bei `runden`).")
    return 1 if kaputt else 0


if __name__ == "__main__":
    raise SystemExit(main())
