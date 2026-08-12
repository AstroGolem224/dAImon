"""T-5.9 -- das Deklassifizierungs-Gate.

Fuenf Faelle stehen in der Akzeptanzliste, und sie werden hier EINZELN
geprueft, nicht als eine Kette: ohne Marke keine Freigabe; mit Kontingent aus
dem Wake-Word keine; mit Marke aber ohne Bildschirmbezug keine; mit beidem
schon; abgelaufene Marke keine. Dazu der Angriff auf die Quarantaene aus
T-5.7 -- direkte Leseversuche am Speicher ohne Schein muessen scheitern.
"""
from __future__ import annotations

import pytest

from daimon.common.protocol import Mark
from daimon.common.taint import pruefe_senke, SenkenFehler
from daimon.eyes import context as ctx
from daimon.hub import declassify as dk
from daimon.hub.marks import MarkenBuch


class Uhr:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def weiter(self, s: float) -> None:
        self.t += s


class AuditAttrappe:
    def __init__(self) -> None:
        self.zeilen: list[dict] = []

    def schreiben(self, **felder):
        self.zeilen.append(felder)
        return felder


def aufbau(tmp_path, uhr=None):
    uhr = uhr or Uhr()
    marken = MarkenBuch(frist_s=120.0, jetzt=uhr)
    speicher = ctx.Kontextspeicher(verzeichnis=tmp_path / "context")
    speicher.hinzufuegen(ctx.ART_TITEL, "editor", "Posteingang")
    speicher.hinzufuegen(ctx.ART_OCR, "editor", "Sehr geehrte Damen")
    audit = AuditAttrappe()
    gate = dk.Deklassifizierung(marken=marken, speicher=speicher, audit=audit)
    return gate, marken, speicher, audit, uhr


def marke(marken, turn_id="t1"):
    return marken.ausgeben(quelle="auth", turn_id=turn_id).turn_id


FRAGE = "Was steht gerade auf meinem Bildschirm?"


# -- Die fuenf Faelle, einzeln ---------------------------------------------

def test_ohne_marke_keine_freigabe(tmp_path):
    gate, *_ = aufbau(tmp_path)
    with pytest.raises(dk.GateFehler) as f:
        gate.freigeben(aeusserung=FRAGE)
    assert f.value.grund == dk.GRUND_KEINE_MARKE


def test_ein_kontingent_aus_dem_wake_word_deklassifiziert_nichts(tmp_path):
    """Sonst reichte ein Video, das den Namen sagt und nach dem Bildschirm
    fragt (Design 7.2b)."""
    gate, *_ = aufbau(tmp_path)
    with pytest.raises(dk.GateFehler) as f:
        gate.freigeben(aeusserung=FRAGE, kontingent="k-123")
    assert f.value.grund == dk.GRUND_KONTINGENT


def test_kontingent_neben_gueltiger_marke_reicht_auch_nicht(tmp_path):
    """Beides zugleich ist kein Zufall, sondern der Versuch, die schwaechere
    Bedingung mitlaufen zu lassen."""
    gate, marken, *_ = aufbau(tmp_path)
    t = marke(marken)
    with pytest.raises(dk.GateFehler) as f:
        gate.freigeben(aeusserung=FRAGE, turn_id=t, kontingent="k-123")
    assert f.value.grund == dk.GRUND_KONTINGENT


def test_mit_marke_aber_ohne_bildschirmbezug_keine_freigabe(tmp_path):
    gate, marken, *_ = aufbau(tmp_path)
    t = marke(marken)
    with pytest.raises(dk.GateFehler) as f:
        gate.freigeben(aeusserung="Wie spaet ist es?", turn_id=t)
    assert f.value.grund == dk.GRUND_KEIN_BEZUG


def test_mit_marke_und_bezug_gibt_es_die_freigabe(tmp_path):
    gate, marken, *_ = aufbau(tmp_path)
    frei = gate.freigeben(aeusserung=FRAGE, turn_id=marke(marken))
    assert frei.umfang[ctx.ART_TITEL] == 1
    assert frei.umfang[ctx.ART_OCR] == 1
    assert len(frei.eintraege) == 2


def test_eine_abgelaufene_marke_reicht_nicht(tmp_path):
    uhr = Uhr()
    gate, marken, *_ = aufbau(tmp_path, uhr)
    t = marke(marken)
    uhr.weiter(121.0)
    with pytest.raises(dk.GateFehler) as f:
        gate.freigeben(aeusserung=FRAGE, turn_id=t)
    assert f.value.grund == dk.GRUND_MARKE_UNGUELTIG


# -- Ohne Nutzerhandlung ---------------------------------------------------

def test_proaktiv_gibt_es_nichts_auch_mit_gueltiger_marke(tmp_path):
    """Die Regel, die am ehesten aufgeweicht wird -- ein Assistent, der von
    selbst etwas Kluges zum Bildschirm sagt, faehlt sich besser an. Er waere
    einer, der den Bildschirm ungefragt liest."""
    gate, marken, *_ = aufbau(tmp_path)
    with pytest.raises(dk.GateFehler) as f:
        gate.freigeben(aeusserung=FRAGE, turn_id=marke(marken), proaktiv=True)
    assert f.value.grund == dk.GRUND_PROAKTIV


# -- Die Marke gehoert dem, was gelingt ------------------------------------

def test_eine_abgelehnte_freigabe_verbrennt_die_marke_nicht(tmp_path):
    """Im ersten Entwurf wurde die Marke VOR dem Bezug eingeloest. Eine
    Aeusserung ohne Bezug verbrannte sie, und der Nutzer konnte in derselben
    Runde nicht nachfassen -- er haette die Taste erneut druecken muessen,
    ohne zu erfahren warum.
    """
    gate, marken, *_ = aufbau(tmp_path)
    t = marke(marken)
    with pytest.raises(dk.GateFehler):
        gate.freigeben(aeusserung="Wie spaet ist es?", turn_id=t)
    frei = gate.freigeben(aeusserung=FRAGE, turn_id=t)
    assert frei.turn_id == t


def test_dieselbe_marke_gibt_nur_einmal_frei(tmp_path):
    gate, marken, *_ = aufbau(tmp_path)
    t = marke(marken)
    gate.freigeben(aeusserung=FRAGE, turn_id=t)
    with pytest.raises(dk.GateFehler) as f:
        gate.freigeben(aeusserung=FRAGE, turn_id=t)
    assert f.value.grund == dk.GRUND_MARKE_UNGUELTIG


# -- Der Angriff auf die Quarantaene ---------------------------------------

def test_direktes_lesen_am_speicher_scheitert(tmp_path):
    """Der Angriff aus der Akzeptanzliste: am Gate vorbei."""
    _, _, speicher, *_ = aufbau(tmp_path)
    with pytest.raises(ctx.QuarantaeneFehler):
        speicher.freigeben()
    with pytest.raises(ctx.QuarantaeneFehler):
        speicher.freigeben(object())


def test_ein_gefaelschter_schein_ohne_turn_id_scheitert(tmp_path):
    _, _, speicher, *_ = aufbau(tmp_path)
    with pytest.raises(ctx.QuarantaeneFehler):
        speicher.freigeben(dk.Freigabeschein(turn_id=""))


# -- Nur Durchgang 2 -------------------------------------------------------

def test_freigegebener_kontext_darf_nicht_in_durchgang_1(tmp_path):
    """Die Senkentabelle aus T-3.13b erledigt das -- dieses Modul muss die
    Markierung nur richtig setzen."""
    gate, marken, *_ = aufbau(tmp_path)
    frei = gate.freigeben(aeusserung=FRAGE, turn_id=marke(marken))
    assert frei.senke == "durchgang2"
    for eintrag in frei.eintraege:
        assert eintrag.mark is Mark.TAINTED
        pruefe_senke(eintrag, senke="durchgang2")     # darf nicht werfen
        with pytest.raises(SenkenFehler):
            pruefe_senke(eintrag, senke="durchgang1")


# -- Der Bildschirmbezug ---------------------------------------------------

@pytest.mark.parametrize("satz", [
    "Was steht auf meinem Bildschirm?",
    "Was siehst du gerade?",
    "Welches Fenster ist offen?",
    "what is on my screen right now",
    "Ist der Fehler noch sichtbar?",
])
def test_erkannte_bildschirmfragen(satz):
    assert dk.bildschirmbezug(satz) is True


@pytest.mark.parametrize("satz", [
    "Wie spaet ist es?",
    "Schau mal hier.",
    "Was ist das da?",
    "Erzaehl mir was.",
    "",
])
def test_nicht_als_bildschirmfrage_gezaehlt(satz):
    """Die Liste ist bewusst eng. „hier" und „das da" kommen in jedem zweiten
    Satz vor -- mit ihnen waere die Bedingung erfuellt, sobald jemand
    ueberhaupt spricht."""
    assert dk.bildschirmbezug(satz) is False


# -- Durchgang 1 bekommt opake Referenzen ----------------------------------

def test_durchgang_1_sieht_keine_fenstertitel():
    """Ein Fenstertitel ist Angreifertext: er steht in einem Browsertab, den
    irgendwer benannt hat. Auch als typisiertes Feld bleibt er das."""
    r = dk.referenzen([{"app_id": "konsole", "title": "sudo rm -rf /"}],
                      bekannte_app_ids=["konsole"])
    assert r == [{"window_ref": "w_0", "app_id": "konsole"}]
    assert "sudo" not in str(r)
    assert "title" not in str(r)


def test_eine_unbekannte_app_id_wird_nicht_durchgereicht():
    """Sie waere eine Zeichenkette aus fremder Quelle in einem Feld, das als
    geschlossen gilt."""
    r = dk.referenzen([{"app_id": "boeser-name-<script>"}],
                      bekannte_app_ids=["konsole"])
    assert r[0]["app_id"] == "unbekannt"


def test_die_fensterreferenz_ist_eine_laufende_nummer():
    r = dk.referenzen([{"app_id": "a"}, {"app_id": "b"}], bekannte_app_ids=["a"])
    assert [x["window_ref"] for x in r] == ["w_0", "w_1"]


# -- Audit -----------------------------------------------------------------

def test_jede_freigabe_landet_im_audit_mit_umfang_und_turn_id(tmp_path):
    gate, marken, _, audit, _ = aufbau(tmp_path)
    t = marke(marken)
    gate.freigeben(aeusserung=FRAGE, turn_id=t)
    ok = [z for z in audit.zeilen if z["outcome"] == "ok"]
    assert len(ok) == 1
    assert ok[0]["turn_id"] == t
    assert "titel" in ok[0]["prompt_shown"]


def test_auch_jede_ablehnung_landet_im_audit(tmp_path):
    """Eine Ablehnung, die niemand aufschreibt, ist von einer nie gestellten
    Frage nicht zu unterscheiden."""
    gate, _, _, audit, _ = aufbau(tmp_path)
    with pytest.raises(dk.GateFehler):
        gate.freigeben(aeusserung=FRAGE, kontingent="k-1")
    denied = [z for z in audit.zeilen if z["outcome"] == "denied"]
    assert len(denied) == 1
    assert dk.GRUND_KONTINGENT in denied[0]["prompt_shown"]


def test_ein_klemmendes_audit_verwandelt_keine_ablehnung_in_eine_freigabe(tmp_path):
    class Kaputt:
        def schreiben(self, **_):
            raise RuntimeError("Platte voll")

    _, marken, speicher, _, _ = aufbau(tmp_path)
    gate = dk.Deklassifizierung(marken=marken, speicher=speicher, audit=Kaputt())
    with pytest.raises(dk.GateFehler):
        gate.freigeben(aeusserung=FRAGE)


def test_abgelehnte_gruende_werden_gezaehlt(tmp_path):
    gate, *_ = aufbau(tmp_path)
    for _ in range(3):
        with pytest.raises(dk.GateFehler):
            gate.freigeben(aeusserung=FRAGE)
    assert gate.abgelehnt[dk.GRUND_KEINE_MARKE] == 3
