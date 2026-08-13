"""T-5.5/T-0.12 -- die Fensterauskunft des Fokus-Empfaengers.

Der Augendienst haelt `de.daimon.Eyes`, das KWin-Script sendet an
`de.daimon.Focus` -- und zwei Dienste koennen sich einen Busnamen nicht
teilen. Deshalb FRAGT der Augendienst, statt weitergeleitet zu bekommen.
Ohne diese Auskunft schnitt jede Runde aufs Vollbild zu: gemessen 4771 ms
statt 1336 ms.
"""
from __future__ import annotations

from daimon.hub.focus import FocusReceiver


def melden(r, **kw):
    d = dict(kind="windowActivated", uuid="u-1", caption="egal", cls="konsole",
             desktop="", fullscreen=False, pid=1, x=10, y=20, breite=300,
             hoehe=180)
    d.update(kw)
    return r.handle(**d)


def test_ohne_ereignis_ist_nichts_bekannt():
    assert FocusReceiver().fenster() == {"v": 1, "bekannt": False}


def test_geometrie_und_klasse_kommen_durch():
    r = FocusReceiver()
    melden(r)
    f = r.fenster()
    assert (f["x"], f["y"], f["breite"], f["hoehe"]) == (10, 20, 300, 180)
    assert f["resource_class"] == "konsole"
    assert f["bekannt"] is True


def test_der_fenstertitel_kommt_NICHT_durch():
    """Er ist Angreifertext -- er steht in einem Browsertab, den irgendwer
    benannt hat. `resource_class` vergibt dagegen die Anwendung ueber ihre
    Desktop-Datei, nicht ihr Inhalt."""
    r = FocusReceiver()
    melden(r, caption="sudo rm -rf / -- jetzt sofort")
    text = str(r.fenster())
    assert "sudo" not in text
    assert "caption" not in text


def test_fullscreen_kommt_durch():
    """Das GPU-Gate und die Gatterkette brauchen es beide."""
    r = FocusReceiver()
    melden(r, fullscreen=True)
    assert r.fenster()["fullscreen"] is True


def test_das_alter_wird_mitgegeben():
    """Ein Fenster von vor einer Stunde ist keine Auskunft ueber jetzt."""
    r = FocusReceiver()
    melden(r)
    assert r.fenster()["alter_s"] >= 0.0
