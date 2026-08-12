"""T-5.3 -- die Kette, gegen die sich ohne Bildschirm etwas sagen laesst.

Was hier NICHT geprueft wird, ist absichtlich weggelassen: ob Frames kommen,
ob sie schwarz sind und was die Ruhe kostet. Das braucht einen echten
Bildschirm, ein laufendes PipeWire und einen Menschen, der einmal geklickt
hat -- und gehoert deshalb in den eingefrorenen Pruefstand, nicht hierher.

Pruefbar ist die Beschreibung der Kette. Sie traegt alle Entscheidungen, die
am 12.08. gemessen wurden, und ein spaeterer Griff, der eine davon
zurueckdreht, faellt hier auf statt erst am schwarzen Bild.
"""
from __future__ import annotations

import pytest

from daimon.eyes import capture as cap


def test_die_serial_schlaegt_die_node_id():
    """Node-IDs werden nach einem Hotplug wiederverwendet, Serials nicht.

    Wer die Node-ID festhaelt, filmt nach dem Aus- und Einschalten eines
    Ausgangs unter Umstaenden einen anderen Bildschirm -- und merkt es nicht,
    weil weiter Bilder kommen.
    """
    beide = cap.pipeline_beschreibung(serial="3908", node_id=128)
    assert 'target-object="3908"' in beide
    assert "path=128" not in beide

    nur_id = cap.pipeline_beschreibung(node_id=128)
    assert "path=128" in nur_id


def test_ohne_ziel_gibt_es_keine_kette():
    with pytest.raises(ValueError):
        cap.pipeline_beschreibung()


def test_videoconvert_bleibt_drin():
    """KDE-Bug 476602. Ohne das Glied scheitert die Aushandlung stumm."""
    assert "videoconvert" in cap.pipeline_beschreibung(serial="1")


def test_videorate_statt_caps_deckel():
    """Gemessen am 12.08.: ein Bereich `[0/1,5/1]` handelt `0/1` aus.

    `0/1` heisst „variabel", liegt im Bereich und ist damit erlaubt -- die
    Quelle lieferte daraufhin rund 35 fps. Ein fester Wert direkt an der
    Quelle liefert gar nichts. Nur `videorate` erzwingt die Rate wirklich.
    """
    k = cap.pipeline_beschreibung(serial="1")
    assert "videorate drop-only=true" in k
    assert f"framerate={cap.FRAMERATE}/1" in k
    # Kein Bereich: eckige Klammern waeren die Variante, die nicht wirkt.
    assert "[" not in k


def test_die_senke_haelt_genau_einen_puffer():
    """22 MB je Frame. Ohne Deckel waechst der Bedarf mit der Zeit."""
    k = cap.pipeline_beschreibung(serial="1")
    assert "max-buffers=1" in k
    assert "drop=true" in k


def test_ohne_gstreamer_sagt_die_meldung_welcher_interpreter_fehlt():
    """Unter dem venv-Python fehlt PyGObject -- genau dieser Lauf hier."""
    try:
        import gi                                        # noqa: F401
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst                    # noqa: F401
    except Exception:
        pass
    else:
        pytest.skip("dieser Interpreter HAT GStreamer -- nichts zu zeigen")

    a = cap.Aufnahme(fd=3, serial="1")
    with pytest.raises(cap.AufnahmeFehler) as fehler:
        a.frame()
    text = str(fehler.value)
    assert "venv" in text and "python3" in text
