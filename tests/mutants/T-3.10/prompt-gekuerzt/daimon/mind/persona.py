"""Gut-Muster des Persona-Laders nach dem festgeschriebenen Vertrag."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daimon.common.config import Config


SCHWELLEN = frozenset({"silent", "urgent", "helpful", "chatty"})
VORGABE_VOICE = "de_DE-thorsten-high"
VORGABE_PALETTE = {"idle": "#3a2418", "active": "#ff6b1a", "alert": "#ffd24a"}
VORGABE_WAKE_WORDS: tuple[str, ...] = ()


class PersonaFehler(ValueError):
    """Fehlerhafte oder fehlende Persona-Datei."""


@dataclass(frozen=True)
class Persona:
    name: str
    wake_words: tuple[str, ...]
    voice: str
    palette: dict[str, str]
    speech_threshold: str
    traits: tuple[str, ...]
    system_prompt: str
    quelle: Path
    herkunft: dict[str, str]

    def prompt(self) -> str:
        text = self.system_prompt.splitlines()[0]  # MUTATION: nur erste Zeile
        if self.traits:
            text += "\n\nEigenschaften: " + ", ".join(self.traits)
        return text


def _liste(datei: Path, feld: str, wert: Any, *, optional: bool = False) -> tuple[str, ...] | None:
    if wert is None and optional:
        return None
    if not isinstance(wert, list) or not all(isinstance(e, str) for e in wert):
        raise PersonaFehler(f"{datei}: Feld {feld} muss eine Liste von Zeichenketten sein")
    return tuple(wert)


def _optional(cfg: Config, daten: dict[str, Any], feld: str, config_pfad: str, vorgabe: Any) -> tuple[Any, str]:
    if feld in daten:
        return daten[feld], "persona"
    marker = object()
    wert = cfg.get(config_pfad, marker)
    if wert is not marker:
        return wert, "config"
    return vorgabe, "vorgabe"


def lade(cfg: Config) -> Persona:
    gesucht = str(cfg.get("persona.name", "Ember")).lower()
    repo = Path(__file__).resolve().parents[2]
    xdg = cfg.config_dir / "persona" / f"{gesucht}.toml"
    mitgeliefert = repo / "config" / "persona" / f"{gesucht}.toml"
    kandidaten = (xdg, mitgeliefert)
    datei = next((p for p in kandidaten if p.is_file()), None)
    if datei is None:
        raise PersonaFehler(
            f"Persona {gesucht!r} fehlt; geprueft: {xdg} und {mitgeliefert}"
        )
    try:
        with datei.open("rb") as fh:
            daten = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise PersonaFehler(f"{datei}: TOML: {exc}") from exc
    except OSError as exc:
        raise PersonaFehler(f"{datei}: Datei nicht lesbar ({exc})") from exc

    for feld in ("name", "system_prompt"):
        if not isinstance(daten.get(feld), str):
            raise PersonaFehler(f"{datei}: Feld {feld} fehlt oder ist keine Zeichenkette")
    schwelle = daten.get("speech_threshold", "helpful")
    if schwelle not in SCHWELLEN:
        raise PersonaFehler(f"{datei}: Feld speech_threshold hat unbekannten Wert {schwelle!r}")
    traits = _liste(datei, "traits", daten.get("traits", []))
    h_traits = "persona" if "traits" in daten else "vorgabe"

    voice, h_voice = _optional(cfg, daten, "voice", "persona.voice", VORGABE_VOICE)
    if not isinstance(voice, str):
        raise PersonaFehler(f"{datei}: Feld voice muss eine Zeichenkette sein")
    palette, h_palette = _optional(cfg, daten, "palette", "face.palette", VORGABE_PALETTE)
    if not isinstance(palette, dict) or not all(
        isinstance(palette.get(k), str) for k in ("idle", "active", "alert")
    ):
        raise PersonaFehler(f"{datei}: Feld palette braucht idle, active und alert als Zeichenketten")
    wake, h_wake = _optional(cfg, daten, "wake_words", "ears.wake_words", VORGABE_WAKE_WORDS)
    if isinstance(wake, tuple) and all(isinstance(e, str) for e in wake):
        wake_words = wake
    else:
        wake_words = _liste(datei, "wake_words", wake)

    prompt = daten["system_prompt"].strip("\n")
    return Persona(
        name=daten["name"], wake_words=wake_words, voice=voice,
        palette=dict(palette), speech_threshold=schwelle, traits=traits,
        system_prompt=prompt, quelle=datei,
        herkunft={"name": "persona", "system_prompt": "persona",
                  "speech_threshold": "persona", "voice": h_voice,
                  "traits": h_traits, "wake_words": h_wake,
                  "palette": h_palette},
    )
