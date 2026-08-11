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


def calldbus_argumente(quelle: str) -> list[int]:
    """Zahl der Argumente je `callDBus(`-Aufruf, Klammern mitgezaehlt."""
    ergebnis = []
    text = ohne_kommentare(quelle)
    for start in [i for i in range(len(text)) if text.startswith("callDBus(", i)]:
        tiefe, kommas = 0, 0
        for zeichen in text[start + len("callDBus"):]:
            if zeichen in "([{":
                tiefe += 1
            elif zeichen in ")]}":
                tiefe -= 1
                if tiefe == 0:
                    break
            elif zeichen == "," and tiefe == 1:
                kommas += 1
        ergebnis.append(kommas + 1)
    return ergebnis


def test_calldbus_bleibt_unter_der_kwin_grenze():
    """KWin kappt bei 13 Argumenten und protokolliert nur "Too many arguments,
    ignoring 2" -- die Meldung erreicht den Hub dann nie. Gemessen am 03.08.:
    elf Einzelwerte ergaben 15 Argumente, `Zustand()` blieb (false, -1.0)."""
    argumente = calldbus_argumente(SCRIPT.read_text())
    assert argumente, "kein callDBus gefunden"
    assert max(argumente) <= 13, argumente


def test_script_schickt_einen_json_string():
    """Ein neues Feld verlaengert damit den String statt der Argumentliste --
    die Grenze ist strukturell nicht mehr erreichbar."""
    assert "JSON.stringify" in ohne_kommentare(SCRIPT.read_text())


# --------------------------------------------------------------------------
# T-0.12.v2 — der Empfaenger ueberlebt einen kaputten Watcher
# --------------------------------------------------------------------------

def nutzlast(**kw) -> str:
    daten = dict(kind="activated", uuid="u1", caption="Titel", cls="konsole",
                 desktop="org.kde.konsole", fullscreen=False, pid=4711,
                 x=0, y=0, breite=1920, hoehe=1080)
    daten.update(kw)
    return json.dumps(daten)


def test_json_nutzlast_wird_ausgepackt():
    ev = FocusReceiver().handle_json(nutzlast(fullscreen=True))
    assert ev is not None
    assert ev.uuid == "u1" and ev.pid == 4711 and ev.fullscreen is True
    assert ev.geometrie == (0, 0, 1920, 1080)


def test_unbekannte_felder_werden_ignoriert():
    """Der Watcher laeuft im Compositor und wird nicht mit dem Hub zusammen
    ausgeliefert -- eine neuere Fassung darf Felder mitbringen."""
    ev = FocusReceiver().handle_json(nutzlast(neues_feld=[1, 2, 3]))
    assert ev is not None and ev.uuid == "u1"


def test_fehlende_felder_bekommen_vorgaben():
    ev = FocusReceiver().handle_json('{"kind": "activated"}')
    assert ev is not None
    assert ev.uuid == "" and ev.pid == 0 and ev.geometrie == (0, 0, 0, 0)
    assert ev.fullscreen is False


@pytest.mark.parametrize("muell", [
    "", "{", "nicht mal json", "[1, 2, 3]", "null", '"nur ein string"', "17",
])
def test_kaputte_nutzlast_wird_verworfen_und_gezaehlt(muell):
    """Ein Watcher, der Muell schickt, darf den Hub nicht umbringen -- und
    "es kommt nichts" muss von "es kommt Muell" unterscheidbar bleiben."""
    rx = FocusReceiver()
    assert rx.handle_json(muell) is None
    assert rx.verworfen == 1
    assert rx.letztes is None
    assert rx.zustand() == (False, -1.0)


def test_falsche_feldtypen_werfen_nicht():
    ev = FocusReceiver().handle_json(json.dumps(
        {"kind": 7, "uuid": None, "caption": {"a": 1}, "pid": "viele",
         "x": True, "breite": 3.9, "fullscreen": "ja"}))
    assert ev is not None
    assert ev.kind == "unbekannt" and ev.uuid == "" and ev.pid == 0
    assert ev.geometrie == (0, 0, 3, 0)
    # `bool("ja")` waere True: ein Watcher mit falschem Feldtyp koennte damit
    # Vollbild behaupten, und das GPU-Gate haengt daran.
    assert ev.fullscreen is False


def test_signatur_ist_ein_einziger_string():
    from daimon.hub.focus import DBUS_SIGNATUR
    assert DBUS_SIGNATUR == "s"


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
