"""T-3.15 — der Kill-Switch der Ohren.

Warum das Ergebnis am STROM haengt und nicht am Rueckgabewert
----------------------------------------------------------------------------
`systemctl --user stop` liefert 0, sobald der Prozess weg ist. Das ist NICHT
die Zusage dieses Tasks. Die Zusage ist: danach nimmt nichts mehr auf. Ein
Dienst, der beim Beenden seinen PipeWire-Strom nicht schliesst, hinterlaesst
ihn beim Server, das Mikrofonsymbol bleibt stehen -- und `rc=0` haette es
bestaetigt. Deshalb misst `stoppe()` vorher und nachher und macht das
Messergebnis zum `ok`.

Aus demselben Grund ist eine **nicht gemessene** Stromzahl (`None`) kein
Erfolg. Wer ein fehlendes `pw-dump` als "null Stroeme" liest, macht aus einem
Werkzeugfehler eine Sicherheitsaussage.

Warum die Unit nicht frei waehlbar ist
----------------------------------------------------------------------------
Dieselbe Grenze wie bei `wahrnehmung_aus` im Hub (T-2.7): der schlimmste
Missbrauch soll "Ohren gehen aus" sein und nicht "der Auth-Agent geht aus".
Eine Allowlist mit zwei Eintraegen kostet nichts und deckelt genau das.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Callable

EARS_UNIT = "daimon-ears.service"
# Was dieser Schalter ueberhaupt anfassen darf. Bewusst dieselbe Menge wie
# WAHRNEHMUNG_ZIELE im Hub: Wahrnehmung aus, sonst nichts.
ERLAUBTE_UNITS = (EARS_UNIT, "daimon-eyes.service")

# media.class eines aufnehmenden Stroms. Ein `Audio/Source` ist das GERAET und
# steht immer da; nur ein `Stream/Input/Audio` heisst, dass jemand zuhoert.
AUFNAHME_KLASSE = "Stream/Input/Audio"


def _pw_dump_text(timeout_s: float = 5.0) -> str | None:
    try:
        lauf = subprocess.run(["pw-dump"], capture_output=True, text=True,
                              timeout=timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    return lauf.stdout if lauf.returncode == 0 else None


def aufnahmestroeme(*, dump_text: str | None = ...) -> int | None:
    """Wie viele Aufnahmestroeme laufen. `None` = nicht messbar.

    `None` und `0` duerfen nie verwechselt werden: das eine heisst "niemand
    hoert zu", das andere "wir wissen es nicht".
    """
    text = _pw_dump_text() if dump_text is ... else dump_text
    if not text:
        return None
    try:
        knoten = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(knoten, list):
        return None
    return sum(
        1 for k in knoten
        if isinstance(k, dict)
        and (k.get("info") or {}).get("props", {}).get("media.class") == AUFNAHME_KLASSE
    )


def _ist_aktiv(unit: str, lauf: Callable[..., Any]) -> bool:
    e = lauf(["systemctl", "--user", "is-active", unit],
             capture_output=True, text=True, timeout=10.0)
    return (e.stdout or "").strip() == "active"


def stoppe(unit: str = EARS_UNIT, *, timeout_s: float = 10.0,
           lauf: Callable[..., Any] = subprocess.run,
           stroeme: Callable[[], int | None] = aufnahmestroeme) -> dict:
    """Die Ohren abschalten und BELEGEN, dass sie aus sind.

    `lauf` und `stroeme` sind injizierbar, damit der Schalter ohne laufendes
    systemd und ohne PipeWire pruefbar ist -- und damit der Fall "rc=0, Strom
    laeuft weiter" ueberhaupt herstellbar ist.
    """
    if unit not in ERLAUBTE_UNITS:
        raise ValueError(
            f"{unit!r} steht nicht in der Allowlist. Erlaubt: "
            + ", ".join(ERLAUBTE_UNITS))

    begonnen = time.monotonic()
    war_aktiv = _ist_aktiv(unit, lauf)
    vorher = stroeme()
    e = lauf(["systemctl", "--user", "stop", unit],
             capture_output=True, text=True, timeout=timeout_s)
    rc = int(getattr(e, "returncode", 1))
    nachher = stroeme()
    dauer_ms = round((time.monotonic() - begonnen) * 1000, 3)

    if rc != 0:
        ok, meldung = False, (getattr(e, "stderr", "") or "").strip()[:200]
    elif nachher is None:
        ok, meldung = False, "Aufnahmestroeme nicht messbar (pw-dump fehlt?)"
    elif nachher > 0:
        ok, meldung = False, f"{nachher} Aufnahmestrom/-stroeme laufen weiter"
    else:
        ok, meldung = True, ""

    return {"v": 1, "ok": ok, "unit": unit, "rc": rc, "war_aktiv": war_aktiv,
            "aufnahmestroeme_vorher": vorher, "aufnahmestroeme_nachher": nachher,
            "dauer_ms": dauer_ms, "meldung": meldung}


def main(argv: list[str] | None = None) -> int:
    """`python -m daimon.ears.killswitch` -- der Weg, den der Hotkey nimmt."""
    import argparse

    ap = argparse.ArgumentParser(description="T-3.15: Ohren abschalten")
    ap.add_argument("--unit", default=EARS_UNIT)
    args = ap.parse_args(argv)
    ergebnis = stoppe(args.unit)
    print(json.dumps(ergebnis, ensure_ascii=False))
    return 0 if ergebnis["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
