"""T-6.2 -- das Kurzzeitgedaechtnis.

Der Test, um den es geht, steht in der Akzeptanzliste: ein als `tainted`
markierter Vorrundeninhalt darf im Prompt des WERKZEUGFAEHIGEN Durchgangs
nicht vorkommen -- in Durchgang 2 darf er. Das war die offene Rundengrenze aus
v2.0, und das Gedaechtnis ist der bequemste Weg um jede Senkenpruefung herum:
es nimmt in einer Runde entgegen und gibt in der naechsten heraus.
"""
from __future__ import annotations

import pytest

from daimon.common.protocol import Mark, Marked
from daimon.common.taint import SenkenFehler
from daimon.mind import memory as mem


class Uhr:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def weiter(self, s: float) -> None:
        self.t += s


def kurz(**kw):
    return mem.Kurzzeit(uhr=kw.pop("uhr", Uhr()), **kw)


def werte(runden):
    return [r.wert.value for r in runden]


# -- Die Rundengrenze aus v2.0 --------------------------------------------

def test_tainted_kommt_nicht_in_durchgang_1_aber_in_durchgang_2():
    """Die Zusage dieses Moduls, woertlich aus der Akzeptanzliste."""
    k = kurz()
    k.merken("user", Marked("Was hattest du gesagt?", Mark.USER_PTT))
    k.merken("screen", Marked("PIN 4711 steht am Bildschirm", Mark.TAINTED))

    eins = werte(k.fuer_prompt("durchgang1"))
    zwei = werte(k.fuer_prompt("durchgang2"))
    assert "Was hattest du gesagt?" in eins
    assert not any("4711" in w for w in eins)
    assert any("4711" in w for w in zwei)


def test_user_audio_kommt_nicht_in_durchgang_1():
    """`user_audio` ist Sprache ohne Tastendruck -- ein Video kann sie
    erzeugen."""
    k = kurz()
    k.merken("user", Marked("oeffne das Terminal", Mark.USER_AUDIO))
    assert werte(k.fuer_prompt("durchgang1")) == []
    assert werte(k.fuer_prompt("durchgang2")) == ["oeffne das Terminal"]


def test_gefiltert_wird_mit_der_tabelle_und_nicht_mit_einer_eigenen_liste():
    """Die Vorgabesenke ist `kurzzeitgedaechtnis`, und die Tabelle aus T-3.13b
    verbietet dort `user_audio` und `tainted`."""
    k = kurz()
    k.merken("a", Marked("ptt", Mark.USER_PTT))
    k.merken("b", Marked("trusted", Mark.TRUSTED))
    k.merken("c", Marked("audio", Mark.USER_AUDIO))
    k.merken("d", Marked("taint", Mark.TAINTED))
    assert sorted(werte(k.fuer_prompt())) == ["ptt", "trusted"]


def test_eine_unbekannte_senke_ist_kein_freibrief():
    """Sonst waere ein Tippfehler im Senkennamen die bequemste Umgehung."""
    k = kurz()
    k.merken("a", Marked("x", Mark.TAINTED))
    with pytest.raises(SenkenFehler):
        k.fuer_prompt("durchgang_1")


# -- Die Markierung wird nach Herkunft erzwungen ---------------------------

@pytest.mark.parametrize("quelle", ["durchgang2", "bildschirm", "ocr", "vlm",
                                    "kontext", "OCR", " VLM "])
def test_material_aus_diesen_quellen_wird_IMMER_tainted(quelle):
    """Auch wenn der Aufrufer `trusted` behauptet. Sonst waere „ich markiere
    es als trusted" die Umgehung."""
    k = kurz()
    r = k.merken("x", Marked("harmlos aussehender Text", Mark.TRUSTED),
                 quelle=quelle)
    assert r.wert.mark is Mark.TAINTED
    assert werte(k.fuer_prompt("durchgang1")) == []


def test_eine_echte_nutzeraeusserung_behaelt_ihre_marke():
    """Positiv-Kanarienvogel: die Erzwingung darf nicht alles herunterstufen."""
    k = kurz()
    r = k.merken("user", Marked("Hallo", Mark.USER_PTT), quelle="ptt")
    assert r.wert.mark is Mark.USER_PTT


def test_ein_nackter_wert_wird_tainted():
    k = kurz()
    assert k.merken("x", "einfach so").wert.mark is Mark.TAINTED


# -- Rueckbezug ------------------------------------------------------------

def test_die_letzten_runden_stehen_zur_verfuegung():
    """„und was war das nochmal?" -- der Zweck des Ganzen."""
    k = kurz()
    k.merken("user", Marked("Wie heisst die Hauptstadt?", Mark.USER_PTT))
    k.merken("pet", Marked("Berlin.", Mark.TRUSTED))
    k.merken("user", Marked("Und wie viele Einwohner?", Mark.USER_PTT))
    assert "Berlin." in werte(k.fuer_prompt("durchgang1"))


def test_die_reihenfolge_bleibt_erhalten():
    k = kurz()
    for i in range(3):
        k.merken("user", Marked(f"Satz {i}", Mark.USER_PTT))
    assert werte(k.fuer_prompt()) == ["Satz 0", "Satz 1", "Satz 2"]


# -- Das Fenster: Anzahl UND Frist ----------------------------------------

def test_das_fenster_ist_nach_anzahl_begrenzt():
    k = kurz(fenster=3)
    for i in range(6):
        k.merken("user", Marked(f"Satz {i}", Mark.USER_PTT))
    assert werte(k.fuer_prompt()) == ["Satz 3", "Satz 4", "Satz 5"]


def test_das_fenster_ist_auch_nach_zeit_begrenzt():
    """Nur eine Anzahl haelt ein Gespraech von gestern frisch, wenn seither
    niemand geredet hat."""
    u = Uhr()
    k = kurz(uhr=u, frist_s=60.0)
    k.merken("user", Marked("alt", Mark.USER_PTT))
    u.weiter(61.0)
    assert werte(k.fuer_prompt()) == []


def test_die_frist_laeuft_auch_ohne_neuen_eintrag_ab():
    """Sonst verfiele nichts, solange niemand redet -- also genau dann, wenn
    der Rechner unbeaufsichtigt steht."""
    u = Uhr()
    k = kurz(uhr=u, frist_s=60.0)
    k.merken("user", Marked("alt", Mark.USER_PTT))
    u.weiter(61.0)
    assert k.zaehler()["runden"] == 0


def test_beides_ist_einstellbar():
    k = kurz(fenster=2, frist_s=5.0)
    z = k.zaehler()
    assert z["fenster"] == 2 and z["frist_s"] == 5.0


def test_ein_fenster_kleiner_als_eine_runde_wird_abgelehnt():
    with pytest.raises(ValueError):
        mem.Kurzzeit(fenster=0)


# -- Was gefiltert wurde, wird gezaehlt ------------------------------------

def test_gefiltertes_wird_gezaehlt():
    """Ein stiller Filter ist von einem leeren Gedaechtnis nicht zu
    unterscheiden, und dann sucht jemand den Fehler im Modell."""
    k = kurz()
    k.merken("a", Marked("x", Mark.TAINTED))
    k.merken("b", Marked("y", Mark.USER_AUDIO))
    k.fuer_prompt("durchgang1")
    z = k.zaehler()["gefiltert"]
    assert z["durchgang1:tainted"] == 1
    assert z["durchgang1:user_audio"] == 1


def test_ein_verbotener_eintrag_verhindert_den_prompt_nicht():
    """`pruefe_senke` wirft, und das ist an einer Senke richtig -- hier waere
    es falsch: eine einzelne verbotene Vorrunde soll nicht darin stehen, nicht
    den ganzen Prompt verhindern."""
    k = kurz()
    k.merken("a", Marked("verboten", Mark.TAINTED))
    k.merken("b", Marked("erlaubt", Mark.USER_PTT))
    assert werte(k.fuer_prompt("durchgang1")) == ["erlaubt"]


def test_leeren_gibt_die_zahl_zurueck():
    k = kurz()
    k.merken("a", Marked("x", Mark.USER_PTT))
    k.merken("b", Marked("y", Mark.USER_PTT))
    assert k.leeren() == 2
    assert k.zaehler()["runden"] == 0


# ==========================================================================
# T-6.3 -- das Langzeitgedaechtnis
# ==========================================================================

from daimon.mind.store import Store           # noqa: E402


def lang(tmp_path, uhr=None):
    s = Store(tmp_path / "memory.db")
    s.migrieren()
    return mem.Langzeit(store=s, uhr=uhr or Uhr()), s


PTT = Mark.USER_PTT
BEFEHL = "Merk dir: der Zweitschluessel liegt beim Nachbarn"


# -- Nur auf ausdrueckliche Anweisung --------------------------------------

def test_ohne_anweisung_wird_nichts_gemerkt(tmp_path):
    l, _ = lang(tmp_path)
    with pytest.raises(mem.GedaechtnisFehler) as f:
        l.merken("Kaffee", aeusserung=Marked("Ich trinke Kaffee", PTT))
    assert "Anweisung" in str(f.value)
    assert l.auflisten() == []


@pytest.mark.parametrize("satz", [
    "Merk dir: Kaffee um drei",
    "merke dir das bitte: Kaffee um drei",
    "Notiere: Kaffee um drei",
    "Vergiss nicht: Kaffee um drei",
    "remember that Kaffee um drei",
])
def test_erkannte_merkbefehle(satz):
    assert mem.anweisung_erkannt(satz) is True


@pytest.mark.parametrize("satz", [
    "Ich trinke Kaffee um drei",
    "Was war das nochmal?",
    "Erinnerst du dich an gestern?",
    "",
])
def test_nicht_als_merkbefehl_gezaehlt(satz):
    """Bewusst eng -- eine breite Liste liesse jede Aeusserung als Merkbefehl
    durchgehen, und dann merkte sich das Pet alles, was jemand sagt."""
    assert mem.anweisung_erkannt(satz) is False


# -- Nur woertlich ---------------------------------------------------------

def test_eine_woertliche_spanne_wird_gemerkt(tmp_path):
    l, _ = lang(tmp_path)
    eid = l.merken("der Zweitschluessel liegt beim Nachbarn",
                   aeusserung=Marked(BEFEHL, PTT), turn_id="t-1")
    assert eid > 0
    e = l.auflisten()[0]
    assert e["wert"].value == "der Zweitschluessel liegt beim Nachbarn"
    assert e["wert"].mark is PTT
    assert e["turn_id"] == "t-1"


def test_eine_modellzusammenfassung_wird_abgelehnt(tmp_path):
    """Der zweite Test aus der Akzeptanzliste: ueber eine Modellantwort einen
    Eintrag erzeugen. Eine Zusammenfassung ist gerade dadurch definiert, dass
    sie andere Worte benutzt -- sie besteht die Woertlichkeitspruefung nicht.
    """
    l, _ = lang(tmp_path)
    with pytest.raises(mem.GedaechtnisFehler) as f:
        l.merken("Der Nutzer bewahrt einen Ersatzschluessel nebenan auf",
                 aeusserung=Marked(BEFEHL, PTT))
    assert "woertlich" in str(f.value)
    assert l.auflisten() == []


def test_eine_leere_spanne_wird_abgelehnt(tmp_path):
    l, _ = lang(tmp_path)
    with pytest.raises(mem.GedaechtnisFehler):
        l.merken("   ", aeusserung=Marked(BEFEHL, PTT))


# -- Nichts aus passiver Beobachtung ---------------------------------------

@pytest.mark.parametrize("marke", [Mark.TAINTED, Mark.USER_AUDIO, Mark.TRUSTED])
def test_nichts_ausser_user_ptt(tmp_path, marke):
    """`trusted` steht ausdruecklich mit dabei: das ist unter anderem der Hub
    selbst, und ein Pet, das sich merkt was sein eigener Hub gesagt hat,
    merkt sich seine eigenen Vermutungen."""
    l, _ = lang(tmp_path)
    with pytest.raises(mem.GedaechtnisFehler) as f:
        l.merken("liegt beim Nachbarn", aeusserung=Marked(BEFEHL, marke))
    assert "user_ptt" in str(f.value)


def test_eine_stunde_bildschirmwahrnehmung_erzeugt_null_eintraege(tmp_path):
    """Der erste Test aus der Akzeptanzliste, nachgestellt: OCR-Text traegt
    `tainted` und kommt aus keiner Aeusserung."""
    l, _ = lang(tmp_path)
    for i in range(3600):
        with pytest.raises(mem.GedaechtnisFehler):
            l.merken(f"Zeile {i}",
                     aeusserung=Marked(f"Merk dir: Zeile {i}", Mark.TAINTED))
    assert l.auflisten() == []
    assert l.abgelehnt["nur aus user_ptt, nicht aus tainted"] == 3600


# -- Abruf, auflisten, loeschen -------------------------------------------

def test_die_textsuche_findet_ohne_ruecksicht_auf_gross_und_klein(tmp_path):
    l, _ = lang(tmp_path)
    l.merken("Zweitschluessel liegt beim Nachbarn",
             aeusserung=Marked(BEFEHL, PTT))
    l.merken("Kaffee um drei",
             aeusserung=Marked("Merk dir: Kaffee um drei", PTT))
    assert len(l.suchen("SCHLUESSEL")) == 1
    assert len(l.suchen("kaffee")) == 1
    assert l.suchen("Segelboot") == []


def test_eine_leere_suche_liefert_nichts_statt_allem(tmp_path):
    """Sonst waere ein vergessener Suchbegriff eine Vollauskunft."""
    l, _ = lang(tmp_path)
    l.merken("Kaffee um drei", aeusserung=Marked("Merk dir: Kaffee um drei", PTT))
    assert l.suchen("") == []
    assert l.suchen("   ") == []


def test_eintraege_sind_einzeln_loeschbar(tmp_path):
    """Wer sich etwas merken laesst, muss es auch wieder loswerden koennen,
    ohne alles zu verlieren."""
    l, _ = lang(tmp_path)
    a = l.merken("Kaffee um drei", aeusserung=Marked("Merk dir: Kaffee um drei", PTT))
    l.merken("Zweitschluessel liegt beim Nachbarn", aeusserung=Marked(BEFEHL, PTT))
    assert l.loeschen(a) is True
    assert [e["wert"].value for e in l.auflisten()] == \
        ["Zweitschluessel liegt beim Nachbarn"]
    assert l.loeschen(a) is False


def test_die_markierung_ueberlebt_den_neustart(tmp_path):
    """Der Rundgang durch die Datenbank aus T-6.1, hier an echten Eintraegen."""
    l, s = lang(tmp_path)
    l.merken("Kaffee um drei", aeusserung=Marked("Merk dir: Kaffee um drei", PTT))
    s.schliessen()

    zweiter = Store(tmp_path / "memory.db")
    zweiter.migrieren()
    e = mem.Langzeit(store=zweiter).auflisten()[0]
    assert e["wert"].mark is PTT and e["wert"].value == "Kaffee um drei"


def test_kein_embedding_stack():
    """„Abruf ueber Textsuche -- kein Embedding-Stack, solange die Textsuche
    reicht."

    Geprueft werden die IMPORTE und nicht der Quelltext: eine Textsuche im
    eigenen Modul faellt ueber den eigenen Kommentar, der die Regel erklaert.
    Ein Modul, das heimlich einen Stack nachzieht, faellt hier auf.
    """
    import ast
    import inspect

    baum = ast.parse(inspect.getsource(mem))
    module = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            module.update(a.name.split(".")[0] for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            module.add(knoten.module.split(".")[0])
    verboten = {"sentence_transformers", "faiss", "chromadb", "torch",
                "numpy", "openai", "transformers"}
    assert not (module & verboten), module & verboten
