"""T-4.9 — der Dateisystem-Broker: aufloesen und mutieren ohne TOCTOU.

Warum `openat2` und nicht `os.open`
----------------------------------------------------------------------------
Zwischen Policy, Consent, Undo und Mutation liegen Sekunden -- in denen der
Vorschautext gelesen und ein Knopf gedrueckt wird. Wer den Pfad danach ein
zweites Mal aufloest, prueft eine andere Datei als die, ueber die entschieden
wurde: es genuegt, in der Zwischenzeit ein Verzeichnisglied durch einen
Symlink auf `~/.ssh/id_ed25519` zu ersetzen.

Deshalb wird der Pfad **genau einmal** aufgeloest, und zwar in einen
Verzeichnis-Deskriptor plus einen einzelnen Namen. Jede spaetere Operation
laeuft ueber diesen FD (`*at`-Aufrufe) und mit
`RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS`. Der Kernel
weist den Symlink dann ab, egal wann er auftaucht.

Kein Rueckfall auf `os.open`
----------------------------------------------------------------------------
Fehlt `openat2` (Kernel < 5.6), wird **abgebrochen**. Ein Rueckfall waere
genau die stille Verschlechterung, die dieses Projekt an mehreren Stellen
teuer bezahlt hat: die Zusage "kein TOCTOU" haenge dann daran, auf welchem
Kernel jemand startet, und die Meldung sagte weiter `ok`.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
from dataclasses import dataclass
from pathlib import Path

# uapi/linux/openat2.h
RESOLVE_NO_XDEV = 0x01
RESOLVE_NO_MAGICLINKS = 0x02
RESOLVE_NO_SYMLINKS = 0x04
RESOLVE_BENEATH = 0x08
RESOLVE_IN_ROOT = 0x10

# x86_64. Auf einer anderen Architektur ist die Nummer eine andere -- und
# eine geratene Syscall-Nummer waere schlimmer als keine.
SYS_OPENAT2 = 437

_MASCHINEN = {"x86_64": 437, "aarch64": 437}


class FSFehler(OSError):
    """Die Operation unterbleibt. Nennt den Grund."""


class _OpenHow(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64),
                ("mode", ctypes.c_uint64),
                ("resolve", ctypes.c_uint64)]


def _syscall_nummer() -> int:
    maschine = os.uname().machine
    nummer = _MASCHINEN.get(maschine)
    if nummer is None:
        raise FSFehler(
            f"openat2 hat auf {maschine} eine andere Syscall-Nummer; eine "
            f"geratene waere schlimmer als keine")
    return nummer


def openat2(dirfd: int, name: str, *, flags: int = os.O_RDONLY,
            mode: int = 0, resolve: int | None = None) -> int:
    """Der rohe Aufruf. Wirft `FSFehler`, wenn der Kernel ihn nicht kennt."""
    if resolve is None:
        resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    # `mode` ist nur mit O_CREAT/O_TMPFILE erlaubt -- sonst antwortet der
    # Kernel mit EINVAL. Gemessen beim Bauen: ein `mode=0o600` an einem
    # reinen O_WRONLY liess jedes Schreiben scheitern.
    if not flags & (os.O_CREAT | getattr(os, "O_TMPFILE", 0)):
        mode = 0
    how = _OpenHow(flags=flags | os.O_CLOEXEC, mode=mode, resolve=resolve)
    fd = libc.syscall(ctypes.c_long(_syscall_nummer()), ctypes.c_int(dirfd),
                      ctypes.c_char_p(name.encode()), ctypes.byref(how),
                      ctypes.c_size_t(ctypes.sizeof(how)))
    if fd < 0:
        nummer = ctypes.get_errno()
        if nummer == errno.ENOSYS:
            raise FSFehler(
                "openat2 fehlt (Kernel < 5.6). KEIN Rueckfall auf os.open: "
                "die Zusage 'kein TOCTOU' haenge dann am Kernel, und die "
                "Meldung saegte weiter ok.")
        raise FSFehler(nummer, os.strerror(nummer), name)
    return fd


@dataclass
class Griff:
    """Ein einmal aufgeloester Ort: Verzeichnis-FD plus Name.

    Nach dem Aufloesen wird der Pfad NICHT mehr angefasst. Alles Weitere
    laeuft ueber `dirfd` -- das ist der Unterschied zwischen "die Datei, ueber
    die entschieden wurde" und "die Datei, die jetzt unter dem Namen liegt".
    """

    dirfd: int
    name: str
    anzeige: str

    def schliessen(self) -> None:
        if self.dirfd >= 0:
            os.close(self.dirfd)
            object.__setattr__(self, "dirfd", -1)

    def __enter__(self) -> "Griff":
        return self

    def __exit__(self, *_):
        self.schliessen()


def aufloesen(wurzel: Path, relativ: str) -> Griff:
    """Einmal aufloesen, unterhalb der Wurzel, ohne Symlinks.

    `relativ` darf nicht absolut sein und kein `..` enthalten -- beides
    weist schon `RESOLVE_BENEATH` ab, aber eine Meldung, die den Grund nennt,
    ist besser als `EXDEV` aus dem Kernel.
    """
    wurzel = Path(wurzel)
    teil = Path(relativ)
    if teil.is_absolute():
        raise FSFehler(f"{relativ!r} ist absolut; erwartet wird ein Pfad "
                       f"unterhalb von {wurzel}")
    if ".." in teil.parts:
        raise FSFehler(f"{relativ!r} enthaelt '..'")
    if not teil.parts:
        raise FSFehler("leerer Pfad")

    wurzel_fd = os.open(wurzel, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    aktuell = wurzel_fd
    try:
        for stueck in teil.parts[:-1]:
            naechster = openat2(aktuell, stueck,
                                flags=os.O_RDONLY | os.O_DIRECTORY)
            if aktuell != wurzel_fd:
                os.close(aktuell)
            aktuell = naechster
        return Griff(dirfd=aktuell, name=teil.parts[-1],
                     anzeige=str(wurzel / teil))
    except Exception:
        if aktuell != wurzel_fd:
            os.close(aktuell)
        os.close(wurzel_fd)
        raise


def lesen(griff: Griff, groesse: int = -1) -> bytes:
    fd = openat2(griff.dirfd, griff.name, flags=os.O_RDONLY)
    try:
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read() if groesse < 0 else fh.read(groesse)
    finally:
        os.close(fd)


def schreiben(griff: Griff, daten: bytes) -> int:
    """Ueberschreibt die Datei AN DIESEM Griff -- nicht den Pfad."""
    fd = openat2(griff.dirfd, griff.name,
                 flags=os.O_WRONLY | os.O_TRUNC, mode=0o600)
    try:
        return os.write(fd, daten)
    finally:
        os.close(fd)


def umbenennen(griff: Griff, neuer_name: str) -> None:
    if "/" in neuer_name:
        raise FSFehler(f"{neuer_name!r} ist kein Name, sondern ein Pfad")
    os.rename(griff.name, neuer_name, src_dir_fd=griff.dirfd,
              dst_dir_fd=griff.dirfd)


def verfuegbar() -> bool:
    """Ob dieser Kernel `openat2` kennt. Fuer den Start, nicht fuer Rueckfaelle."""
    fd = -1
    try:
        fd = openat2(os.open("/", os.O_RDONLY | os.O_DIRECTORY), ".",
                     flags=os.O_RDONLY | os.O_DIRECTORY)
        return True
    except FSFehler:
        return False
    finally:
        if fd > 0:
            os.close(fd)
