"""T-0.6 — Tests fuer das strukturierte Logging.

Der Journal-Weg wird hier NICHT geprueft: er haengt an einem laufenden
journald und gehoert damit in den Verifizierer, der zurueckliest. Hier geht es
um die Feldregeln und den Rueckfall -- also um das, was auch ohne Journal
stimmen muss.
"""

import io

import pytest

from daimon.common.logging import FieldNameError, Logger, get_logger


def fallback_logger():
    """Socketpfad, den es nicht gibt -> garantierter stderr-Rueckfall."""
    return Logger("test", socket_path="/nicht/vorhanden/socket",
                  stream=io.StringIO())


def test_ohne_journal_kein_absturz():
    lg = fallback_logger()
    assert lg.nutzt_journal is False
    lg.info("laeuft trotzdem", DAIMON_ACTION="probe")
    assert "laeuft trotzdem" in lg._stream.getvalue()


def test_rueckfall_traegt_die_eigenen_felder():
    lg = fallback_logger()
    lg.info("nachricht", DAIMON_ACTION="probe", DAIMON_N=3)
    text = lg._stream.getvalue()
    assert "DAIMON_ACTION=probe" in text
    assert "DAIMON_N=3" in text


def test_identifier_steht_im_rueckfall():
    lg = Logger("mein-prozess", socket_path="/nicht/da", stream=io.StringIO())
    lg.info("x")
    assert "[mein-prozess]" in lg._stream.getvalue()


@pytest.mark.parametrize("name", ["_PID", "_UID", "_SYSTEMD_UNIT"])
def test_fuehrender_unterstrich_abgewiesen(name):
    """journald setzt diese Felder selbst und faelscht sie nicht. Wer sie
    schreiben koennte, koennte seine eigene Herkunft behaupten."""
    with pytest.raises(FieldNameError) as ex:
        fallback_logger().info("x", **{name: "1"})
    assert "journald" in str(ex.value)


@pytest.mark.parametrize("name", ["klein", "MiT_Gemischt", "MIT-BINDESTRICH",
                                  "1ZAHL", "MIT PUNKT", ""])
def test_ungueltige_feldnamen_abgewiesen(name):
    """journald verwirft sie STILL -- man merkt es erst, wenn man den
    Datensatz sucht und nicht findet. Deshalb hart ablehnen."""
    with pytest.raises(FieldNameError):
        fallback_logger().info("x", **{name: "1"})


@pytest.mark.parametrize("name", ["DAIMON_ACTION", "A", "X1", "DAIMON_A_B_C"])
def test_gueltige_feldnamen_gehen_durch(name):
    fallback_logger().info("x", **{name: "1"})


def test_mehrzeiliger_wert_wird_binaer_gerahmt():
    """Ohne Rahmung zerfaellt ein mehrzeiliger Wert in kaputte Journal-Zeilen."""
    from daimon.common.logging import _kodiere
    roh = _kodiere("DAIMON_TEXT", "zeile1\nzeile2")
    assert roh.startswith(b"DAIMON_TEXT\n")
    assert b"=" not in roh[:12]
    assert roh.endswith(b"zeile1\nzeile2\n")


def test_einzeiliger_wert_bleibt_einfach():
    from daimon.common.logging import _kodiere
    assert _kodiere("DAIMON_A", "b") == b"DAIMON_A=b\n"


def test_get_logger_liefert_unabhaengige_objekte():
    a = get_logger("a", socket_path="/nicht/da")
    b = get_logger("b", socket_path="/nicht/da")
    assert a is not b
    assert a.identifier != b.identifier
