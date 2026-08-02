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
    "face": {
        "poll_ms": 250,
        "palette": {"idle": "#3a2418", "active": "#ff6b1a", "alert": "#ffd24a"},
    },
    "persona": {"name": "Ember"},
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
