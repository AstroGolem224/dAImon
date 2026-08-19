"""Ein Auftrag kommt vom Hub -- oder er kommt nicht an.

BEFUND T-4.5 Akzeptanzpunkt 6, gemessen von der Reviewer-Sitzung am 18.08.
(Ledger-Ausgang produktdefekt-rot):

    `ipc.peer_of` 0x gerufen, Rumpf der Nachricht 1x gelesen.

Ein untergeschobener Absender wurde vollstaendig gelesen, ohne dass jemand
fragte, wer er ist. Das wog schwerer als eine fehlende Zusatzpruefung, weil
der Auftrag BEWUSST keine Signatur traegt (Design 6.2 hat den HMAC
gestrichen): die Herkunft ueber den Socket war die einzige Zusage ihrer Art
an dieser Stelle -- und sie fand nicht statt.

**Was hier NICHT geprueft wird.** Dass die Peer-Pruefung einen
same-uid-Angreifer aufhielte. Sie tut es nicht, sie ist ein Wegweiser
(DESIGN.md fuehrt den Gegenglauben in der Tabelle der Irrtuemer). Geprueft
wird die Zusage, die das Design wirklich macht: "Broker nehmen nur
Verbindungen vom Hub an."

**Warum diese Tests die echte Kette fahren.** Ein Test, der `ipc.peer_of`
ersetzt, prueft die Attrappe und nicht die Vorrichtung -- genau der Fehler,
an dem `test_hub_kontext.py` den Kontextspeicher-Befund nicht finden konnte.
Hier laeuft `SO_PEERPIDFD` echt; verschoben wird nur, WELCHE Unit als
erlaubt gilt. Im Positivfall ist das die Unit des Testprozesses selbst.
"""
from __future__ import annotations

import ast
import socket
import threading
from pathlib import Path

import pytest

from daimon.brokers import dienst
from daimon.common import ipc

REPO = Path(__file__).resolve().parents[1]


class _Log:
    def __init__(self) -> None:
        self.warnungen = []

    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass

    def warn(self, text, **kw):
        self.warnungen.append((text, kw))


def _eigene_unit(tmp_path) -> str:
    """Unter welcher Unit laeuft dieser Test -- echt gemessen, nicht geraten.

    Ueber eine echte Verbindung auf einen echten Socket, damit im
    Positivfall dieselbe Kette laeuft wie im Betrieb.
    """
    pfad = tmp_path / "messung.sock"
    srv = dienst.socket_anlegen(pfad)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.connect(str(pfad))
            conn, _ = srv.accept()
            with conn:
                return ipc.peer_of(conn, "messung").unit
    finally:
        srv.close()


def _einen_auftrag_schicken(pfad: Path, nutzlast: bytes = b'{"v":1}\n'):
    """Gibt die Antwort zurueck, oder None, wenn die Verbindung zu ist."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(5.0)
        c.connect(str(pfad))
        c.sendall(nutzlast)
        try:
            return c.recv(4096) or None
        except OSError:
            return None


def _broker_fahren(tmp_path, monkeypatch, erlaubte_unit: str):
    """Ein echter Mantel-Lauf in einem Thread. `einmal=True`, damit er endet.

    Gibt zurueck: (Socketpfad, Liste der verarbeiteten Nutzlasten, Log).
    """
    monkeypatch.setattr(dienst, "HUB_UNIT", erlaubte_unit)
    pfad = tmp_path / "broker.sock"
    gelesen = []
    log = _Log()

    def verarbeite(rohbytes):
        gelesen.append(rohbytes)
        return {"ok": True}

    t = threading.Thread(
        target=dienst.lauf,
        args=(pfad, verarbeite),
        kwargs={"einmal": True, "log": log},
        daemon=True)
    t.start()
    for _ in range(200):                      # auf READY warten
        if pfad.exists():
            break
        threading.Event().wait(0.01)
    return pfad, gelesen, log, t


# -- Der Befund -----------------------------------------------------------

def test_ein_fremder_absender_wird_nicht_einmal_gelesen(tmp_path, monkeypatch):
    """DER BEFUND: vorher stand hier `srv.accept()`, und die Nutzlast war
    gelesen, bevor irgendjemand nach dem Absender fragte.

    Geprueft wird nicht nur "abgewiesen", sondern "NICHT GELESEN" -- die
    Reihenfolge ist der Punkt. Ein Broker, der erst liest und dann fragt,
    hat die Nutzlast schon im Speicher.
    """
    pfad, gelesen, log, t = _broker_fahren(
        tmp_path, monkeypatch, "irgendein-anderer.service")
    _einen_auftrag_schicken(pfad, b'{"v":1,"geheim":"nutzlast"}\n')
    assert gelesen == [], "der Rumpf wurde gelesen, obwohl der Absender fremd ist"
    assert log.warnungen, "die Abweisung steht nirgends -- sie ist unsichtbar"
    assert log.warnungen[0][1]["DAIMON_GRUND"] == "fremde_unit"


def test_der_hub_kommt_durch(tmp_path, monkeypatch):
    """DIE POSITIVKONTROLLE, und sie ist hier nicht optional: eine Fassung,
    die IMMER abweist, bestuende den Test darueber ebenfalls -- und haette
    jede Aktion des Systems still abgeschaltet.

    Erlaubt ist die Unit des Testprozesses; die Kette darunter ist dieselbe
    wie im Betrieb.
    """
    pfad, gelesen, log, t = _broker_fahren(
        tmp_path, monkeypatch, _eigene_unit(tmp_path))
    antwort = _einen_auftrag_schicken(pfad, b'{"v":1,"art":"test"}\n')
    t.join(timeout=5.0)
    assert gelesen == [b'{"v":1,"art":"test"}'], gelesen
    assert antwort and b'"ok"' in antwort


def test_ein_abgewiesener_absender_toetet_den_broker_nicht(tmp_path,
                                                           monkeypatch):
    """Sonst waere die Sperre ein Denial-of-Service auf den eigenen Dienst:
    einmal fremd verbinden, und die Aktionen des Nutzers stehen.

    Dreimal abgewiesen, und die Schleife dreht weiter. Ein Durchlass laesst
    sich hier nicht anhaengen -- die Allowlist wird gelesen, BEVOR `accept()`
    blockiert, und im Betrieb aendert sie sich ohnehin nie. Dass ein
    erlaubter Absender durchkommt, steht im Test darueber.
    """
    pfad, gelesen, log, t = _broker_fahren(tmp_path, monkeypatch, "fremd.service")
    for _ in range(3):
        _einen_auftrag_schicken(pfad)
    assert gelesen == []
    assert t.is_alive(), "die Schleife ist an einer Abweisung gestorben"
    assert len(log.warnungen) == 3, (
        "nicht jede Abweisung ist sichtbar", log.warnungen)


# -- Der Zulauf: benutzt ihn auch jeder ----------------------------------

BROKER_SCHLEIFEN = (
    "daimon/brokers/dienst.py",          # fs, exec, input
    "daimon/brokers/dbus/daemon.py",     # eigene Schleife, historisch
)


@pytest.mark.parametrize("datei", BROKER_SCHLEIFEN)
def test_keine_schleife_ruft_noch_nacktes_accept(datei):
    """Der Waechter fuer den Tag, an dem jemand eine dritte Schleife baut
    oder eine bestehende zurueckdreht.

    Gemessen am Baum und nicht per Textsuche: ein `grep` haette hier den
    Kommentar getroffen, der den Befund beschreibt -- derselbe Fehler, der am
    18.08. schon einmal einen Waechter wertlos gemacht hat.
    """
    baum = ast.parse((REPO / datei).read_text(encoding="utf-8"))
    nackt = [k.lineno for k in ast.walk(baum)
             if isinstance(k, ast.Call)
             and isinstance(k.func, ast.Attribute)
             and k.func.attr == "accept"
             and isinstance(k.func.value, ast.Name)
             and k.func.value.id == "srv"]
    assert not nackt, (
        f"{datei}:{nackt} nimmt an, ohne nach dem Absender zu fragen "
        "(T-4.5 K6) -- `dienst.annehmen` benutzen")


def test_es_gibt_genau_eine_fassung_der_erlaubten_unit():
    """Zwei Fassungen einer Regel sind eine Regel und eine Attrappe. Der
    dbus-Broker hat eine eigene Schleife; die Allowlist hat er NICHT."""
    treffer = []
    for datei in sorted((REPO / "daimon").rglob("*.py")):
        if "__pycache__" in str(datei):
            continue
        for nr, zeile in enumerate(
                datei.read_text(encoding="utf-8").splitlines(), 1):
            if "daimon-hub.service" in zeile:
                treffer.append(f"{datei.relative_to(REPO)}:{nr}")
    assert treffer == ["daimon/brokers/dienst.py:"
                       + str(_zeile_von_hub_unit())], treffer


def _zeile_von_hub_unit() -> int:
    text = (REPO / "daimon" / "brokers" / "dienst.py").read_text(encoding="utf-8")
    for nr, zeile in enumerate(text.splitlines(), 1):
        if zeile.startswith("HUB_UNIT"):
            return nr
    raise AssertionError("HUB_UNIT steht nicht mehr in dienst.py")
