#!/usr/bin/env python3
"""T-5.6 -- der dauerhafte OCR-Arbeitsprozess. Portiert aus `spikes/ocr/worker.py`.

Dieses Modul importiert `ctypes`, `os`, `socket`, `sys` -- und sonst nichts.
Das ist keine Sparsamkeit, das ist der Zweck: Spike T--1.10 hat gemessen, dass
libtesseract im selben Prozess wie `numpy` reproduzierbar rund 800 ms je
Vollbild MEHR kostet, weil OpenMP und OpenBLAS sich die Laufzeitumgebung
teilen. Wer hier `import numpy` schreibt, macht die Aufgabe rueckgaengig.

Der Prozess haelt EINE `TessBaseAPI` am Leben. Die 60 ms Festkosten je Aufruf,
die der CLI-Weg misst (fork/exec, traineddata laden, Temporaerdatei), fallen
damit auf null -- gemessen 18 % eines Zuschnitts.

Protokoll ueber ein Socketpaar, roh und ohne Rahmenwerk:

    "<breite> <hoehe> <bytes>\\n" + RGB-Bytes   ->   "<bytes>\\n" + UTF-8
    "-1 -1 -1\\n"                               ->   Ende
"""
import ctypes
import os
import socket
import sys

BIBLIOTHEK = "libtesseract.so"


def main() -> None:
    tessdata, sprachen, psm = sys.argv[1], sys.argv[2], sys.argv[3]
    sock = socket.socket(fileno=int(os.environ["WORKER_FD"]))

    L = ctypes.CDLL(BIBLIOTHEK)
    L.TessBaseAPICreate.restype = ctypes.c_void_p
    L.TessBaseAPIInit3.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                   ctypes.c_char_p]
    L.TessBaseAPISetPageSegMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    L.TessBaseAPISetImage.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                      ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int]
    L.TessBaseAPIGetUTF8Text.argtypes = [ctypes.c_void_p]
    L.TessBaseAPIGetUTF8Text.restype = ctypes.c_void_p
    L.TessDeleteText.argtypes = [ctypes.c_void_p]
    L.TessBaseAPIEnd.argtypes = [ctypes.c_void_p]
    L.TessBaseAPIDelete.argtypes = [ctypes.c_void_p]

    h = ctypes.c_void_p(L.TessBaseAPICreate())
    if L.TessBaseAPIInit3(h, tessdata.encode(), sprachen.encode()) != 0:
        # Ohne diese Pruefung liefe der Prozess weiter und gaebe zu jedem
        # Bild einen leeren String zurueck -- ununterscheidbar von einem
        # Bildschirm ohne Text.
        sys.stderr.write(f"tesseract kennt {sprachen!r} in {tessdata!r} nicht\n")
        raise SystemExit(2)
    L.TessBaseAPISetPageSegMode(h, int(psm))
    sock.sendall(b"BEREIT\n")

    f = sock.makefile("rb")
    while True:
        zeile = f.readline()
        if not zeile:
            break
        breite, hoehe, n = (int(x) for x in zeile.split())
        if breite < 0:
            break
        puffer = f.read(n)
        L.TessBaseAPISetImage(h, puffer, breite, hoehe, 3, breite * 3)
        p = L.TessBaseAPIGetUTF8Text(h)
        text = ctypes.string_at(p) if p else b""
        if p:
            L.TessDeleteText(ctypes.c_void_p(p))
        sock.sendall(f"{len(text)}\n".encode())
        sock.sendall(text)

    # Ohne das schreibt tesseract beim Prozessende fuer jedes geladene
    # Woerterbuch eine LEAK-Warnung auf stderr. Eine Warnung, die immer
    # erscheint, verdeckt die eine, die etwas bedeutet.
    L.TessBaseAPIEnd(h)
    L.TessBaseAPIDelete(h)


if __name__ == "__main__":
    main()
