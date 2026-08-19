"""T-0.6 — strukturiertes Logging ins Journal, stderr als Rueckfall.

Ohne `python-systemd`. Das Paket ist auf dieser Maschine nicht installiert, und
es zu fordern waere eine Abhaengigkeit fuer etwa dreissig Zeilen: das native
Journal-Protokoll ist ein Datagramm auf einen Unix-Socket, mehr nicht. Der
Rueckfall auf stderr ist ohnehin gefordert, also gibt es zwei Wege statt drei.

Feldregeln des Journals, und sie sind nicht kosmetisch:

  * Namen duerfen nur `[A-Z0-9_]` enthalten. Ein Feld mit Kleinbuchstaben oder
    Bindestrich wird von journald **stillschweigend verworfen** -- man merkt es
    erst, wenn man den Datensatz sucht und nicht findet.
  * Namen duerfen **nicht** mit `_` beginnen. Diese Kennung ist journald selbst
    vorbehalten; es setzt darunter `_PID`, `_UID`, `_SYSTEMD_UNIT` und faelscht
    sie nicht. Wer sie selbst schreiben koennte, koennte seine eigene Herkunft
    behaupten. Wir weisen solche Namen deshalb hart ab, statt sie zu putzen --
    ein still umbenanntes Feld waere schlimmer als ein Fehler.

Werte mit Zeilenumbruch brauchen die binaere Rahmung: `FELD\\n<8 Byte
Laenge, little endian>\\n<Daten>\\n`. Ohne sie zerfaellt ein mehrzeiliger Wert
in kaputte Zeilen.
"""

from __future__ import annotations

import os
import re
import socket
import struct
import sys
from typing import Any

JOURNAL_SOCKET = "/run/systemd/journal/socket"

# Syslog-Schweregrade, damit `journalctl -p` funktioniert.
EMERG, ALERT, CRIT, ERR, WARNING, NOTICE, INFO, DEBUG = range(8)

_ERLAUBT = re.compile(r"^[A-Z][A-Z0-9_]*$")


class FieldNameError(ValueError):
    """Feldname, den journald verwerfen oder falsch zuordnen wuerde."""


def _pruefe_feldname(name: str) -> None:
    if name.startswith("_"):
        raise FieldNameError(
            f"{name!r}: fuehrender Unterstrich ist journald vorbehalten "
            "(_PID, _UID, _SYSTEMD_UNIT). Eigene Felder duerfen ihre Herkunft "
            "nicht behaupten koennen."
        )
    if not _ERLAUBT.match(name):
        raise FieldNameError(
            f"{name!r}: nur A-Z, 0-9 und _ erlaubt, und der erste Buchstabe "
            "muss ein Buchstabe sein. journald verwirft alles andere still."
        )


def _kodiere(name: str, value: Any) -> bytes:
    roh = str(value).encode("utf-8", "replace")
    if b"\n" in roh:
        return (name.encode() + b"\n"
                + struct.pack("<Q", len(roh)) + roh + b"\n")
    return name.encode() + b"=" + roh + b"\n"


class Logger:
    """Ein Logger je Prozess. Kein Modul-Singleton -- der `identifier`
    unterscheidet die Prozesse im Journal, und der ist prozessabhaengig."""

    def __init__(self, identifier: str, *, socket_path: str = JOURNAL_SOCKET,
                 stream: Any = None) -> None:
        self.identifier = identifier
        self.socket_path = socket_path
        self._stream = stream if stream is not None else sys.stderr
        self._sock: socket.socket | None = None
        if os.path.exists(socket_path):
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            except OSError:
                self._sock = None

    @property
    def nutzt_journal(self) -> bool:
        return self._sock is not None

    def send(self, message: str, *, priority: int = INFO, **felder: Any) -> None:
        for name in felder:
            _pruefe_feldname(name)

        eintrag = {
            "MESSAGE": message,
            "PRIORITY": priority,
            "SYSLOG_IDENTIFIER": self.identifier,
            **felder,
        }

        if self._sock is not None:
            nutzlast = b"".join(_kodiere(k, v) for k, v in eintrag.items())
            try:
                self._sock.sendto(nutzlast, self.socket_path)
                return
            except OSError:
                # Journal weg (Neustart, Container). Nicht sterben -- der
                # Logger haengt in jedem Prozess, und ein Absturz hier waere
                # ein Ausfall aus einem Nebenweg.
                self._sock = None

        self._fallback(eintrag)

    def _fallback(self, eintrag: dict[str, Any]) -> None:
        extra = " ".join(
            f"{k}={v}" for k, v in eintrag.items()
            if k not in ("MESSAGE", "PRIORITY", "SYSLOG_IDENTIFIER")
        )
        zeile = f"[{self.identifier}] {eintrag['MESSAGE']}"
        if extra:
            zeile += f"  {extra}"
        print(zeile, file=self._stream, flush=True)

    # Bequemlichkeit
    def info(self, message: str, **f: Any) -> None:
        self.send(message, priority=INFO, **f)

    def warn(self, message: str, **f: Any) -> None:
        self.send(message, priority=WARNING, **f)

    def error(self, message: str, **f: Any) -> None:
        self.send(message, priority=ERR, **f)

    def debug(self, message: str, **f: Any) -> None:
        self.send(message, priority=DEBUG, **f)


def get_logger(identifier: str = "daimon", **kw: Any) -> Logger:
    return Logger(identifier, **kw)
