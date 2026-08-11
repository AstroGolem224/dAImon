"""T-3.14 — der Sprachzustand des Hubs.

Vertrag: `UMBRA-Notes/DDs/dAImon/T-3.14-Sprachzustaende-Plan.md`, §2 (die
Zustandsmaschine), §4 (die gepinnte API) und §5 (die Drahtform).

Die Zusage, an der alles haengt, steht in §1: **der Zustand ist abgeleitet,
nicht setzbar**. Wer ihn setzen kann, kann ihn behaupten, ohne dass das
zugrundeliegende Ereignis stattgefunden hat -- und dann ist die Anzeige eine
Meinung. Deshalb steht der Test darauf hier zuerst.
"""

import io
import json
import socket
import time
from pathlib import Path

import pytest

from daimon.common import ipc
from daimon.common.logging import get_logger
from daimon.hub.daemon import STATE_SOCKET, Hub
from daimon.hub.state import DENK_FRIST_S, PTT_FRIST_S, HubState


def stiller_logger():
    return get_logger("test", socket_path="/nicht/da", stream=io.StringIO())


@pytest.fixture
def hub(tmp_path):
    h = Hub(runtime_dir=tmp_path / "rt", log=stiller_logger())
    h.start()
    time.sleep(0.3)
    yield h
    h.stop()


def sende(rt: Path, produzent: str, typ: str, payload: dict) -> None:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / f"{produzent}.sock"))
    c.sendall(json.dumps({"v": 1, "type": typ, "payload": payload}).encode() + b"\n")
    time.sleep(0.25)
    c.close()


def schnappschuss(rt: Path) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / STATE_SOCKET))
    with c, c.makefile("rb") as fh:
        return json.loads(fh.readline())


# -- §1/§4: der Zustand ist abgeleitet ------------------------------------

def test_state_ist_nicht_setzbar():
    """`set_voice(state=...)` darf nichts bewegen -- die Kernzusage aus §1."""
    s = HubState()
    s.set_voice(state="speaking")
    assert s.voice_state() == "idle"
    assert s.snapshot()["voice"]["state"] == "idle"


def test_schnappschuss_zeigt_immer_den_abgeleiteten_zustand():
    s = HubState()
    s.set_voice(listening=True)
    assert s.snapshot()["voice"]["state"] == "listening"


def test_flags_stehen_neben_dem_zustand():
    """§5.1: wer den abgeleiteten Wert anzweifelt, sieht die Eingangsgroessen."""
    s = HubState()
    voice = s.snapshot()["voice"]
    assert set(voice) == {"state", "listening", "tts_active", "denkt"}


# -- §2: Vorrang ----------------------------------------------------------

@pytest.mark.parametrize(
    "listening,tts_active,denkt,erwartet",
    [
        (False, False, False, "idle"),
        (False, False, True, "processing"),
        (False, True, False, "speaking"),
        (False, True, True, "speaking"),
        (True, False, False, "listening"),
        (True, False, True, "listening"),
        (True, True, False, "listening"),
        (True, True, True, "listening"),
    ],
)
def test_vorrang_aller_acht_kombinationen(listening, tts_active, denkt, erwartet):
    s = HubState()
    s.set_voice(listening=listening, tts_active=tts_active)
    if denkt:
        s.voice_denkt_an()
    assert s.voice_state() == erwartet


def test_zuhoeren_schlaegt_sprechen():
    """Der Einwurf gewinnt: druecken waehrend des Sprechens heisst `listening`."""
    s = HubState()
    s.set_voice(tts_active=True)
    assert s.voice_state() == "speaking"
    s.set_voice(listening=True)
    assert s.voice_state() == "listening"


# -- §2/§4: die beiden Fristen -------------------------------------------

def test_denkfrist_laeuft_ab():
    s = HubState()
    s.voice_denkt_an(jetzt=1000.0)
    assert s.voice_state(jetzt=1000.0 + DENK_FRIST_S - 0.1) == "processing"
    assert s.voice_state(jetzt=1000.0 + DENK_FRIST_S + 0.1) == "idle"


def test_ptt_frist_laeuft_ab():
    """Bleibt die Abschaltmeldung aus, haengt `listening` sonst am Overlay."""
    s = HubState()
    s.set_voice(listening=True, jetzt=1000.0)
    assert s.voice_state(jetzt=1000.0 + PTT_FRIST_S - 0.1) == "listening"
    assert s.voice_state(jetzt=1000.0 + PTT_FRIST_S + 0.1) == "idle"


def test_ptt_frist_liegt_ueber_dem_zeitlimit_des_automaten():
    """150 > 120: im Normalfall meldet die Quelle zuerst (§9 L2)."""
    from daimon.auth.ptt import PTTAutomat

    assert PTT_FRIST_S > PTTAutomat(zeitlimit_s=120.0)._zeitlimit


def test_denken_endet_beim_sprechen():
    s = HubState()
    s.voice_denkt_an()
    s.voice_denkt_aus()
    assert s.voice_state() == "idle"


def test_rev_steigt_beim_denken():
    """Ohne steigendes `rev` bekaeme das Face den Wechsel nie zu sehen."""
    s = HubState()
    vorher = s.snapshot()["rev"]
    s.voice_denkt_an()
    assert s.snapshot()["rev"] > vorher


# -- Befund des blinden T-3.14.v-Pruefstands ------------------------------
#
# §4 sagt ZWEI Dinge zu: die Uhr sei einspeisbar, "damit die Frist ohne
# Warten pruefbar ist", UND `rev` steige auch beim stillen Fristablauf.
# Beides galt nur fuer `voice_state()`: `snapshot()` las immer
# `time.monotonic()`. Wer also -- wie der Vertrag es anbietet -- eine
# synthetische Zeit einspeiste und danach den Schnappschuss las, mischte
# zwei Zeitbasen, und alles sah abgelaufen aus.

def test_schnappschuss_nimmt_dieselbe_eingespeiste_uhr_wie_voice_state():
    s = HubState()
    s.set_voice(tts_active=True, jetzt=100.0)
    assert s.voice_state(jetzt=101.0) == "speaking"
    assert s.snapshot(voice_jetzt=101.0)["voice"]["state"] == "speaking"


def test_rev_steigt_beim_stillen_fristablauf_mit_eingespeister_uhr():
    """Die Zusage aus §4 -- ohne einspeisbare Uhr nur durch 30 s Warten
    pruefbar, und was nur durch Warten pruefbar ist, wird nie geprueft."""
    s = HubState()
    s.voice_denkt_an(jetzt=1000.0)
    vorher = s.snapshot(voice_jetzt=1000.1)["rev"]
    assert s.snapshot(voice_jetzt=1000.1)["voice"]["state"] == "processing"
    spaeter = s.snapshot(voice_jetzt=1000.0 + DENK_FRIST_S + 0.1)
    assert spaeter["voice"]["state"] == "idle"
    assert spaeter["rev"] > vorher


def test_ohne_argument_bleibt_der_schnappschuss_bei_der_echten_uhr():
    """Die Einspeisung ist eine Pruefhilfe, kein zweiter Betriebsweg."""
    s = HubState()
    s.set_voice(tts_active=True)
    assert s.snapshot()["voice"]["state"] == "speaking"


def test_rev_und_zustand_verlassen_den_schnappschuss_gemeinsam():
    """Der neue Zustand darf nie mit dem alten `rev` hinausgehen.

    Ein Dict wertet seine Werte in Reihenfolge aus; stand `rev` vor `voice`,
    meldete ein stiller Fristablauf den Rueckfall auf `idle` zusammen mit dem
    unveraenderten `rev`. Ein Poller, der `rev` vergleicht, sieht dann
    "nichts passiert" -- und zeigt weiter "denkt nach".
    """
    s = HubState()
    s.voice_denkt_an(jetzt=1000.0)
    vor_ablauf = s.snapshot(voice_jetzt=1000.1)
    nach_ablauf = s.snapshot(voice_jetzt=1000.0 + DENK_FRIST_S + 0.1)
    assert vor_ablauf["voice"]["state"] == "processing"
    assert nach_ablauf["voice"]["state"] == "idle"
    # Der Zustandswechsel und sein `rev` stehen in DERSELBEN Antwort.
    assert nach_ablauf["rev"] == vor_ablauf["rev"] + 1


# -- §5.2/§3: das PTT-Ereignis am echten Socket ---------------------------

def test_ptt_typ_gehoert_dem_auth_produzenten():
    assert "ptt" in ipc.PRODUZENTEN["auth"]


@pytest.mark.parametrize("produzent", ["face", "ears", "hookbridge", "eyes"])
def test_kein_anderer_produzent_darf_ptt(produzent):
    """§3: der Zuwachs ist genau EIN Typ bei genau EINEM Produzenten."""
    assert "ptt" not in ipc.PRODUZENTEN[produzent]
    with pytest.raises(ipc.MessageTypeError):
        ipc.pruefe_typ(produzent, "ptt")


def test_ptt_an_setzt_listening_im_schnappschuss(hub, tmp_path):
    rt = tmp_path / "rt"
    sende(rt, "auth", "ptt", {"an": True})
    assert schnappschuss(rt)["voice"]["state"] == "listening"


def test_ptt_aus_faellt_auf_idle_zurueck(hub, tmp_path):
    rt = tmp_path / "rt"
    sende(rt, "auth", "ptt", {"an": True})
    sende(rt, "auth", "ptt", {"an": False})
    assert schnappschuss(rt)["voice"]["state"] == "idle"


def test_ptt_ohne_wahrheitswert_wird_verworfen(hub, tmp_path):
    """Unsinn ist kein Rollenbruch: Zeile weg, Hub lebt, Zustand unveraendert."""
    rt = tmp_path / "rt"
    sende(rt, "auth", "ptt", {"an": "ja"})
    assert schnappschuss(rt)["voice"]["state"] == "idle"
    sende(rt, "auth", "ptt", {"an": True})
    assert schnappschuss(rt)["voice"]["state"] == "listening"


def test_utterance_setzt_processing(hub, tmp_path):
    """§3: `processing` haengt am bestehenden `utterance`, kein neuer Typ."""
    rt = tmp_path / "rt"
    sende(rt, "ears", "utterance", {"text": "wie spaet ist es"})
    assert schnappschuss(rt)["voice"]["state"] == "processing"


# -- Die dritte Frist: ein gestorbener Sprecher darf nicht ewig sprechen --

def test_sprechfrist_laeuft_ab():
    """Am 09.08. live gefunden: der TTS-Dienst war inaktiv, der Hub stand
    seit Minuten auf `tts_active: true` -- ein Sprecher hatte `beginnt`
    gemeldet und `gesprochen` nie. Folge: die Rueckkopplungssperre verwarf
    JEDEN Mikrofonblock, und die Ohren waren dauerhaft taub.

    `listening` und `denkt` hatten ihre Ausfallgrenze von Anfang an,
    `tts_active` nicht. Dasselbe Muster, dieselbe Loesung.
    """
    from daimon.hub.state import SPRECH_FRIST_S

    s = HubState()
    s.set_voice(tts_active=True, jetzt=1000.0)
    assert s.voice_state(jetzt=1000.0 + SPRECH_FRIST_S - 0.1) == "speaking"
    assert s.voice_state(jetzt=1000.0 + SPRECH_FRIST_S + 0.1) == "idle"


def test_sprechfrist_deckt_die_freigabefrist_des_hubs_ab():
    """Kuerzer als die Sprechfreigabe waere falsch: dann galte der Sprecher
    als still, waehrend seine Freigabe noch laeuft."""
    from daimon.hub.daemon import TTS_FRIST_S
    from daimon.hub.state import SPRECH_FRIST_S

    assert SPRECH_FRIST_S >= TTS_FRIST_S


def test_ein_gemeldetes_ende_beendet_sofort():
    """Positivkontrolle: die Frist ist die zweite Reihe, nicht der Weg."""
    s = HubState()
    s.set_voice(tts_active=True, jetzt=1000.0)
    s.set_voice(tts_active=False, jetzt=1001.0)
    assert s.voice_state(jetzt=1001.0) == "idle"
