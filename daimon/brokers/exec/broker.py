"""T-4.10 — der Exec-Broker: Anwendungen starten, ausserhalb der Sandbox.

Whitelist ueber `desktop_id`, nie ueber `Exec`
----------------------------------------------------------------------------
Freigegeben wird eine Anwendung, nicht eine Kommandozeile. Ein freier
`Exec`-String waere ein Shell-Aufruf mit Katalogsegen -- und die Zeile
`Exec=sh -c "..."` steht in mancher `.desktop`-Datei voellig regulaer drin.
Der Broker kennt deshalb nur `desktop_id` und liest den Rest aus der Datei.

Die Freigabe haengt am sha256 der aufgeloesten Datei
----------------------------------------------------------------------------
`~/.local/share/applications` ist fuer den Nutzer schreibbar -- und damit
auch fuer jeden Prozess unter derselben uid. Zwischen Vorschau und Start
liesse sich die Datei austauschen. Der Broker rechnet den Abdruck deshalb
**bei der Freigabe** und **unmittelbar vor dem Start noch einmal**; weichen
sie ab, wird nicht gestartet. Systemweite, root-eigene Dateien werden
bevorzugt: sie liegen ausserhalb der Reichweite dieser uid.

Der gestartete Prozess gehoert nicht uns
----------------------------------------------------------------------------
`systemd-run --user --collect` haengt ihn in eine eigene transiente Unit --
also in eine eigene cgroup, ausserhalb der Broker-Sandbox. Sonst erbte jede
gestartete Anwendung die Schranken dieses Dienstes und waere entweder kaputt
oder ein Loch: wer die Sandbox lockert, damit die Anwendung laeuft, lockert
sie fuer den Broker.

Bei `DBusActivatable=true` uebernimmt das der Sitzungsbus, und der Weg ist
derselbe: der Prozess entsteht woanders.
"""

from __future__ import annotations

import configparser
import hashlib
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# In dieser Reihenfolge gesucht. Systemweit ZUERST: eine root-eigene Datei
# schlaegt die gleichnamige im Home, weil letztere unter derselben uid
# schreibbar ist wie der Angreifer, den wir nicht abwehren.
SUCHPFADE = (
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path.home() / ".local/share/applications",
)


class ExecFehler(RuntimeError):
    """Es wird nicht gestartet. Nennt den Grund."""


@dataclass(frozen=True)
class Anwendung:
    desktop_id: str
    pfad: Path
    abdruck: str
    dbus_aktivierbar: bool
    dbus_name: str
    root_eigen: bool


def _abdruck(pfad: Path) -> str:
    return "sha256:" + hashlib.sha256(pfad.read_bytes()).hexdigest()


def aufloesen(desktop_id: str, *, suchpfade=None) -> Anwendung:
    # Erst beim Aufruf gelesen, nicht beim Definieren: ein an die Funktion
    # gebundener Vorgabewert waere zur Laufzeit nicht mehr austauschbar --
    # und ein Broker mit einer anderen Suchreihenfolge waere nicht pruefbar.
    suchpfade = suchpfade or SUCHPFADE
    if "/" in desktop_id or not desktop_id.endswith(".desktop"):
        # Ein Pfad waere die Umgehung der Suchreihenfolge -- und damit der
        # Bevorzugung root-eigener Dateien.
        raise ExecFehler(
            f"{desktop_id!r} ist keine desktop_id (kein Pfad, endet auf "
            f".desktop)")
    for verzeichnis in suchpfade:
        kandidat = Path(verzeichnis) / desktop_id
        if not kandidat.is_file():
            continue
        parser = configparser.RawConfigParser(strict=False)
        parser.optionxform = str
        try:
            parser.read(kandidat, encoding="utf-8")
        except configparser.Error as fehler:
            raise ExecFehler(f"{kandidat}: unlesbar ({fehler})") from fehler
        eintrag = parser["Desktop Entry"] if parser.has_section(
            "Desktop Entry") else {}
        aktivierbar = str(eintrag.get("DBusActivatable", "false")).strip().lower() == "true"
        try:
            root_eigen = kandidat.stat().st_uid == 0
        except OSError:
            root_eigen = False
        return Anwendung(
            desktop_id=desktop_id, pfad=kandidat, abdruck=_abdruck(kandidat),
            dbus_aktivierbar=aktivierbar,
            dbus_name=desktop_id[:-len(".desktop")], root_eigen=root_eigen)
    raise ExecFehler(f"{desktop_id} in keinem Suchpfad gefunden")


@dataclass
class ExecBroker:
    """Die freigegebenen Anwendungen samt Abdruck zum Zeitpunkt der Freigabe."""

    freigaben: dict  # desktop_id -> abdruck
    lauf: Callable[..., Any] = subprocess.run
    suchpfade: tuple = ()

    @classmethod
    def aus_katalog(cls, katalog: dict, *, suchpfade=None, **kw) -> "ExecBroker":
        suchpfade = tuple(suchpfade or SUCHPFADE)
        freigaben = {}
        for eintrag in katalog.values():
            if eintrag.get("status") != "approved":
                continue
            kennung = eintrag.get("desktop_id")
            if not kennung:
                continue
            anwendung = aufloesen(kennung, suchpfade=suchpfade)
            freigaben[kennung] = anwendung.abdruck
        return cls(freigaben=freigaben, suchpfade=suchpfade, **kw)

    def starten(self, desktop_id: str) -> dict:
        abdruck_freigabe = self.freigaben.get(desktop_id)
        if abdruck_freigabe is None:
            return {"ok": False, "grund": "nicht_freigegeben",
                    "meldung": f"{desktop_id} steht nicht im Katalog"}

        anwendung = aufloesen(desktop_id, suchpfade=self.suchpfade or None)
        # Unmittelbar vor dem Start noch einmal. Zwischen Freigabe und hier
        # liegt die Vorschau, und die Datei ist in dieser Zeit schreibbar.
        if anwendung.abdruck != abdruck_freigabe:
            return {"ok": False, "grund": "datei_getauscht",
                    "meldung": f"{anwendung.pfad} hat jetzt {anwendung.abdruck}, "
                               f"freigegeben war {abdruck_freigabe}"}

        if anwendung.dbus_aktivierbar:
            argv = ["gdbus", "call", "--session", "--dest", anwendung.dbus_name,
                    "--object-path", "/" + anwendung.dbus_name.replace(".", "/"),
                    "--method", "org.freedesktop.Application.Activate", "{}"]
        else:
            # `--collect` raeumt die transiente Unit nach dem Ende ab; ohne
            # das bliebe je Start eine tote Unit stehen.
            #
            # `KillMode=none`: `gio launch` gabelt die Anwendung und kehrt
            # sofort zurueck. Ohne diese Zeile ist `gio` der Hauptprozess der
            # transienten Unit, die Unit gilt mit seinem Ende als beendet,
            # und systemd raeumt ihre cgroup ab -- samt der eben gestarteten
            # Anwendung (Befund T-4.10 K4: `systemd-run` meldete `rc=0`, der
            # Broker meldete `ok`, und nichts lief).
            argv = ["systemd-run", "--user", "--collect", "--quiet",
                    "--property=KillMode=none",
                    f"--unit=daimon-app-{anwendung.dbus_name}",
                    "gio", "launch", str(anwendung.pfad)]

        # shell=False, argv-Array, nichts zusammengebaut. Ein
        # `shlex.join`-Rueckweg steht hier nur fuer die Meldung.
        e = self.lauf(argv, capture_output=True, text=True, timeout=20)
        rc = int(getattr(e, "returncode", 1))
        return {"ok": rc == 0, "grund": "" if rc == 0 else "start",
                "desktop_id": desktop_id, "abdruck": anwendung.abdruck,
                "weg": "dbus" if anwendung.dbus_aktivierbar else "systemd-run",
                "argv": shlex.join(argv), "rc": rc,
                "meldung": (getattr(e, "stderr", "") or "").strip()[:200]}
