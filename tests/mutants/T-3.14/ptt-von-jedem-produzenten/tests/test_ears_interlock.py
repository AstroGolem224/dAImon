"""T-3.4 -- was sich ohne Lautsprecher und ohne Mikrofon pruefen laesst.

Geprueft wird die **Logik** des Tors: Sperrzustaende, der Nachlauf an einer
eingespeisten Uhr, der Echo-Abgleich an eingespeisten Puffern und das Verhalten
bei gedruecktem PTT. Die akustische Kette Lautsprecher -> Raum -> Mikrofon ->
STT ist hier nicht messbar und wird auch nicht behauptet; sie gehoert nach
T-3.15.

Zwei Dinge werden absichtlich anders geprueft, als es bequem waere:

  * der **Nachlauf** wird an beiden Seiten der Grenze gemessen (1 ms davor
    gesperrt, 1 ms danach frei). Nur "danach frei" waere auch fuer eine Sperre
    gruen, die gar nicht sperrt.
  * das **Echo** wird gegen eine Positivkontrolle geprueft: fremdes Rauschen
    darf nicht als Echo durchgehen. Ohne die waere "alles wird verworfen" die
    einfachste bestandene Umsetzung -- und die waere nutzlos.
"""

from __future__ import annotations

import json
import math
import os
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

interlock = pytest.importorskip("daimon.ears.interlock")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Uhr:
    """Eingespeiste monotone Zeit. Geht nur vorwaerts, wenn man sie schiebt."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def plus(self, dt: float) -> None:
        self.t += dt


def _chunk(fuellung: int = 0x11) -> bytes:
    return bytes([fuellung]) * interlock.CHUNK_BYTES


def _rauschen(saat: int, n: int = interlock.CHUNK_SAMPLES) -> bytes:
    r = random.Random(saat)
    aus = bytearray()
    for _ in range(n):
        v = r.randint(-8000, 8000) & 0xFFFF
        aus += bytes((v & 0xFF, v >> 8))
    return bytes(aus)


def _ton(n: int = interlock.CHUNK_SAMPLES, phase: float = 0.0) -> bytes:
    aus = bytearray()
    for i in range(n):
        v = int(6000 * math.sin(2 * math.pi * 440 * (i + phase) / interlock.RATE))
        aus += (v & 0xFFFF).to_bytes(2, "little")
    return bytes(aus)


# -- Kriterium 1: gesperrt waehrend der Wiedergabe ---------------------------


def test_frei_ohne_wiedergabe():
    s = interlock.Sperre(uhr=Uhr())
    assert s.annehmen(_chunk()) is True
    assert s.zustand()["gesperrt"] is False
    assert s.zustand()["grund"] is None


def test_gesperrt_waehrend_der_wiedergabe():
    u = Uhr()
    s = interlock.Sperre(uhr=u)
    s.wiedergabe_an()
    assert s.annehmen(_chunk()) is False
    z = s.zustand()
    assert z["gesperrt"] is True
    assert z["grund"] == "wiedergabe"


def test_haengende_wiedergabe_laeuft_nicht_ab():
    """Fail-closed: kein Zeitablauf hebt eine nicht abgemeldete Wiedergabe auf."""
    u = Uhr()
    s = interlock.Sperre(uhr=u)
    s.wiedergabe_an()
    u.plus(3600.0)
    assert s.annehmen(_chunk()) is False
    assert s.zustand()["grund"] == "wiedergabe"


# -- Kriterium 1: 500 ms Nachlauf, an beiden Seiten der Grenze ---------------


def test_nachlauf_kurz_vor_und_kurz_nach_der_grenze():
    u = Uhr()
    s = interlock.Sperre(nachlauf_s=0.5, uhr=u)
    s.wiedergabe_an()
    s.wiedergabe_aus()
    bis = s.zustand()["bis"]
    assert bis == pytest.approx(u.t + 0.5, abs=1e-9)

    for versatz in (0.0, 0.25, 0.499):
        u.t = bis - 0.5 + versatz
        assert s.annehmen(_chunk()) is False, versatz
        assert s.zustand()["grund"] == "nachlauf"

    u.t = bis - 0.001
    assert s.annehmen(_chunk()) is False
    u.t = bis + 0.001
    assert s.annehmen(_chunk()) is True
    assert s.zustand()["gesperrt"] is False


def test_nachlauf_mit_echter_uhr():
    """Dieselbe Grenze, ohne eingespeiste Uhr -- 60 ms statt 500, sonst gleich."""
    s = interlock.Sperre(nachlauf_s=0.06)
    s.wiedergabe_an()
    s.wiedergabe_aus()
    time.sleep(0.03)
    assert s.annehmen(_chunk()) is False
    time.sleep(0.05)
    assert s.annehmen(_chunk()) is True


def test_zweite_wiedergabe_verkuerzt_den_nachlauf_nicht():
    u = Uhr()
    s = interlock.Sperre(nachlauf_s=0.5, uhr=u)
    s.wiedergabe_an()
    s.wiedergabe_aus()
    erstes_ende = s.zustand()["bis"]
    u.plus(0.2)
    s.wiedergabe_an()
    s.wiedergabe_aus()
    assert s.zustand()["bis"] > erstes_ende


def test_abmelden_ohne_anmelden_sperrt_trotzdem():
    u = Uhr()
    s = interlock.Sperre(nachlauf_s=0.5, uhr=u)
    s.wiedergabe_aus()
    assert s.annehmen(_chunk()) is False
    u.plus(0.6)
    assert s.annehmen(_chunk()) is True


# -- Kriterium 2: die Sperre gilt auch bei gedruecktem PTT -------------------


def test_ptt_gedrueckt_hebt_die_sperre_nicht_auf():
    u = Uhr()
    s = interlock.Sperre(uhr=u)
    s.wiedergabe_an()
    for _ in range(5):
        assert s.annehmen(_chunk(), ptt_gedrueckt=True) is False
    s.wiedergabe_aus()
    for _ in range(5):
        assert s.annehmen(_chunk(), ptt_gedrueckt=True) is False
    z = s.zustand()
    assert z["ptt_waehrend_sperre"] == 10
    assert z["angenommen"] == 0
    # Positivkontrolle: nach dem Nachlauf laesst dieselbe Sperre denselben
    # Chunk mit derselben gedrueckten Taste durch.
    u.plus(1.0)
    assert s.annehmen(_chunk(), ptt_gedrueckt=True) is True


# -- Kriterium 3: Echo-Referenz, einspeisbar --------------------------------


def test_eingespeiste_referenz_wird_als_echo_verworfen():
    s = interlock.Sperre(uhr=Uhr())
    puffer = _rauschen(1)
    s.echo_referenz(puffer)
    assert s.annehmen(puffer) is False
    assert s.zustand()["verworfen_echo"] == 1


def test_gedaempftes_echo_wird_erkannt():
    """Normierte Korrelation: eine leisere Kopie ist dieselbe Kopie."""
    s = interlock.Sperre(uhr=Uhr())
    roh = _rauschen(2)
    s.echo_referenz(roh)
    leise = b"".join(
        (int(int.from_bytes(roh[i:i + 2], "little", signed=True) * 0.2)
         & 0xFFFF).to_bytes(2, "little")
        for i in range(0, len(roh), 2)
    )
    assert s.annehmen(leise) is False
    assert s.zustand()["verworfen_echo"] == 1


def test_fremdes_signal_geht_durch():
    """Positivkontrolle. Ohne sie waere "verwirf alles" die beste Umsetzung."""
    s = interlock.Sperre(uhr=Uhr())
    s.echo_referenz(_rauschen(3))
    assert s.annehmen(_rauschen(4)) is True
    assert s.zustand()["angenommen"] == 1


def test_echo_im_referenzfenster_mit_versatz():
    """Die Referenz kommt in Stuecken; das Echo liegt irgendwo darin."""
    s = interlock.Sperre(uhr=Uhr())
    treffer = _rauschen(5)
    for teil in (_rauschen(6), treffer, _rauschen(7)):
        s.echo_referenz(teil)
    assert s.annehmen(treffer) is False


def test_referenzfenster_ist_begrenzt():
    s = interlock.Sperre(uhr=Uhr(), echo_fenster_s=0.1)
    grenze = int(0.1 * interlock.RATE) * 2
    for i in range(20):
        s.echo_referenz(_rauschen(100 + i))
    assert s.zustand()["referenz_bytes"] <= grenze


def test_stiller_chunk_ist_nicht_vergleichbar_und_wird_verworfen():
    s = interlock.Sperre(uhr=Uhr())
    s.echo_referenz(_rauschen(8))
    assert s.annehmen(b"\x00" * interlock.CHUNK_BYTES) is False
    assert s.zustand()["verworfen_unklar"] == 1


def test_ohne_chunk_bei_vorliegender_referenz_wird_gesperrt():
    s = interlock.Sperre(uhr=Uhr())
    s.echo_referenz(_rauschen(9))
    assert s.annehmen(None) is False
    assert s.zustand()["verworfen_unklar"] == 1


def test_kaputte_chunkgroesse_wirft_nicht_sondern_sperrt():
    """Ein Tor, das wirft, wird von einem `except` des Aufrufers geoeffnet."""
    s = interlock.Sperre(uhr=Uhr())
    s.echo_referenz(_rauschen(10))
    assert s.annehmen(b"\x01\x02\x03") is False
    assert s.zustand()["verworfen_unklar"] == 1


def test_referenz_leeren():
    s = interlock.Sperre(uhr=Uhr())
    roh = _rauschen(11)
    s.echo_referenz(roh)
    assert s.annehmen(roh) is False
    s.echo_referenz_leeren()
    assert s.zustand()["referenz_bytes"] == 0
    assert s.annehmen(roh) is True


def test_ton_gegen_ton_mit_anderer_phase_ist_immer_noch_echo():
    s = interlock.Sperre(uhr=Uhr())
    s.echo_referenz(_ton(interlock.CHUNK_SAMPLES * 4))
    assert s.annehmen(_ton(phase=137.0)) is False


# -- Monotone Zeit, fail-closed ---------------------------------------------


def test_rueckwaerts_springende_uhr_sperrt():
    u = Uhr()
    s = interlock.Sperre(uhr=u)
    assert s.annehmen(_chunk()) is True
    u.plus(-5.0)
    assert s.annehmen(_chunk()) is False
    z = s.zustand()
    assert z["grund"] == "uhr"
    assert z["uhr_kaputt"] is True
    # Und sie bleibt gesperrt: eine Uhr, die gesprungen ist, ist keine
    # Grundlage mehr, auf der man entsperrt.
    u.plus(100.0)
    assert s.annehmen(_chunk()) is False


def test_werfende_uhr_sperrt():
    def kaputt() -> float:
        raise OSError("keine Uhr")

    s = interlock.Sperre(uhr=kaputt)
    assert s.annehmen(_chunk()) is False
    assert s.zustand()["grund"] == "uhr"


def test_vorgabe_ist_monotonic():
    s = interlock.Sperre()
    assert s._uhr is time.monotonic


# -- Kriterium 4: Diagnose ---------------------------------------------------


def test_zustand_hat_die_drei_felder():
    u = Uhr()
    s = interlock.Sperre(nachlauf_s=0.5, uhr=u)
    z = s.zustand()
    for feld in ("gesperrt", "grund", "bis"):
        assert feld in z
    assert z["bis"] is None
    s.wiedergabe_an()
    s.wiedergabe_aus()
    z = s.zustand()
    assert z["gesperrt"] is True
    assert z["grund"] == "nachlauf"
    assert z["bis"] == pytest.approx(u.t + 0.5)
    assert z["restsekunden"] == pytest.approx(0.5)
    assert json.dumps(z)  # muss durch den Diagnose-Socket passen


def test_diagnose_socket_zeigt_die_sperre():
    """Der Zustand von aussen, ueber einen echten Unix-Socket."""
    with tempfile.TemporaryDirectory() as d:
        pfad = os.path.join(d, "diag.sock")
        p = subprocess.Popen(
            [sys.executable, "-m", "daimon.ears.interlock",
             "--diag-socket", pfad, "--sekunden", "2.5",
             "--wiedergabe-s", "0.5", "--nachlauf-s", "0.5"],
            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            for _ in range(200):
                if os.path.exists(pfad):
                    break
                time.sleep(0.01)
            assert os.path.exists(pfad), "Diagnose-Socket kam nicht hoch"

            gesehen = []
            ende = time.monotonic() + 2.0
            while time.monotonic() < ende:
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    c.connect(pfad)
                    z = json.loads(c.makefile("rb").readline())
                except (OSError, ValueError):
                    break
                finally:
                    c.close()
                gesehen.append((z["gesperrt"], z["grund"]))
                time.sleep(0.05)
        finally:
            p.terminate()
            p.wait(timeout=5)

    gruende = [g for _, g in gesehen]
    assert gesehen, "keine Antwort vom Diagnose-Socket"
    assert "wiedergabe" in gruende
    assert "nachlauf" in gruende
    assert (False, None) in gesehen, "die Sperre ging nie wieder auf"


# -- Threadsicherheit --------------------------------------------------------


def test_ring_unter_demselben_schloss():
    ring = pytest.importorskip("daimon.ears.ring")
    r = ring.Ring()
    try:
        s = interlock.Sperre(uhr=Uhr(), ring=r)
        s.schreibe(_chunk(0x7F))
        assert r.zustand()["geschrieben"] == 1
        assert len(s.vorlauf()) >= 1
        s.verwirf()
        assert r.zustand()["geschrieben"] == 0
    finally:
        r.schliessen()


def test_gleichzeitiger_zugriff_bleibt_widerspruchsfrei():
    """Kein Beweis der Abwesenheit von Wettlaeufen -- aber der Nachweis, dass
    die beiden Pfade sich wirklich beruehren und es aushalten."""
    ring = pytest.importorskip("daimon.ears.ring")
    r = ring.Ring()
    fehler: list[BaseException] = []
    try:
        s = interlock.Sperre(nachlauf_s=0.001, ring=r)

        def schreiber() -> None:
            try:
                for _ in range(500):
                    s.schreibe(_chunk(0x22))
                    s.annehmen(_chunk(0x22), ptt_gedrueckt=True)
            except BaseException as e:  # noqa: BLE001
                fehler.append(e)

        def wiedergabe() -> None:
            try:
                for _ in range(500):
                    s.wiedergabe_an()
                    s.wiedergabe_aus()
                    s.zustand()
            except BaseException as e:  # noqa: BLE001
                fehler.append(e)

        def verwerfer() -> None:
            try:
                for _ in range(200):
                    s.verwirf()
            except BaseException as e:  # noqa: BLE001
                fehler.append(e)

        t = [threading.Thread(target=f) for f in
             (schreiber, schreiber, wiedergabe, verwerfer)]
        for x in t:
            x.start()
        for x in t:
            x.join(timeout=30)
        assert not any(x.is_alive() for x in t)
        assert not fehler, fehler
        z = r.zustand()
        assert 0 <= z["geschrieben"] <= 1000
        assert z["verworfen"] == 200
    finally:
        r.schliessen()


# -- Konfiguration -----------------------------------------------------------


def test_vorgaben_kommen_aus_der_konfiguration(tmp_path):
    from daimon.common import config

    datei = tmp_path / "daimon.toml"
    datei.write_text(
        "[ears.interlock]\nnachlauf_s = 0.75\n"
        "echo_fenster_s = 2.0\necho_schwelle = 0.9\n"
    )
    cfg = config.load(config_path=datei, make_dirs=False)
    assert interlock.einstellungen(cfg) == {
        "nachlauf_s": 0.75, "echo_fenster_s": 2.0, "echo_schwelle": 0.9,
    }


def test_mitgelieferte_toml_haelt_die_planvorgabe():
    import tomllib
    from pathlib import Path

    wurzel = Path(__file__).resolve().parent.parent
    with (wurzel / "config" / "daimon.toml").open("rb") as fh:
        v = tomllib.load(fh)["ears"]["interlock"]
    # 500 ms sind die Vorgabe aus der Akzeptanzliste, und weniger als das
    # laesst den Raumhall der letzten Silbe durch.
    assert v["nachlauf_s"] >= interlock.NACHLAUF_S
    assert 0.0 < v["echo_schwelle"] <= 1.0
