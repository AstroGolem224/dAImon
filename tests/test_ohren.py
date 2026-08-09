"""T-3.15 — der Ohren-Dienst, das fehlende Gelenk.

Vertrag: `UMBRA-Notes/DDs/dAImon/T-3.15-Ohren-Killswitch-Plan.md` §2–§5.

Geprueft wird ohne Mikrofon, ohne PipeWire und ohne einen einzigen der vier
Dienste: Aufnahme, Erkenner und die Socket-Rufe sind injiziert. Was hier
NICHT injiziert ist, ist die Sperre aus T-3.4 -- die soll echt mitlaufen,
sonst prueft K8 eine Attrappe.
"""

import json
import time

import numpy as np
import pytest

from daimon.ears.daemon import Ohren, marke_fuer


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

    def __call__(self, pfad: str, anfrage: dict) -> dict:
        self.anfragen.append((str(pfad), anfrage))
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
