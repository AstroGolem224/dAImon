#!/usr/bin/env python3
"""Haelt die Abkuehlung ueber einen echten Neustart? T-3.9, Wanduhrzweig.

Der eine Zweig der Abkuehlung, den niemand gemessen hat. `daimon/hub/abkuehlung.py`
entscheidet nach der Boot-ID: gleiche Laufzeit -> monotone Zeit, andere Laufzeit
-> Wanduhr. Der monotone Zweig ist im Verifizierer belegt (Hub-Neustart, gestellte
Uhren). Der Wanduhrzweig braucht einen echten Reboot, und zwar aus einem Grund,
der keine Formsache ist: nach dem Boot faengt `time.monotonic()` wieder klein an,
und eine gespeicherte monotone Zahl liegt dann Tage in der Zukunft. Wer das falsch
macht, bekommt eine Abkuehlung, die nach jedem Neustart entweder ewig sperrt oder
gar nicht greift -- und beides sieht im Betrieb nach "das Pet ist kaputt" aus.

Warum eine eigene Instanz und nicht der laufende Hub
----------------------------------------------------------------------------
Die Produktivfristen sind 20/10/3 Sekunden. Ein Reboot dauert laenger, also waere
nach dem Neustart jede Frist ohnehin um -- man wuerde "frei" messen und nichts
darueber wissen, WELCHER Zweig das entschieden hat. Dieser Lauf nimmt deshalb ein
eigenes Zustandsverzeichnis und eine Konfiguration mit einer Frist, die die
Ausfallzeit ueberdauert -- 24 Stunden, damit es nicht darauf ankommt, WANN der
Neustart kommt. Mit einer Stunde (erster Anlauf) haette ein Neustart am naechsten
Tag "frei" gemessen, und das ist von einer kaputten Persistenz nicht zu
unterscheiden (`ungefragt = 86400 s`, also 24 h).

Die Positivkontrolle, ohne die "immer noch gesperrt" nichts heisst
----------------------------------------------------------------------------
Ein Bestand, der nach dem Neustart einfach ALLES sperrt, wuerde die Hauptpruefung
auch bestehen -- ein kaputter Leser, der jede Frist fuer unendlich haelt, ist von
einer funktionierenden Persistenz nicht zu unterscheiden. Deshalb wird ein zweiter
Kanal mit einer KURZEN Frist vermerkt (`rueckfrage = 5 s`), und der muss nach dem
Neustart frei sein. Zusammen sagen die zwei: die Frist wird gelesen, nicht
geraten.

Aufruf, zwei Phasen:

    python3 spikes/tts-abkuehlung/reboot_check.py vor      # vor dem Neustart
    #  ... reboot ...
    python3 spikes/tts-abkuehlung/reboot_check.py nach     # danach

Beide Phasen schreiben nach `spikes/tts-abkuehlung/runs/`. Phase `nach` faellt
mit Exit 1 aus, wenn die Zusage nicht haelt, und nennt den Grund.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HIER = Path(__file__).resolve().parent
RUNS = HIER / "runs"
STATE = HIER / "state"          # ueberlebt den Reboot -- das ist der Punkt
LANGE_FRIST_S = 86400.0
KURZE_FRIST_S = 5.0
PY = REPO / ".venv" / "bin" / "python"


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def hub_starten(rt: Path, conf: Path) -> subprocess.Popen:
    """Ein eigener Hub mit eigenem Runtime- und Zustandsverzeichnis."""
    umgebung = {
        **os.environ,
        "XDG_RUNTIME_DIR": str(rt),
        "XDG_CONFIG_HOME": str(conf),
        "XDG_STATE_HOME": str(STATE),          # -> STATE/daimon
    }
    # Reste des vorigen Laufs weg: ein liegengebliebener Socketpfad ist EXISTENT
    # und trotzdem tot -- beim ersten Anlauf dieses Skripts kam daraus ein
    # `ConnectionRefusedError`, weil ich auf den Dateinamen gewartet habe statt
    # auf den Horcher. Gewartet wird deshalb auf ein erfolgreiches `connect`.
    sock = rt / "daimon" / "tts.sock"
    if sock.exists():
        sock.unlink()
    p = subprocess.Popen([str(PY), "-m", "daimon.hub.daemon"], cwd=REPO,
                         env=umgebung, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE)
    for _ in range(200):
        if sock.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(1.0)
            try:
                probe.connect(str(sock))
                return p
            except OSError:
                pass
            finally:
                probe.close()
        time.sleep(0.05)
    p.kill()
    raise SystemExit("Hub kam nicht hoch (tts.sock antwortet nicht)")


def konfiguration(conf: Path) -> None:
    (conf / "daimon").mkdir(parents=True, exist_ok=True)
    (conf / "daimon" / "daimon.toml").write_text(
        "# Nur fuer diesen Spike: eine Frist, die eine Ausfallzeit ueberdauert,\n"
        "# und eine kurze als Positivkontrolle.\n"
        "[tts.abkuehlung]\n"
        f"ungefragt  = {LANGE_FRIST_S}\n"
        f"rueckfrage = {KURZE_FRIST_S}\n"
        "reaktion   = 10.0\n")


def frage(sock: Path, anfrage: dict, timeout: float = 15.0) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout)
    try:
        c.connect(str(sock))
        c.sendall(json.dumps(anfrage).encode() + b"\n")
        return json.loads(c.makefile("rb").readline(1 << 16))
    finally:
        c.close()


def frist_setzen(sock: Path, kanal: str, anlass: str) -> dict:
    """Freigabe holen und `beginnt` melden -- dort vermerkt der Hub die Frist.

    `gesprochen` MUSS danach kommen, auch wenn hier kein TTS-Dienst laeuft: es
    setzt `voice.tts_active` zurueck. Ohne das haelt der Hub die Wiedergabe fuer
    laufend, und jede weitere Anfrage gilt als UNTERBRECHUNG -- die darf die
    Abkuehlung umgehen (Design §8.3, Nachtrag 03.08.), und der Aufbau messe dann
    genau nichts. Beim ersten Lauf dieses Skripts ist mir das passiert: `rest_s`
    war 3599,999 und die Anfrage trotzdem freigegeben.
    """
    frei = frage(sock, {"v": 1, "art": "freigabe", "kanal": kanal,
                        "anlass": anlass})
    if not frei.get("ok"):
        raise SystemExit(f"Freigabe fuer {kanal} abgelehnt: {frei}")
    begonnen = frage(sock, {"v": 1, "art": "beginnt",
                            "marke": frei["marke"]})
    if not begonnen.get("ok"):
        raise SystemExit(f"`beginnt` fuer {kanal} abgelehnt: {begonnen}")
    fertig = frage(sock, {"v": 1, "art": "gesprochen", "marke": frei["marke"]})
    if not fertig.get("ok"):
        raise SystemExit(f"`gesprochen` fuer {kanal} abgelehnt: {fertig}")
    # Erst jetzt nachfragen: die Wiedergabe gilt als beendet, also entscheidet
    # die Abkuehlung und nicht die Unterbrechungs-Ausnahme.
    return frage(sock, {"v": 1, "art": "freigabe", "kanal": kanal,
                        "anlass": anlass})


def ablage() -> dict:
    pfad = STATE / "daimon" / "tts-abkuehlung.json"
    if not pfad.exists():
        return {"fehlt": str(pfad)}
    return json.loads(pfad.read_text())


def phase_vor() -> int:
    RUNS.mkdir(parents=True, exist_ok=True)
    rt = HIER / "rt"
    (rt / "daimon").mkdir(parents=True, exist_ok=True)
    conf = HIER / "conf"
    konfiguration(conf)
    # Alter Bestand weg: sonst misst man den Lauf von vorgestern.
    alt = STATE / "daimon" / "tts-abkuehlung.json"
    if alt.exists():
        alt.unlink()

    hub = hub_starten(rt, conf)
    sock = rt / "daimon" / "tts.sock"
    try:
        lang = frist_setzen(sock, "ungefragt", "leerlauf")
        kurz = frist_setzen(sock, "rueckfrage", "tests_gruen")
        erg = {
            "phase": "vor",
            "zeit": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "wanduhr_s": time.time(),
            "boot_id": boot_id(),
            "lange_frist_s": LANGE_FRIST_S,
            "kurze_frist_s": KURZE_FRIST_S,
            "ungefragt_gesperrt": not lang.get("ok"),
            "ungefragt_rest_s": lang.get("rest_s"),
            "rueckfrage_gesperrt": not kurz.get("ok"),
            "rueckfrage_rest_s": kurz.get("rest_s"),
            "ablage": ablage(),
        }
    finally:
        hub.terminate()
        hub.wait(timeout=10)

    (RUNS / "vor.json").write_text(json.dumps(erg, indent=2) + "\n")
    print(json.dumps(erg, indent=2))
    if not erg["ungefragt_gesperrt"]:
        print("PHASE VOR FEHLGESCHLAGEN: die Frist wurde nicht gesetzt.")
        return 1
    print("\nPhase `vor` steht. Jetzt neu starten, danach:")
    print("  python3 spikes/tts-abkuehlung/reboot_check.py nach")
    return 0


def phase_nach() -> int:
    vor_pfad = RUNS / "vor.json"
    if not vor_pfad.exists():
        print(f"Keine Phase `vor` gefunden ({vor_pfad}).")
        return 1
    vor = json.loads(vor_pfad.read_text())
    jetzt_boot = boot_id()

    rt = HIER / "rt"
    (rt / "daimon").mkdir(parents=True, exist_ok=True)
    conf = HIER / "conf"
    konfiguration(conf)
    hub = hub_starten(rt, conf)
    sock = rt / "daimon" / "tts.sock"
    try:
        lang = frage(sock, {"v": 1, "art": "freigabe", "kanal": "ungefragt",
                            "anlass": "leerlauf"})
        kurz = frage(sock, {"v": 1, "art": "freigabe", "kanal": "rueckfrage",
                            "anlass": "tests_gruen"})
    finally:
        hub.terminate()
        hub.wait(timeout=10)

    ausfall_s = time.time() - float(vor["wanduhr_s"])
    neuer_boot = jetzt_boot != vor["boot_id"]
    lange_haelt = not lang.get("ok") and lang.get("grund") == "abkuehlung"
    kurze_frei = bool(kurz.get("ok"))
    # Die Restzeit muss zur Wanduhr passen: Frist minus Ausfallzeit, mit
    # Toleranz. Eine Sperre, deren Rest NICHT dazu passt, waere geraten und
    # nicht gelesen.
    erwartet_rest = LANGE_FRIST_S - ausfall_s
    rest = lang.get("rest_s")
    # Toleranz 300 s: die Wanduhr darf zwischen den Phasen per NTP nachgestellt
    # worden sein, und ein Reboot dauert Minuten, nicht Sekunden.
    rest_passt = (isinstance(rest, (int, float))
                  and abs(rest - erwartet_rest) < 300.0)

    erg = {
        "phase": "nach",
        "zeit": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ausfall_s": round(ausfall_s, 1),
        "boot_id_vor": vor["boot_id"],
        "boot_id_nach": jetzt_boot,
        "neuer_boot": neuer_boot,
        "ungefragt_antwort": lang,
        "rueckfrage_antwort": kurz,
        "erwarteter_rest_s": round(erwartet_rest, 1),
        "rest_passt_zur_wanduhr": rest_passt,
        "ablage": ablage(),
        "zusagen": {
            "die_lange_frist_haelt_ueber_den_reboot": lange_haelt,
            "POSITIVKONTROLLE_kurze_frist_ist_frei": kurze_frei,
            "der_wanduhrzweig_wurde_wirklich_genommen": neuer_boot,
            "der_rest_stammt_aus_der_wanduhr": rest_passt,
        },
    }
    (RUNS / "nach.json").write_text(json.dumps(erg, indent=2) + "\n")
    print(json.dumps(erg, indent=2))

    if not neuer_boot:
        print("\nUNGUELTIG: dieselbe Boot-ID -- es war kein Neustart dazwischen. "
              "Der Wanduhrzweig wurde also nicht betreten.")
        return 1
    schlecht = [k for k, v in erg["zusagen"].items() if not v]
    if schlecht:
        print("\nFEHLGESCHLAGEN: " + ", ".join(schlecht))
        return 1
    print("\nBESTANDEN: die Abkuehlung ueberlebt den Neustart ueber die Wanduhr, "
          "und die kurze Frist ist abgelaufen (also wird gelesen, nicht geraten).")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("vor", "nach"):
        print(__doc__)
        return 2
    return phase_vor() if argv[1] == "vor" else phase_nach()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
