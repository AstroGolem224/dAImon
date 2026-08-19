"""Fuenf Hub-Endpunkte, fuenf Absenderlisten -- und keine geraten.

BEFUND T-4.5, Abschnitt ZUSAETZLICH (Reviewer-Sitzung 18.08.), gemessen:

    KONTEXT_SOCKET True -- AKTION False, TICKET False, GPU False, TTS False

Ueber `aktion.sock` laufen Aktionsbitte UND Ticketeinloesung. Der eigentliche
Befund jener Karte -- die Broker-Sockets pruefen den Absender ueberhaupt
nicht -- ist seit Commit 590ee02 geschlossen; dies ist die andere Haelfte.

**Warum das nicht im selben Schnitt ging.** Die Broker-Seite hat genau EINEN
legitimen Absender, den Hub. Auf `aktion.sock` schreiben fuenf, auf
`ticket.sock` vier. Eine geratene Liste sperrt im Betrieb den Falschen aus,
und der Fehler faellt erst auf, wenn jemand eine Aktion ausloest. Der erste
Schritt war deshalb Messen: jeder Eintrag in `daemon.py` ist ein im
Quelltext belegter Absender.

Diese Datei haelt die Messung fest. Sie prueft ZWEI Richtungen, und die
zweite ist die wichtigere: dass jeder gemessene Absender auch wirklich
durchkommt. Eine Liste, die zu viel sperrt, ist im Betrieb schlimmer als
keine -- sie schaltet still eine Zusage ab, und die Suite bleibt gruen.
"""
from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

from daimon.common import ipc
from daimon.hub import daemon as D

REPO = Path(__file__).resolve().parents[1]
UNITS = REPO / "config" / "systemd"
# Dienste, die NICHT zu dAImon gehoeren und trotzdem auf einer
# Allowlist stehen. Siehe `test_fremde_dienste_stehen_hier_ausdruecklich`.
FREMDE_DIENSTE = ("mimic-worker.service",)

# Endpunkt -> (Liste im Code, die Module, die dort nachweislich hinschreiben)
ABSENDER = {
    "AKTION": (D.AKTION_UNITS, {
        "daimon/auth/agent.py": "daimon-auth.service",
        "daimon/brokers/dienst.py": None,        # fs, exec, input -- Mantel
        "daimon/brokers/dbus/daemon.py": "daimon-dbus.service",
    }),
    "TICKET": (D.TICKET_UNITS, {
        "daimon/brokers/lokal/broker.py": "daimon-lokal-broker.service",
        "daimon/brokers/egress/broker.py": "daimon-egress.service",
        "daimon/brokers/cli/broker.py": "daimon-cli-broker.service",
        "daimon/mind/daemon.py": "daimon-mind.service",
    }),
    "GPU": (D.GPU_UNITS, {"daimon/gpu/worker.py": None}),      # Template
    "TTS": (D.TTS_UNITS, {"daimon/face/tts.py": "daimon-tts.service"}),
}


# -- Die Listen selbst -----------------------------------------------------

def test_alle_fuenf_endpunkte_haben_eine_liste():
    """DER BEFUND in einer Zeile: vorher hatte genau einer eine."""
    for name in ("KONTEXT", "AKTION", "TICKET", "GPU", "TTS"):
        liste = getattr(D, f"{name}_UNITS")
        assert liste, f"{name}_UNITS ist leer"


@pytest.mark.parametrize("endpunkt", sorted(ABSENDER))
def test_jeder_eintrag_ist_eine_unit_die_es_gibt(endpunkt):
    """Ein Tippfehler in einer Allowlist sperrt einen Dienst aus, und das
    faellt erst im Betrieb auf. Geprueft gegen die Unit-DATEIEN."""
    liste, _ = ABSENDER[endpunkt]
    for eintrag in liste:
        if eintrag in FREMDE_DIENSTE:
            continue
        datei = (eintrag[:-1] + "@.service" if eintrag.endswith("@")
                 else eintrag)
        assert (UNITS / datei).exists(), (
            f"{endpunkt}_UNITS nennt {eintrag!r} -- {datei} gibt es nicht")


def test_fremde_dienste_stehen_hier_ausdruecklich():
    """Ein Eintrag, der zu keiner Unit im Repo gehoert, ist entweder ein
    Tippfehler oder eine Entscheidung. Diese Liste macht den Unterschied
    sichtbar -- wer einen fremden Dienst zulaesst, traegt ihn HIER ein und
    schreibt dazu, warum.

    `mimic-worker.service` ist der einzige, und er ist ein Beispiel dafuer,
    wozu das GPU-Gate da ist: ein fremder Nutzer der Grafikkarte, der vor dem
    Laden um Erlaubnis fragt und alle drei Absagegruende respektiert. Wer
    sich an die Serialisierung haelt, gehoert an sie herangelassen.
    """
    aus_listen = {e for liste, _ in ABSENDER.values() for e in liste}
    unbekannt = {e for e in aus_listen
                 if not e.endswith("@")
                 and not (UNITS / e).exists()}
    assert unbekannt == set(FREMDE_DIENSTE), (
        f"fremde Eintraege ohne Vermerk: {unbekannt - set(FREMDE_DIENSTE)}; "
        f"Vermerke ohne Eintrag: {set(FREMDE_DIENSTE) - unbekannt}")


def test_die_verdrahtung_steht_am_start(endpunkt=None):
    """DER ZULAUF. Eine Liste, die `start()` nicht uebergibt, ist keine --
    genau das Muster aus CLAUDE.md, und in diesem Modul schon einmal
    passiert (die Ankerschleife war nicht verdrahtet).
    """
    quelle = (REPO / "daimon" / "hub" / "daemon.py").read_text(encoding="utf-8")
    start = quelle[quelle.index("def start("):]
    for name in ("KONTEXT", "AKTION", "TICKET", "GPU", "TTS"):
        muster = rf'"erlaubte_units":\s*{name}_UNITS'
        assert re.search(muster, start), (
            f"{name}_UNITS wird in start() nicht uebergeben")


# -- Die Absender, gegengeprueft -------------------------------------------

@pytest.mark.parametrize("endpunkt", sorted(ABSENDER))
def test_jeder_gemessene_absender_steht_auf_der_liste(endpunkt):
    """Die Richtung, die im Betrieb weh tut. Ein fehlender Eintrag sperrt
    einen echten Dienst aus, und die Suite bliebe gruen."""
    liste, module = ABSENDER[endpunkt]
    for modul, unit in module.items():
        assert (REPO / modul).exists(), f"{modul} gibt es nicht mehr"
        if unit is None:
            continue                       # Mantel oder Template, s. unten
        assert ipc.unit_erlaubt(unit, liste), (
            f"{modul} schreibt an {endpunkt}, aber {unit} steht nicht auf "
            f"der Liste {liste}")


def test_der_mantel_deckt_die_drei_broker_ab():
    """`brokers/dienst.py` ist die Schleife von fs, exec und input -- drei
    Units, ein Modul. Ohne diese Zeile faende der Test darueber sie nicht."""
    for unit in ("daimon-fs.service", "daimon-exec.service",
                 "daimon-input.service"):
        assert ipc.unit_erlaubt(unit, D.AKTION_UNITS), unit


def test_der_gpu_worker_kommt_als_INSTANZ_durch():
    """Der Template-Fall, und der einzige seiner Art. Der Instanzname ist das
    Modell und steht erst zur Laufzeit fest."""
    for modell in ("qwen3", "llama3.2", "whisper-base"):
        assert ipc.unit_erlaubt(f"daimon-gpu@{modell}.service", D.GPU_UNITS)


# -- `unit_erlaubt` selbst -------------------------------------------------

def test_ohne_klammeraffe_wird_exakt_verglichen():
    """Kein Beinahe-Treffer. Ein Praefixvergleich fuer alle waere eine zweite
    Tuer: `daimon-fs.service` erlaubte dann `daimon-fs.service.boese`."""
    liste = ("daimon-tts.service",)
    assert ipc.unit_erlaubt("daimon-tts.service", liste)
    for fremd in ("daimon-tts.service.boese", "daimon-tts", "daimon-ttsX",
                  "xdaimon-tts.service", ""):
        assert not ipc.unit_erlaubt(fremd, liste), fremd


def test_der_klammeraffe_erlaubt_nur_echte_instanzen():
    """`daimon-gpu@` ist kein Freibrief fuer alles, was so anfaengt."""
    liste = ("daimon-gpu@",)
    assert ipc.unit_erlaubt("daimon-gpu@qwen3.service", liste)
    for fremd in ("daimon-gpu.service",          # das Template selbst
                  "daimon-gpu@qwen3.socket",     # anderer Typ
                  "daimon-gpu@qwen3",            # ohne Endung
                  "daimon-gpuX@a.service"):
        assert not ipc.unit_erlaubt(fremd, liste), fremd


def test_eine_leere_liste_erlaubt_nichts():
    """Fail-closed. `erlaubte_units=()` ist etwas anderes als `None`, und
    `None` heisst weiterhin "keine Pruefung"."""
    assert not ipc.unit_erlaubt("daimon-hub.service", ())


# -- Bis zur Wirkung: die echte Peer-Kette ---------------------------------

def _eigene_unit(tmp_path: Path) -> str:
    """Unter welcher Unit dieser Test laeuft -- echt gemessen.

    Nicht `ipc.peer_of` ersetzt: eine Attrappe raeumte genau die Vorrichtung
    aus dem Weg, um die es hier geht.
    """
    pfad = tmp_path / "messung.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    srv.listen(1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.connect(str(pfad))
            conn, _ = srv.accept()
            with conn:
                return ipc.peer_of(conn, "messung").unit
    finally:
        srv.close()
        pfad.unlink(missing_ok=True)


def test_die_sperre_greift_an_einer_echten_verbindung(tmp_path):
    """`ipc.accept` mit der echten SO_PEERPIDFD-Kette: einmal abgewiesen,
    einmal durchgelassen. Ohne den zweiten Teil bestuende der erste auch bei
    einer Fassung, die immer sperrt."""
    eigene = _eigene_unit(tmp_path)
    protokoll = []

    def fahren(liste):
        pfad = tmp_path / "probe.sock"
        pfad.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(pfad))
        srv.listen(1)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.connect(str(pfad))
                conn, _ = ipc.accept(srv, "probe", erlaubte_units=liste,
                                     audit=lambda was, peer:
                                     protokoll.append(was))
                conn.close()
        finally:
            srv.close()
            pfad.unlink(missing_ok=True)

    with pytest.raises(ipc.PeerError):
        fahren(("daimon-nichts.service",))
    assert protokoll == ["fremde_unit"]

    fahren((eigene,))
    assert protokoll == ["fremde_unit", "angenommen"]
