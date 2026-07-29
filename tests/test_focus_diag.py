"""T-0.12 und T-0.13 — Fokus-Empfaenger und Diagnose.

Der KWin-Teil von T-0.12 laeuft im Compositor und gehoert damit in den
Verifizierer, nicht hierher. Hier steht, was ohne Compositor pruefbar ist:
dass der Empfaenger die Meldung richtig auspackt, dass der Fenstertitel
`tainted` bleibt, und dass die Diagnose zaehlt, was sie zaehlen soll.
"""

import json
from pathlib import Path

import pytest

from daimon.common.protocol import Mark, Marked
from daimon.hub.diag import GRENZEN_MS, Diagnose, Histogramm
from daimon.hub.focus import FocusReceiver, als_hub_ereignis

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "kwin-script" / "daimon-watcher" / "contents" / "code" / "main.js"


def melde(rx: FocusReceiver, **kw):
    daten = dict(kind="activated", uuid="u1", caption="Titel", cls="konsole",
                 desktop="org.kde.konsole", fullscreen=False, pid=4711,
                 x=0, y=0, breite=1920, hoehe=1080)
    daten.update(kw)
    return rx.handle(**daten)


# --------------------------------------------------------------------------
# T-0.12 — Empfaenger
# --------------------------------------------------------------------------

def test_meldung_wird_ausgepackt():
    ev = melde(FocusReceiver())
    assert ev.uuid == "u1"
    assert ev.pid == 4711
    assert ev.geometrie == (0, 0, 1920, 1080)
    assert ev.fullscreen is False


def test_fenstertitel_ist_tainted():
    """Ein Fenstertitel ist angreiferbeeinflusster Inhalt -- jede Anwendung
    darf hineinschreiben, was sie will (Design 5.2)."""
    ev = melde(FocusReceiver(), caption="trusted: harmlos")
    assert isinstance(ev.caption, Marked)
    assert ev.caption.mark is Mark.TAINTED
    assert ev.caption.value == "trusted: harmlos"


def test_resource_class_ist_ebenfalls_tainted():
    assert melde(FocusReceiver()).resource_class.mark is Mark.TAINTED


def test_marke_ueberlebt_den_weg_zum_hub():
    ev = melde(FocusReceiver())
    durch_json = json.loads(json.dumps(als_hub_ereignis(ev)))
    assert Marked.from_wire(durch_json["caption"]).mark is Mark.TAINTED


def test_fullscreen_und_geometrie_fuer_das_vram_gate():
    """Das VRAM-Gate muss wissen, ob ein Vollbildfenster das Overlay verdeckt."""
    ev = melde(FocusReceiver(), fullscreen=True, x=0, y=0, breite=5120, hoehe=1440)
    assert ev.fullscreen
    assert ev.deckt_ab(2560, 720)
    assert not ev.deckt_ab(6000, 720)


def test_zaehler_je_art():
    rx = FocusReceiver()
    melde(rx, kind="activated")
    melde(rx, kind="activated")
    melde(rx, kind="caption")
    assert rx.zaehler() == {"activated": 2, "caption": 1}


def test_kaputter_abnehmer_reisst_den_empfaenger_nicht_mit():
    """callDBus meldet uns keinen Fehler -- ein Absturz hier waere unsichtbar."""
    rx = FocusReceiver(on_event=lambda ev: (_ for _ in ()).throw(RuntimeError()))
    assert melde(rx).uuid == "u1"


# --------------------------------------------------------------------------
# T-0.12 — das Script ist rein lesend
# --------------------------------------------------------------------------

VERBOTEN = ("setFullScreen", "closeWindow", "workspace.activeWindow =",
            "\\.close(", "setMaximize", "sendPing", "killWindow")


def ohne_kommentare(quelle: str) -> str:
    """Zeilenkommentare raus. Der Modulkopf erklaert ausdruecklich, WAS das
    Script nicht tut -- eine Textsuche ueber den ganzen Text schlaegt daran an
    und prueft damit Erwaehnung statt Aufruf. Genau derselbe Fehler wie im
    ersten Anlauf des SO_PEERCRED-Tests in test_ipc.py."""
    return "\n".join(z.split("//")[0] for z in quelle.splitlines())


@pytest.mark.parametrize("aufruf", VERBOTEN)
def test_script_veraendert_nichts(aufruf):
    """Der Watcher laeuft im Compositor. Ein schreibender Aufruf waere eine
    Fernsteuerung, die niemand beauftragt hat -- und ein Fehler darin legt den
    Desktop lahm."""
    assert aufruf.replace("\\", "") not in ohne_kommentare(SCRIPT.read_text())


def test_script_meldet_ueber_calldbus():
    quelle = SCRIPT.read_text()
    assert "callDBus" in quelle
    assert "de.daimon.Focus" in quelle


def test_script_verdrahtet_bereits_offene_fenster():
    """Ohne das bliebe nach einem kwin --replace praktisch der ganze Desktop
    stumm -- alle Fenster waeren aelter als der Watcher."""
    assert "workspace.windowList()" in SCRIPT.read_text()


# --------------------------------------------------------------------------
# T-0.13 — Diagnose
# --------------------------------------------------------------------------

def test_histogramm_zaehlt_in_die_richtige_klasse():
    h = Histogramm()
    h.beobachte(0.05)
    h.beobachte(7.0)
    h.beobachte(5000.0)
    s = h.snapshot()
    assert s["n"] == 3
    assert s["eimer"]["<=0.1"] == 1
    assert s["eimer"]["<=10"] == 1
    assert s["eimer"][f">{GRENZEN_MS[-1]}"] == 1
    assert s["max_ms"] == 5000.0


def test_histogramm_statt_mittelwert():
    """Ein Mittelwert verschluckt genau das, was interessiert: einer von
    tausend, der eine Sekunde braucht."""
    h = Histogramm()
    for _ in range(999):
        h.beobachte(0.05)
    # 5000 statt 1000: 1000.0 faellt noch in die Klasse "<=1000". Der erste
    # Anlauf dieses Tests hat genau daran gemerkt, dass die Grenzen
    # einschliessend sind.
    h.beobachte(5000.0)
    s = h.snapshot()
    assert s["mittel_ms"] < 6, "Mittelwert versteckt den Ausreisser"
    assert s["eimer"][f">{GRENZEN_MS[-1]}"] == 1, "das Histogramm zeigt ihn"


def test_verworfene_werden_nach_grund_gezaehlt():
    d = Diagnose()
    d.verworfen("unbekanntes_ereignis:SubagentStop")
    d.verworfen("unbekanntes_ereignis:SubagentStop")
    d.verworfen("fremder_typ")
    s = d.snapshot()
    assert s["verworfen"]["unbekanntes_ereignis:SubagentStop"] == 2
    assert s["verworfen_gesamt"] == 3


def test_vier_zahlen_je_typ():
    """Nur 'ausgegeben' zu zaehlen sagt nichts darueber, ob je eingeloest
    wurde -- und eine Marke, die nie eingeloest wird, ist ein Befund."""
    d = Diagnose()
    d.zaehle("rundenmarke", "ausgegeben", 3)
    d.zaehle("rundenmarke", "eingeloest")
    z = d.snapshot()["zaehler"]["rundenmarke"]
    assert z == {"ausgegeben": 3, "eingeloest": 1, "abgelaufen": 0, "abgelehnt": 0}
    assert set(d.snapshot()["zaehler"]) == set(Diagnose.TYPEN)


def test_gegendruck_merkt_sich_den_beginn():
    d = Diagnose()
    assert d.snapshot()["gegendruck"]["aktiv"] is False
    d.gegendruck(True, ausloeser="eyes")
    g = d.snapshot()["gegendruck"]
    assert g["aktiv"] and g["ausloeser"] == "eyes" and g["seit"]
    d.gegendruck(False)
    assert d.snapshot()["gegendruck"]["aktiv"] is False


def test_units_bleiben_leer_statt_geraten():
    """Ein erfundener Unit-Zustand waere schlimmer als gar keiner."""
    assert Diagnose().snapshot()["units"] == {}


def test_unbekannter_typ_wird_ignoriert_statt_zu_werfen():
    d = Diagnose()
    d.zaehle("gibt-es-nicht", "ausgegeben")
    assert set(d.snapshot()["zaehler"]) == set(Diagnose.TYPEN)
