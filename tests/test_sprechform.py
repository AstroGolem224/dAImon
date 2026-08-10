"""Die Antwort wird VORGELESEN -- das gehoert in die Anfrage, nicht in eine
Filterstufe danach.

Am 09.08. live: Nordom antwortete zweizeilig, der Validator aus T-3.9 wies
`mehrzeilig` zurueck, und das Pet sagte den Ersatzsatz ("die Antwort steht auf
dem Bildschirm"). Der Validator hat dabei genau das getan, wofuer er da ist.
Falsch war die Stelle davor: dem Modell hatte niemand gesagt, dass seine
Antwort gesprochen wird.
"""

import io

from daimon.common.logging import get_logger
from daimon.hub import sprechtext
from daimon.mind.daemon import Mind
from daimon.mind.persona import Persona


def mind():
    p = Persona(name="Nordom", prompt_text="Du bist Nordom, ein Modron.",
                herkunft={}) if hasattr(Persona, "prompt_text") else None
    return p


def test_koerper_nennt_die_ausgabeform():
    from daimon.mind import daemon as D

    koerper = D.SPRECHFORM
    assert "vorgelesen" in koerper.lower() or "gesprochen" in koerper.lower()
    assert str(D.SPRECHFORM_ZEICHEN) in koerper, \
        "die genannte Zahl muss die gerechnete sein, nicht eine getippte"


def test_die_grenze_haengt_am_validator():
    """Zwei Zahlen an zwei Orten laufen auseinander. Der Validator ist die
    Quelle -- er weist ja auch ab. Die Anweisung nennt einen Wert DARUNTER
    (siehe Rand), aber sie rechnet ihn aus der Grenze und tippt ihn nicht."""
    from daimon.mind import daemon as D

    assert D.SPRECHFORM_ZEICHEN == sprechtext.MAX_ZEICHEN - D.SPRECHFORM_RAND


def test_die_vorgabe_laesst_rand_zur_harten_grenze():
    """Am 09.08. an gemma4:26b gemessen: die Vorgabe sagte 140, das Modell
    lieferte 146 -- und der Validator haette abgewiesen. Sonnet trifft solche
    Zahlen genauer als ein lokales 26B-Modell.

    Die Zahl in der ANWEISUNG liegt deshalb unter der harten Grenze. Sie ist
    ein Kalibrierknopf: wer ein Modell einsetzt, das enger trifft, darf ihn
    hochdrehen.
    """
    from daimon.mind import daemon as D

    assert D.SPRECHFORM_ZEICHEN < sprechtext.MAX_ZEICHEN
    assert str(D.SPRECHFORM_ZEICHEN) in D.SPRECHFORM
    assert str(sprechtext.MAX_ZEICHEN) not in D.SPRECHFORM, \
        "die harte Grenze gehoert nicht in die Anweisung -- sonst zielt das "
