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
    assert str(sprechtext.MAX_ZEICHEN) in koerper, \
        "die Zahl muss aus dem Validator kommen, nicht danebenstehen"


def test_die_grenze_stammt_aus_dem_validator():
    """Zwei Zahlen an zwei Orten laufen auseinander. Der Validator ist die
    Quelle -- er weist ja auch ab."""
    from daimon.mind import daemon as D

    assert D.SPRECHFORM_ZEICHEN is sprechtext.MAX_ZEICHEN
