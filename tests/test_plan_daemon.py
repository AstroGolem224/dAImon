"""T-8.3 -- der Scheduler des Zeitplaners.

Was hier geprueft wird, ist die Kette, nicht ein Glied: ein Eintrag wird
faellig, die Blase geht raus, die Sprache geht durchs Gatter, der Eintrag
ist danach `gemeldet` -- und eine zweite Runde feuert ihn NICHT noch einmal.
Das ist die Stelle, an der ein „Erinnerungsdienst", der jede Minute dasselbe
sagt, entstehen wuerde.
"""
from __future__ import annotations

import pytest

from daimon.common.protocol import Mark
from daimon.mind.proactive import Proaktiv
from daimon.mind.threshold import Schwelle
from daimon.plan import daemon as d
from daimon.plan.store import Store


class Uhr:
    """Eine Uhr, die man von Hand weiterdreht."""
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class Senke:
    """Sammelt, was der Dienst nach draussen geben wollte."""
    def __init__(self) -> None:
        self.nachrichten: list[dict] = []

    def __call__(self, n: dict) -> None:
        self.nachrichten.append(n)


def plan(tmp_path, uhr=None, **kw):
    uhr = uhr or Uhr()
    store = Store(tmp_path / "plan.db", uhr=uhr)
    store.migrieren()
    senken = {"ereignisse": Senke(), "sprueche": Senke()}
    p = d.Plan(store, uhr=uhr,
               ereignis_senden=senken["ereignisse"],
               spruch_senden=senken["sprueche"],
               **kw)
    return p, senken, uhr


# -- Die Kette: faellig -> Blase + Sprache + gemeldet -----------------------

def test_ein_faelliger_termin_wird_zu_blase_und_spruch(tmp_path):
    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten", "marke": "user_ptt"},
                      unit=d.MIND_UNIT)
    uhr.t += 21 * 60
    assert p.runde() == 1

    assert senken["ereignisse"].nachrichten[0]["type"] == "termin_faellig"
    assert senken["ereignisse"].nachrichten[0]["payload"]["text"] == "Tee"
    spruch = senken["sprueche"].nachrichten[0]
    assert spruch["anlass"] == "termin_faellig"
    assert spruch["kanal"] == "ungefragt"
    assert spruch["werte"] == {"titel": "Tee"}
    # Die ECHTE Marke, nicht in `trusted` umgeschrieben. Ob daraus Sprache
    # wird, entscheidet `hub/sprechtext.aus_vorlage` -- siehe unten.
    assert spruch["markierung"] == "user_ptt"


def test_ein_gemeldeter_termin_feuert_nicht_noch_einmal(tmp_path):
    """Genau hier entstuende ein Dienst, der alle 15 Sekunden dasselbe sagt."""
    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten"})
    uhr.t += 21 * 60
    assert p.runde() == 1
    assert p.runde() == 0
    assert p.runde() == 0
    assert len(senken["ereignisse"].nachrichten) == 1


def test_ein_noch_nicht_faelliger_termin_bleibt_ruhig(tmp_path):
    """Positivkontrolle in die andere Richtung: ohne Faelligkeit passiert
    nichts -- ein Dienst, der immer feuert, bestuende die oberen Tests."""
    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten"})
    assert p.runde() == 0
    assert senken["ereignisse"].nachrichten == []
    assert senken["sprueche"].nachrichten == []


def test_neustart_doppelt_nicht(tmp_path):
    """Der Dienst stirbt zwischen Faelligkeit und Neustart. `gemeldet` steht
    in der Datenbank -- der zweite Prozess sieht denselben Stand."""
    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten"})
    uhr.t += 21 * 60
    p.runde()
    p.store.schliessen()

    p2, senken2, _ = plan(tmp_path, uhr=uhr)
    assert p2.runde() == 0
    assert senken2["ereignisse"].nachrichten == []


def test_ein_waehrend_des_stopps_verpasster_termin_holt_auf(tmp_path):
    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Anruf",
                       "wann": "in 20 minuten"})
    uhr.t += 3 * 86400          # der Rechner war drei Tage aus
    assert p.runde() == 1
    assert senken["ereignisse"].nachrichten[0]["payload"]["text"] == "Anruf"


# -- Das Sprach-Gatter -------------------------------------------------------

def test_stufe_silent_laest_die_blase_und_schweigt(tmp_path):
    """Die Blase ist Sichtbarmachung und kommt immer. Die Sprache ist eine
    Aeusserung und geht durch die Persona-Schwelle."""
    p, senken, uhr = plan(tmp_path, proaktiv=Proaktiv(schwelle=Schwelle("silent")))
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten"})
    uhr.t += 21 * 60
    assert p.runde() == 1
    assert len(senken["ereignisse"].nachrichten) == 1
    assert senken["sprueche"].nachrichten == []


def test_ein_tainted_titel_wird_nur_zur_blase(tmp_path):
    """`aus_vorlage` nimmt variable Anteile nur als `trusted`. Ein Titel ohne
    Nutzerherkunft bleibt gesprochen ungesagt -- sichtbar ist er trotzdem."""
    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten"})   # keine marke -> tainted
    uhr.t += 21 * 60
    assert p.runde() == 1
    assert senken["sprueche"].nachrichten[0]["markierung"] == "tainted"


def test_die_marke_kommt_vom_peer_und_nie_aus_dem_feld(tmp_path):
    """„Ein Feld, das der Absender setzt, sagt nichts" (hub/policy.py).

    Drei Faelle: ohne Peer wird jede Behauptung `tainted`; der Mind darf die
    Marke der Aeusserung durchreichen; `trusted` kann sich niemand geben --
    es ist die einzige Marke, die spricht.
    """
    p, _, _ = plan(tmp_path)

    def marke_von(anfrage_marke, unit):
        antwort = p.bediene_anfrage(
            {"v": 1, "art": "neu", "titel": "x", "wann": "in 20 minuten",
             "marke": anfrage_marke}, unit=unit)
        eintrag = [e for e in p.store.liste() if e["id"] == antwort["id"]][0]
        return eintrag["titel"].mark

    assert marke_von("user_ptt", "") is Mark.TAINTED
    assert marke_von("trusted", "") is Mark.TAINTED
    assert marke_von("trusted", d.MIND_UNIT) is Mark.TAINTED
    # Positivkontrolle: der Mind reicht sonst durch -- eine Ableitung, die
    # immer `tainted` liefert, bestuende die drei Zeilen darueber.
    assert marke_von("user_ptt", d.MIND_UNIT) is Mark.USER_PTT


def test_ein_ptt_titel_kommt_an_der_wirklichen_senke_nicht_durch(tmp_path):
    """Die Naht bis zur Senke, nicht bis zum Feld: was der Dienst schickt,
    geht in `hub.sprechtext.aus_vorlage` -- und die nimmt nur `trusted`."""
    from daimon.hub import sprechtext

    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten", "marke": "user_ptt"},
                      unit=d.MIND_UNIT)
    uhr.t += 21 * 60
    p.runde()
    spruch = senken["sprueche"].nachrichten[0]
    urteil = sprechtext.aus_vorlage(spruch["anlass"], spruch["werte"],
                                    markierung=spruch["markierung"])
    assert urteil.ok is False
    assert urteil.grund == sprechtext.GRUND_NICHT_TRUSTED
    # Positivkontrolle: dieselbe Vorlage mit `trusted` geht durch -- eine
    # Senke, die alles ablehnt, bestuende die Zeilen darueber.
    assert sprechtext.aus_vorlage(spruch["anlass"], spruch["werte"],
                                  markierung="trusted").ok is True


def test_der_mindestabstand_gilt_auch_fuer_termine(tmp_path):
    """Drei richtige Entscheidungen in einer Minute ergeben einen
    unertraeglichen Assistenten -- der Abstand aus T-6.6 gilt hier mit."""
    p, senken, uhr = plan(tmp_path, proaktiv=Proaktiv(uhr=lambda: 5000.0))
    for titel in ("eins", "zwei"):
        p.bediene_anfrage({"v": 1, "art": "neu", "titel": titel,
                           "wann": "in 20 minuten"})
    uhr.t += 21 * 60
    p.runde()
    assert len(senken["ereignisse"].nachrichten) == 2   # beide Blasen
    assert len(senken["sprueche"].nachrichten) == 1     # aber nur ein Spruch


# -- Zustellung: `gemeldet` ist eine Messung, keine Behauptung ---------------

def test_eine_nicht_zugestellte_blase_laesst_den_eintrag_offen(tmp_path):
    """Der Hub startet gerade neu, waehrend der Termin faellig wird. Wer hier
    `gemeldet` schreibt, hat die Erinnerung lautlos verschluckt."""
    def tote_senke(_n):
        raise ConnectionRefusedError(111, "Connection refused")

    p, senken, uhr = plan(tmp_path)
    p._ereignis = tote_senke
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten"})
    uhr.t += 21 * 60
    assert p.runde() == 0
    assert [e["status"] for e in p.store.liste()] == ["offen"]
    assert senken["sprueche"].nachrichten == []

    # Der Hub ist wieder da: dieselbe Zeile wird jetzt gemeldet.
    p._ereignis = senken["ereignisse"]
    assert p.runde() == 1
    assert [e["status"] for e in p.store.liste()] == ["gemeldet"]


def test_ein_toter_sprechweg_wiederholt_den_termin_nicht(tmp_path):
    """Die Gegenrichtung: die Blase ist die Zusage, die Sprache der Zusatz.
    Ein toter TTS darf nicht dazu fuehren, dass alle 15 s dieselbe Blase
    kommt."""
    def tote_senke(_n):
        raise ConnectionRefusedError(111, "Connection refused")

    p, senken, uhr = plan(tmp_path)
    p._spruch = tote_senke
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                       "wann": "in 20 minuten"})
    uhr.t += 21 * 60
    assert p.runde() == 1
    assert p.runde() == 0
    assert len(senken["ereignisse"].nachrichten) == 1


# -- Fokus -------------------------------------------------------------------


def test_zwei_fokusbloecke_hintereinander_feuern_beide(tmp_path):
    """`Proaktiv` sperrt das Paar (anlass, sachverhalt) fuer die
    Prozesslebensdauer. Mit der Konstanten „Kurz durchatmen." als Sachverhalt
    schwieg der Dienst ab dem ZWEITEN Fokusblock -- und niemand haette es
    gemerkt, weil die Blase weiter kam."""
    p, senken, uhr = plan(tmp_path, proaktiv=Proaktiv(uhr=lambda: uhr.t))
    for _ in range(2):
        p.bediene_anfrage({"v": 1, "art": "fokus_start", "minuten": 25})
        uhr.t += 26 * 60
        assert p.runde() == 1
    assert len(senken["ereignisse"].nachrichten) == 2
    assert len(senken["sprueche"].nachrichten) == 2


def test_zwei_termine_mit_gleichem_titel_sprechen_beide(tmp_path):
    p, senken, uhr = plan(tmp_path, proaktiv=Proaktiv(uhr=lambda: uhr.t))
    for _ in range(2):
        p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Tee",
                           "wann": "in 20 minuten"})
        uhr.t += 21 * 60
        assert p.runde() == 1
    assert len(senken["sprueche"].nachrichten) == 2




def test_fokus_start_legt_einen_block_an_und_das_ende_feurt(tmp_path):
    p, senken, uhr = plan(tmp_path)
    antwort = p.bediene_anfrage({"v": 1, "art": "fokus_start", "minuten": 45})
    assert antwort["ok"] is True
    assert antwort["ts_faellig"] == uhr.t + 45 * 60

    uhr.t += 46 * 60
    assert p.runde() == 1
    assert senken["ereignisse"].nachrichten[0]["type"] == "fokus_ende"
    spruch = senken["sprueche"].nachrichten[0]
    assert spruch["anlass"] == "fokus_ende"
    assert spruch["markierung"] == "trusted"   # der Dienst selbst ist die Quelle


def test_fokus_stop_verhindert_das_ende(tmp_path):
    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "fokus_start", "minuten": 45})
    antwort = p.bediene_anfrage({"v": 1, "art": "fokus_stop"})
    assert antwort["gestoppt"] == 1
    uhr.t += 46 * 60
    assert p.runde() == 0
    assert senken["ereignisse"].nachrichten == []


@pytest.mark.parametrize("minuten", [0, -5, 481, "bald", None])
def test_unbrauchbare_fokusdauern_werden_abgelehnt(tmp_path, minuten):
    p, _, _ = plan(tmp_path)
    antwort = p.bediene_anfrage({"v": 1, "art": "fokus_start",
                                 "minuten": minuten})
    assert antwort["ok"] is False


# -- Anfragen ----------------------------------------------------------------

def test_neu_ohne_titel_oder_zeitpunkt_wird_abgelehnt(tmp_path):
    p, _, _ = plan(tmp_path)
    assert p.bediene_anfrage({"v": 1, "art": "neu", "wann": "um 8"})["ok"] is False
    assert p.bediene_anfrage({"v": 1, "art": "neu", "titel": "x"})["ok"] is False


def test_neu_mit_unlesbarer_zeit_wird_abgelehnt(tmp_path):
    p, _, _ = plan(tmp_path)
    antwort = p.bediene_anfrage({"v": 1, "art": "neu", "titel": "x",
                                 "wann": "irgendwann"})
    assert antwort["ok"] is False
    assert antwort["grund"] == "unbrauchbar"


def test_eine_absurde_frist_wird_abgelehnt_und_vergiftet_nichts(tmp_path):
    """`--in 99999999999999h` legte die Zeile an und liess danach jedes
    `--liste` an `OSError(75)` aus `time.localtime` scheitern -- dauerhaft,
    ueber Neustarts hinweg. Zwei Wege, beide zu."""
    p, _, _ = plan(tmp_path)
    # Weg 1: die Obergrenze in `zeit.parse`. Die Zeile entsteht gar nicht.
    antwort = p.bediene_anfrage({"v": 1, "art": "neu", "titel": "x",
                                 "wann": "in 99999999999999 stunden"})
    assert antwort["ok"] is False
    assert p.bediene_anfrage({"v": 1, "art": "liste"})["eintraege"] == []

    # Weg 2: eine Zeile, die schon in der Datenbank steht (angelegt vor der
    # Obergrenze). `--liste` bleibt lesbar, und der Eintrag ist loeschbar.
    eintrag_id = p.store.anlegen("termin", "Altlast", 9e18)
    liste = p.bediene_anfrage({"v": 1, "art": "liste"})
    assert liste["ok"] is True
    assert liste["eintraege"][0]["titel"] == "Altlast"
    assert p.bediene_anfrage({"v": 1, "art": "loeschen",
                              "id": eintrag_id})["entfernt"] is True


def test_liste_loeschen_status(tmp_path):
    p, _, _ = plan(tmp_path)
    a = p.bediene_anfrage({"v": 1, "art": "neu", "titel": "a",
                           "wann": "in 20 minuten"})
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "b",
                       "wann": "in 30 minuten"})
    liste = p.bediene_anfrage({"v": 1, "art": "liste"})
    assert liste["ok"] and len(liste["eintraege"]) == 2
    assert p.bediene_anfrage({"v": 1, "art": "loeschen",
                              "id": a["id"]})["entfernt"] is True
    status = p.bediene_anfrage({"v": 1, "art": "status"})
    assert status["offen"] == 1


def test_loeschen_ohne_id_wird_abgelehnt(tmp_path):
    p, _, _ = plan(tmp_path)
    assert p.bediene_anfrage({"v": 1, "art": "loeschen"})["ok"] is False


def test_unbekannte_art_und_kein_dict_werden_abgelehnt(tmp_path):
    p, _, _ = plan(tmp_path)
    assert p.bediene_anfrage({"v": 1, "art": "alles_weg"})["ok"] is False
    assert p.bediene_anfrage("kein dict")["ok"] is False
    assert p.bediene_anfrage(None)["ok"] is False


# -- Wer schreiben darf --------------------------------------------------------

def am_draht(p, nutzlast: dict, unit: str) -> dict:
    """Eine Anfrage durch `bediene` -- die Stelle, an der die Unit ankommt.

    Ueber ein echtes Socketpaar, nicht am Draht vorbei: die Schranke sitzt
    genau hier und nicht in `bediene_anfrage`.
    """
    import json
    import socket as so
    a, b = so.socketpair(so.AF_UNIX, so.SOCK_STREAM)
    with a:
        a.sendall(json.dumps(nutzlast).encode() + b"\n")
        d.bediene(p, b, unit)
        return json.loads(a.makefile("rb").readline())


@pytest.mark.parametrize("art,rest", [
    ("neu", {"titel": "Fremdtext", "wann": "in 20 minuten"}),
    ("loeschen", {"id": 1}),
    ("fokus_start", {"minuten": 30}),
    ("fokus_stop", {}),
])
def test_eine_fremde_unit_darf_nicht_schreiben(tmp_path, art, rest):
    """Gemessen am 26.08.: ein fremder Prozess ohne Unit bekam auf `loeschen`
    ein `{"ok": true}` und auf `neu` eine Blase mit seinem Text auf dem
    Overlay -- damit war die Unit-Allowlist des Hub-Produzentensockets
    `plan.sock` gewaschen."""
    p, _, _ = plan(tmp_path)
    antwort = am_draht(p, {"v": 1, "art": art, **rest}, unit="app-fremd.scope")
    assert antwort["ok"] is False and antwort["grund"] == "fremde_unit"
    assert p.store.liste() == []


@pytest.mark.parametrize("unit", [d.MIND_UNIT, d.CLI_UNIT])
def test_mind_und_cli_duerfen_schreiben(tmp_path, unit):
    """Positivkontrolle: eine Schranke, die alles abweist, bestuende den Test
    oben ebenfalls."""
    p, _, _ = plan(tmp_path)
    antwort = am_draht(p, {"v": 1, "art": "neu", "titel": "Tee",
                           "wann": "in 20 minuten"}, unit=unit)
    assert antwort["ok"] is True
    assert len(p.store.liste()) == 1


def test_lesen_bleibt_fuer_jeden_offen(tmp_path):
    """`liste` und `status` geben nur zurueck, was der Anrufer als Nutzer
    ohnehin lesen koennte -- eine Sperre haette hier jedes Skript getroffen,
    das nur nachsehen will."""
    p, _, _ = plan(tmp_path)
    am_draht(p, {"v": 1, "art": "neu", "titel": "Tee", "wann": "in 20 minuten"},
             unit=d.CLI_UNIT)
    assert am_draht(p, {"v": 1, "art": "liste"}, unit="")["ok"] is True
    assert am_draht(p, {"v": 1, "art": "status"}, unit="")["offen"] == 1


# -- Positivkontrolle ---------------------------------------------------------

def test_positivkontrolle_die_kette_kann_ueberhaupt_feuern(tmp_path):
    """Negativtests allein beweisen nichts -- ein Dienst, der nie feuert,
    bestuende sie alle."""
    p, senken, uhr = plan(tmp_path)
    p.bediene_anfrage({"v": 1, "art": "neu", "titel": "Beweis",
                       "wann": "in 1 minute", "marke": "user_ptt"})
    uhr.t += 61
    p.runde()
    assert senken["ereignisse"].nachrichten != []
    assert senken["sprueche"].nachrichten != []
