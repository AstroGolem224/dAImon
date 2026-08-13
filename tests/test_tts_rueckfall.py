"""T-6.4 -- der Rueckfall der Charakterstufe steht im Zustand.

Die Verifikation belegt VRAM kuenstlich und sieht danach im `/diag` nach. Eine
Logzeile, die inzwischen durchgerollt ist, beantwortet das nicht -- also muss
der Rueckfall GEZAEHLT werden.

Der Plan nennt fuer T-6.4 die Datei `daimon/gpu/tts_character.py` und
Kartoffelbox. Beides ist ueberholt: DESIGN.md haelt seit dem 09.08. fest, dass
die Charakterstufe Mimic ist -- ein eigener socket-aktivierter Dienst, den
`daimon/face/mimic.py` anspricht. Die Aufgabe war also schon gebaut; offen war
nur diese Sichtbarkeit.
"""
from __future__ import annotations

import inspect

import daimon.face.tts as tts


def test_der_rueckfall_wird_im_zustand_gezaehlt():
    quelle = inspect.getsource(tts.Sprecher.zustand)
    assert "mimic_rueckfaelle" in quelle
    assert "mimic_letzter_grund" in quelle


def test_ein_frischer_sprecher_hat_null_rueckfaelle():
    """Ein Zaehler, der nicht bei null anfaengt, ist kein Zaehler."""
    quelle = inspect.getsource(tts.Sprecher.__init__)
    assert "self._mimic_rueckfaelle = 0" in quelle
    assert 'self._mimic_letzter_grund = ""' in quelle


def test_die_charakterstufe_ist_nicht_die_vorgabe():
    """Piper bleibt Vorgabe; die Charakterstufe kommt erst ab 80 Zeichen --
    unter 80 waere der stochastische Vorlauf von dots.tts hoerbar (p95 858 ms
    bis zum ersten hoerbaren Sample, gemessen 09.08.)."""
    assert tts.MIMIC_AB_ZEICHEN == 80


def test_nur_warm_ist_vorgabe():
    """Bei belegtem VRAM stiller Rueckfall: `nur_warm` fragt erst, ob der
    Dienst ueberhaupt geladen ist, statt das Laden auszuloesen."""
    assert tts.MIMIC_NUR_WARM is True
