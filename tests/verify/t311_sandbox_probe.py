#!/usr/bin/env python3
"""Harmlose Laufzeitsonde fuer die Härtung der geprüften Unit."""

from __future__ import annotations

import json
import ctypes
import hashlib
import os
import socket
import sys
import time
from pathlib import Path

ziel = Path(sys.argv[1])
pruefer_pid = int(sys.argv[2]) if len(sys.argv) > 2 else 0
# Die Reviewer-Sonde erlaubt ausschließlich ihrem aufrufenden Prüfstand das
# Lesen. So misst der Prüfstand den Adressraum von außen; die Sonde muss die
# gesuchte Token-Zeichenkette selbst nie kennen oder halten.
if pruefer_pid:
    ctypes.CDLL(None).prctl(0x59616D61, pruefer_pid, 0, 0, 0)  # PR_SET_PTRACER
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
    af_inet = True
except OSError as exc:
    af_inet = False
    fehler = f"{type(exc).__name__}: {exc}"
else:
    fehler = ""


def eigene_caps() -> dict:
    """Die Faehigkeiten dieses Prozesses, im Prozess selbst gelesen.

    Der Pruefstand kann sie spaeter nicht mehr aus /proc holen: diese Sonde
    lebt 30 s, die Haertungspruefung (Kapitel 15) kommt danach. Gemessen wird
    damit die WIRKUNG von `CapabilityBoundingSet=`, nicht die Zeile.
    """
    werte = {}
    for zeile in Path("/proc/self/status").read_text().splitlines():
        if zeile.startswith("Cap"):
            name, _, wert = zeile.partition(":")
            werte[name] = wert.strip()
    return werte


cred_hash = ""
cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
if cred_dir:
    try:
        cred_hash = hashlib.sha256(
            (Path(cred_dir) / "anthropic-token").read_bytes()).hexdigest()
    except OSError:
        pass

ziel.write_text(json.dumps({
    "pid": os.getpid(),
    "af_inet": af_inet,
    "fehler": fehler,
    "caps": eigene_caps(),
    "credential_sha256": cred_hash,
    "proxy_umgebung": {n: os.environ.get(n) for n in
                         ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
                         if n in os.environ},
}, sort_keys=True), encoding="utf-8")
time.sleep(30)
