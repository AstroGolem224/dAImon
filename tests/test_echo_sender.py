"""Echo-Referenz, Senderseite (Vertrag: UMBRA-Notes/DDs/dAImon/Echo-Referenz-Plan.md).

Ein Datagramm je Block, int16 mono 16 kHz, base64. Best effort: ein toter
Empfaenger darf die Stimme um nichts verzoegern -- und erst recht keinen
Fehler werfen.
"""
from __future__ import annotations

import base64
import json
import socket

import numpy as np
import pytest

from daimon.face.echo import EchoSender, nach_16k


def _sinus(rate: int, dauer_s: float = 0.1) -> bytes:
    t = np.arange(int(rate * dauer_s)) / rate
    return (np.sin(2 * np.pi * 440 * t) * 20000).astype("<i2").tobytes()


def test_resampling_trifft_das_laengenverhaeltnis():
    roh = _sinus(22050)
    ziel = nach_16k(roh, 22050)
    erwartet = (len(roh) // 2) * 16000 // 22050
    assert abs(len(ziel) // 2 - erwartet) <= 1
    assert len(ziel) % 2 == 0


def test_16k_geht_unveraendert_durch():
    roh = _sinus(16000)
    assert nach_16k(roh, 16000) == roh


def test_das_datagramm_kommt_an_und_traegt_das_pcm(tmp_path):
    pfad = tmp_path / "echo.sock"
    empf = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    empf.bind(str(pfad))
    empf.settimeout(2.0)

    s = EchoSender(pfad)
    roh = _sinus(22050)
    s.senden(roh, 22050)

    paket = json.loads(empf.recv(1 << 18).decode("utf-8"))
    empf.close()
    assert paket["art"] == "echo" and paket["rate"] == 16000
    pcm = base64.b64decode(paket["pcm"])
    assert pcm == nach_16k(roh, 22050)


def test_grosse_bloecke_kommen_zerteilt_und_vollstaendig_an(tmp_path):
    """Ein langes Segment (6 s) muss ankommen -- notfalls in Stuecken. Ein
    verworfener Block waere ein Loch in der Referenz, genau dort, wo die
    Korrelation es braucht."""
    pfad = tmp_path / "echo.sock"
    empf = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    empf.bind(str(pfad))
    empf.settimeout(2.0)

    s = EchoSender(pfad)
    roh = _sinus(22050, dauer_s=6.0)
    s.senden(roh, 22050)

    stuecke = []
    while True:
        try:
            paket = json.loads(empf.recv(1 << 18).decode("utf-8"))
        except socket.timeout:
            break
        stuecke.append(base64.b64decode(paket["pcm"]))
    empf.close()
    assert len(stuecke) >= 2
    assert b"".join(stuecke) == nach_16k(roh, 22050)


def test_ohne_empfaenger_faellt_nichts(tmp_path):
    s = EchoSender(tmp_path / "gibt-es-nicht.sock")
    s.senden(_sinus(48000), 48000)  # darf nicht werfen
    s.senden(_sinus(48000), 48000)  # auch nicht beim zweiten Mal


def test_leere_und_schiefe_bloecke_sind_kein_fehler(tmp_path):
    s = EchoSender(tmp_path / "gibt-es-nicht.sock")
    s.senden(b"", 22050)
    s.senden(b"\x01", 22050)  # ungerade Laenge: halbes Sample
