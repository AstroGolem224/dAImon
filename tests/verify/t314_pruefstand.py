#!/usr/bin/env python3
"""Blinder Pruefstand fuer T-3.14 nach Vertrag und Schnittstellen-Addendum."""
from __future__ import annotations

import argparse
import errno
import importlib
import json
import math
import os
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, Sequence


ZUSTAENDE = {"idle", "listening", "processing", "speaking"}
MUTANTEN_GRENZEN = {
    "zustand-direkt-setzbar": "K3",
    "sprechen-schlaegt-zuhoeren": "K2",
    "ptt-von-jedem-produzenten": "K5",
    "denken-ohne-frist": "K6",
    "face-zeigt-eigene-rechnung": "K9/K11",
    "indikator-ersetzt-sprite": "K8",
}
REGRESSIONS_VERIFIZIERER = (
    "tests/verify/T-0.7.sh",
    "tests/verify/T-0.9.sh",
    "tests/verify/T-2.5.sh",
    "tests/verify/T-2.4.sh",
    "tests/verify/T-3.4.sh",
    "tests/verify/T-3.9.sh",
)


@dataclass(frozen=True)
class Ergebnis:
    kriterium: str
    ok: bool
    text: str


class Bericht:
    def __init__(self) -> None:
        self.ergebnisse: list[Ergebnis] = []

    def pruefe(self, kriterium: str, bedingung: bool, text: str) -> bool:
        ok = bool(bedingung)
        self.ergebnisse.append(Ergebnis(kriterium, ok, text))
        if not ok:
            print(f"FAIL {kriterium}: {text}", file=sys.stderr)
        return ok

    def fehler(self, kriterium: str, text: str) -> None:
        self.pruefe(kriterium, False, text)

    def bilanz(self) -> int:
        print("\nBilanz T-3.14:")
        rot_gesamt = 0
        gruppen: dict[str, list[Ergebnis]] = defaultdict(list)
        for ergebnis in self.ergebnisse:
            gruppen[ergebnis.kriterium].append(ergebnis)
        for nummer in range(1, 14):
            kriterium = f"K{nummer}"
            werte = gruppen.get(kriterium, [])
            rot = sum(not wert.ok for wert in werte)
            rot_gesamt += rot
            print(f"{kriterium}: {len(werte)} Pruefungen, {rot} rot")
        return 1 if rot_gesamt else 0


def perzentil(werte: Sequence[float], q: float) -> float:
    """Perzentil als naechstliegender Rang nach Addendum 10.6."""
    if not werte:
        raise ValueError("Perzentil einer leeren Stichprobe")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q ausserhalb [0, 1]")
    sortiert = sorted(float(wert) for wert in werte)
    rang = max(1, math.ceil(len(sortiert) * q))
    return sortiert[rang - 1]


def schreibe_latenz(pfad: Path, werte_ms: Sequence[float]) -> None:
    werte = [float(wert) for wert in werte_ms]
    daten = {
        "n": len(werte),
        "p50_ms": perzentil(werte, 0.50) if werte else None,
        "p95_ms": perzentil(werte, 0.95) if werte else None,
        "werte_ms": werte,
    }
    pfad.parent.mkdir(parents=True, exist_ok=True)
    temporaer = pfad.with_name(pfad.name + ".tmp")
    temporaer.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporaer.replace(pfad)


def lade_state_modul(pruefling: Path) -> ModuleType:
    sys.path.insert(0, str(pruefling))
    importlib.invalidate_caches()
    for name in tuple(sys.modules):
        if name == "daimon" or name.startswith("daimon."):
            del sys.modules[name]
    modul = importlib.import_module("daimon.hub.state")
    quelle = Path(modul.__file__ or "").resolve()
    try:
        quelle.relative_to(pruefling.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Import entkam dem Pruefling: {quelle}") from exc
    return modul


def neuer_zustand(modul: ModuleType):
    # Der Konstruktor ist im Vertrag nicht gepinnt; die kleinste Annahme ist
    # der parameterlose Aufruf. Sein Fehlschlag wird als Vertragsluecke sichtbar.
    try:
        return modul.HubState()
    except TypeError as exc:
        raise RuntimeError(
            "CONTRACT GAP: HubState-Konstruktor ist nicht gepinnt; "
            "der parameterlose Aufruf wurde abgelehnt"
        ) from exc


def flags_setzen(zustand, *, listening: bool, tts_active: bool, denkt: bool) -> None:
    zustand.set_voice(listening=listening, tts_active=tts_active, jetzt=100.0)
    if denkt:
        zustand.voice_denkt_an(jetzt=100.0)
    else:
        zustand.voice_denkt_aus()


def erwarteter_zustand(listening: bool, tts_active: bool, denkt: bool) -> str:
    if listening:
        return "listening"
    if tts_active:
        return "speaking"
    if denkt:
        return "processing"
    return "idle"


def pruefe_k1_k2(modul: ModuleType, bericht: Bericht) -> None:
    gesehen: set[str] = set()
    for listening in (False, True):
        for tts_active in (False, True):
            for denkt in (False, True):
                bezeichnung = f"listening={listening}, tts_active={tts_active}, denkt={denkt}"
                try:
                    zustand = neuer_zustand(modul)
                    flags_setzen(zustand, listening=listening, tts_active=tts_active, denkt=denkt)
                    ist = zustand.voice_state(jetzt=100.0)
                except Exception as exc:  # jede Kombination wird weiter abgerechnet
                    bericht.fehler("K1", f"{bezeichnung}: Aufruf fehlgeschlagen: {exc!r}")
                    bericht.fehler("K2", f"{bezeichnung}: Vorrang nicht messbar: {exc!r}")
                    continue
                gesehen.add(ist)
                bericht.pruefe("K1", ist in ZUSTAENDE, f"{bezeichnung}: unzulaessiger Zustand {ist!r}")
                soll = erwarteter_zustand(listening, tts_active, denkt)
                bericht.pruefe("K2", ist == soll, f"{bezeichnung}: erwartet {soll!r}, erhalten {ist!r}")
    bericht.pruefe("K1", gesehen == ZUSTAENDE, f"Positivkontrolle: gesehen {sorted(gesehen)!r}")


def pruefe_k3(modul: ModuleType, bericht: Bericht) -> None:
    try:
        positiv = neuer_zustand(modul)
        positiv.set_voice(tts_active=True, jetzt=100.0)
        positiv_direkt = positiv.voice_state(jetzt=100.0)
        positiv_snapshot = positiv.snapshot(voice_jetzt=100.0)
        bericht.pruefe(
            "K3",
            positiv_snapshot["voice"]["state"] == "speaking",
            "Positivkontrolle mit Uhrvergleich: "
            f"voice_state(jetzt=100.0)={positiv_direkt!r}, unmittelbar danach "
            f"snapshot(voice_jetzt=100.0)={positiv_snapshot['voice']['state']!r}; "
            "erwartet snapshot='speaking'",
        )

        zustand = neuer_zustand(modul)
        zustand.set_voice(state="speaking", jetzt=100.0)
        snapshot = zustand.snapshot(voice_jetzt=100.0)
        direkt = zustand.voice_state(jetzt=100.0)
        bericht.pruefe("K3", direkt == "idle", f"state= darf voice_state nicht setzen; erhalten {direkt!r}")
        bericht.pruefe(
            "K3", snapshot["voice"]["state"] == "idle",
            "state= darf den Schnappschuss nicht setzen; "
            f"voice_state(jetzt=100.0)={direkt!r}, "
            f"snapshot(voice_jetzt=100.0)={snapshot['voice']['state']!r}",
        )
    except Exception as exc:
        bericht.fehler("K3", f"API-/Schnappschusspruefung fehlgeschlagen: {exc!r}")


def pruefe_k6(modul: ModuleType, bericht: Bericht) -> None:
    bericht.pruefe("K6", getattr(modul, "DENK_FRIST_S", None) == 30.0, "DENK_FRIST_S muss 30.0 sein")
    bericht.pruefe("K6", getattr(modul, "PTT_FRIST_S", None) == 150.0, "PTT_FRIST_S muss 150.0 sein")
    bericht.pruefe("K6", getattr(modul, "SPRECH_FRIST_S", None) == 30.0,
                   "SPRECH_FRIST_S muss 30.0 sein")
    try:
        denk = neuer_zustand(modul)
        denk.voice_denkt_an(jetzt=100.0)
        denk_vorher = denk.voice_state(jetzt=129.999)
        denk_nachher = denk.voice_state(jetzt=130.0)
        bericht.pruefe("K6", denk_vorher == "processing",
                       f"Denken knapp vor injizierter Frist: erhalten {denk_vorher!r}")
        bericht.pruefe("K6", denk_nachher == "idle",
                       f"Denken am injizierten Fristpunkt: erhalten {denk_nachher!r}")

        ptt = neuer_zustand(modul)
        ptt.set_voice(listening=True, jetzt=100.0)
        ptt_vorher = ptt.voice_state(jetzt=249.999)
        ptt_nachher = ptt.voice_state(jetzt=250.0)
        bericht.pruefe("K6", ptt_vorher == "listening",
                       f"PTT knapp vor injizierter Obergrenze: erhalten {ptt_vorher!r}")
        bericht.pruefe("K6", ptt_nachher == "idle",
                       f"PTT am injizierten Fristpunkt: erhalten {ptt_nachher!r}")

        # §11.1 macht die dritte Ausfallgrenze messbar. Der Blick unmittelbar
        # davor ist die Positivkontrolle, der Grenzpunkt gilt als abgelaufen.
        sprechen = neuer_zustand(modul)
        sprechen.set_voice(tts_active=True, jetzt=100.0)
        sprechen_vorher = sprechen.voice_state(jetzt=129.999)
        sprechen_nachher = sprechen.voice_state(jetzt=130.0)
        bericht.pruefe(
            "K6", sprechen_vorher == "speaking",
            f"Sprechen knapp vor injizierter Frist: erhalten {sprechen_vorher!r}",
        )
        bericht.pruefe(
            "K6", sprechen_nachher == "idle",
            f"Sprechen am injizierten Fristpunkt: erhalten {sprechen_nachher!r}",
        )
    except Exception as exc:
        bericht.fehler("K6", f"Injizierte Fristpruefung fehlgeschlagen: {exc!r}")

    # Ein Startpunkt in der Vergangenheit nutzt die gepinnte injizierbare Uhr,
    # ohne Annahmen darueber, wie das Modul time.monotonic importiert.
    try:
        aktuell = time.monotonic()
        positiv = neuer_zustand(modul)
        positiv.voice_denkt_an(jetzt=aktuell)
        vorher = positiv.snapshot(voice_jetzt=aktuell)
        bericht.pruefe(
            "K6", vorher["voice"]["denkt"] is True,
            "Uhrvergleich vor Frist: "
            f"voice_denkt_an(jetzt={aktuell:.6f}), "
            f"snapshot(voice_jetzt={aktuell:.6f}).voice.denkt={vorher['voice']['denkt']!r}",
        )

        zustand = neuer_zustand(modul)
        ganz_vorher = zustand.snapshot(voice_jetzt=aktuell)
        zustand.voice_denkt_an(jetzt=aktuell - 31.0)
        nachher = zustand.snapshot(voice_jetzt=aktuell)
        bericht.pruefe(
            "K6", nachher["voice"]["denkt"] is False,
            "Uhrvergleich nach Frist: "
            f"voice_denkt_an(jetzt={aktuell - 31.0:.6f}), "
            f"snapshot(voice_jetzt={aktuell:.6f}).voice.denkt={nachher['voice']['denkt']!r}",
        )
        bericht.pruefe(
            "K6", nachher["rev"] >= ganz_vorher["rev"] + 2,
            "rev bei injiziertem Beginn und snapshot-eigener Uhr: "
            f"vorher={ganz_vorher['rev']!r}, nachher={nachher['rev']!r}, erwartet mindestens +2",
        )

        sprechen = neuer_zustand(modul)
        sprechen.set_voice(tts_active=True, jetzt=100.0)
        sprechen_vorher = sprechen.snapshot(voice_jetzt=129.999)
        sprechen_nachher = sprechen.snapshot(voice_jetzt=130.0)
        bericht.pruefe(
            "K6", sprechen_vorher["voice"]["state"] == "speaking",
            "Positivkontrolle des Sprechzustands vor der Snapshot-Frist: "
            f"state={sprechen_vorher['voice']['state']!r}",
        )
        bericht.pruefe(
            "K6", sprechen_vorher["voice"]["tts_active"] is True,
            "Positivkontrolle des tts_active-Flags vor der Snapshot-Frist: "
            f"tts_active={sprechen_vorher['voice']['tts_active']!r}",
        )
        bericht.pruefe(
            "K6", sprechen_nachher["voice"]["state"] == "idle",
            "Sprechzustand am Snapshot-Grenzpunkt: "
            f"state={sprechen_nachher['voice']['state']!r}",
        )
        bericht.pruefe(
            "K6", sprechen_nachher["voice"]["tts_active"] is False,
            "tts_active am Snapshot-Grenzpunkt: "
            f"tts_active={sprechen_nachher['voice']['tts_active']!r}",
        )
        bericht.pruefe(
            "K6", sprechen_nachher["rev"] > sprechen_vorher["rev"],
            "Sprechfrist und rev verlassen denselben Snapshot: "
            f"vorher={sprechen_vorher['rev']!r}, nachher={sprechen_nachher['rev']!r}",
        )
    except Exception as exc:
        bericht.fehler("K6", f"Schnappschuss-/rev-Fristpruefung fehlgeschlagen: {exc!r}")

BEOBACHTUNG_S = 2.0


def json_zeile_lesen(pfad: Path, timeout_s: float = BEOBACHTUNG_S) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        sock.connect(str(pfad))
        datei = sock.makefile("rb")
        zeile = datei.readline()
    if not zeile:
        raise RuntimeError(f"{pfad.name}: EOF vor JSON-Zeile")
    objekt = json.loads(zeile.decode("utf-8"))
    if not isinstance(objekt, dict):
        raise RuntimeError(f"{pfad.name}: JSON ist kein Objekt")
    return objekt


def warten_auf(
    lesen: Callable[[], dict], passt: Callable[[dict], bool], beschreibung: str,
    timeout_s: float = BEOBACHTUNG_S,
) -> dict:
    ende = time.monotonic() + timeout_s
    letzter_fehler: Exception | None = None
    letztes: dict | None = None
    while time.monotonic() < ende:
        try:
            letztes = lesen()
            if passt(letztes):
                return letztes
        except (OSError, ValueError, RuntimeError) as exc:
            letzter_fehler = exc
        time.sleep(0.02)
    zusatz = f", letzter Wert={letztes!r}, letzter Fehler={letzter_fehler!r}"
    raise TimeoutError(f"Zeitfenster fuer {beschreibung} abgelaufen{zusatz}")


def ereignis(typ: str, payload: dict) -> bytes:
    return (json.dumps({"v": 1, "type": typ, "payload": payload}, separators=(",", ":")) + "\n").encode()


def tts_anfrage(**felder) -> bytes:
    return (json.dumps({"v": 1, **felder}, separators=(",", ":")) + "\n").encode()


def startzeit(pid: int) -> str | None:
    lauf = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    wert = lauf.stdout.strip()
    return wert if lauf.returncode == 0 and wert else None


class Prozessgruppe:
    def __init__(self, befehl: Sequence[str], cwd: Path, env: dict[str, str]) -> None:
        self.log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        self.prozess = subprocess.Popen(
            list(befehl), cwd=cwd, env=env, stdout=self.log,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        self.pid = self.prozess.pid
        self.start = startzeit(self.pid)
        if self.start is None:
            raise RuntimeError(f"Startzeit von PID {self.pid} nicht lesbar")

    def lebt(self) -> bool:
        return self.prozess.poll() is None

    def ausgabe(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return self.log.read()[-4000:]

    def stop(self) -> None:
        if not self.lebt():
            return
        ist_start = startzeit(self.pid)
        if ist_start != self.start:
            raise RuntimeError(
                f"PID {self.pid} vor kill wiederverwendet: erwartet {self.start!r}, ist {ist_start!r}"
            )
        if os.getpgid(self.pid) != self.pid:
            raise RuntimeError(f"PID {self.pid} ist nicht Leiter der eigenen Prozessgruppe")
        os.killpg(self.pid, signal.SIGTERM)
        try:
            self.prozess.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            ist_start = startzeit(self.pid)
            if ist_start != self.start:
                raise RuntimeError(f"PID {self.pid} vor SIGKILL wiederverwendet")
            os.killpg(self.pid, signal.SIGKILL)
            self.prozess.wait(timeout=5.0)


class Sitzungsdienste:
    """Stoppt nur aktive Units, deren ExecStart einem gepinnten Startweg entspricht."""

    def __init__(self) -> None:
        self.gestoppt: list[str] = []

    @staticmethod
    def _systemctl(*argumente: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["systemctl", "--user", *argumente], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30.0,
        )

    def stoppen(self) -> None:
        liste = self._systemctl(
            "list-units", "--type=service", "--state=active", "--no-legend", "--plain"
        )
        if liste.returncode != 0:
            raise RuntimeError(f"aktive Sitzungsdienste nicht erfassbar: {liste.stdout[-1000:]}")
        kandidaten: list[str] = []
        for zeile in liste.stdout.splitlines():
            teile = zeile.split()
            if not teile:
                continue
            unit = teile[0]
            details = self._systemctl("show", "-p", "ExecStart", "--value", unit)
            if details.returncode != 0:
                raise RuntimeError(f"ExecStart von {unit} nicht erfassbar: {details.stdout[-1000:]}")
            if "daimon.hub.daemon" in details.stdout or "daimon-face" in details.stdout:
                kandidaten.append(unit)
        for unit in kandidaten:
            # Schon vor dem mutierenden Aufruf merken: Auch ein Timeout kann
            # bedeuten, dass systemd die Anforderung bereits angenommen hat.
            self.gestoppt.append(unit)
            gestoppt = self._systemctl("stop", unit)
            if gestoppt.returncode != 0:
                raise RuntimeError(f"Sitzungsdienst {unit} nicht stoppbar: {gestoppt.stdout[-1000:]}")

    def wiederherstellen(self) -> list[str]:
        fehler: list[str] = []
        for unit in self.gestoppt:
            try:
                lauf = self._systemctl("start", unit)
                if lauf.returncode != 0:
                    fehler.append(f"{unit}: {lauf.stdout[-500:]}")
            except Exception as exc:
                fehler.append(f"{unit}: {exc!r}")
        self.gestoppt.clear()
        return fehler


class LiveSystem:
    def __init__(self, pruefling: Path) -> None:
        self.pruefling = pruefling
        self.temp = tempfile.TemporaryDirectory(prefix="t314-")
        self.xdg = Path(self.temp.name)
        self.rt = self.xdg / "daimon"
        self.rt.mkdir(mode=0o700)
        self.env = os.environ.copy()
        self.env["XDG_RUNTIME_DIR"] = str(self.xdg)
        self.dienste = Sitzungsdienste()
        self.hub: Prozessgruppe | None = None
        self.face: Prozessgruppe | None = None
        self.aktive_marke: object | None = None
        venv_python = pruefling / ".venv/bin/python"
        self.python = venv_python if venv_python.is_file() and os.access(venv_python, os.X_OK) else Path(
            subprocess.check_output(["bash", "-lc", "command -v python3"], text=True).strip()
        )
        self.python_fallback = self.python != venv_python

    def state(self) -> dict:
        return json_zeile_lesen(self.rt / "state.sock")

    def face_diag(self) -> dict:
        return json_zeile_lesen(self.rt / "face-diag.sock")

    def warte_state(self, state: str) -> dict:
        return warten_auf(self.state, lambda x: x.get("voice", {}).get("state") == state,
                         f"Hub voice.state={state}")

    def warte_face(self, state: str, *, zaehler_groesser: int | None = None) -> dict:
        def passt(objekt: dict) -> bool:
            if objekt.get("voice_state") != state:
                return False
            if zaehler_groesser is not None:
                return objekt.get("voice_indikator_gezeichnet", -1) > zaehler_groesser
            return True
        return warten_auf(self.face_diag, passt, f"Face voice_state={state}")

    def sende(self, socketname: str, daten: bytes) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(BEOBACHTUNG_S)
            sock.connect(str(self.rt / socketname))
            sock.sendall(daten)

    def tts(self, **felder) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(BEOBACHTUNG_S)
            sock.connect(str(self.rt / "tts.sock"))
            sock.sendall(tts_anfrage(**felder))
            zeile = sock.makefile("rb").readline()
        if not zeile:
            raise RuntimeError(f"tts {felder.get('art')}: Antwort fehlt")
        antwort = json.loads(zeile)
        if not isinstance(antwort, dict):
            raise RuntimeError(f"tts {felder.get('art')}: Antwort ist kein Objekt")
        return antwort

    def tts_beginnt(
        self, text: str | None, *, kanal: str, anlass: str | None = None,
        abkuehlung_abwarten: bool = True,
    ) -> tuple[object, dict]:
        if kanal not in {"ungefragt", "reaktion", "rueckfrage"}:
            raise ValueError(f"ungepinnter TTS-Kanal: {kanal!r}")
        if kanal == "ungefragt":
            erlaubte_anlaesse = {
                "begruessung", "lange_sitzung", "build_fertig", "tests_gruen",
                "tests_rot", "leerlauf", "steht_am_bildschirm",
            }
            if text is not None:
                raise ValueError("ungefragt akzeptiert keinen freien Text")
            if anlass not in erlaubte_anlaesse:
                raise ValueError(f"ungepinnter ungefragt-Anlass: {anlass!r}")
            freigabe_felder = {"art": "freigabe", "kanal": kanal, "anlass": anlass}
        else:
            if text is None or anlass is not None:
                raise ValueError(f"{kanal} verlangt freien Text und keinen Anlass")
            freigabe_felder = {"art": "freigabe", "kanal": kanal, "text": text}
        freigabe = self.tts(**freigabe_felder)
        abkuehlungen = 0
        while (
            "marke" not in freigabe
            and freigabe.get("grund") == "abkuehlung"
            and abkuehlung_abwarten
        ):
            abkuehlungen += 1
            if abkuehlungen > 3:
                raise RuntimeError(f"tts freigabe: Abkuehlung endet trotz rest_s nicht: {freigabe!r}")
            rest = freigabe.get("rest_s")
            if isinstance(rest, bool) or not isinstance(rest, (int, float)):
                raise RuntimeError(f"tts freigabe: rest_s ist keine Zahl: {freigabe!r}")
            if not math.isfinite(float(rest)) or rest < 0:
                raise RuntimeError(f"tts freigabe: rest_s ist unzulaessig: {freigabe!r}")
            print(f"TTS-Abkuehlung {kanal}: gemeldete {float(rest):.3f} s werden abgewartet")
            time.sleep(float(rest) + 0.05)
            freigabe = self.tts(**freigabe_felder)
        if "ok" not in freigabe:
            raise RuntimeError(f"tts freigabe: Antwortfeld ok fehlt: {freigabe!r}")
        if "marke" not in freigabe:
            raise RuntimeError(f"tts freigabe: Einmal-Marke fehlt: {freigabe!r}")
        marke = freigabe["marke"]
        antwort = self.tts(art="beginnt", marke=marke)
        if antwort.get("ok") is not True:
            raise RuntimeError(f"tts beginnt wurde abgewiesen: {antwort!r}")
        self.aktive_marke = marke
        return marke, antwort

    def tts_gesprochen(self, marke: object) -> dict:
        antwort = self.tts(art="gesprochen", marke=marke)
        if antwort.get("ok") is True and self.aktive_marke == marke:
            self.aktive_marke = None
        return antwort

    def normalisiere_idle(self) -> None:
        self.sende("auth.sock", ereignis("ptt", {"an": False}))
        snapshot = warten_auf(
            self.state,
            lambda x: x.get("voice", {}).get("state") != "listening",
            "PTT-aus vor Normalisierung",
        )
        state = snapshot.get("voice", {}).get("state")
        if state == "speaking" and self.aktive_marke is not None:
            self.tts_gesprochen(self.aktive_marke)
        elif state == "processing":
            marke, _ = self.tts_beginnt(
                "t314 normalisierung", kanal="rueckfrage", abkuehlung_abwarten=True,
            )
            self.tts_gesprochen(marke)
        elif state != "idle":
            raise RuntimeError(f"Normalisierung kennt Zustand {state!r} nicht")
        self.warte_state("idle")

    def starten(self) -> None:
        build_env = os.environ.copy()
        rc, ausgabe = lauf_mit_aufraeumen(
            ["cargo", "build", "--release"], self.pruefling / "face", build_env, timeout_s=600.0
        )
        if rc != 0:
            raise RuntimeError(f"cargo build --release Exit {rc}: {ausgabe}")
        face_bin = self.pruefling / "face/target/release/daimon-face"
        if not face_bin.is_file():
            raise RuntimeError(f"Face-Binary fehlt nach Build: {face_bin}")

        self.dienste.stoppen()
        self.hub = Prozessgruppe(
            [str(self.python), "-m", "daimon.hub.daemon", "--runtime-dir", str(self.rt)],
            self.pruefling, self.env,
        )
        warten_auf(self.state, lambda _: True, "Hub-Bereitschaft")

        # Das Face braucht den echten Sitzungs-Runtime-Pfad fuer wayland-0;
        # seine Daimon-Sockets sind vollstaendig ueber Argumente festgelegt.
        face_env = os.environ.copy()
        face_env["DAIMON_MAX_SECS"] = "180"
        self.face = Prozessgruppe(
            [str(face_bin),
             "--hub-socket", str(self.rt / "events.sock"),
             "--diag-socket", str(self.rt / "face-diag.sock"),
             "--control-socket", str(self.rt / "face-control.sock"),
             "--pet-manifest", str(self.pruefling / "face/assets/pet.json")],
            self.pruefling, face_env,
        )
        warten_auf(self.face_diag, lambda _: True, "Face-Bereitschaft")

    def schliessen(self) -> list[str]:
        fehler: list[str] = []
        for name, prozess in (("Face", self.face), ("Hub", self.hub)):
            if prozess is None:
                continue
            try:
                prozess.stop()
            except Exception as exc:
                fehler.append(f"{name}-Aufraeumen: {exc!r}")
        fehler.extend(f"Sitzungsdienst-Wiederherstellung: {x}" for x in self.dienste.wiederherstellen())
        try:
            self.temp.cleanup()
        except Exception as exc:
            fehler.append(f"Runtime-Verzeichnis-Aufraeumen: {exc!r}")
        return fehler


def verbindung_wird_verworfen(pfad: Path, daten: bytes) -> bool:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        fehler = sock.connect_ex(str(pfad))
        if fehler not in (0, errno.EINPROGRESS):
            raise OSError(fehler, os.strerror(fehler))
        _, schreibbar, _ = select.select([], [sock], [], BEOBACHTUNG_S)
        if not schreibbar:
            raise TimeoutError(f"Verbindung zu {pfad.name} nicht schreibbar")
        verbindungsfehler = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if verbindungsfehler:
            raise OSError(verbindungsfehler, os.strerror(verbindungsfehler))
        sock.setblocking(True)
        sock.settimeout(0.1)
        sock.sendall(daten)
        ende = time.monotonic() + BEOBACHTUNG_S
        while time.monotonic() < ende:
            lesbar, _, _ = select.select([sock], [], [], 0.05)
            if lesbar:
                try:
                    if sock.recv(1) == b"":
                        return True
                except ConnectionResetError:
                    return True
            try:
                sock.sendall(daten)
            except OSError as exc:
                if exc.errno in (errno.EPIPE, errno.ECONNRESET):
                    return True
                raise
        return False
    finally:
        sock.close()


def pruefe_k4(system: LiveSystem, bericht: Bericht) -> None:
    try:
        system.normalisiere_idle()
        system.sende("auth.sock", ereignis("ptt", {"an": True}))
        an = system.warte_state("listening")
        bericht.pruefe("K4", an["voice"]["listening"] is True, "PTT an im echten Schnappschuss")
        system.sende("auth.sock", ereignis("ptt", {"an": False}))
        aus = system.warte_state("idle")
        bericht.pruefe("K4", aus["voice"]["listening"] is False, "PTT aus im echten Schnappschuss")
        bericht.pruefe("K4", isinstance(system.state().get("rev"), int),
                       "Positivkontrolle: Hub beantwortet danach state.sock")
    except Exception as exc:
        bericht.fehler("K4", f"echte Socketmessung fehlgeschlagen: {exc!r}")


def pruefe_k5(system: LiveSystem, bericht: Bericht) -> None:
    try:
        system.normalisiere_idle()
        # Positivkontrolle der Wirkungsmessung und der offenen erlaubten Verbindung.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as erlaubt:
            erlaubt.settimeout(BEOBACHTUNG_S)
            erlaubt.connect(str(system.rt / "auth.sock"))
            erlaubt.sendall(ereignis("ptt", {"an": True}))
            system.warte_state("listening")
            erlaubt.sendall(ereignis("ptt", {"an": False}))
            system.warte_state("idle")
        bericht.pruefe("K5", True, "Positivkontrolle: erlaubtes auth-ptt wirkt und Verbindung traegt mehrere Zeilen")

        for socketname in ("face.sock", "ears.sock", "hookbridge.sock"):
            verworfen = verbindung_wird_verworfen(system.rt / socketname, ereignis("ptt", {"an": True}))
            bericht.pruefe("K5", verworfen, f"ptt auf {socketname} schliesst die Verbindung")
            snapshot = system.state()
            bericht.pruefe("K5", snapshot["voice"]["state"] == "idle",
                           f"ptt auf {socketname} aendert den Zustand nicht")
            bericht.pruefe("K5", isinstance(snapshot.get("rev"), int),
                           f"Hub lebt nach Rollenbruch auf {socketname}")
    except Exception as exc:
        bericht.fehler("K5", f"Rollenmessung fehlgeschlagen: {exc!r}")


def pruefe_k6_live(system: LiveSystem, bericht: Bericht) -> None:
    try:
        system.normalisiere_idle()
        system.sende("ears.sock", ereignis("utterance", {"text": "t314 fristweg"}))
        bericht.pruefe("K6", system.warte_state("processing")["voice"]["denkt"] is True,
                       "utterance setzt processing am echten Hub")
        marke, _ = system.tts_beginnt(
            "t314 fristweg antwort", kanal="reaktion", abkuehlung_abwarten=True,
        )
        snapshot = system.warte_state("speaking")
        bericht.pruefe("K6", snapshot["voice"]["denkt"] is False,
                       "tts beginnt beendet processing am echten Hub")
        system.tts_gesprochen(marke)
        system.warte_state("idle")
    except Exception as exc:
        bericht.fehler("K6", f"echter utterance-/tts-Weg fehlgeschlagen: {exc!r}")


def volle_runde(
    system: LiveSystem, kriterium: str, bericht: Bericht, *, kanal: str,
) -> None:
    system.normalisiere_idle()
    system.sende("auth.sock", ereignis("ptt", {"an": True}))
    bericht.pruefe(kriterium, system.warte_state("listening")["voice"]["state"] == "listening", "PTT an")
    system.sende("ears.sock", ereignis("utterance", {"text": "t314 pruefung"}))
    system.sende("auth.sock", ereignis("ptt", {"an": False}))
    bericht.pruefe(kriterium, system.warte_state("processing")["voice"]["state"] == "processing", "Aeusserung eingegangen")
    marke, antwort = system.tts_beginnt(
        "t314 rundenantwort", kanal=kanal, abkuehlung_abwarten=True,
    )
    bericht.pruefe(kriterium, "ok" in antwort, "tts beginnt lieferte das gepinnte Antwortfeld")
    bericht.pruefe(kriterium, system.warte_state("speaking")["voice"]["state"] == "speaking", "Sprechen beginnt")
    antwort = system.tts_gesprochen(marke)
    bericht.pruefe(kriterium, "ok" in antwort, "tts gesprochen lieferte das gepinnte Antwortfeld")
    bericht.pruefe(kriterium, system.warte_state("idle")["voice"]["state"] == "idle", "Runde faellt auf idle")


def pruefe_k7(system: LiveSystem, bericht: Bericht) -> None:
    try:
        volle_runde(system, "K7", bericht, kanal="rueckfrage")
    except Exception as exc:
        bericht.fehler("K7", f"vollstaendige Runde fehlgeschlagen: {exc!r}")


def mood_bewegen(system: LiveSystem, bericht: Bericht) -> tuple[dict, dict]:
    vorher = system.state()
    system.sende("hookbridge.sock", ereignis("hook", {
        "hook_event_name": "Notification", "session_id": "t314-1",
        "notification_type": "permission_prompt", "message": "probe",
    }))
    nachher = warten_auf(system.state, lambda x: x.get("mood") != vorher.get("mood"), "Mood-Aenderung")
    bericht.pruefe("K8", nachher.get("mood") != vorher.get("mood"),
                   "Positivkontrolle: Schnappschussapparat sieht eine Mood-Aenderung")
    return vorher, nachher


def pruefe_k8(system: LiveSystem, bericht: Bericht) -> None:
    try:
        system.normalisiere_idle()
        diag_vorher = system.face_diag()
        _, hub_basis = mood_bewegen(system, bericht)
        basis = warten_auf(
            system.face_diag,
            lambda x: x.get("mood") == hub_basis.get("mood"),
            "Mood-Positivkontrolle im Face",
        )
        if basis.get("sprite") == diag_vorher.get("sprite"):
            bericht.fehler(
                "K8",
                "CONTRACT GAP: Der Hook garantiert eine Mood-Aenderung, aber keinen davon "
                "verschiedenen Diagnose-Sprite; die Abwesenheitsmessung fuer sprite hat "
                "keine positive Kontrolle",
            )
            return
        bericht.pruefe("K8", True, "Positivkontrolle: Diagnoseapparat sieht einen anderen Sprite")
        mood, sprite = basis.get("mood"), basis.get("sprite")

        system.sende("auth.sock", ereignis("ptt", {"an": True}))
        system.warte_state("listening")
        zustandsdiag = [system.warte_face("listening")]
        system.sende("auth.sock", ereignis("ptt", {"an": False}))
        system.sende("ears.sock", ereignis("utterance", {"text": "t314 mood"}))
        system.warte_state("processing")
        zustandsdiag.append(system.warte_face("processing"))
        marke, _ = system.tts_beginnt(
            None, kanal="ungefragt", anlass="tests_gruen", abkuehlung_abwarten=True,
        )
        system.warte_state("speaking")
        zustandsdiag.append(system.warte_face("speaking"))
        system.tts_gesprochen(marke)
        system.warte_state("idle")
        zustandsdiag.append(system.warte_face("idle"))
        for name, diag in zip(("listening", "processing", "speaking", "idle"), zustandsdiag):
            bericht.pruefe("K8", diag.get("mood") == mood, f"Mood bleibt bei {name}")
            bericht.pruefe("K8", diag.get("sprite") == sprite, f"Sprite bleibt bei {name}")
    except Exception as exc:
        bericht.fehler("K8", f"Mood-/Sprite-Messung fehlgeschlagen: {exc!r}")


def pruefe_k9(system: LiveSystem, bericht: Bericht) -> None:
    try:
        system.normalisiere_idle()
        idle = system.warte_face("idle")
        zaehler = idle["voice_indikator_gezeichnet"]

        system.sende("auth.sock", ereignis("ptt", {"an": True}))
        system.warte_state("listening")
        listening = system.warte_face("listening", zaehler_groesser=zaehler)
        bericht.pruefe("K9", listening["voice_indikator_gezeichnet"] > zaehler,
                       "listening wurde wirklich in den Sprite-Buffer gezeichnet")
        zaehler = listening["voice_indikator_gezeichnet"]

        system.sende("auth.sock", ereignis("ptt", {"an": False}))
        system.warte_face("idle")
        system.sende("ears.sock", ereignis("utterance", {"text": "t314 indikator"}))
        system.warte_state("processing")
        processing = system.warte_face("processing", zaehler_groesser=zaehler)
        bericht.pruefe("K9", processing["voice_indikator_gezeichnet"] > zaehler,
                       "processing wurde wirklich in den Sprite-Buffer gezeichnet")
        zaehler = processing["voice_indikator_gezeichnet"]

        marke, _ = system.tts_beginnt(
            "t314 indikator antwort", kanal="rueckfrage", abkuehlung_abwarten=True,
        )
        system.warte_state("speaking")
        speaking = system.warte_face("speaking", zaehler_groesser=zaehler)
        bericht.pruefe("K9", speaking["voice_indikator_gezeichnet"] > zaehler,
                       "speaking wurde wirklich in den Sprite-Buffer gezeichnet")
        system.tts_gesprochen(marke)
        idle = system.warte_face("idle")
        zaehler_idle = idle["voice_indikator_gezeichnet"]
        time.sleep(BEOBACHTUNG_S)
        idle_spaeter = system.face_diag()
        bericht.pruefe("K9", idle_spaeter.get("voice_state") == "idle", "Face bleibt im Beobachtungsfenster idle")
        bericht.pruefe("K9", idle_spaeter.get("voice_indikator_gezeichnet") == zaehler_idle,
                       "Bei idle wird zwei Sekunden lang kein Indikator gezeichnet")
    except Exception as exc:
        bericht.fehler("K9", f"Face-Zustands-/Zeichnungsmessung fehlgeschlagen: {exc!r}")


def pruefe_k10(system: LiveSystem, bericht: Bericht, evidenz: Path | None) -> None:
    werte: list[float] = []
    try:
        for nummer in range(20):
            system.sende("auth.sock", ereignis("ptt", {"an": False}))
            system.warte_face("idle")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(BEOBACHTUNG_S)
                sock.connect(str(system.rt / "auth.sock"))
                beginn = time.monotonic_ns()
                sock.sendall(ereignis("ptt", {"an": True}))
            system.warte_face("listening")
            werte.append((time.monotonic_ns() - beginn) / 1_000_000.0)
            bericht.pruefe("K10", True, f"Ausloesung {nummer + 1}: {werte[-1]:.3f} ms")
        p95 = perzentil(werte, 0.95)
        bericht.pruefe("K10", len(werte) == 20, "Positivkontrolle: genau 20 reale Messwerte")
        bericht.pruefe("K10", p95 < 200.0, f"p95={p95:.3f} ms muss kleiner als 200 ms sein")
    except Exception as exc:
        bericht.fehler("K10", f"Latenzmessung nach {len(werte)} Werten fehlgeschlagen: {exc!r}")
    finally:
        if evidenz is not None:
            schreibe_latenz(evidenz, werte)
    try:
        system.sende("auth.sock", ereignis("ptt", {"an": False}))
        system.warte_face("idle")
    except Exception:
        pass


def pruefe_k11(system: LiveSystem, bericht: Bericht) -> None:
    try:
        system.normalisiere_idle()
        basis = system.face_diag()
        system.sende("auth.sock", ereignis("ptt", {"an": True}))
        system.warte_state("listening")
        vorher = system.warte_face(
            "listening", zaehler_groesser=basis.get("voice_indikator_gezeichnet", -1)
        )
        bericht.pruefe("K11", vorher["voice_indikator_gezeichnet"] >= 1,
                       "Positivkontrolle: vor Hub-Tod wurde ein Indikator gezeichnet")
        if system.hub is None:
            raise RuntimeError("Hub-Prozessgruppe fehlt")
        system.hub.stop()
        nachher = system.warte_face("idle")
        zaehler = nachher["voice_indikator_gezeichnet"]
        time.sleep(BEOBACHTUNG_S)
        spaeter = system.face_diag()
        bericht.pruefe("K11", spaeter.get("voice_state") == "idle", "Face bleibt nach Hub-Tod idle")
        bericht.pruefe("K11", spaeter.get("voice_indikator_gezeichnet") == zaehler,
                       "Nach Hub-Tod wird zwei Sekunden lang kein Indikator gezeichnet")
        bericht.pruefe("K11", system.face is not None and system.face.lebt(), "Face lebt nach Hub-Tod weiter")
    except Exception as exc:
        bericht.fehler("K11", f"Hub-Tod-Messung fehlgeschlagen: {exc!r}")


def pruefe_live_kriterien(
    bericht: Bericht, pruefling: Path, verifier_repo: Path, fixture_lauf: bool,
) -> None:
    prozessmuster = re.escape(str(pruefling.resolve()))
    prozesse_vorher = prozesszahl(prozessmuster)
    system = LiveSystem(pruefling)
    try:
        system.starten()
        if system.python_fallback:
            print(f"HINWEIS: .venv/bin/python fehlt; Hub laeuft mit {system.python}")
        pruefe_k4(system, bericht)
        pruefe_k5(system, bericht)
        pruefe_k6_live(system, bericht)
        pruefe_k7(system, bericht)
        pruefe_k8(system, bericht)
        pruefe_k9(system, bericht)
        evidenz = None if fixture_lauf else verifier_repo / "tests/evidence/T-3.14-ptt-latenz.json"
        pruefe_k10(system, bericht, evidenz)
        pruefe_k11(system, bericht)
    except Exception as exc:
        for kriterium in ("K4", "K5", "K6", "K7", "K8", "K9", "K10", "K11"):
            bericht.fehler(kriterium, f"Live-Pruefstand nicht startbar: {exc!r}")
    finally:
        for fehler in system.schliessen():
            bericht.fehler("K11", fehler)
        prozesse_nachher = prozesszahl(prozessmuster)
        ende = time.monotonic() + BEOBACHTUNG_S
        while prozesse_nachher != prozesse_vorher and time.monotonic() < ende:
            time.sleep(0.05)
            prozesse_nachher = prozesszahl(prozessmuster)
        bericht.pruefe(
            "K11", prozesse_nachher == prozesse_vorher,
            f"Live-Prozesszaehlung vor/nach Aufraeumen: {prozesse_vorher}/{prozesse_nachher}",
        )


def prozesszahl(muster: str) -> int:
    lauf = subprocess.run(
        ["pgrep", "-cf", muster], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if lauf.returncode not in (0, 1):
        raise RuntimeError(f"pgrep -cf fehlgeschlagen: {lauf.stderr[-1000:]}")
    ausgabe = lauf.stdout.strip()
    return int(ausgabe or "0")


def lauf_mit_aufraeumen(
    befehl: Sequence[str], cwd: Path, env: dict[str, str], timeout_s: float = 600.0
) -> tuple[int, str]:
    prozess = subprocess.Popen(
        list(befehl), cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    start = startzeit(prozess.pid)
    if start is None:
        return 125, "Startzeit nicht lesbar; aus Sicherheitsgruenden kein kill ausgefuehrt"
    try:
        ausgabe, _ = prozess.communicate(timeout=timeout_s)
        rc = int(prozess.returncode)
    except subprocess.TimeoutExpired:
        if startzeit(prozess.pid) != start:
            return 125, "PID vor SIGTERM wiederverwendet; kein kill ausgefuehrt"
        os.killpg(prozess.pid, signal.SIGTERM)
        try:
            ausgabe, _ = prozess.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            if startzeit(prozess.pid) != start:
                return 125, "PID vor SIGKILL wiederverwendet; kein kill ausgefuehrt"
            os.killpg(prozess.pid, signal.SIGKILL)
            ausgabe, _ = prozess.communicate()
        rc = 124
    return rc, ausgabe[-4000:]


def pruefe_prozesszaehler(pruefling: Path, bericht: Bericht) -> None:
    marker = str(pruefling.resolve() / ".t314-prozess-positivkontrolle")
    muster = re.escape(marker)
    vorher = prozesszahl(muster)
    prozess = Prozessgruppe(
        [sys.executable, "-c", "import time; time.sleep(60)", marker],
        Path.cwd(), os.environ.copy(),
    )
    try:
        for _ in range(20):
            waehrend = prozesszahl(muster)
            if waehrend > vorher:
                break
            time.sleep(0.01)
        bericht.pruefe("K13", waehrend > vorher,
                       "Positivkontrolle: Prozesszaehler erkennt einen markierten Prozess")
    finally:
        prozess.stop()


def k13_element_mit_nachlauf(
    name: str, befehl: Sequence[str], cwd: Path, env: dict[str, str],
    muster: str, bericht: Bericht,
) -> tuple[int, str]:
    vorher = prozesszahl(muster)
    rc_erstlauf, ausgabe_erstlauf = lauf_mit_aufraeumen(befehl, cwd, env)
    nachher = prozesszahl(muster)
    bericht.pruefe(
        "K13", nachher == vorher,
        f"Prozesszaehlung {name}, Erstlauf: vorher {vorher}, nachher {nachher}",
    )
    if rc_erstlauf == 0:
        return rc_erstlauf, ausgabe_erstlauf

    vorher_nachlauf = nachher
    rc_nachlauf, ausgabe_nachlauf = lauf_mit_aufraeumen(befehl, cwd, env)
    nachher_nachlauf = prozesszahl(muster)
    bericht.pruefe(
        "K13", nachher_nachlauf == vorher_nachlauf,
        f"Prozesszaehlung {name}, Nachlauf: "
        f"vorher {vorher_nachlauf}, nachher {nachher_nachlauf}",
    )
    print(
        f"K13: {name} Erstlauf Exit {rc_erstlauf}, erlaubter Nachlauf Exit {rc_nachlauf}; "
        "massgeblich ist der Nachlauf"
    )
    ausgabe = ausgabe_erstlauf + "\nNACHLAUF:\n" + ausgabe_nachlauf
    return rc_nachlauf, ausgabe


def pruefe_k13(pruefling: Path, verifier_repo: Path, bericht: Bericht) -> None:
    pruefe_prozesszaehler(pruefling, bericht)
    muster = re.escape(str(pruefling.resolve()))
    env = os.environ.copy()
    env["DAIMON_FIXTURE"] = str(pruefling)

    for relativ in REGRESSIONS_VERIFIZIERER:
        skript = verifier_repo / relativ
        if not skript.is_file():
            bericht.fehler("K13", f"eingefrorener Verifizierer fehlt: {relativ}")
            continue
        name = Path(relativ).stem
        rc, ausgabe = k13_element_mit_nachlauf(
            name, [str(skript)], verifier_repo, env, muster, bericht,
        )
        bericht.pruefe("K13", rc == 0, f"{relativ} Exit {rc}: {ausgabe}")

    rc, ausgabe = k13_element_mit_nachlauf(
        "pytest", [sys.executable, "-m", "pytest"], pruefling, env, muster, bericht,
    )
    bericht.pruefe("K13", rc == 0, f"pytest Exit {rc}: {ausgabe}")
    rc, ausgabe = k13_element_mit_nachlauf(
        "cargo test -p face", ["cargo", "test", "-p", "face"],
        pruefling / "face", env, muster, bericht,
    )
    bericht.pruefe("K13", rc == 0, f"cargo test -p face Exit {rc}: {ausgabe}")


def pruefe_k12(bericht: Bericht) -> None:
    # K12 ist eine Eigenschaft der meta.sh-Matrix, nicht eines einzelnen
    # Prueflingslaufs. Hier wird die endliche Zuordnung separat inventarisiert;
    # die eigentliche Rotwirkung prueft meta.sh spaeter an jeder Fixture.
    erwartet = {
        "zustand-direkt-setzbar": "K3",
        "sprechen-schlaegt-zuhoeren": "K2",
        "ptt-von-jedem-produzenten": "K5",
        "denken-ohne-frist": "K6",
        "face-zeigt-eigene-rechnung": "K9/K11",
        "indikator-ersetzt-sprite": "K8",
    }
    for name, kriterium in erwartet.items():
        bericht.pruefe("K12", MUTANTEN_GRENZEN.get(name) == kriterium,
                       f"Meta-Zuordnung {name} -> {kriterium}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pruefling", type=Path)
    args = parser.parse_args(argv)
    pruefling = args.pruefling.resolve()
    verifier_repo = Path(__file__).resolve().parents[2]
    fixture_lauf = "DAIMON_FIXTURE" in os.environ
    bericht = Bericht()

    try:
        modul = lade_state_modul(pruefling)
    except Exception as exc:
        for kriterium in ("K1", "K2", "K3", "K6"):
            bericht.fehler(kriterium, f"daimon.hub.state nicht ladbar: {exc!r}")
    else:
        pruefe_k1_k2(modul, bericht)
        pruefe_k3(modul, bericht)
        pruefe_k6(modul, bericht)

    pruefe_live_kriterien(bericht, pruefling, verifier_repo, fixture_lauf)
    pruefe_k12(bericht)
    pruefe_k13(pruefling, verifier_repo, bericht)
    return bericht.bilanz()


if __name__ == "__main__":
    raise SystemExit(main())
