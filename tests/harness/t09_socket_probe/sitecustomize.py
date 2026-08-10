"""Erhebt Socket-Eigentum im gestarteten Prozess trotz PR_SET_DUMPABLE=0."""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

_gehalten: list[socket.socket] = []


def _inodes(datei: str, zustand: str) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        zeilen = Path(datei).read_text().splitlines()[1:]
    except OSError:
        return result
    for zeile in zeilen:
        teile = zeile.split()
        if datei.endswith("unix"):
            if len(teile) >= 7 and teile[5] == zustand:
                result[teile[6]] = teile[7] if len(teile) >= 8 else ""
        elif len(teile) >= 10 and teile[3] == zustand:
            result[teile[9]] = teile[1]
    return result


def _eigene_socket_inodes() -> set[str]:
    result: set[str] = set()
    for fd in Path("/proc/self/fd").iterdir():
        try:
            ziel = os.readlink(fd)
        except OSError:
            continue
        if ziel.startswith("socket:[") and ziel.endswith("]"):
            result.add(ziel[8:-1])
    return result


def _schreiben(ziel: Path) -> None:
    for _ in range(400):
        eigene = _eigene_socket_inodes()
        unix = _inodes("/proc/net/unix", "01")
        tcp = _inodes("/proc/net/tcp", "0A") | _inodes("/proc/net/tcp6", "0A")
        daten = {
            "pid": os.getpid(),
            "unix": [{"inode": i, "path": unix[i]} for i in sorted(eigene & unix.keys())],
            "tcp": [{"inode": i, "adresse": tcp[i]} for i in sorted(eigene & tcp.keys())],
        }
        tmp = ziel.with_suffix(".tmp")
        tmp.write_text(json.dumps(daten), encoding="utf-8")
        os.replace(tmp, ziel)
        time.sleep(0.025)


def _start() -> None:
    ziel = os.environ.get("DAIMON_T09_SOCKET_BEFUND")
    if not ziel:
        return
    positiv = os.environ.get("DAIMON_T09_POSITIV_SOCKET")
    if positiv:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(positiv)
        s.listen(1)
        _gehalten.append(s)
    if os.environ.get("DAIMON_T09_TCP_MUTANT") == "1":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        _gehalten.append(s)
    threading.Thread(target=_schreiben, args=(Path(ziel),), daemon=True).start()


_start()
