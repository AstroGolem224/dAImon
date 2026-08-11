"""T-4.11 — eine Antwort zaehlt nur mit passender Nonce UND passendem Absender.

`cancel` ist kein `decline`: ein weggewischtes Fenster heisst "keine
Antwort". Wer daraus ein Nein macht, protokolliert eine Entscheidung, die
niemand getroffen hat.
"""
from __future__ import annotations

import json

import pytest

from daimon.hub import consent as C


def stellen(c: C.Consent, *, jetzt=0.0, action_id="fs.file.delete",
            params_hash="sha256:aa", absender=":1.42") -> C.Rueckfrage:
    return c.stellen(action_id=action_id, params_hash=params_hash,
                     prompt_shown=f"{action_id} ausfuehren?",
                     absender=absender, jetzt=jetzt)


def test_zustimmung_erzeugt_eine_einmalige_freigabe(tmp_path):
    c = C.Consent.laden(tmp_path)
    r = stellen(c)
    c.antwort(rueckfrage_id=r.id, nonce=r.nonce, absender=r.absender,
              zustand=C.ZUSTIMMUNG, jetzt=1.0)
    assert c.freigabe_einloesen(action_id=r.action_id,
                                params_hash=r.params_hash, jetzt=2.0)
    # Genau einmal.
    assert not c.freigabe_einloesen(action_id=r.action_id,
                                    params_hash=r.params_hash, jetzt=3.0)


def test_die_freigabe_gilt_nur_fuer_dieselben_parameter(tmp_path):
    c = C.Consent.laden(tmp_path)
    r = stellen(c, params_hash="sha256:aa")
    c.antwort(rueckfrage_id=r.id, nonce=r.nonce, absender=r.absender,
              zustand=C.ZUSTIMMUNG, jetzt=1.0)
    assert not c.freigabe_einloesen(action_id=r.action_id,
                                    params_hash="sha256:bb", jetzt=2.0)
    # Positivkontrolle im selben Test.
    assert c.freigabe_einloesen(action_id=r.action_id,
                                params_hash="sha256:aa", jetzt=2.0)


def test_eine_falsche_nonce_zaehlt_nicht(tmp_path):
    c = C.Consent.laden(tmp_path)
    r = stellen(c)
    with pytest.raises(C.ConsentFehler) as f:
        c.antwort(rueckfrage_id=r.id, nonce="ausgedacht", absender=r.absender,
                  zustand=C.ZUSTIMMUNG, jetzt=1.0)
    assert "Nonce" in str(f.value)
    assert not c.hat_freigabe(action_id=r.action_id, params_hash=r.params_hash)
    # Und die Rueckfrage steht noch offen -- ein Fehlversuch beantwortet sie
    # nicht.
    assert r.id in c.offen


def test_ein_fremder_absender_zaehlt_nicht(tmp_path):
    """Die Nonce steht in der Benachrichtigung; wer sie sieht, koennte sie
    zurueckschicken."""
    c = C.Consent.laden(tmp_path)
    r = stellen(c, absender=":1.42")
    with pytest.raises(C.ConsentFehler) as f:
        c.antwort(rueckfrage_id=r.id, nonce=r.nonce, absender=":1.99",
                  zustand=C.ZUSTIMMUNG, jetzt=1.0)
    assert "Absender" in str(f.value)
    assert not c.hat_freigabe(action_id=r.action_id, params_hash=r.params_hash)


def test_ablehnung_und_abbruch_sind_verschiedene_zustaende(tmp_path):
    c = C.Consent.laden(tmp_path)
    eins = stellen(c)
    zwei = stellen(c)
    a = c.antwort(rueckfrage_id=eins.id, nonce=eins.nonce,
                  absender=eins.absender, zustand=C.ABLEHNUNG, jetzt=1.0)
    b = c.antwort(rueckfrage_id=zwei.id, nonce=zwei.nonce,
                  absender=zwei.absender, zustand=C.ABBRUCH, jetzt=1.0)
    assert a.zustand == "declined"
    assert b.zustand == "cancelled"
    assert a.zustand != b.zustand
    for r in (a, b):
        assert not c.hat_freigabe(action_id=r.action_id,
                                  params_hash=r.params_hash)


def test_eine_zustimmung_nach_der_frist_wird_zum_abbruch(tmp_path):
    """Auch ein `granted`, das zu spaet eintrifft."""
    c = C.Consent.laden(tmp_path)
    r = stellen(c, jetzt=0.0)
    ergebnis = c.antwort(rueckfrage_id=r.id, nonce=r.nonce,
                         absender=r.absender, zustand=C.ZUSTIMMUNG,
                         jetzt=r.frist + 1.0)
    assert ergebnis.zustand == C.ABBRUCH
    assert not c.hat_freigabe(action_id=r.action_id, params_hash=r.params_hash)


def test_abgelaufene_rueckfragen_werden_cancelled_nicht_declined(tmp_path):
    c = C.Consent.laden(tmp_path)
    r = stellen(c, jetzt=0.0)
    faellig = c.ablaufen_lassen(jetzt=r.frist + 0.1)
    assert [x.id for x in faellig] == [r.id]
    assert faellig[0].zustand == C.ABBRUCH
    assert c.offen == {}


def test_mehrere_gleichzeitige_rueckfragen_sind_unterscheidbar(tmp_path):
    c = C.Consent.laden(tmp_path)
    a = stellen(c, action_id="media.stop")
    b = stellen(c, action_id="fs.file.delete")
    assert a.id != b.id and a.nonce != b.nonce
    assert len(c.offen) == 2
    # Die Nonce der einen beantwortet die andere nicht.
    with pytest.raises(C.ConsentFehler):
        c.antwort(rueckfrage_id=b.id, nonce=a.nonce, absender=b.absender,
                  zustand=C.ZUSTIMMUNG, jetzt=1.0)


def test_eine_beantwortete_rueckfrage_laesst_sich_nicht_erneut_beantworten(tmp_path):
    c = C.Consent.laden(tmp_path)
    r = stellen(c)
    c.antwort(rueckfrage_id=r.id, nonce=r.nonce, absender=r.absender,
              zustand=C.ABLEHNUNG, jetzt=1.0)
    with pytest.raises(C.ConsentFehler):
        c.antwort(rueckfrage_id=r.id, nonce=r.nonce, absender=r.absender,
                  zustand=C.ZUSTIMMUNG, jetzt=2.0)


def test_ein_neustart_hinterlaesst_keine_verwaiste_genehmigung(tmp_path):
    """Die offene Rueckfrage wird NICHT wiederhergestellt.

    Ihr Fenster hat der Neustart weggeraeumt; sie offen zu lassen hiesse, auf
    eine Antwort zu warten, die niemand mehr geben kann.
    """
    c = C.Consent.laden(tmp_path)
    r = stellen(c)
    assert json.loads((tmp_path / C.DATEI).read_text(encoding="utf-8"))

    nach_neustart = C.Consent.laden(tmp_path)
    assert nach_neustart.offen == {}
    assert not nach_neustart.hat_freigabe(action_id=r.action_id,
                                          params_hash=r.params_hash)
    # Und die Datei ist danach leer, statt die Leiche zu behalten.
    assert json.loads((tmp_path / C.DATEI).read_text(encoding="utf-8")) == []


def test_der_prompt_text_gehoert_zur_rueckfrage(tmp_path):
    """`prompt_shown` geht so ins Audit -- der EXAKTE gezeigte Text."""
    c = C.Consent.laden(tmp_path)
    r = stellen(c, action_id="fs.file.delete")
    assert r.prompt_shown == "fs.file.delete ausfuehren?"


def test_die_freigabe_haengt_an_der_tat_nicht_an_der_rueckfrage(tmp_path):
    c = C.Consent.laden(tmp_path)
    erste = stellen(c)
    zweite = stellen(c)
    assert erste.action_hash == zweite.action_hash
    c.antwort(rueckfrage_id=erste.id, nonce=erste.nonce,
              absender=erste.absender, zustand=C.ZUSTIMMUNG, jetzt=1.0)
    # Dieselbe Tat, deshalb dieselbe Freigabe -- aber nur einmal.
    assert c.freigabe_einloesen(action_id=zweite.action_id,
                                params_hash=zweite.params_hash, jetzt=2.0)
    assert not c.freigabe_einloesen(action_id=zweite.action_id,
                                    params_hash=zweite.params_hash, jetzt=2.0)
