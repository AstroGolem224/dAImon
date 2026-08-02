"""T-3.3 -- was sich ohne Mikrofon pruefen laesst.

Der Schwerpunkt liegt bewusst auf **Bytes**, nicht auf Zaehlern. Ein Test, der
nach `verwirf()` nur `geschrieben == 0` prueft, ist gruen fuer eine Umsetzung,
die bloss den Schreibzeiger zuruecksetzt und das Audio im Speicher stehen
laesst -- also fuer genau den Fehler, den dieser Task ausschliessen soll.
Deshalb wird hier jedes Mal der ganze Puffer gelesen und mit Nullen verglichen.
"""

from __future__ import annotations

import pytest

ring = pytest.importorskip("daimon.ears.ring")


def _chunk(fuellung: int = 0xA5) -> bytes:
    return bytes([fuellung]) * ring.CHUNK_BYTES


def _roh(r: ring.Ring) -> bytes:
    """Der ganze Puffer als bytes -- nur im Test, und nur zum Vergleichen."""
    return bytes(memoryview(r._mm))


@pytest.fixture()
def r():
    x = ring.Ring()
    try:
        yield x
    finally:
        x.schliessen()


# -- Groesse, vorab alloziert -------------------------------------------------


def test_zahlen_aus_dem_plan():
    assert ring.CHUNK_BYTES == 1024
    assert ring.CHUNKS == 625
    assert ring.BYTES == 640_000            # 20 s * 16000 Hz * 2 Byte
    assert ring.BYTES == 625 * 1024         # = 625 KiB, geht glatt auf


def test_speicher_steht_vor_dem_ersten_chunk_bereit(r):
    # "vorab alloziert" heisst: die vollen 640 000 Byte existieren, bevor
    # irgendetwas hineingeschrieben wurde -- und sie sind null.
    assert r.zustand()["geschrieben"] == 0
    assert len(_roh(r)) == ring.BYTES
    assert _roh(r) == b"\x00" * ring.BYTES


def test_ring_waechst_nicht(r):
    for i in range(ring.CHUNKS + 7):
        r.schreibe(_chunk(i % 251 + 1))
    assert len(_roh(r)) == ring.BYTES
    assert r.zustand()["gefuellt"] == ring.CHUNKS
    assert r.zustand()["geschrieben"] == ring.CHUNKS + 7


def test_falsche_chunkgroesse_wird_gemeldet_nicht_aufgefuellt(r):
    with pytest.raises(ring.ChunkGroesseFehler):
        r.schreibe(b"\x01" * (ring.CHUNK_BYTES - 2))
    with pytest.raises(ring.ChunkGroesseFehler):
        r.schreibe(b"\x01" * (ring.CHUNK_BYTES + 2))
    assert _roh(r) == b"\x00" * ring.BYTES, "ein Fehlversuch darf nichts ablegen"


# -- mlock --------------------------------------------------------------------


def test_mlock_hat_gegriffen_laut_kernel(r):
    """Nicht "mlock() lieferte 0", sondern was der Kernel in smaps sagt."""
    assert r.gesperrt is True
    kib = r.gesperrte_kib()
    if kib < 0:
        pytest.skip("/proc/self/smaps nicht lesbar -- keine Aussage moeglich")
    assert kib >= ring.BYTES // 1024 == 625


def test_mlock_fehlschlag_ist_laut(monkeypatch):
    """Ein stillschweigend ungelockter Ring erfuellt die Zusage nicht."""

    class KaputteLibc:
        def mlock(self, *_a):
            return -1

        def munlock(self, *_a):
            return 0

    monkeypatch.setattr(ring, "_libc", lambda: KaputteLibc())
    with pytest.raises(ring.MlockFehler):
        ring.Ring()


# -- Vorlauf: Sicht, keine Kopie ---------------------------------------------


def test_vorlauf_ist_eine_sicht_in_den_puffer(r):
    r.schreibe(_chunk(0x11))
    (mv,) = r.vorlauf()
    assert isinstance(mv, memoryview)
    # Beweis, dass es keine Kopie ist: eine Aenderung am Ring schlaegt durch.
    r._mm[0:1] = b"\x42"
    assert mv[0] == 0x42


def test_vorlauf_laenge_und_inhalt(r):
    werte = [(k % 251) + 1 for k in range(59)]
    for w in werte:
        r.schreibe(_chunk(w))
    assert r.vorlauf_chunks == 39            # 1,25 s / 32 ms, abgerundet
    stuecke = r.vorlauf()
    assert len(stuecke) == 1
    zusammen = b"".join(bytes(s) for s in stuecke)
    assert len(zusammen) == 39 * ring.CHUNK_BYTES
    # Zeitliche Reihenfolge, nicht Ringreihenfolge: die letzten 39.
    gelesen = [zusammen[k * ring.CHUNK_BYTES] for k in range(39)]
    assert gelesen == werte[-39:]


def test_vorlauf_ueber_die_ringgrenze_bleibt_in_reihenfolge(r):
    # So weit schreiben, dass der Vorlauf ueber die Ringgrenze umbricht.
    werte = [(k % 251) + 1 for k in range(ring.CHUNKS + 10)]
    for w in werte:
        r.schreibe(_chunk(w))
    stuecke = r.vorlauf()
    assert len(stuecke) == 2, "hier muss der Ring umbrechen"
    zusammen = b"".join(bytes(s) for s in stuecke)
    assert len(zusammen) == 39 * ring.CHUNK_BYTES
    gelesen = [zusammen[k * ring.CHUNK_BYTES] for k in range(39)]
    assert gelesen == werte[-39:]


def test_vorlauf_am_anfang_liefert_nur_was_da_ist(r):
    assert r.vorlauf() == []
    r.schreibe(_chunk(7))
    r.schreibe(_chunk(8))
    (mv,) = r.vorlauf()
    assert len(mv) == 2 * ring.CHUNK_BYTES


def test_vorlauf_fenster_liegt_im_plan():
    """31--47 Chunks laut Akzeptanzliste; abgerundet wird, nie aufgerundet.

    Aufrunden waere bei 1,5 s = 46,875 Chunks ein Vorlauf von 1,504 s und
    damit ueber dem Planfenster. Abrunden kostet an der Untergrenze weniger
    als einen Chunk (31 statt 31,25 -- 0,992 s), und der Plan nennt fuer
    1,0 s selbst 31 Chunks.
    """
    for s, chunks in ((1.0, 31), (1.25, 39), (1.5, 46)):
        r = ring.Ring(vorlauf_s=s)
        try:
            assert r.vorlauf_chunks == chunks
            assert 31 <= r.vorlauf_chunks <= 47
            assert r.vorlauf_chunks * ring.CHUNK_MS / 1000.0 <= 1.5
        finally:
            r.schliessen()


def test_vorlauf_ausserhalb_des_plans_warnt(caplog):
    with caplog.at_level("WARNING", logger="daimon.ears.ring"):
        r = ring.Ring(vorlauf_s=0.2)
        r.schliessen()
    assert "vorlauf_s" in caplog.text


# -- Der Kern: verwerfen heisst ueberschreiben --------------------------------


def test_verwerfen_nullt_die_bytes_nicht_nur_den_zeiger(r):
    for i in range(1, 200):
        r.schreibe(_chunk(i % 251 + 1))
    vorher = _roh(r)
    assert vorher.count(0) < ring.BYTES, "vorher steht Material im Puffer"

    r.verwirf()

    nachher = _roh(r)
    assert nachher == b"\x00" * ring.BYTES, (
        "nach verwirf() darf im Puffer kein einziges Byte Audio mehr stehen"
    )
    assert set(nachher) == {0}
    assert r.zustand()["geschrieben"] == 0
    assert r.vorlauf() == []


def test_verwerfen_nullt_den_ganzen_ring_nicht_nur_das_letzte_segment(r):
    """Zwei Runden: die zweite darf die erste nicht ueberleben lassen.

    Wer nur `geschrieben` viele Chunks nullt, laesst nach einem kurzen zweiten
    Segment das lange erste stehen -- der klassische Halbfehler.
    """
    for i in range(ring.CHUNKS):
        r.schreibe(_chunk(0xA5))
    r._n = 0                      # Zeiger zuruecksetzen OHNE zu nullen
    r.schreibe(_chunk(0x5A))      # nur ein einziger neuer Chunk
    r.verwirf()
    assert _roh(r) == b"\x00" * ring.BYTES
    assert b"\xa5" not in _roh(r)


def test_verwerfen_erwischt_auch_schon_ausgegebene_sichten(r):
    """Weil `vorlauf()` Sichten liefert, kann der Aufrufer nichts behalten,
    ohne es ausdruecklich zu kopieren."""
    for i in range(1, 50):
        r.schreibe(_chunk(i))
    (mv,) = r.vorlauf()
    assert bytes(mv) != b"\x00" * len(mv)
    r.verwirf()
    assert bytes(mv) == b"\x00" * len(mv)


def test_nach_dem_verwerfen_ist_der_ring_weiter_benutzbar(r):
    r.schreibe(_chunk(0x33))
    r.verwirf()
    r.schreibe(_chunk(0x44))
    (mv,) = r.vorlauf()
    assert len(mv) == ring.CHUNK_BYTES
    assert bytes(mv) == _chunk(0x44)
    assert r.zustand()["verworfen"] == 1


def test_schliessen_nullt_vor_der_freigabe(monkeypatch):
    """`munmap` gibt die Seiten an den Kernel zurueck -- was dann noch
    drinsteht, steht drin. Also wird vorher genullt, und das wird hier im
    letzten Moment vor der Freigabe an den Bytes abgelesen."""
    r = ring.Ring()
    for i in range(1, 30):
        r.schreibe(_chunk(i))
    gesehen: list[bytes] = []
    echt = ring.Ring._freigeben

    def spion(self):
        gesehen.append(bytes(memoryview(self._mm)))
        echt(self)

    monkeypatch.setattr(ring.Ring, "_freigeben", spion)
    r.schliessen()
    assert gesehen == [b"\x00" * ring.BYTES]
    assert r.zustand()["offen"] is False
    assert r.gesperrt is False
    r.schliessen()  # idempotent
    # Nach der Freigabe darf nichts mehr roh auf die Adresse schreiben.
    with pytest.raises(ValueError):
        r.verwirf()
    with pytest.raises(ValueError):
        r.schreibe(_chunk(1))
    assert r.zustand()["gesperrte_kib"] == -1


# -- niemals auf Platte -------------------------------------------------------


def test_puffer_ist_ein_anonymes_mapping(r):
    """Anonym heisst: inode 0 und kein Dateiname in /proc/self/maps -- also
    nichts, was der Kernel je auf Platte zurueckschreiben koennte."""
    zeile = None
    with open("/proc/self/maps") as fh:
        for z in fh:
            a, _, rest = z.split(" ", 1)[0].partition("-")
            if int(a, 16) <= r.adresse < int(rest, 16):
                zeile = z
                break
    assert zeile is not None, "die Abbildung muss in maps stehen"
    felder = zeile.split()
    # `p` statt `s`: ein SHARED anonymes Mapping ist unter Linux
    # tmpfs-gestuetzt und traegt "/dev/zero (deleted)" samt Inode. Das ist
    # der Vorgabewert von mmap.mmap(-1, n) -- siehe ring.py.
    assert felder[1].endswith("p"), f"MAP_PRIVATE erwartet, ist {felder[1]}"
    assert felder[4] == "0", f"inode muss 0 sein (anonym), ist {felder[4]}"
    assert len(felder) == 5, f"kein Dateiname erwartet: {zeile!r}"


def test_modul_haelt_keine_datei_offen_und_kein_tempfile(r):
    """Positivkontrolle inbegriffen: die Messkette wuerde eine offene Datei
    sehen. Sonst waere "keine Datei offen" die Nullaussage schlechthin."""
    import os

    def offene() -> set[str]:
        fds = set()
        for fd in os.listdir("/proc/self/fd"):
            try:
                fds.add(os.readlink(f"/proc/self/fd/{fd}"))
            except OSError:
                pass
        return fds

    for i in range(1, 40):
        r.schreibe(_chunk(i))
    r.vorlauf()
    r.verwirf()
    # ring.py selbst ist eine regulaere Datei, die sonst niemand offen haelt.
    assert ring.__file__ not in offene()
    # Positivkontrolle: ein echter Deskriptor MUSS hier auftauchen, sonst
    # waere die Zeile darueber die Nullaussage schlechthin.
    kontrolle = open(ring.__file__)
    try:
        assert ring.__file__ in offene(), (
            "die Messung sieht offene Dateien nicht -- sie sagt nichts aus"
        )
    finally:
        kontrolle.close()
    assert ring.__file__ not in offene()

    # Zur Laufzeit gelesen, nicht im Dateitext gesucht: ein `grep` auf die
    # Quelle ist an der Schreibweise zu umgehen und schlaegt hier ausserdem
    # am eigenen Modulkopf an, der das Wort erklaert.
    assert "tempfile" not in dir(ring)
    assert "pathlib" not in dir(ring)


def test_einstellungen_aus_der_config():
    from daimon.common import config as cfgmod

    class Fake:
        def get(self, pfad, fallback=None):
            return {"ears.ring.vorlauf_s": 1.1}.get(pfad, fallback)

    assert ring.einstellungen(Fake()) == {"vorlauf_s": 1.1}
    assert cfgmod.VORGABEN["ears"]["ring"]["vorlauf_s"] == ring.VORLAUF_S
