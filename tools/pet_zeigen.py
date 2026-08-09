#!/usr/bin/env python3
"""Startknopf hinter dem Schreibtisch-Icon: Unit starten UND das Pet zeigen.

Warum beides
----------------------------------------------------------------------------
`systemctl --user start` auf eine laufende Unit ist folgenlos -- und das Pet
blendet sich bei `mood = sleeping` selbst aus. Ein Icon, das dann stumm
bleibt, sieht aus wie ein kaputtes Programm. Also: erst starten, dann
`sichtbar an` ueber den Steuer-Socket.

Was das NICHT ist
----------------------------------------------------------------------------
Kein Dauerzustand. Der Mood gehoert dem Hub; die naechste Hub-Meldung mit
`sleeping` blendet wieder aus. Das ist richtig so -- das Pet zeigt Sitzungen
an, und ein Pet, das dauerhaft sichtbar bleibt, ohne dass etwas laeuft, waere
eine Anzeige ohne Aussage. Der Knopf holt es hervor, er haelt es nicht fest.

Nur stdlib, damit der Schreibtisch-Knopf nicht am venv haengt.
"""
import os
import socket
import subprocess
import sys
import time

UNIT = "daimon-face.service"
SOCKET = "face-control.sock"


def steuer_pfad() -> str:
    lauf = os.environ.get("XDG_RUNTIME_DIR")
    if not lauf:
        raise SystemExit("XDG_RUNTIME_DIR ist nicht gesetzt; ohne das gibt es "
                         "keinen Steuer-Socket.")
    return os.path.join(lauf, "daimon", SOCKET)


def sagen(pfad: str, befehl: str) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect(pfad)
        s.sendall(befehl.encode() + b"\n")
        return s.recv(64).decode(errors="replace").strip()


def main() -> int:
    start = subprocess.run(["systemctl", "--user", "start", UNIT],
                           capture_output=True, text=True)
    if start.returncode != 0:
        print(start.stderr.strip() or f"{UNIT} liess sich nicht starten",
              file=sys.stderr)
        return 1

    pfad = steuer_pfad()
    # Gewartet wird auf eine ANTWORT, nicht auf die Datei: die Socketdatei des
    # vorigen Laufs liegt noch da (`RuntimeDirectoryPreserve`), und ein
    # `os.path.exists` darauf ist sofort wahr -- gemessen, kostete einen
    # Fehlschlag mit "Connection refused". Bis das neue Face gebunden hat,
    # dauert es hier ~0,3 s; fuenf Sekunden sind Luft, kein Richtwert.
    frist = time.monotonic() + 5.0
    while True:
        try:
            antwort = sagen(pfad, "sichtbar an")
            break
        except OSError as fehler:
            if time.monotonic() >= frist:
                # Die Unit laeuft; nur das Zeigen hat nicht geklappt. Das ist
                # ein halber Erfolg und wird auch so gemeldet.
                print(f"{UNIT} laeuft, aber {pfad} antwortet nicht ({fehler})",
                      file=sys.stderr)
                return 1
            time.sleep(0.05)
    if antwort != "ok":
        print(f"Steuer-Socket antwortete {antwort!r} statt 'ok'", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
