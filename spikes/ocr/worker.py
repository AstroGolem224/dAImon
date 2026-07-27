#!/usr/bin/env python3
"""Persistent OCR worker: holds one libtesseract API alive, takes raw RGB
frames over a unix socketpair. Protocol: "<w> <h> <nbytes>\\n" + bytes,
answers "<nbytes>\\n" + utf8. "-1 -1 -1\\n" quits.
"""
import ctypes
import os
import socket
import sys


def main():
    tessdata, lang, psm = sys.argv[1], sys.argv[2], sys.argv[3]
    fd = int(os.environ["WORKER_FD"])
    sock = socket.socket(fileno=fd)

    L = ctypes.CDLL("/usr/lib/libtesseract.so")
    L.TessBaseAPICreate.restype = ctypes.c_void_p
    L.TessBaseAPIInit3.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    L.TessBaseAPISetPageSegMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    L.TessBaseAPISetImage.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                      ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int]
    L.TessBaseAPIGetUTF8Text.argtypes = [ctypes.c_void_p]
    L.TessBaseAPIGetUTF8Text.restype = ctypes.c_void_p
    L.TessDeleteText.argtypes = [ctypes.c_void_p]

    h = ctypes.c_void_p(L.TessBaseAPICreate())
    L.TessBaseAPIInit3(h, tessdata.encode(), lang.encode())
    L.TessBaseAPISetPageSegMode(h, int(psm))
    sock.sendall(b"READY\n")

    f = sock.makefile("rb")
    while True:
        line = f.readline()
        if not line:
            break
        w, ht, n = (int(x) for x in line.split())
        if w < 0:
            break
        buf = f.read(n)
        L.TessBaseAPISetImage(h, buf, w, ht, 3, w * 3)
        p = L.TessBaseAPIGetUTF8Text(h)
        out = ctypes.string_at(p) if p else b""
        if p:
            L.TessDeleteText(ctypes.c_void_p(p))
        sock.sendall(f"{len(out)}\n".encode())
        sock.sendall(out)


if __name__ == "__main__":
    main()
