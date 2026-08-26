"""T-3.9 — Stimme, Validator, Abkuehlung.

Was diese Tests koennen: den Validator vollstaendig, die Abkuehlung samt
Persistenz, die Sperrmechanik im Hub, die Segmentierung, die Lizenzpruefung, und
am echten Prozess: Socket-Aktivierung, Ausgabe und Unterbrechung -- letztere
gegen einen `pw-cat`-Stub im PATH, der protokolliert, was ausgegeben werden
sollte. Gemessen wird also, was der Pruefling **tun wollte**, nicht was die
Soundkarte tat.

Was sie nicht koennen: TTFA-p95 und "0 zusaetzliche Compute-Prozesse". Das erste
braucht 20 Laeufe mit echtem Modell (Messung in `tests/evidence/T-3.9-tts.json`),
das zweite den laufenden Dienst. Beides gehoert in `tests/verify/T-3.9.sh`.

Die Tests laufen OHNE sherpa-onnx: alles ausser den Modelltests haengt nicht am
Modell. Wo es gebraucht wird, steht ein `importorskip` -- ein Test, der wegen
einer fehlenden Abhaengigkeit gruen ist, ist ein Test, der nichts sagt, und
`skipped` sagt es sichtbar.
"""

import io
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import eigene_unit
from daimon.common.config import Config
from daimon.common.logging import get_logger
from daimon.face import tts as T
from daimon.hub.abkuehlung import Abkuehlung
from daimon.hub import sprechtext as S
from daimon.hub.daemon import Hub, TTS_SOCKET

REPO = Path(__file__).resolve().parents[1]
MODELL = REPO / "spikes/nvidia-voice/models/vits-piper-de_DE-thorsten-high"
UNIT_SERVICE = REPO / "config/systemd/daimon-tts.service"
UNIT_SOCKET = REPO / "config/systemd/daimon-tts.socket"


def stiller_logger():
    return get_logger("test", socket_path="/nicht/da", stream=io.StringIO())


def pw_cat_stub(bin_dir: Path, protokoll: Path, *, verzoegerung: float = 0.0) -> None:
    """Ein `pw-cat`, das nichts abspielt und alles aufschreibt.

    Es protokolliert Startzeit, Argumente, empfangene Bytes und Endezeit. Damit
    ist "es wurde etwas ausgegeben" an einer Datei messbar und nicht an einer
    Selbstauskunft des Dienstes (Regel 9).
    """
    p = bin_dir / "pw-cat"
    p.write_text(
        "#!/bin/sh\n"
        f'PROT="{protokoll}"\n'
        'printf "start %s %s\\n" "$(date +%s.%N)" "$*" >> "$PROT"\n'
        f'{"sleep " + str(verzoegerung) if verzoegerung else "true"}\n'
        'N=$(cat | wc -c)\n'
        'printf "bytes %s %s\\n" "$N" "$(date +%s.%N)" >> "$PROT"\n')
    p.chmod(0o755)


def warte_auf_ausgabe(prot: Path, *, bytes_mindestens: int = 1,
                      timeout_s: float = 30.0) -> list[str]:
    """Auf die `bytes`-Zeile des Stubs warten.

    Seit die Wiedergabe asynchron laeuft, ist die Antwort des Sprechers da,
    BEVOR der Stub fertig gelesen hat. Ein Test, der sofort nach der Antwort
    das Protokoll liest, misst deshalb einen Zwischenstand -- und wuerde
    zufaellig gruen oder rot.
    """
    ende = time.monotonic() + timeout_s
    while time.monotonic() < ende:
        zeilen = prot.read_text().splitlines() if prot.exists() else []
        if len([z for z in zeilen if z.startswith("bytes ")]) >= bytes_mindestens:
            return zeilen
        time.sleep(0.05)
    return prot.read_text().splitlines() if prot.exists() else []


@pytest.fixture(autouse=True)
def _tts_darf_sprechen(monkeypatch, tmp_path):
    """Seit dem 19.08. laesst `tts.sock` nur `daimon-tts.service` heran.

    Diese Tests laufen nicht unter dieser Unit; dass sie vorher alle gruen
    waren, zeigt genau das, was der Befund sagte -- es gab keine Pruefung.
    Erlaubt wird die Unit des TESTPROZESSES, echt gemessen. Geprueft wird die
    Sperre selbst in `test_hub_socket_allowlisten.py`.
    """
    from daimon.hub import daemon as _D
    monkeypatch.setattr(_D, "TTS_UNITS", (eigene_unit(tmp_path),))


@pytest.fixture
def hub(tmp_path):
    """Ein eigener Hub mit eigenem Runtime- und Zustandsverzeichnis."""
    rt, st = tmp_path / "rt", tmp_path / "state"
    rt.mkdir(), st.mkdir()
    cfg = Config(data=dict(), state_dir=st, runtime_dir=rt)
    h = Hub(cfg=cfg, runtime_dir=rt, log=stiller_logger())
    h.start()
    time.sleep(0.3)
    yield h
    h.stop()


def frage(rt: Path, anfrage: dict) -> dict:
    return T.hub_anfrage(str(rt / TTS_SOCKET), anfrage, timeout_s=10.0)


# --------------------------------------------------------------------------
# Kriterium 6 -- die Regeltabelle aus Design §8.3, jede Regel einzeln
# --------------------------------------------------------------------------

def test_ein_harmloser_satz_geht_durch():
    # Positivkontrolle. Ohne sie sagen alle Ablehnungen unten nichts: ein
    # Validator, der ALLES ablehnt, bestuende jeden Negativtest.
    u = S.pruefe("Der Build ist durch, zwei Warnungen.", kanal="reaktion")
    assert u.ok and u.text == "Der Build ist durch, zwei Warnungen."


@pytest.mark.parametrize("text,grund", [
    ("a" * 141, S.GRUND_ZU_LANG),
    ("zwei\nZeilen", S.GRUND_MEHRZEILIG),
    ("zwei Zeilen", S.GRUND_MEHRZEILIG),      # Line Separator
    ("const x = 1;", S.GRUND_CODE),
    ("```python", S.GRUND_CODE),
    ("import os", S.GRUND_CODE),
    ("schau auf https://evil.example", S.GRUND_URL),
    ("schreib an mail@example.com", S.GRUND_URL),
    ("das liegt in ~/Bilder/urlaub.png", S.GRUND_PFAD),
    ("siehe /etc/passwd", S.GRUND_PFAD),
    ("siehe src/main.py", S.GRUND_PFAD),
    ("api_key = sk-12345", S.GRUND_GEHEIMNIS),
    ("das password lautet hunter2", S.GRUND_GEHEIMNIS),
    ("token: abc", S.GRUND_GEHEIMNIS),
    ("", S.GRUND_LEER),
    ("   ", S.GRUND_LEER),
])
def test_jede_regel_hat_ihren_eigenen_grund(text, grund):
    u = S.pruefe(text, kanal="reaktion")
    assert not u.ok
    # Der Grund muss der RICHTIGE sein, nicht irgendeiner. Ein gemeinsames
    # `ungueltig` waere hier gruen und in T-3.14 unbrauchbar.
    assert u.grund == grund, (text, u.grund)
    assert u.ersatz, "eine Ablehnung ohne Ersatzsatz laesst das Pet stumm"


def test_deutscher_text_mit_schraegstrich_ist_kein_pfad():
    # Negativkontrolle zur Pfadregel: eine zu gierige Heuristik wuerde
    # normalen Text ablehnen, und das faellt in der Praxis niemandem auf,
    # weil eine Ablehnung wie Schweigen aussieht.
    assert S.pruefe("nimm das und/oder jenes", kanal="reaktion").ok
    assert S.pruefe("Kapitel 3/4 ist fertig.", kanal="reaktion").ok


def test_unsichtbare_zeichen_werden_entfernt_nicht_escapt():
    # Der Unterschied zur Bubble-Vorschau, und der ganze Grund fuer ein
    # zweites Modul: `‮` escapt waere vorgelesen "backslash u zwei null".
    u = S.pruefe("ur‮al​ub ist fertig", kanal="reaktion")
    assert u.ok
    assert u.text == "uralub ist fertig"
    assert "\\u" not in u.text and "‮" not in u.text


def test_umlaute_bleiben():
    u = S.pruefe("Der Käse ist grün, die Prüfung läuft.", kanal="reaktion")
    assert u.ok and "ä" in u.text and "ü" in u.text


def test_geheimnis_schlaegt_code():
    # Reihenfolge ist eine Zusage: `token = "..."` verletzt beide Regeln, und
    # der teurere Befund gehoert in die Meldung.
    assert S.pruefe('token = "x"', kanal="reaktion").grund == S.GRUND_GEHEIMNIS


def test_140_zeichen_sind_erlaubt_141_nicht():
    assert S.pruefe("a" * 140, kanal="reaktion").ok
    assert not S.pruefe("a" * 141, kanal="reaktion").ok


# --------------------------------------------------------------------------
# Kriterium 5 -- ungefragt zieht nur aus Vorlagen
# --------------------------------------------------------------------------

def test_ungefragt_lehnt_freien_text_ab_auch_wenn_er_harmlos_ist():
    u = S.pruefe("Hallo, alles gut.", kanal="ungefragt")
    assert not u.ok and u.grund == S.GRUND_FREIER_TEXT


def test_vorlage_mit_trusted_werten_geht():
    u = S.aus_vorlage("build_fertig", {"warnungen": "2"},
                      markierung="trusted")
    assert u.ok and u.text == "Build durch, 2 Warnungen."


def test_vorlage_mit_taintedem_wert_wird_abgelehnt():
    u = S.aus_vorlage("build_fertig", {"warnungen": "2"}, markierung="tainted")
    assert not u.ok and u.grund == S.GRUND_NICHT_TRUSTED


def test_eine_fehlende_markierung_ist_tainted_und_nicht_trusted():
    """Der Befund vom 26.08.: die Vorgabe war `trusted`, damit war die Regel
    fuer `tts_ungefragt` durch WEGLASSEN umgehbar."""
    u = S.aus_vorlage("build_fertig", {"warnungen": "2"})
    assert not u.ok and u.grund == S.GRUND_NICHT_TRUSTED


def test_unbekannte_vorlage_wird_abgelehnt():
    assert S.aus_vorlage("gibt_es_nicht").grund == S.GRUND_UNBEKANNTE_VORLAGE


def test_ein_trusted_wert_darf_trotzdem_kein_pfad_sein():
    # `cwd` ist trusted und kann "/home/x/api_key=1" heissen. Die Vorlage waere
    # sonst der Traeger, durch den ein Pfad doch vorgelesen wird.
    u = S.aus_vorlage("begruessung", {"projekt": "/home/x/geheim/secrets.env"},
                      markierung="trusted")
    assert not u.ok and u.grund == S.GRUND_PFAD


def test_es_gibt_einen_ersatzsatz_und_er_ist_selbst_sprechbar():
    ersatz = S.VORLAGEN[S.ERSATZ_VORLAGE]
    assert S.pruefe(ersatz, kanal="reaktion").ok


# --------------------------------------------------------------------------
# Kriterium 8 -- Abkuehlung, und sie ueberlebt einen Neustart
# --------------------------------------------------------------------------

def test_fristen_sind_20_10_3():
    assert S.ABKUEHLUNG_S == {"ungefragt": 20.0, "reaktion": 10.0,
                              "rueckfrage": 3.0}


def test_abkuehlung_sperrt_und_gibt_wieder_frei(tmp_path):
    uhr = {"t": 1000.0}
    a = Abkuehlung(tmp_path / "ab.json", jetzt=lambda: uhr["t"])
    assert a.darf("reaktion")[0]
    a.vermerke("reaktion")
    darf, rest = a.darf("reaktion")
    assert not darf and 9.0 < rest <= 10.0
    uhr["t"] += 10.1
    assert a.darf("reaktion")[0]


def test_abkuehlung_ueberlebt_den_neustart(tmp_path):
    # Das ist der Mutant "nur im Speicher": ein zweites Objekt auf derselben
    # Datei steht fuer den neu gestarteten Prozess.
    pfad = tmp_path / "ab.json"
    uhr = {"t": 5000.0}
    a = Abkuehlung(pfad, jetzt=lambda: uhr["t"])
    a.vermerke("ungefragt")
    neu = Abkuehlung(pfad, jetzt=lambda: uhr["t"] + 1.0)
    darf, rest = neu.darf("ungefragt")
    assert not darf and rest > 18.0


def test_kanaele_sperren_sich_nicht_gegenseitig(tmp_path):
    a = Abkuehlung(tmp_path / "ab.json")
    a.vermerke("ungefragt")
    assert a.darf("rueckfrage")[0]


def test_uhrsprung_gibt_frei_statt_zu_sperren(tmp_path):
    # Wanduhr heisst Spruenge. Eine Frist, die weiter vorn liegt als die
    # laengste konfigurierte, ist ein Sprung -- und dann wird freigegeben:
    # ein stummes Pet sieht aus wie ein abgestuerztes.
    pfad = tmp_path / "ab.json"
    pfad.write_text(json.dumps({"v": 1, "bis": {"reaktion": 9e9}}))
    a = Abkuehlung(pfad, jetzt=lambda: 1000.0, log=stiller_logger())
    assert a.darf("reaktion")[0]


def test_kaputte_datei_ist_ein_leerer_bestand_kein_absturz(tmp_path):
    pfad = tmp_path / "ab.json"
    pfad.write_text("{kaputt")
    a = Abkuehlung(pfad, log=stiller_logger())
    assert a.darf("reaktion")[0]


def test_abkuehlung_wird_atomar_geschrieben(tmp_path):
    a = Abkuehlung(tmp_path / "ab.json")
    a.vermerke("reaktion")
    uebrig = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert not uebrig, uebrig


# --------------------------------------------------------------------------
# Kriterium 6 -- der Validator sitzt im HUB
# --------------------------------------------------------------------------

def test_hub_gibt_frei_und_liefert_den_gesaeuberten_text(hub):
    a = frage(hub.runtime_dir, {"v": 1, "art": "freigabe", "kanal": "reaktion",
                                "text": "ur‮laub ist fertig"})
    assert a["ok"] and a["text"] == "urlaub ist fertig" and a["marke"]


def test_hub_lehnt_ab_und_nennt_die_regel(hub):
    a = frage(hub.runtime_dir, {"v": 1, "art": "freigabe", "kanal": "reaktion",
                                "text": "api_key = 1"})
    assert not a["ok"] and a["grund"] == "geheimnis" and a["ersatz"]
    assert "marke" not in a, "eine abgelehnte Aeusserung darf keine Marke haben"


def test_hub_spricht_nicht_ungefragt_wenn_die_markierung_FEHLT(hub):
    """SICHERHEIT, Befund 26.08.: `markierung` weglassen war der Umweg um die
    verschaerfte Regel -- die Vorgabe `trusted` schenkte die staerkste Marke.
    Die Naht: die Zeile geht durch den ECHTEN Socket, nicht in den Validator
    hinein."""
    a = frage(hub.runtime_dir, {"v": 1, "art": "freigabe", "kanal": "ungefragt",
                                "anlass": "termin_faellig",
                                "werte": {"titel": "Zahnarzt"}})
    assert not a["ok"] and a["grund"] == "nicht_trusted"

    # Positivkontrolle: mit ausdruecklicher Marke geht dieselbe Zeile durch --
    # sonst pruefte der Test nur, dass ungefragt nie spricht.
    b = frage(hub.runtime_dir, {"v": 1, "art": "freigabe", "kanal": "ungefragt",
                                "anlass": "termin_faellig",
                                "werte": {"titel": "Zahnarzt"},
                                "markierung": "trusted"})
    assert b["ok"] and "Zahnarzt" in b["text"]


def test_hub_prueft_vor_der_abkuehlung(hub):
    # Reihenfolge als Zusage: nach einer gueltigen Aeusserung laeuft die
    # Abkuehlung, und ein Angriffstext muss trotzdem `geheimnis` heissen und
    # nicht `abkuehlung` -- sonst verschwindet ein Injektionsversuch hinter
    # einer Frist.
    assert frage(hub.runtime_dir, {"v": 1, "art": "freigabe",
                                   "kanal": "reaktion", "text": "Alles gut."})["ok"]
    a = frage(hub.runtime_dir, {"v": 1, "art": "freigabe", "kanal": "reaktion",
                                "text": "api_key = 1"})
    assert a["grund"] == "geheimnis"


def test_zweite_gueltige_aeusserung_faellt_in_die_abkuehlung(hub):
    erste = frage(hub.runtime_dir, {"v": 1, "art": "freigabe",
                                    "kanal": "reaktion", "text": "Alles gut."})
    frage(hub.runtime_dir, {"v": 1, "art": "gesprochen", "marke": erste["marke"]})
    zweite = frage(hub.runtime_dir, {"v": 1, "art": "freigabe",
                                     "kanal": "reaktion", "text": "Noch was."})
    assert not zweite["ok"] and zweite["grund"] == "abkuehlung"
    assert zweite["rest_s"] > 0


def test_fremde_marke_bewegt_nichts(hub):
    a = frage(hub.runtime_dir, {"v": 1, "art": "gesprochen", "marke": "0" * 32})
    assert not a["ok"] and a["grund"] == "fremde_marke"


def test_tts_active_geht_an_und_aus(hub):
    frei = frage(hub.runtime_dir, {"v": 1, "art": "freigabe",
                                   "kanal": "reaktion", "text": "Alles gut."})
    assert hub.state.snapshot()["voice"]["tts_active"] is False
    frage(hub.runtime_dir, {"v": 1, "art": "beginnt", "marke": frei["marke"]})
    assert hub.state.snapshot()["voice"]["tts_active"] is True
    frage(hub.runtime_dir, {"v": 1, "art": "gesprochen", "marke": frei["marke"]})
    assert hub.state.snapshot()["voice"]["tts_active"] is False


def test_unlesbares_und_unbekanntes_sind_keine_regelverletzungen(hub):
    # Ein Protokollfehler darf sich nicht als eine der Regeln tarnen -- sonst
    # sieht ein kaputter Client aus wie ein Injektionsversuch.
    assert frage(hub.runtime_dir, {"v": 1, "art": "quatsch"})["grund"] == \
        "unbekannte_art"
    a = frage(hub.runtime_dir, {"v": 1, "art": "freigabe", "kanal": "erfunden",
                                "text": "Alles gut."})
    assert a["grund"] == "unbekannter_kanal"


# --------------------------------------------------------------------------
# Segmentierung und Lizenz
# --------------------------------------------------------------------------

def test_segmentierung_trennt_an_satzzeichen():
    assert T.segmente("Der Build ist durch, zwei Warnungen.") == \
        ["Der Build ist durch,", "zwei Warnungen."]


def test_kurze_fetzen_werden_angehaengt():
    assert T.segmente("Ja, und? Was jetzt.") == ["Ja, und? Was jetzt."]


def test_segmentierung_verliert_kein_zeichen():
    for text in ("Eins, zwei, drei.", "Ohne Satzzeichen", "A; B: C! D? E."):
        assert "".join(T.segmente(text)).replace(" ", "") == \
            text.replace(" ", "")


def test_lizenz_der_mitgelieferten_stimme_ist_cc0():
    assert MODELL.is_dir(), f"Stimme fehlt: {MODELL}"
    assert "CC0" in T.lizenz_pruefen(str(MODELL)).upper()


def test_stimme_ohne_lizenzzeile_wird_abgelehnt(tmp_path):
    (tmp_path / "MODEL_CARD").write_text("# irgendwas\nkeine Lizenz hier\n")
    with pytest.raises(T.StimmFehler):
        T.lizenz_pruefen(str(tmp_path))


def test_unfreie_stimme_wird_abgelehnt(tmp_path):
    # pavoque, der Fall aus Design §8.2. Geprueft wird die Karte, nicht der
    # Name -- ein Namensvergleich waere beim naechsten Modell veraltet.
    (tmp_path / "MODEL_CARD").write_text("* License: CC-BY-NC-SA 4.0\n")
    with pytest.raises(T.StimmFehler) as exc:
        T.lizenz_pruefen(str(tmp_path))
    assert "CC-BY-NC-SA" in str(exc.value)


def test_fehlende_model_card_ist_ein_fehler_kein_durchlassen(tmp_path):
    with pytest.raises(T.StimmFehler):
        T.lizenz_pruefen(str(tmp_path))


# --------------------------------------------------------------------------
# Am echten Prozess: Ausgabe, Unterbrechung, Socket-Aktivierung
# --------------------------------------------------------------------------

@pytest.fixture
def sprecher_umgebung(tmp_path, monkeypatch):
    pytest.importorskip("sherpa_onnx")
    b, prot = tmp_path / "bin", tmp_path / "pw-cat.log"
    b.mkdir()
    pw_cat_stub(b, prot)
    monkeypatch.setenv("PATH", f"{b}:{os.environ.get('PATH','')}")
    return b, prot


def _ohne_abkuehlung(tmp_path) -> Config:
    return Config(data={"tts": {"abkuehlung": {"reaktion": 0.0,
                                               "ungefragt": 0.0,
                                               "rueckfrage": 0.0}}},
                  state_dir=tmp_path / "st2", runtime_dir=tmp_path / "rt2")


@pytest.fixture
def hub_ohne_abkuehlung(tmp_path):
    cfg = _ohne_abkuehlung(tmp_path)
    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    h = Hub(cfg=cfg, runtime_dir=cfg.runtime_dir, log=stiller_logger())
    h.start()
    time.sleep(0.3)
    yield h
    h.stop()


def test_es_kommt_wirklich_audio_an_der_ausgabe_an(sprecher_umgebung,
                                                  hub_ohne_abkuehlung):
    _, prot = sprecher_umgebung
    s = T.Sprecher(hub_socket=str(hub_ohne_abkuehlung.runtime_dir / TTS_SOCKET),
                   modell_dir=str(MODELL), threads=4, log=stiller_logger())
    s.laden()
    a = s.sprich(kanal="reaktion", text="Der Build ist durch, zwei Warnungen.")
    assert a["ok"]
    # Kriterium 1, zur Laufzeit statt am Quelltext: eine `grep`-Pruefung ist
    # an der Schreibweise zu umgehen (T-1.7.v3).
    assert a["engine"] == "sherpa-onnx-vits" and a["provider"] == "cpu"
    assert a["modell"] == "de_DE-thorsten-high" and "CC0" in a["lizenz"]
    zeilen = warte_auf_ausgabe(prot)
    # Gemessen am Stub, nicht an der Selbstauskunft des Sprechers.
    assert any(z.startswith("start ") for z in zeilen)
    bytes_zeilen = [z for z in zeilen if z.startswith("bytes ")]
    assert bytes_zeilen, prot.read_text()
    assert int(bytes_zeilen[0].split()[1]) > 20000, "zu wenig Audio"
    # 22050 Hz mono s16 -- die Argumente muessen zur Samplerate passen, sonst
    # spricht das Pet zu schnell oder zu langsam.
    start = next(z for z in zeilen if z.startswith("start "))
    assert "--rate=22050" in start and "--channels=1" in start
    assert "--format=s16" in start


def test_ein_angriffstext_erreicht_die_ausgabe_nicht_und_der_ersatz_schon(
        sprecher_umgebung, hub_ohne_abkuehlung):
    _, prot = sprecher_umgebung
    s = T.Sprecher(hub_socket=str(hub_ohne_abkuehlung.runtime_dir / TTS_SOCKET),
                   modell_dir=str(MODELL), threads=4, log=stiller_logger())
    s.laden()
    a = s.sprich(kanal="reaktion", text="api_key = sk-123 in ~/x/secrets.env")
    assert not a["ok"] and a["grund"] == "geheimnis"
    warte_auf_ausgabe(prot)   # der Ersatzsatz laeuft im Hintergrund
    # Kriterium 7: das Pet sagt, dass die Antwort am Bildschirm steht.
    assert a["ersatz_gesprochen"] is True
    # Und der Angriffstext selbst ist nirgends durch die Ausgabe gegangen.
    assert "sk-123" not in prot.read_text()


def test_die_antwort_kommt_vor_dem_ende_der_wiedergabe(sprecher_umgebung,
                                                      hub_ohne_abkuehlung):
    """Die Zusage, die aus dem Gegenlesen kam.

    Der Sprecher antwortet, sobald die ersten Samples draussen sind, und spielt
    im Hintergrund weiter. Vorher blockierte er bis zum letzten Ton -- damit
    konnte ein Aufrufer nicht unterbrechen, und weil die Abkuehlung am Ende
    vermerkt wird, war die naechste Aeusserung garantiert eine Absage.
    """
    b, prot = sprecher_umgebung
    pw_cat_stub(b, prot, verzoegerung=3.0)   # der Stub haelt die Wiedergabe auf
    s = T.Sprecher(hub_socket=str(hub_ohne_abkuehlung.runtime_dir / TTS_SOCKET),
                   modell_dir=str(MODELL), threads=4, log=stiller_logger())
    s.laden()
    t0 = time.monotonic()
    a = s.sprich(kanal="reaktion",
                 text="Ein langer Satz, der in mehreren Segmenten entsteht, "
                      "damit die Wiedergabe eine Weile dauert.")
    dauer_s = time.monotonic() - t0
    assert a["ok"] and a["ttfa_ms"] is not None
    assert dauer_s < 2.0, f"Antwort erst nach {dauer_s:.2f}s -- blockiert"
    assert s.zustand()["spricht"] is True


def test_eine_neue_aeusserung_bricht_die_laufende_ab(sprecher_umgebung,
                                                    hub_ohne_abkuehlung):
    b, prot = sprecher_umgebung
    # Dieser Stub haelt die Wiedergabe auf, damit es etwas zu unterbrechen gibt.
    pw_cat_stub(b, prot, verzoegerung=5.0)
    s = T.Sprecher(hub_socket=str(hub_ohne_abkuehlung.runtime_dir / TTS_SOCKET),
                   modell_dir=str(MODELL), threads=4, log=stiller_logger())
    s.laden()
    erste = s.sprich(
        kanal="reaktion",
        text="Ein langer Satz, der in mehreren Segmenten entsteht, "
             "damit es etwas zu unterbrechen gibt.")
    assert erste["ok"]
    assert s.zustand()["spricht"] is True, "es gibt nichts zu unterbrechen"

    t0 = time.monotonic()
    zweite = s.sprich(kanal="reaktion", text="Kurz.")
    dauer_ms = (time.monotonic() - t0) * 1000

    # Die Zusage: die neue Aeusserung wartet nicht auf die alte.
    assert zweite["ok"], zweite
    assert dauer_ms < 1000.0, f"die zweite Aeusserung wartete {dauer_ms:.0f} ms"
    assert s.abgebrochen >= 1, "die alte Wiedergabe wurde nicht abgebrochen"
    # Und sie ist wirklich gelaufen: zwei pw-cat-Aufrufe, kein Schweigen.
    zeilen = warte_auf_ausgabe(prot, bytes_mindestens=1)
    assert len([z for z in zeilen if z.startswith("start ")]) == 2, zeilen


def test_dienst_startet_ueber_den_socket_und_spricht(tmp_path, sprecher_umgebung,
                                                    hub_ohne_abkuehlung):
    """Der Prozess, nicht das Objekt: Socket-Aktivierung ist nur am echten
    Prozess messbar."""
    b, prot = sprecher_umgebung
    sock = tmp_path / "say.sock"
    p = subprocess.Popen(
        [sys.executable, "-m", "daimon.face.tts", "--socket", str(sock),
         "--hub-socket", str(hub_ohne_abkuehlung.runtime_dir / TTS_SOCKET),
         "--modell-dir", str(MODELL), "--threads", "4"],
        cwd=REPO, env={**os.environ, "PATH": os.environ["PATH"]},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(200):
            if sock.exists():
                break
            time.sleep(0.05)
        assert sock.exists(), "kein Socket"
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(30)
        c.connect(str(sock))
        # `markierung` ausdruecklich: seit dem 26.08. ist die fehlende Marke
        # `tainted`, und ohne sie schweigt der Dienst -- so soll es sein.
        c.sendall(json.dumps({"v": 1, "art": "sprich", "kanal": "ungefragt",
                              "anlass": "tests_gruen",
                              "markierung": "trusted"}).encode() + b"\n")
        antwort = json.loads(c.makefile("rb").readline())
        c.close()
        assert antwort["ok"] and antwort["ttfa_ms"] is not None
        zeilen = warte_auf_ausgabe(prot)
        assert int([z for z in zeilen
                    if z.startswith("bytes ")][0].split()[1]) > 10000
    finally:
        p.kill()
        p.wait(timeout=10)


def test_ohne_socket_und_ohne_systemd_startet_er_gar_nicht():
    p = subprocess.run(
        [sys.executable, "-m", "daimon.face.tts", "--modell-dir", str(MODELL)],
        cwd=REPO, capture_output=True, text=True, timeout=120)
    assert p.returncode != 0
    assert "LISTEN_FDS" in (p.stderr + p.stdout)


def test_sd_socket_prueft_die_pid(monkeypatch):
    monkeypatch.setenv("LISTEN_FDS", "1")
    monkeypatch.setenv("LISTEN_PID", str(os.getpid() + 1))
    assert T.sd_socket() is None


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

def ohne_kommentare(text: str) -> str:
    """Die Units erklaeren im Fliesstext, was sie NICHT tun. Eine Textsuche
    ueber das Ganze prueft sonst Erwaehnung statt Direktive."""
    return "\n".join(z for z in text.splitlines()
                     if not z.lstrip().startswith("#"))


def test_der_dienst_wird_nur_ueber_den_socket_gestartet():
    text = ohne_kommentare(UNIT_SERVICE.read_text())
    assert "Requires=daimon-tts.socket" in text
    assert "[Install]" not in text, "ein enable waere ein Modell beim Anmelden"


def test_socket_und_hub_endpunkt_haben_verschiedene_namen():
    # Sonst kollidiert die Sprechschnittstelle mit dem Validator-Endpunkt des
    # Hubs -- und wer den falschen anspricht, umgeht die Pruefung.
    socket_text = ohne_kommentare(UNIT_SOCKET.read_text())
    assert "ListenStream=%t/daimon/tts-say.sock" in socket_text
    assert f"/daimon/{TTS_SOCKET}" not in socket_text


def test_unit_ist_gehaertet():
    text = ohne_kommentare(UNIT_SERVICE.read_text())
    # `ProtectProc=`/`ProcSubset=` sind in `--user`-Units wirkungslos
    # (Befund T-4.14 K1, systemd.exec(5): "only available to system
    # services") -- aus der Basis entfernt, nicht mehr erwartet.
    for direktive in ("NoNewPrivileges=yes", "CapabilityBoundingSet=",
                      "ProtectSystem=strict", "ProtectHome=read-only",
                      "PrivateTmp=yes", "LimitCORE=0", "UMask=0077",
                      "RestrictAddressFamilies=AF_UNIX",
                      "MemoryDenyWriteExecute=yes", "PrivateDevices=yes",
                      "RuntimeDirectory=daimon",
                      "RuntimeDirectoryPreserve=yes"):
        assert direktive in text, direktive


def test_resources_bleibt_ungefiltert_und_das_ist_gemessen():
    # Mit `~@resources` stirbt der Dienst mit status=31/SYS: ein PipeWire-Client
    # setzt Echtzeitprioritaet und ruft mlock. Der Test haelt die Messung fest,
    # damit die naechste "Haertung" nicht denselben Nachmittag kostet.
    text = ohne_kommentare(UNIT_SERVICE.read_text())
    filterzeilen = [z for z in text.splitlines()
                    if z.startswith("SystemCallFilter=~")]
    assert filterzeilen
    assert all("@resources" not in z for z in filterzeilen), filterzeilen


def test_beide_units_legen_das_runtime_verzeichnis_an():
    # Ohne das stirbt die Unit nach jedem Neustart mit 226/NAMESPACE.
    for datei in (UNIT_SERVICE, UNIT_SOCKET):
        text = ohne_kommentare(datei.read_text())
        assert "RuntimeDirectory=daimon" in text, datei.name
        assert "RuntimeDirectoryPreserve=yes" in text, datei.name


def test_die_konfiguration_traegt_die_fristen_und_die_stimme():
    import tomllib
    with (REPO / "config/daimon.toml").open("rb") as fh:
        cfg = tomllib.load(fh)
    assert cfg["tts"]["abkuehlung"] == {"ungefragt": 20.0, "reaktion": 10.0,
                                        "rueckfrage": 3.0}
    assert cfg["persona"]["voice"] == "de_DE-thorsten-high"
    # Vier Threads erfuellen das Kriterium nur mit 13 ms Marge -- gemessen.
    assert cfg["tts"]["threads"] >= 8


# --------------------------------------------------------------------------
# Der Dienst darf nicht ueberleben, wenn er nichts mehr bedienen kann
#
# Befund vom 05.08.: 140 verwaiste `daimon.face.tts`-Prozesse, zusammen 10 GiB
# RSS, aeltester 15 Stunden alt, alle auf `systemd --user` umgehaengt und alle
# auf Sockets in laengst geloeschten Temp-Verzeichnissen horchend. Gemessen
# ueber einen einzigen T-3.9-Lauf: vorher 0, nachher 4.
#
# Der Leerlauf-Exit ist NICHT die Antwort -- der Modulkopf begruendet seine
# Abwesenheit mit dem TTFA-Kriterium, und das gilt weiter. Ein Dienst, der
# nichts zu tun hat, soll warten. Ein Dienst, dessen Gegenstelle nachweislich
# weg ist, soll enden.
# --------------------------------------------------------------------------

def test_der_waechter_beendet_bei_dauerhaft_fehlendem_hub_socket(tmp_path):
    sock = tmp_path / "tts.sock"
    sock.write_bytes(b"")          # existiert erst
    beendet = []
    uhr = iter([0.0, 10.0, 20.0, 30.0, 40.0, 50.0])

    w = T.HubWaechter(str(sock), intervall_s=0.0, geduld=3,
                      ende=lambda: beendet.append(True),
                      uhr=lambda: next(uhr))
    assert w.runde() is True          # Socket da -> weiterleben
    sock.unlink()
    assert w.runde() is True          # 1. Fehlschlag: noch geduldig
    assert w.runde() is True          # 2.
    assert w.runde() is False         # 3. -> Schluss
    assert beendet == [True]


def test_der_waechter_verzeiht_ein_kurzes_verschwinden(tmp_path):
    # Ein Hub-Neustart darf den Dienst nicht umbringen: sonst kostet die
    # naechste Aeusserung den vollen Modell-Ladevorgang, und genau den soll
    # der fehlende Leerlauf-Exit ja vermeiden.
    sock = tmp_path / "tts.sock"
    beendet = []
    w = T.HubWaechter(str(sock), intervall_s=0.0, geduld=3,
                      ende=lambda: beendet.append(True))
    assert w.runde() is True          # weg
    assert w.runde() is True          # weg
    sock.write_bytes(b"")             # wieder da
    assert w.runde() is True
    assert w.runde() is True          # Zaehler zurueckgesetzt
    assert beendet == []


def test_ohne_hub_socket_pfad_waecht_niemand(tmp_path):
    # Kein Pfad, kein Urteil: ein Dienst ohne konfigurierten Hub soll nicht
    # aus Prinzip sterben.
    w = T.HubWaechter("", intervall_s=0.0, geduld=1, ende=lambda: None)
    for _ in range(5):
        assert w.runde() is True


def test_pdeathsig_wird_gesetzt_und_das_rennen_erkannt(monkeypatch):
    gesetzt = []
    monkeypatch.setattr(T, "_prctl_pdeathsig", lambda sig: gesetzt.append(sig))
    # Elternteil lebt: nur setzen.
    monkeypatch.setattr(T.os, "getppid", lambda: 4242)
    assert T.sterbe_mit_elternteil() is True
    assert gesetzt == [T.signal.SIGTERM]
    # Elternteil schon tot (auf init umgehaengt): das Signal kaeme nie mehr,
    # also selbst Schluss machen.
    monkeypatch.setattr(T.os, "getppid", lambda: 1)
    assert T.sterbe_mit_elternteil() is False


def test_der_dienst_verdrahtet_beide_netze(monkeypatch, tmp_path):
    # Nicht die Absicht wird geprueft, sondern der Weg: startet `main()` den
    # Dienstbetrieb, muessen BEIDE Netze gespannt sein. Eines allein reicht
    # nicht -- PDEATHSIG verpasst den SIGKILL-Fall, der Waechter den Fall
    # ohne konfigurierten Hub.
    gerufen = {}
    monkeypatch.setattr(T, "sterbe_mit_elternteil",
                        lambda: gerufen.setdefault("pdeathsig", True))

    class Waechter:
        def __init__(self, pfad, **kw):
            gerufen["waechter"] = pfad

        def starten(self):
            gerufen["gestartet"] = True

    monkeypatch.setattr(T, "HubWaechter", Waechter)
    monkeypatch.setattr(T, "lauf", lambda *a, **kw: 0)
    T.netze_spannen("/pfad/zum/hub.sock")
    assert gerufen == {"pdeathsig": True, "waechter": "/pfad/zum/hub.sock",
                       "gestartet": True}


# --------------------------------------------------------------------------
# Der Waechter gegen die Wiederkehr: keine verwaisten Dienste auf dieser Kiste
# --------------------------------------------------------------------------

def verwaiste_tts() -> list[tuple[int, str]]:
    """`(pid, hub_socket)` aller TTS-Prozesse, deren Hub-Socket es nicht gibt.

    Gezaehlt wird der NACHWEISLICH verwaiste Fall: ein Dienst, dessen
    Socketpfad verschwunden ist, kann nichts mehr bedienen. Ein gerade
    laufender Verifiziererlauf hat sein Temp-Verzeichnis noch und wird deshalb
    nicht mitgezaehlt -- sonst waere dieser Test ein Zufallsgenerator.
    """
    import glob

    gefunden = []
    for eintrag in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(eintrag, "rb") as fh:
                argv = fh.read().split(b"\0")
        except OSError:
            continue
        if b"daimon.face.tts" not in argv:
            continue
        pfad = ""
        for i, a in enumerate(argv):
            if a == b"--hub-socket" and i + 1 < len(argv):
                pfad = argv[i + 1].decode("utf-8", "replace")
        if pfad and not os.path.exists(pfad):
            gefunden.append((int(eintrag.split("/")[2]), pfad))
    return gefunden


def test_kein_tts_dienst_horcht_auf_einen_geloeschten_socket():
    """Am 05.08. waren es 140 Stueck, zusammen 10 GiB, der aelteste 15 h alt.

    Sie kamen aus den Verifiziererlaeufen: der Dienst wird ueber
    `systemd-socket-activate` gestartet, der Prueftand kannte nur die PID des
    Aktivators, und das Enkelkind ueberlebte jedes Aufraeumen. Zwei Netze im
    Dienst selbst (`PR_SET_PDEATHSIG` und der `HubWaechter`) schliessen das --
    dieser Test merkt, wenn eines von beiden wieder ausfaellt.
    """
    waisen = verwaiste_tts()
    assert waisen == [], (
        f"{len(waisen)} verwaiste TTS-Dienste: "
        + "; ".join(f"pid={p} socket={s}" for p, s in waisen[:5]))
