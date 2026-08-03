"""T-0.5 — Konfiguration aus XDG-Pfaden, mit mitgelieferten Vorgaben.

Kein globaler Zustand. `load()` gibt ein Objekt zurueck, und wer es braucht,
bekommt es gereicht. Ein Modul-Singleton waere bequemer und genau deshalb
falsch: Tests muessten es zuruecksetzen, und zwei Prozesse im selben
Interpreter -- etwa im Test -- wuerden sich gegenseitig die Werte umschreiben.

Eine fehlende Konfigurationsdatei ist kein Fehler. Eine fehlerhafte schon, und
dann nennt die Meldung die Zeile: eine Fehlermeldung ohne Ort zwingt zum Raten.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP = "daimon"

VORGABEN: dict[str, Any] = {
    "hub": {
        "socket_dir": None,      # None -> $XDG_RUNTIME_DIR/daimon
        "state_ttl_s": 3600,
        # T-2.7: die einzigen Units, die ueber `wahrnehmung_aus` gestoppt
        # werden koennen. Die Nachricht traegt nur den Schluessel links --
        # der Unit-Name rechts kommt ausschliesslich von hier. Sonst koennte
        # das Overlay den Hub, den Auth-Agenten oder jede Nutzer-Unit
        # stoppen. Ein Schluessel, den der Hub nicht kennt, wird verworfen.
        "wahrnehmung_units": {
            "ears": "daimon-ears.service",
            "eyes": "daimon-eyes.service",
        },
    },
    "ears": {
        # T-3.2: asymmetrisch, und das ist der Punkt. `ende` < `einsatz` plus
        # Nachlauf, sonst werden leise Endsilben abgeschnitten. Begruendung und
        # Grenzen in daimon/ears/vad.py.
        "vad": {"einsatz": 0.5, "ende": 0.35, "nachlauf_ms": 400},
        # T-3.3: Vorlauf, der beim Ausloesen aus dem Ringpuffer mitgegeben
        # wird. Plan: 1,0--1,5 s, Vorgabe in der Mitte. Die Ringgroesse
        # selbst (20 s) ist keine Einstellung -- siehe daimon/ears/ring.py.
        "ring": {"vorlauf_s": 1.25},
    },
    # T-3.7: das GPU-Gate. `sperrfrist_s` ist die Frist, nach der eine
    # Ladesperre von selbst verfaellt -- sie faengt einen beim Laden
    # gestorbenen Worker ab und darf deshalb nicht knapp am Kaltstart liegen.
    # `reserve_mib` bleibt nach dem Laden frei; ohne Reserve gaebe die Pruefung
    # genau dann gruen, wenn danach nichts mehr uebrig ist. `ladedauer_s` ist
    # der Platzhalter-Ladevorgang und faellt mit T-3.8 weg.
    # `modelle.<name>` ist der VERMUTETE Bedarf in MiB, keine Messung.
    "gpu": {
        "sperrfrist_s": 120.0,
        "reserve_mib": 1024,
        "idle_s": 60.0,
        "ladedauer_s": 0.5,
        "modelle": {"stt": 2600},
    },
    # T-3.9: die Stimme. `threads` ist der Kalibrierknopf. Gemessen ueber die
    # ganze Kette, 20 Aeusserungen: 2 Threads p95 316 ms (verfehlt das
    # Kriterium), 4 Threads p95 187 ms, 8 Threads p95 148 ms. Acht ist die
    # Vorgabe, weil 13 ms Marge beim naechsten Hintergrund-Build weg sind.
    # Weniger Kerne im Rechner: Wert nach unten.
    # `freigabefrist_s` ist die Gueltigkeit einer Sprechfreigabe im Hub, nicht
    # die Wiedergabedauer. `abkuehlung` ist Design §8.4, persistiert.
    "tts": {
        "modell_dir": "spikes/nvidia-voice/models/vits-piper-de_DE-thorsten-high",
        "threads": 8,
        "freigabefrist_s": 30.0,
        "abkuehlung": {"ungefragt": 20.0, "reaktion": 10.0, "rueckfrage": 3.0},
    },
    "face": {
        "poll_ms": 250,
        "palette": {"idle": "#3a2418", "active": "#ff6b1a", "alert": "#ffd24a"},
    },
    # `voice` gehoert eigentlich in die Persona-Datei (Design §10.1), und T-3.10
    # baut deren Lader. Bis dahin steht die Stimme hier -- gelesen, nicht im
    # Code verdrahtet, damit T-3.10 nur die Quelle austauscht und nicht den
    # Aufrufer.
    "persona": {"name": "Ember", "voice": "de_DE-thorsten-high"},
    "logging": {"identifier": "daimon"},
}


class ConfigError(ValueError):
    """Fehlerhafte Konfiguration. Nennt Datei und Zeile."""


@dataclass(frozen=True)
class Config:
    data: dict[str, Any] = field(default_factory=dict)
    config_dir: Path = field(default_factory=Path)
    state_dir: Path = field(default_factory=Path)
    runtime_dir: Path = field(default_factory=Path)
    quelle: Path | None = None

    def get(self, pfad: str, fallback: Any = None) -> Any:
        """`cfg.get("face.poll_ms")` -- punktierter Pfad, kein Suchen in dicts."""
        knoten: Any = self.data
        for teil in pfad.split("."):
            if not isinstance(knoten, dict) or teil not in knoten:
                return fallback
            knoten = knoten[teil]
        return knoten


def _xdg(name: str, fallback: Path) -> Path:
    wert = os.environ.get(name)
    return Path(wert) if wert else fallback


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / APP


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP


def runtime_dir() -> Path:
    # Ohne XDG_RUNTIME_DIR gibt es kein tmpfs mit den richtigen Rechten. Dann
    # /tmp zu nehmen waere eine stille Verschlechterung -- der Pfad landet auf
    # der Platte und ist fuer andere Nutzer sichtbar.
    wert = os.environ.get("XDG_RUNTIME_DIR")
    if not wert:
        raise ConfigError(
            "XDG_RUNTIME_DIR ist nicht gesetzt. Sockets brauchen ein "
            "nutzereigenes tmpfs; /tmp waere fuer andere Nutzer einsehbar."
        )
    return Path(wert) / APP


def _mische(vorgabe: dict, drueber: dict) -> dict:
    """Rekursiv, damit eine Teilangabe nicht den ganzen Abschnitt ersetzt."""
    out = dict(vorgabe)
    for k, v in drueber.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _mische(out[k], v)
        else:
            out[k] = v
    return out


def load(*, config_path: Path | None = None, make_dirs: bool = True) -> Config:
    cfg_dir = config_path.parent if config_path else config_dir()
    datei = config_path or (cfg_dir / f"{APP}.toml")

    data = dict(VORGABEN)
    quelle: Path | None = None
    if datei.is_file():
        try:
            with datei.open("rb") as fh:
                data = _mische(VORGABEN, tomllib.load(fh))
            quelle = datei
        except tomllib.TOMLDecodeError as exc:
            # tomllib nennt Zeile und Spalte in der Meldung -- durchreichen
            # statt zu verschlucken.
            raise ConfigError(f"{datei}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"{datei}: nicht lesbar ({exc})") from exc

    st = state_dir()
    rt = runtime_dir()
    if make_dirs:
        # 0700, weil hier Zustand liegt, der aus dem Bildschirminhalt stammt.
        # exist_ok deckt den Fall ab, dass es das Verzeichnis schon gibt --
        # den Modus setzen wir dann trotzdem, sonst bleibt ein zu offenes
        # Verzeichnis aus einem frueheren Lauf stehen.
        st.mkdir(parents=True, exist_ok=True)
        st.chmod(0o700)
        rt.mkdir(parents=True, exist_ok=True)
        rt.chmod(0o700)

    return Config(data=data, config_dir=cfg_dir, state_dir=st,
                  runtime_dir=rt, quelle=quelle)
