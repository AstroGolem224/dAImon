"""T-3.2 -- die Hysterese, gegen eingespeiste Wahrscheinlichkeiten.

Hier steht **kein Audio**. Silero haelt einen synthetischen Ton nicht fuer
Sprache (gemessen: Rauschen 0,0134, Stille 0,0017); ein Test, der Toene
hineinschiebt, findet null Segmente und waere nur dadurch gruen zu bekommen,
dass man die Schwellen kaputtmacht. Also wird genau das geprueft, was der Task
zusagt -- die Zustandsmaschine -- und zwar deterministisch.

Die Modellanbindung (512 Samples) steht weiter unten und getrennt.
"""

from __future__ import annotations

import pytest

from daimon.ears import vad

LAUT = 0.9
STILL = 0.02
# Zwischen `ende` (0,35) und `einsatz` (0,5): eine leise Endsilbe. Sie kann kein
# Segment starten, muss ein laufendes aber am Leben halten.
LEISE = 0.42


def chunks(ms: float) -> int:
    return round(ms / vad.CHUNK_MS)


def folge(*abschnitte: tuple[float, float]) -> list[float]:
    """[(wahrscheinlichkeit, dauer_ms), ...] -> Chunk-Folge."""
    out: list[float] = []
    for p, ms in abschnitte:
        out += [p] * chunks(ms)
    return out


# --------------------------------------------------------------------------
# Kriterium 4: 200 ms Pause -> ein Segment, 800 ms Pause -> zwei
# --------------------------------------------------------------------------

def test_kurze_pause_bleibt_ein_segment():
    # 200 ms sind 6 Chunks (192 ms), der Nachlauf verlangt 13 (416 ms).
    p = folge((LAUT, 640), (STILL, 200), (LAUT, 640), (STILL, 1000))
    assert len(vad.segmentieren(p)) == 1


def test_lange_pause_ergibt_zwei_segmente():
    # 800 ms sind 25 Chunks -- mehr als die 13 des Nachlaufs.
    p = folge((LAUT, 640), (STILL, 800), (LAUT, 640), (STILL, 1000))
    assert len(vad.segmentieren(p)) == 2


def test_die_grenze_liegt_wo_der_nachlauf_sie_hinlegt():
    """Positivkontrolle zu den beiden oben: die Umschaltung ist an EINEM Chunk.

    Ohne diese Probe wuerde ein Detektor, der grundsaetzlich ein Segment
    liefert, den 200-ms-Fall bestehen -- und einer, der bei jeder Luecke
    trennt, den 800-ms-Fall.
    """
    n = vad.Hysterese().nachlauf_chunks
    assert n == 13, n  # ceil(400 / 32)
    knapp = [LAUT] * 20 + [STILL] * (n - 1) + [LAUT] * 20
    reicht = [LAUT] * 20 + [STILL] * n + [LAUT] * 20
    assert len(vad.segmentieren(knapp)) == 1
    assert len(vad.segmentieren(reicht)) == 2


# --------------------------------------------------------------------------
# Kriterium 2: die Asymmetrie -- Wortenden werden nicht abgeschnitten
# --------------------------------------------------------------------------

def test_leise_endsilbe_bleibt_im_segment():
    """Der Kern des Tasks, in Zahlen.

    Nach dem lauten Teil folgen 320 ms bei 0,42 -- unter `einsatz`, ueber
    `ende`. Ein symmetrischer Schwellwert bei 0,5 wuerde hier aufhoeren. Das
    Segment muss diese Chunks enthalten UND den Nachlauf danach.
    """
    laut, silbe = chunks(640), chunks(320)
    p = folge((LAUT, 640), (LEISE, 320), (STILL, 1000))
    (segment,) = vad.segmentieren(p)
    beginn, ende = segment
    assert beginn == 0
    # laut + Silbe + 13 Nachlauf-Chunks, letzter Index also einer weniger.
    assert ende == laut + silbe + vad.Hysterese().nachlauf_chunks - 1
    assert ende >= laut + silbe, "die leise Silbe wurde abgeschnitten"


def test_symmetrischer_schwellwert_schneidet_ab():
    """Die Gegenprobe. Ohne sie waere der Test oben keine Aussage ueber die
    Asymmetrie, sondern nur ueber irgendein Segment.

    Symmetrisch (einsatz = ende = 0,5, kein Nachlauf) endet dasselbe Signal am
    ersten leisen Chunk -- Index 20, direkt hinter dem lauten Teil. Asymmetrisch
    endet es bei 42. Differenz: 22 Chunks, 704 ms, und in diesen 704 ms liegt
    die Endsilbe.
    """
    p = folge((LAUT, 640), (LEISE, 320), (STILL, 1000))
    (asym,) = vad.segmentieren(p)
    (sym,) = vad.segmentieren(p, einsatz=0.5, ende=0.5, nachlauf_ms=0.0)
    assert sym[1] == chunks(640), sym       # der erste leise Chunk, dann Schluss
    assert asym[1] == 42, asym
    verloren = asym[1] - sym[1]
    assert verloren == 22, verloren
    assert verloren * vad.CHUNK_MS == pytest.approx(704.0)


def test_einzelner_leiser_chunk_beendet_nicht():
    # Der Nachlauf zaehlt aufeinanderfolgend; ein Aussetzer setzt zurueck.
    p = [LAUT] * 10 + ([STILL] * 5 + [LAUT]) * 6 + [STILL] * 40
    assert len(vad.segmentieren(p)) == 1


def test_werte_unter_einsatz_starten_kein_segment():
    assert vad.segmentieren([LEISE] * 100) == []
    assert vad.segmentieren([0.49] * 100) == []
    assert vad.segmentieren([0.5]) == [(0, 0)]  # >= einsatz, nicht >


def test_offenes_segment_am_stromende_gilt():
    # Sonst faellt das letzte Wort weg -- bei Push-to-Talk der Normalfall.
    assert vad.segmentieren([STILL] * 5 + [LAUT] * 10) == [(5, 14)]


# --------------------------------------------------------------------------
# Kriterium 3: konfigurierbar, mit Grenzen
# --------------------------------------------------------------------------

def test_konfiguration_wirkt():
    h = vad.Hysterese(einsatz=0.7, ende=0.2, nachlauf_ms=320.0)
    assert h.nachlauf_chunks == 10
    assert vad.segmentieren([0.6] * 50, einsatz=0.7, ende=0.2) == []


def test_nachlauf_wird_aufgerundet():
    # 400 ms / 32 ms = 12,5. Abrunden waere 384 ms und damit unter der Vorgabe.
    assert vad.Hysterese(nachlauf_ms=400.0).nachlauf_chunks == 13


def test_grenzverletzung_wird_geloggt(caplog):
    with caplog.at_level("WARNING", logger="daimon.ears.vad"):
        vad.Hysterese(einsatz=0.2, ende=0.3, nachlauf_ms=50.0)
    text = caplog.text
    assert "einsatz" in text and "ende" in text and "nachlauf_ms" in text


def test_vorgaben_kommen_aus_der_konfiguration(tmp_path):
    from daimon.common import config

    datei = tmp_path / "daimon.toml"
    datei.write_text("[ears.vad]\neinsatz = 0.6\nende = 0.3\nnachlauf_ms = 500\n")
    cfg = config.load(config_path=datei, make_dirs=False)
    assert vad.einstellungen(cfg) == {
        "einsatz": 0.6, "ende": 0.3, "nachlauf_ms": 500.0,
    }


def test_mitgelieferte_toml_haelt_die_planvorgaben():
    import tomllib
    from pathlib import Path

    wurzel = Path(__file__).resolve().parent.parent
    with (wurzel / "config" / "daimon.toml").open("rb") as fh:
        v = tomllib.load(fh)["ears"]["vad"]
    assert v["einsatz"] >= vad.EINSATZ_MIN
    assert v["ende"] < v["einsatz"]
    assert vad.NACHLAUF_MS_MIN <= v["nachlauf_ms"] <= vad.NACHLAUF_MS_MAX


# --------------------------------------------------------------------------
# Kriterium 1: exakt 512 Samples je Modellaufruf, kein stilles Auffuellen
# --------------------------------------------------------------------------

class Attrappe:
    def __init__(self, wert: float = 0.75) -> None:
        self.laengen: list[int] = []
        self.wert = wert

    def __call__(self, roh) -> float:
        self.laengen.append(len(roh))
        return self.wert


def test_erkenner_reicht_exakt_512_samples_durch():
    a = Attrappe()
    e = vad.Erkenner(detektor=a)
    assert e.wahrscheinlichkeit(b"\x00\x01" * 512) == 0.75
    assert a.laengen == [1024]


@pytest.mark.parametrize("samples", [0, 1, 511, 513, 1024])
def test_falsche_chunkgroesse_ist_ein_fehler(samples):
    a = Attrappe()
    e = vad.Erkenner(detektor=a)
    with pytest.raises(vad.ChunkGroesseFehler):
        e.wahrscheinlichkeit(b"\x00\x01" * samples)
    assert a.laengen == [], "der Chunk wurde trotzdem ans Modell gegeben"


def test_kein_stilles_auffuellen():
    """Ausdruecklich als eigener Fall: Auffuellen waere die naheliegende
    'Robustheit' und wuerde genau den Schaden anrichten, den T-3.2 verhindert
    -- ein halb gefuellter Chunk am Wortende ist zur Haelfte Stille."""
    e = vad.Erkenner(detektor=Attrappe())
    with pytest.raises(vad.ChunkGroesseFehler) as exc:
        e.wahrscheinlichkeit(b"\x00" * 600)
    assert "300 Samples statt 512" in str(exc.value)


def test_numpy_block_aus_t31_passt():
    np = pytest.importorskip("numpy")
    # T-3.1 gibt (512, 1) int16 aus dem PortAudio-Callback.
    block = np.zeros((512, 1), dtype=np.int16)
    a = Attrappe()
    vad.Erkenner(detektor=a).wahrscheinlichkeit(block)
    assert a.laengen == [1024]


def test_ganzer_weg_mit_eingespeistem_erkenner():
    # segmentiere_audio() ist nur die Verklebung -- aber sie muss halten.
    reihe = [0.9] * 20 + [0.01] * 30 + [0.9] * 20

    class Folge:
        def __init__(self): self.i = -1
        def __call__(self, roh): self.i += 1; return reihe[self.i]

    stille = [b"\x00" * 1024] * len(reihe)
    segmente = vad.segmentiere_audio(stille, erkenner=vad.Erkenner(detektor=Folge()))
    assert len(segmente) == 2
