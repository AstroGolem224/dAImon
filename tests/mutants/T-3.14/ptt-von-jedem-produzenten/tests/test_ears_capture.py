"""T-3.1 -- was sich ohne Mikrofon pruefen laesst.

Der eigentliche Nachweis ist der Lebenszyklus im PipeWire-Graphen und gehoert in
den Verifizierer; hier steht nur, was auch auf einer Maschine ohne Aufnahmegeraet
gilt. Der Zweck dieser Datei ist eng: dass niemand `close()` versehentlich in
`stop()` zurueckaendert.
"""

from __future__ import annotations

import os

import pytest

capture = pytest.importorskip("daimon.ears.capture")


class AttrappenStrom:
    def __init__(self, **kw):
        self.kw = kw
        self.aufrufe: list[str] = []

    def start(self):
        self.aufrufe.append("start")

    def stop(self):
        self.aufrufe.append("stop")

    def close(self):
        self.aufrufe.append("close")


def test_ausschalten_schliesst_und_pausiert_nicht(monkeypatch):
    stroeme: list[AttrappenStrom] = []

    def fabrik(**kw):
        stroeme.append(AttrappenStrom(**kw))
        return stroeme[-1]

    monkeypatch.setattr(capture.sd, "InputStream", fabrik)

    a = capture.Aufnahme()
    a.start()
    a.stop()
    a.stop()  # idempotent, der Kill-Switch darf doppelt kommen

    assert stroeme[0].aufrufe == ["start", "close"]
    assert "stop" not in stroeme[0].aufrufe


def test_parameter_gehen_so_an_den_strom(monkeypatch):
    stroeme: list[AttrappenStrom] = []
    monkeypatch.setattr(
        capture.sd, "InputStream",
        lambda **kw: (stroeme.append(AttrappenStrom(**kw)), stroeme[-1])[1],
    )

    a = capture.Aufnahme()
    a.start()
    kw = stroeme[0].kw
    assert kw["device"] == "pipewire"      # nicht "default"
    assert kw["samplerate"] == 16000
    assert kw["channels"] == 1
    assert kw["dtype"] == "int16"
    assert kw["blocksize"] == 512
    assert a.zustand() == {"offen": True, "blocks": 0, "rate": 16000,
                           "kanaele": 1, "dtype": "int16", "device": "pipewire"}
    a.stop()
    assert a.zustand()["offen"] is False


def test_latenz_steht_in_der_umgebung():
    # Der Import des Moduls hat sie gesetzt; der Wert passt zu Blockgroesse
    # und Rate.
    assert os.environ["PIPEWIRE_LATENCY"] == "512/16000"


def test_stream_traegt_unseren_namen():
    # Ohne diese Eigenschaften heisst der Knoten `alsa_capture.python3.12`, und
    # dann ist "nach close() kein Aufnahmestrom mehr" keine Aussage ueber uns.
    props = os.environ["PIPEWIRE_PROPS"]
    assert "application.name = daimon-ears" in props
    assert "node.name = daimon-ears" in props


def test_gescheitertes_close_meldet_weiterhin_offen():
    """Ein Kill-Switch, der sich faelschlich als aus meldet, ist schlimmer als
    einer, der scheitert: der Nutzer glaubt dem Indikator.

    Gegenprobe zur Reihenfolge in `Aufnahme.stop()` -- erst schliessen, dann die
    Referenz aufgeben.
    """
    from daimon.ears.capture import Aufnahme

    class KaputterStrom:
        def close(self):
            raise OSError("PortAudio mag nicht")

    a = Aufnahme()
    a._stream = KaputterStrom()
    try:
        a.stop()
    except OSError:
        pass
    else:
        raise AssertionError("stop() muss den Fehler durchreichen")
    assert a.zustand()["offen"] is True, "nach gescheitertem close() gilt: noch offen"
    a._stream = None
