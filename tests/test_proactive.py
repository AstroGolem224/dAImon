"""T-6.6 -- proaktives Verhalten.

Zwei Tests stehen ausdruecklich in der Verifikation: derselbe Anlass spricht
nicht zweimal, und waehrend einer proaktiven Phase sind es NULL API-Aufrufe.
Der zweite wird hier nicht ueber einen Zaehler im Modul belegt -- ein Modul,
das seine eigenen Aufrufe zaehlt, ist eine Selbstauskunft -- sondern daran,
dass es die Aufrufe gar nicht kennt.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from daimon.mind import proactive as pro
from daimon.mind.threshold import Schwelle


class Uhr:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def weiter(self, s: float) -> None:
        self.t += s


def bauen(stufe="helpful", uhr=None, abstand=90.0):
    return pro.Proaktiv(schwelle=Schwelle(stufe), uhr=uhr or Uhr(),
                        mindestabstand_s=abstand)


# -- Die Ausloeser ---------------------------------------------------------

@pytest.mark.parametrize("anlass", ["agent_wartet", "build_kaputt", "fehlerbild"])
def test_die_drei_ausloeser_der_akzeptanzliste_gibt_es(anlass):
    assert anlass in pro.ANLAESSE


def test_ein_unbekannter_anlass_wirft():
    """Ein Anlass, den niemand eingetragen hat, ist kein Anlass, sondern ein
    Tippfehler."""
    p = bauen()
    with pytest.raises(pro.ProaktivFehler):
        p.melden("stimmung_schlecht", "x")


def test_ein_kritischer_anlass_kommt_durch():
    p = bauen()
    v = p.melden("build_kaputt", "commit-abc")
    assert v is not None and v.dringlichkeit == pro.KRITISCH


# -- Die Schwelle wird respektiert ----------------------------------------

def test_silent_schweigt_auch_bei_kaputtem_build():
    """Der Nutzer hat `silent` gewaehlt. Das gilt auch dann, wenn dieses
    Modul die Sache fuer wichtig haelt."""
    p = bauen("silent")
    assert p.melden("build_kaputt", "commit-abc") is None
    assert p.zaehler()["abgewiesen"][pro.GRUND_SCHWELLE] == 1


def test_urgent_hoert_das_fehlerbild_nicht():
    p = bauen("urgent")
    assert p.melden("fehlerbild", "ImportError") is None
    assert p.melden("build_kaputt", "commit-abc") is not None


def test_nur_chatty_hoert_beilaeufiges():
    u = Uhr()
    assert bauen("helpful", u).melden("beobachtung", "x") is None
    assert bauen("chatty", u).melden("beobachtung", "x") is not None


def test_der_anlass_geht_nie_als_gefragt_durch():
    """Wer hier einen gefragten Anlass hineingaebe, haette die Schwelle
    ausgehebelt -- `antwort` steht deshalb gar nicht in der Tabelle."""
    assert "antwort" not in pro.ANLAESSE
    assert "rueckfrage" not in pro.ANLAESSE


# -- Derselbe Sachverhalt spricht genau einmal ----------------------------

def test_derselbe_sachverhalt_spricht_nicht_zweimal():
    """Der erste Test aus der Verifikation. Ein Build, der seit zwanzig
    Minuten kaputt ist, ist nicht zwanzigmal kaputt."""
    u = Uhr()
    p = bauen(uhr=u)
    assert p.melden("build_kaputt", "commit-abc") is not None
    for _ in range(20):
        u.weiter(300.0)
        assert p.melden("build_kaputt", "commit-abc") is None
    assert p.zaehler()["abgewiesen"][pro.GRUND_WIEDERHOLUNG] == 20


def test_ein_anderer_sachverhalt_darf_sprechen():
    u = Uhr()
    p = bauen(uhr=u)
    assert p.melden("build_kaputt", "commit-abc") is not None
    u.weiter(100.0)
    assert p.melden("build_kaputt", "commit-def") is not None


def test_nach_erledigt_darf_derselbe_sachverhalt_wieder_sprechen():
    """Ohne diesen Weg waere „nie wieder zum selben Sachverhalt" nach dem
    ersten Tag gleichbedeutend mit „nie"."""
    u = Uhr()
    p = bauen(uhr=u)
    p.melden("build_kaputt", "commit-abc")
    assert p.erledigt("build_kaputt", "commit-abc") is True
    u.weiter(100.0)
    assert p.melden("build_kaputt", "commit-abc") is not None


def test_erledigt_meldet_wenn_es_nichts_zu_erledigen_gab():
    assert bauen().erledigt("build_kaputt", "nie gesagt") is False


# -- Der Mindestabstand ----------------------------------------------------

def test_zwei_anlaesse_kurz_hintereinander_werden_gedeckelt():
    """Die Schwelle sagt, WAS wichtig genug ist; der Abstand sagt, wie oft.
    Drei kritische Anlaesse in einer Minute sind drei richtige Entscheidungen
    und ein unertraeglicher Assistent."""
    u = Uhr()
    p = bauen(uhr=u)
    assert p.melden("build_kaputt", "a") is not None
    u.weiter(10.0)
    assert p.melden("agent_wartet", "b") is None
    assert p.zaehler()["abgewiesen"][pro.GRUND_ABSTAND] == 1


def test_nach_dem_abstand_geht_es_weiter():
    u = Uhr()
    p = bauen(uhr=u, abstand=90.0)
    p.melden("build_kaputt", "a")
    u.weiter(90.0)
    assert p.melden("agent_wartet", "b") is not None


def test_ein_am_abstand_abgewiesener_sachverhalt_bleibt_sagbar():
    """Wer ihn dort vermerkte, verschluckte ihn fuer immer -- weil zufaellig
    eine Minute vorher etwas anderes war."""
    u = Uhr()
    p = bauen(uhr=u)
    p.melden("build_kaputt", "a")
    u.weiter(10.0)
    assert p.melden("agent_wartet", "b") is None
    u.weiter(100.0)
    assert p.melden("agent_wartet", "b") is not None


# -- Keine Nebenwirkungen: der zweite Test der Verifikation ---------------

def test_das_modul_kennt_keine_nebenwirkenden_wege():
    """„zaehlt die API-Aufrufe waehrend einer proaktiven Phase und verlangt
    null."

    Nicht ueber einen Zaehler IM Modul -- das waere eine Selbstauskunft --
    sondern daran, dass es die Wege gar nicht kennt. Ein Modul, das nichts
    importiert, womit man telefonieren kann, telefoniert nicht.
    """
    baum = ast.parse(inspect.getsource(pro))
    module = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            module.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            module.add(knoten.module)
    verboten = ("egress", "declassify", "consent", "socket", "http",
                "urllib", "requests", "subprocess", "context")
    for m in module:
        assert not any(v in m for v in verboten), m


def test_der_vorschlag_kann_nichts_ausloesen():
    """Ein Datensatz ohne Methoden. Eine Klasse, die selbst sprechen koennte
    und es nur unterlaesst, spricht, sobald jemand eine Zeile hinzufuegt."""
    v = bauen().melden("build_kaputt", "a")
    eigene = [n for n in dir(v)
              if not n.startswith("_") and callable(getattr(v, n))]
    assert eigene == []


def test_es_gibt_keine_naht_fuer_nebenwirkungen():
    """Der erste Entwurf dieses Tests speiste Sonden ein und verlangte, dass
    sie auf null stehen. Sie standen auf null, weil sie nirgends eingespeist
    WERDEN KONNTEN -- der Test haette auch bestanden, wenn das Modul
    telefoniert haette. Geprueft gehoert stattdessen, dass es die Naht gar
    nicht gibt.
    """
    parameter = set(inspect.signature(pro.Proaktiv.__init__).parameters)
    assert parameter == {"self", "schwelle", "mindestabstand_s", "uhr"}


def test_eine_lange_proaktive_phase_bleibt_folgenlos():
    """Zehn Vorschlaege, und nichts davon ist ein Aufruf: `melden` gibt
    Datensaetze zurueck, sonst nichts."""
    u = Uhr()
    p = bauen(stufe="chatty", uhr=u)
    heraus = []
    for i in range(10):
        u.weiter(100.0)
        heraus.append(p.melden("build_kaputt", f"commit-{i}"))
    assert all(isinstance(v, pro.Vorschlag) for v in heraus)
    assert p.zaehler()["vorschlaege"] == 10


def test_die_gegenprobe_in_t59_lehnt_proaktiv_ohnehin_ab(tmp_path):
    """Beide Enden sind zu: hier gibt es keinen Aufruf, und dort wuerde er
    abgewiesen -- auch mit gueltiger Rundenmarke."""
    from daimon.eyes import context as ctx
    from daimon.hub import declassify as dk
    from daimon.hub.marks import MarkenBuch

    marken = MarkenBuch(frist_s=120.0)
    speicher = ctx.Kontextspeicher(verzeichnis=tmp_path / "context")
    gate = dk.Deklassifizierung(marken=marken, speicher=speicher)
    t = marken.ausgeben(quelle="auth", turn_id="t1").turn_id
    with pytest.raises(dk.GateFehler) as f:
        gate.freigeben(aeusserung="Was steht auf meinem Bildschirm?",
                       turn_id=t, proaktiv=True)
    assert f.value.grund == dk.GRUND_PROAKTIV


# -- Zaehler ---------------------------------------------------------------

def test_der_zaehler_nennt_stufe_abstand_und_gruende():
    u = Uhr()
    p = bauen("urgent", u)
    p.melden("fehlerbild", "x")
    z = p.zaehler()
    assert z["stufe"] == "urgent"
    assert z["mindestabstand_s"] == 90.0
    assert z["abgewiesen"][pro.GRUND_SCHWELLE] == 1
    assert z["vorschlaege"] == 0
