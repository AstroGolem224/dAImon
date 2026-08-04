"""Kleine, vertragsgleiche Config fuer das isolierte Gut-Muster."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    data: dict[str, Any] = field(default_factory=dict)
    config_dir: Path = field(default_factory=Path)
    state_dir: Path = field(default_factory=Path)
    runtime_dir: Path = field(default_factory=Path)
    quelle: Path | None = None

    def get(self, pfad: str, fallback: Any = None) -> Any:
        knoten: Any = self.data
        for teil in pfad.split("."):
            if not isinstance(knoten, dict) or teil not in knoten:
                return fallback
            knoten = knoten[teil]
        return knoten
