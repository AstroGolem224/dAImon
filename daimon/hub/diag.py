"""T-0.13 — Diagnose-Endpunkt. Ein elfteiliges System ohne Messpunkte ist
nicht betreibbar.

`GET /diag` auf dem Hub-Socket, also ausschliesslich ueber den Unix-Socket:
die Diagnose verlaesst den Rechner nicht. Kein TCP, damit
`RestrictAddressFamilies=AF_UNIX` erfuellbar bleibt (T-0.14) -- und weil
Warteschlangenlaengen und Fenstertitel-Zaehler mehr ueber den Nutzer verraten,
als in einem HTTP-Endpunkt stehen sollte.

Latenz wird als **Histogramm** gefuehrt, nicht als Mittelwert. Ein Mittelwert
verschluckt genau das, was interessiert: der Hook haengt an jedem
Werkzeugaufruf, und ob dabei einer von tausend eine Sekunde braucht, sieht man
im Mittel nie. Die Klassengrenzen sind fest verdrahtet statt berechnet, damit
zwei Laeufe vergleichbar bleiben.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Feste Klassengrenzen in Millisekunden. Der interessante Bereich liegt unten:
# ein Hop im Hub soll unter einer Millisekunde bleiben, und alles ab 100 ms ist
# bereits ein Befund.
GRENZEN_MS = (0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000)


class Histogramm:
    def __init__(self, grenzen: tuple[float, ...] = GRENZEN_MS) -> None:
        self.grenzen = grenzen
        self._eimer = [0] * (len(grenzen) + 1)
        self._n = 0
        self._summe = 0.0
        self._max = 0.0
        self._lock = threading.Lock()

    def beobachte(self, ms: float) -> None:
        with self._lock:
            self._n += 1
            self._summe += ms
            self._max = max(self._max, ms)
            for i, g in enumerate(self.grenzen):
                if ms <= g:
                    self._eimer[i] += 1
                    return
            self._eimer[-1] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            marken = [f"<={g}" for g in self.grenzen] + [f">{self.grenzen[-1]}"]
            return {
                "n": self._n,
                "eimer": dict(zip(marken, self._eimer)),
                "mittel_ms": round(self._summe / self._n, 3) if self._n else 0.0,
                "max_ms": round(self._max, 3),
            }


@dataclass
class TypZaehler:
    """Je Typ: ausgegeben, eingeloest, abgelaufen, abgelehnt.

    Die vier zusammen sind die einzige Art, eine Marke zu verstehen. Nur
    'ausgegeben' zu zaehlen sagt nichts darueber, ob sie je benutzt wurde --
    und eine Marke, die nie eingeloest wird, ist entweder ueberfluessig oder
    ein Hinweis auf einen kaputten Pfad.
    """

    ausgegeben: int = 0
    eingeloest: int = 0
    abgelaufen: int = 0
    abgelehnt: int = 0

    def als_dict(self) -> dict[str, int]:
        return {"ausgegeben": self.ausgegeben, "eingeloest": self.eingeloest,
                "abgelaufen": self.abgelaufen, "abgelehnt": self.abgelehnt}


class Diagnose:
    """Sammelstelle. Threadsicher, ohne globalen Zustand."""

    TYPEN = ("rundenmarke", "aktionsfreigabe", "api_kontingent", "ticket")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._start = time.time()
        self._hops: dict[str, Histogramm] = {}
        self._typen = {t: TypZaehler() for t in self.TYPEN}
        self._verworfen: dict[str, int] = {}
        self._queue_len: dict[str, int] = {}
        self._gegendruck = {"aktiv": False, "seit": None, "ausloeser": None}

    # -- Schreiben ---------------------------------------------------------

    def hop(self, name: str, ms: float) -> None:
        with self._lock:
            self._hops.setdefault(name, Histogramm()).beobachte(ms)

    def zaehle(self, typ: str, feld: str, n: int = 1) -> None:
        with self._lock:
            z = self._typen.get(typ)
            if z is not None and hasattr(z, feld):
                setattr(z, feld, getattr(z, feld) + n)

    def verworfen(self, grund: str, n: int = 1) -> None:
        with self._lock:
            self._verworfen[grund] = self._verworfen.get(grund, 0) + n

    def queue(self, name: str, laenge: int) -> None:
        with self._lock:
            self._queue_len[name] = laenge

    def gegendruck(self, aktiv: bool, ausloeser: str | None = None) -> None:
        with self._lock:
            if aktiv and not self._gegendruck["aktiv"]:
                self._gegendruck = {"aktiv": True, "seit": time.time(),
                                    "ausloeser": ausloeser}
            elif not aktiv:
                self._gegendruck = {"aktiv": False, "seit": None, "ausloeser": None}

    # -- Lesen -------------------------------------------------------------

    def snapshot(self, *, unit_zustaende: dict[str, str] | None = None) -> dict:
        with self._lock:
            return {
                "v": 1,
                "laufzeit_s": round(time.time() - self._start, 1),
                "queues": dict(self._queue_len),
                "verworfen": dict(self._verworfen),
                "verworfen_gesamt": sum(self._verworfen.values()),
                "gegendruck": dict(self._gegendruck),
                "latenz": {k: h.snapshot() for k, h in self._hops.items()},
                "zaehler": {t: z.als_dict() for t, z in self._typen.items()},
                # T-0.14 liefert die Units. Bis dahin steht hier ein leeres
                # Feld statt einer Schaetzung -- ein erfundener Unit-Zustand
                # waere schlimmer als gar keiner.
                "units": unit_zustaende or {},
            }


def main(argv: list[str] | None = None) -> int:
    """`python -m daimon.hub.diag` -- den Schnappschuss des laufenden Hubs.

    **Diese Funktion fehlte bis zum 17.08.**, und der Befehl stand die ganze
    Zeit in `docs/INSTALL.md` unter "Pruefen, dass es steht". Ohne `main()`
    importiert `python -m` das Modul, fuehrt nichts aus und endet mit 0: eine
    dokumentierte Pruefung, die stumm gelingt. Sie sah aus wie ein
    Pruefschritt und war keiner -- dieselbe Gestalt wie ein Zaehler ohne
    Ableser (siehe `CLAUDE.md`).

    Der Hub ist nicht erreichbar -> `rc=1` und eine Zeile, die sagt WO
    gesucht wurde. Ein Diagnosewerkzeug, das bei fehlendem Dienst schweigt,
    ist genau dann nutzlos, wenn man es braucht.
    """
    import argparse
    import json
    import socket
    from pathlib import Path

    from daimon.common.config import load as load_config

    ap = argparse.ArgumentParser(description="dAImon Hub-Diagnose (T-0.13)")
    ap.add_argument("--runtime-dir", type=Path, default=None)
    args = ap.parse_args(argv)

    rt = args.runtime_dir or load_config(make_dirs=False).runtime_dir
    pfad = Path(rt) / "diag.sock"
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(5.0)
        c.connect(str(pfad))
        with c:
            roh = c.makefile("rb").readline()
    except OSError as exc:
        print(f"kein Hub an {pfad}: {type(exc).__name__}: {exc}")
        return 1
    try:
        schnappschuss = json.loads(roh)
    except (json.JSONDecodeError, ValueError):
        print(f"unlesbare Antwort von {pfad}: {roh[:200]!r}")
        return 1
    print(json.dumps(schnappschuss, ensure_ascii=False, indent=2,
                     sort_keys=True))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
