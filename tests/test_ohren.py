"""T-3.15 — der Ohren-Dienst, das fehlende Gelenk.

Vertrag: `UMBRA-Notes/DDs/dAImon/T-3.15-Ohren-Killswitch-Plan.md` §2–§5.

Geprueft wird ohne Mikrofon, ohne PipeWire und ohne einen einzigen der vier
Dienste: Aufnahme, Erkenner und die Socket-Rufe sind injiziert. Was hier
NICHT injiziert ist, ist die Sperre aus T-3.4 -- die soll echt mitlaufen,
sonst prueft K8 eine Attrappe.
"""

import json
import time
from pathlib import Path

import numpy as np
import pytest

from daimon.common.config import VORGABEN
from daimon.ears.daemon import Ohren, marke_fuer
from daimon.hub.sprechtext import KEINE_ANTWORT_VORLAGE, VORLAGEN


class AufnahmeAttrappe:
    """Zaehlt Leben und Tod des Stroms. Genau das ist Kriterium K5: ohne PTT
    darf es KEINEN Strom geben -- nicht einen, dessen Bloecke verworfen
    werden."""

    lebende: int = 0
    erzeugt: int = 0

    def __init__(self, *, senke=None, **_kwargs) -> None:
        self.senke = senke
        self.offen = False
        AufnahmeAttrappe.erzeugt += 1

    def start(self) -> None:
        self.offen = True
        AufnahmeAttrappe.lebende += 1

    def stop(self) -> None:
        if self.offen:
            AufnahmeAttrappe.lebende -= 1
        self.offen = False

    def zustand(self) -> dict:
        return {"offen": self.offen}


class ErkennerAttrappe:
    """Sprache genau dann, wenn der Block nicht still ist. Damit steuert der
    Test die Segmentgrenzen ueber die Daten, nicht ueber einen Schalter."""

    def wahrscheinlichkeit(self, chunk) -> float:
        return 0.9 if int(np.max(np.abs(np.asarray(chunk)))) > 100 else 0.0


class Rufe:
    """Ersatz fuer die vier Sockets. Merkt sich jede Anfrage."""

    def __init__(self) -> None:
        self.anfragen: list[tuple[str, dict]] = []
        # Die Fristen, mit denen gerufen wurde -- je Art die letzte. Ohne sie
        # waere "der Mind bekommt die konfigurierte Frist" nicht messbar.
        self.fristen: dict[str, float | None] = {}

    def __call__(self, pfad: str, anfrage: dict, *,
                 timeout_s: float | None = None) -> dict:
        self.anfragen.append((str(pfad), anfrage))
        self.fristen[str(anfrage.get("art"))] = timeout_s
        if anfrage.get("art") == "transkribiere":
            return {"v": 1, "ok": True, "text": "wie spaet ist es",
                    "latenz_ms": 118.0}
        if anfrage.get("art") == "frage":
            return {"v": 1, "ok": True, "antwort": "Es ist kurz nach drei.",
                    "dauer_ms": 300.0}
        if anfrage.get("art") == "sprich":
            return {"v": 1, "ok": True, "ttfa_ms": 120.0}
        return {"v": 1, "ok": False, "grund": "unbekannte_art"}

    def art(self, art: str) -> list[dict]:
        return [a for _, a in self.anfragen if a.get("art") == art]


@pytest.fixture(autouse=True)
def _zaehler_zuruecksetzen():
    AufnahmeAttrappe.lebende = 0
    AufnahmeAttrappe.erzeugt = 0


def ohren(tmp_path, rufe=None):
    return Ohren(runtime_dir=tmp_path,
                 aufnahme_fabrik=AufnahmeAttrappe,
                 erkenner=ErkennerAttrappe(),
                 ruf=rufe or Rufe())


def block(laut: bool):
    """512 Samples int16, wie sie aus `capture.Aufnahme` kommen."""
    wert = 8000 if laut else 0
    return np.full(512, wert, dtype=np.int16)


def sprechen(o: Ohren, still_danach: int = 20) -> None:
    """Eine Aeusserung: laut, dann lange genug leise fuer den Nachlauf."""
    for _ in range(10):
        o.block(block(True))
    for _ in range(still_danach):
        o.block(block(False))


# -- K5: kein Mikrofon ohne PTT ------------------------------------------

def test_ohne_ptt_gibt_es_keinen_aufnahmestrom(tmp_path):
    o = ohren(tmp_path)
    o.zustand_uebernehmen({"voice": {"listening": False, "tts_active": False}})
    assert AufnahmeAttrappe.erzeugt == 0
    assert AufnahmeAttrappe.lebende == 0


def test_ptt_oeffnet_genau_einen_strom_und_schliesst_ihn_wieder(tmp_path):
    o = ohren(tmp_path)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    assert AufnahmeAttrappe.lebende == 1
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    assert AufnahmeAttrappe.lebende == 1, "derselbe Zustand darf nichts oeffnen"
    o.zustand_uebernehmen({"voice": {"listening": False, "tts_active": False}})
    assert AufnahmeAttrappe.lebende == 0


def test_stop_schliesst_den_strom(tmp_path):
    o = ohren(tmp_path)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    o.stop()
    assert AufnahmeAttrappe.lebende == 0


# -- Die Kette ------------------------------------------------------------

def test_eine_aeusserung_laeuft_durch_alle_vier_stufen(tmp_path):
    rufe = Rufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    assert len(rufe.art("transkribiere")) == 1
    assert len(rufe.art("frage")) == 1
    assert len(rufe.art("sprich")) == 1
    assert rufe.art("frage")[0]["text"] == "wie spaet ist es"
    assert rufe.art("sprich")[0]["text"] == "Es ist kurz nach drei."
    assert o.runden == 1


# -- Die Frist auf mind.sock und ihr Ablauf (27.08.) ----------------------

def test_der_mind_bekommt_die_konfigurierte_frist_nicht_die_dreissig(tmp_path):
    """Die 30 s der Vorgabe lagen UNTER dem guenstigsten gemessenen Lastfall
    (49,7 s bei zwei Fremdclients). Gemessen wird hier die Frist, mit der
    wirklich gerufen wird -- nicht die Zahl in der Konfiguration."""
    rufe = Rufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    assert rufe.fristen["frage"] == VORGABEN["ears"]["mind_frist_s"]
    assert rufe.fristen["frage"] >= 79.7, "der gemessene Lastfall muss durch"
    # POSITIVKONTROLLE: STT und Sprecher behalten die kurze Vorgabe -- eine
    # global angehobene Frist waere ein toter STT, auf den zwei Minuten
    # gewartet wird.
    assert rufe.fristen["transkribiere"] is None
    assert rufe.fristen["sprich"] is None


def test_die_frist_kommt_aus_der_konfiguration(tmp_path):
    from daimon.common.config import Config

    o = Ohren(Config(data={"ears": {"mind_frist_s": 7.5}}),
              runtime_dir=tmp_path, aufnahme_fabrik=AufnahmeAttrappe,
              erkenner=ErkennerAttrappe(), ruf=Rufe())
    assert o._mind_frist_s == 7.5


def test_der_fristablauf_wird_hoerbar(tmp_path):
    """Der Fall aus dem Betrieb: `mind.sock` antwortet nicht, `ruf_socket`
    gibt `grund: socket` ohne `antwort` zurueck. Bis zum 27.08. war das eine
    Journalzeile und sonst nichts."""
    class ZuSpaetRufe(Rufe):
        def __call__(self, pfad, anfrage, **kw):
            if anfrage.get("art") == "frage":
                self.anfragen.append((str(pfad), anfrage))
                self.fristen["frage"] = kw.get("timeout_s")
                return {"v": 1, "ok": False, "grund": "socket",
                        "meldung": "timed out"}
            return super().__call__(pfad, anfrage, **kw)

    rufe = ZuSpaetRufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    sprich = rufe.art("sprich")
    assert len(sprich) == 1, "der Nutzer darf nicht nichts hoeren"
    assert sprich[0]["anlass"] == KEINE_ANTWORT_VORLAGE
    assert sprich[0]["kanal"] == "ungefragt"


def test_eine_rechtzeitige_antwort_wird_weiterhin_normal_gesprochen(tmp_path):
    """POSITIVKONTROLLE zum Fristablauf: der Regelweg bleibt der Regelweg --
    freier Text auf `reaktion`, keine Vorlage."""
    rufe = Rufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    sprich = rufe.art("sprich")
    assert len(sprich) == 1
    assert sprich[0]["kanal"] == "reaktion"
    assert sprich[0]["text"] == "Es ist kurz nach drei."
    assert "anlass" not in sprich[0]


def test_der_satz_steht_nur_an_einem_ort(tmp_path):
    """Der Ohren-Dienst kennt den Schluessel, nicht den Satz."""
    quelle = (Path(__file__).resolve().parents[1]
              / "daimon" / "ears" / "daemon.py").read_text(encoding="utf-8")
    assert VORLAGEN[KEINE_ANTWORT_VORLAGE] not in quelle


def test_eine_abgelehnte_sprechanfrage_faellt_auf(tmp_path):
    """Auflage 2: die Antwort von `say.sock` wird ausgewertet. Ohne das
    meldete die Runde Erfolg, waehrend die Abkuehlung den Satz verschluckt
    hat -- Teil B waere gebaut und wirkungslos."""
    import io

    from daimon.common.logging import get_logger

    class AbgekuehltRufe(StummeRufe):
        def __call__(self, pfad, anfrage, **kw):
            if anfrage.get("art") == "sprich":
                self.anfragen.append((str(pfad), anfrage))
                return {"v": 1, "ok": False, "grund": "abkuehlung",
                        "rest_s": 7.0}
            return super().__call__(pfad, anfrage, **kw)

    strom = io.StringIO()
    rufe = AbgekuehltRufe()
    o = Ohren(runtime_dir=tmp_path, aufnahme_fabrik=AufnahmeAttrappe,
              erkenner=ErkennerAttrappe(), ruf=rufe,
              log=get_logger("test-ohren", socket_path="/nicht/da",
                             stream=strom))
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    protokoll = strom.getvalue()
    assert "ears_nicht_gesprochen" in protokoll
    assert "abkuehlung" in protokoll


def test_eine_absage_mit_rueckmeldung_wird_gesprochen(tmp_path):
    """T-4.19: der Mind lehnt einen Aktionswunsch ohne Marke ab (ok False,
    marke_verboten) und gibt eine kuratierte Rueckmeldung mit -- die wird
    gesprochen. Sonst sieht die Ablehnung aus wie ein Dienst, der nichts
    verstanden hat."""
    class AbsageRufe(Rufe):
        def __call__(self, pfad, anfrage, **kw):
            if anfrage.get("art") == "frage":
                self.anfragen.append((str(pfad), anfrage))
                return {"v": 1, "ok": False, "grund": "marke_verboten",
                        "antwort": "Fuer eine Aktion brauche ich eine "
                                   "Absichtsmarke — bitte Push-to-Talk "
                                   "druecken.", "marke": "trusted"}
            return super().__call__(pfad, anfrage, **kw)

    rufe = AbsageRufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    assert len(rufe.art("sprich")) == 1
    assert "Absichtsmarke" in rufe.art("sprich")[0]["text"]


class StummeRufe(Rufe):
    """Der Mind sagt ab und gibt keinen Antworttext mit."""

    def __call__(self, pfad, anfrage, **kw):
        if anfrage.get("art") == "frage":
            self.anfragen.append((str(pfad), anfrage))
            self.fristen["frage"] = kw.get("timeout_s")
            return {"v": 1, "ok": False, "grund": "marke_verboten"}
        return super().__call__(pfad, anfrage, **kw)


def test_eine_absage_ohne_rueckmeldung_spricht_die_kuratierte_zeile(tmp_path):
    """Seit dem 27.08.: eine Absage OHNE `antwort` bleibt nicht stumm.

    Kein freier Text -- die Runde nennt nur den Vorlagenschluessel, und der
    Kanal ist `ungefragt`. Ein Stringliteral hier waere eine zweite Fassung
    des kuratierten Satzes.
    """
    rufe = StummeRufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    sprich = rufe.art("sprich")
    assert len(sprich) == 1
    assert sprich[0]["kanal"] == "ungefragt"
    assert sprich[0]["anlass"] == KEINE_ANTWORT_VORLAGE
    assert sprich[0]["markierung"] == "trusted"
    assert "text" not in sprich[0], "die Ohren tragen den Satz nicht selbst"


# -- Echo-Referenz (Vertrag: Echo-Referenz-Plan.md) ------------------------

def _echo_paket(pcm: bytes) -> bytes:
    import base64
    return json.dumps({"v": 1, "art": "echo", "rate": 16000,
                       "pcm": base64.b64encode(pcm).decode("ascii")}).encode()


def test_ein_echo_paket_landet_in_der_sperre(tmp_path):
    o = ohren(tmp_path)
    pcm = np.full(160, 1000, dtype="<i2").tobytes()
    o._echo_verarbeiten(_echo_paket(pcm))
    assert bytes(o.sperre._ref) == pcm
    assert o.echo_pakete == 1


def test_muell_wird_verworfen_nicht_geworfen(tmp_path):
    o = ohren(tmp_path)
    for kaputt in (b"kein json", b"{}",
                   json.dumps({"v": 1, "art": "echo", "rate": 22050,
                               "pcm": "AAAA"}).encode(),
                   json.dumps({"v": 1, "art": "echo", "rate": 16000,
                               "pcm": "!!!"}).encode()):
        o._echo_verarbeiten(kaputt)
    assert o.echo_pakete == 0
    assert bytes(o.sperre._ref) == b""


def test_das_wiedergabeende_leert_die_referenz(tmp_path):
    """Eine Referenz, die die Wiedergabe ueberlebt, koennte spaeter echte
    Sprache als Echo verwerfen."""
    o = ohren(tmp_path)
    o.zustand_uebernehmen({"voice": {"listening": False, "tts_active": True}})
    o._echo_verarbeiten(_echo_paket(b"\x01\x02" * 100))
    assert len(o.sperre._ref) > 0
    o.zustand_uebernehmen({"voice": {"listening": False, "tts_active": False}})
    assert len(o.sperre._ref) == 0


def test_der_echo_socket_nimmt_datagramme_an(tmp_path):
    import socket as S
    o = ohren(tmp_path)
    o.start()
    try:
        pcm = np.full(160, 500, dtype="<i2").tobytes()
        s = S.socket(S.AF_UNIX, S.SOCK_DGRAM)
        s.sendto(_echo_paket(pcm), str(tmp_path / "echo.sock"))
        s.close()
        for _ in range(200):
            if o.echo_pakete:
                break
            time.sleep(0.005)
        assert o.echo_pakete == 1
        assert bytes(o.sperre._ref) == pcm
    finally:
        o.stop()


def test_leeres_transkript_fragt_den_mind_nicht(tmp_path):
    """Ein Segment ohne Worte ist keine Frage. Sonst kostet jedes Rascheln
    ein Kontingent."""
    class Leer(Rufe):
        def __call__(self, pfad, anfrage):
            if anfrage.get("art") == "transkribiere":
                self.anfragen.append((str(pfad), anfrage))
                return {"v": 1, "ok": True, "text": "   ", "latenz_ms": 90.0}
            return super().__call__(pfad, anfrage)

    rufe = Leer()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    assert len(rufe.art("transkribiere")) == 1
    assert rufe.art("frage") == []


def test_die_wav_datei_ueberlebt_die_runde_nicht(tmp_path):
    """Was gesprochen wurde, liegt danach nicht mehr auf der Platte."""
    o = ohren(tmp_path)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    assert list(tmp_path.glob("**/*.wav")) == []


# -- K8: die Rueckkopplungssperre (echt, nicht injiziert) ----------------

def test_waehrend_des_sprechens_wird_verworfen(tmp_path):
    rufe = Rufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": True}})
    sprechen(o)
    assert rufe.art("transkribiere") == []
    assert o.sperre.verworfen_sperre > 0


def test_nach_dem_sprechen_geht_es_wieder(tmp_path):
    """Positivkontrolle zur Sperre: ohne sie bewiese der Test davor nichts."""
    rufe = Rufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": True}})
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    o.sperre._bis = None          # Nachlauf abkuerzen, statt 0,5 s zu warten
    sprechen(o)
    assert len(rufe.art("transkribiere")) == 1


# -- K6: die Marke -------------------------------------------------------

def test_marke_haengt_am_beginn_des_segments():
    assert marke_fuer(listening_bei_beginn=True) == "user_ptt"
    assert marke_fuer(listening_bei_beginn=False) == "user_audio"


def test_die_frage_traegt_user_ptt(tmp_path):
    rufe = Rufe()
    o = ohren(tmp_path, rufe)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    assert rufe.art("frage")[0]["marke"] == "user_ptt"


# -- K9: das Latenzprotokoll ---------------------------------------------

def test_jede_runde_schreibt_eine_latenzzeile(tmp_path):
    o = ohren(tmp_path)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    zeilen = [json.loads(z) for z in
              (tmp_path / "latenz.jsonl").read_text().splitlines() if z.strip()]
    assert len(zeilen) == 1
    for stufe in ("audio_to_stt_ms", "stt_to_mind_ms", "mind_to_tts_ms",
                  "wake_to_audio_ms", "gesamt_ms"):
        assert isinstance(zeilen[0][stufe], float), stufe
    assert zeilen[0]["echt"] is True


def test_synthetische_laeufe_sind_nicht_echt(tmp_path):
    """Ein eingespieltes WAV darf nicht wie ein gesprochener Satz zaehlen --
    sonst waeren die 20 Sprachanfragen aus dem Plan 20 Dateien."""
    o = Ohren(runtime_dir=tmp_path, aufnahme_fabrik=AufnahmeAttrappe,
              erkenner=ErkennerAttrappe(), ruf=Rufe(), echt=False)
    o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
    sprechen(o)
    zeile = json.loads((tmp_path / "latenz.jsonl").read_text().splitlines()[0])
    assert zeile["echt"] is False


# -- §5.3: der Bericht ----------------------------------------------------

def test_bericht_zaehlt_nur_echte_laeufe(tmp_path):
    from daimon.ears.daemon import bericht

    quelle = tmp_path / "latenz.jsonl"
    quelle.write_text("\n".join(json.dumps(z) for z in [
        {"wake_to_audio_ms": 900.0, "audio_to_stt_ms": 100.0, "echt": True},
        {"wake_to_audio_ms": 1100.0, "audio_to_stt_ms": 120.0, "echt": True},
        {"wake_to_audio_ms": 10.0, "audio_to_stt_ms": 5.0, "echt": False},
    ]) + "\n")
    b = bericht(quelle)
    assert b["n"] == 2, "der synthetische Lauf darf nicht mitzaehlen"
    assert b["p95_wake_to_audio_ms"] == 1100.0
    assert b["stufen"]["audio_to_stt"]["p50"] == 100.0
    assert len(b["laeufe"]) == 3, "synthetische Laeufe stehen trotzdem drin"


def test_bericht_ohne_datei_ist_null_und_nicht_leer(tmp_path):
    """Eine Datei mit n=0 ist ehrlicher als keine -- und bleibt rot."""
    from daimon.ears.daemon import bericht

    b = bericht(tmp_path / "gibt-es-nicht.jsonl")
    assert b["n"] == 0
    assert b["p95_wake_to_audio_ms"] is None


# -- Die Push-Verbindung darf nicht von selbst abreissen -----------------

def test_stille_am_push_socket_schliesst_das_mikrofon_nicht(tmp_path):
    """Am 09.08. live gefunden: mit einem Lese-Timeout auf dem Push-Socket
    riss die Verbindung nach jeder Stille ab, und der Wiederaufbau schloss
    und oeffnete das Mikrofon -- alle fuenf Sekunden. Eine Aeusserung, die
    laenger dauert, wird dabei zersaegt.

    Der Hub schickt NUR bei Aenderung. Stille ist hier der Normalfall und
    kein Fehler.
    """
    import socket as s
    import threading

    pfad = tmp_path / "events.sock"
    srv = s.socket(s.AF_UNIX, s.SOCK_STREAM)
    srv.bind(str(pfad))
    srv.listen(1)
    fertig = threading.Event()

    def hub():
        conn, _ = srv.accept()
        with conn:
            conn.sendall(json.dumps(
                {"v": 2, "rev": 1, "voice": {"listening": True,
                                             "tts_active": False}}).encode() + b"\n")
            fertig.wait(2.0)          # danach SCHWEIGEN, wie der echte Hub

    t = threading.Thread(target=hub, daemon=True)
    t.start()

    o = Ohren(runtime_dir=tmp_path, aufnahme_fabrik=AufnahmeAttrappe,
              erkenner=ErkennerAttrappe(), ruf=Rufe(),
              verbinde_timeout_s=0.2)
    o.start()
    try:
        # Deutlich laenger als das Verbindungs-Timeout: ein Lese-Timeout
        # haette hier mehrfach neu verbunden.
        time.sleep(1.2)
        assert AufnahmeAttrappe.erzeugt == 1, (
            f"{AufnahmeAttrappe.erzeugt} Stroeme geoeffnet -- die Verbindung "
            "reisst ab und baut sich neu auf")
        assert AufnahmeAttrappe.lebende == 1
    finally:
        fertig.set()
        o.stop()
        srv.close()


def test_zwei_aeusserungen_nacheinander_ergeben_zwei_runden(tmp_path):
    """Am 09.08. live gefunden: die erste Runde nach dem Start lief, jede
    weitere fiel STILL aus.

    `_puffer_leeren()` rechnete den neuen Startindex, NACHDEM es die Liste
    geleert hatte -- und `_puffer_start + len([])` ist der alte Wert. Der
    Chunk-Zaehler der Hysterese laeuft dagegen weiter, also zeigte der
    Segmentindex ab der zweiten Aeusserung an der Liste vorbei, `stuecke` war
    leer, und `_segment_fertig` kehrte wortlos zurueck.
    """
    rufe = Rufe()
    o = ohren(tmp_path, rufe)
    for _ in range(3):
        o.zustand_uebernehmen({"voice": {"listening": True, "tts_active": False}})
        sprechen(o)
        o.zustand_uebernehmen({"voice": {"listening": False, "tts_active": False}})
    assert o.runden == 3, f"nur {o.runden} von 3 Runden"
    assert len(rufe.art("transkribiere")) == 3
