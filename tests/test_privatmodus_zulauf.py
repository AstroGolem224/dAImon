"""Der Privatmodus lässt sich einschalten -- vom Menü bis ins Tonurteil.

BEFUND (Karte 62b90c95, am 18.08. von T-7.2 abgespalten): der Tonpfad hat
genau EINE Sperre, und das ist dieser Modus. `redaktion.urteil_ton` prüft ihn
und sonst nichts, mit Begründung -- die Anwendungs-Denylist sperrt FENSTER,
und ein gesprochener Satz hat keines.

Nur konnte ihn niemand einschalten. `privat_setzen()` hatte im Produktivcode
keinen einzigen Aufrufer; festgehalten hat das der Wächter in
`tests/test_gate_zulauf.py`, seit dem 17.08., ohne dass jemand den Zulauf
gebaut hätte. Wer ein Passwort diktierte, hatte es im Archiv -- und wer
während eines offenen Passwortmanagers sprach, ebenso: der Bildteil war
gesperrt, der Tonteil nicht.

Das ist wieder die Gestalt aus CLAUDE.md, diesmal an der Stelle, an der sie
am teuersten ist: das Stück war gebaut, dokumentiert, getestet -- und im
Betrieb rief es niemand auf.

DIE NAHT, die diese Datei prüft:

    Menüeintrag (Rust) -> face.sock `privatmodus` -> Hub._privatmodus
      -> redaktion.privat_setzen -> Datei -> urteil_ton -> KEINE Ablage

Der Rust-Teil steht als Quelltextprüfung da: was er sendet, ist eine feste
Zeile, und ob sie ankommt, misst der Rest der Kette. Ein Prüfstand, der das
Face startet, bräuchte einen Compositor.
"""
from __future__ import annotations

import json
import re
import socket
import time
from pathlib import Path

import pytest

from daimon.common import ipc
from daimon.hub import daemon as D
from daimon.recorder import redaktion as R

REPO = Path(__file__).resolve().parents[1]
FACE_MENU = REPO / "face" / "src" / "menu.rs"
FACE_HUB = REPO / "face" / "src" / "hub.rs"


# -- Der Typ darf überhaupt gesendet werden -------------------------------

def test_das_face_darf_privatmodus_senden():
    """Ohne diesen Eintrag weist `ipc.pruefe_typ` die Zeile ab, und der
    Menüpunkt wäre eine Schaltfläche ohne Wirkung."""
    assert "privatmodus" in ipc.PRODUZENTEN["face"]


def test_kein_anderer_produzent_darf_es():
    """Der Privatmodus gehört dem Menschen am Overlay. Ein Dienst, der ihn
    setzen kann, kann auch den Zeitpunkt wählen -- etwa den, an dem gerade
    nichts Interessantes gesagt wird."""
    for produzent, typen in ipc.PRODUZENTEN.items():
        if produzent != "face":
            assert "privatmodus" not in typen, produzent


def test_es_gibt_kein_gegenstueck_zum_ausschalten():
    """Die Richtung ist die ganze Sicherheit dieser Meldung. Ein
    `privatmodus_aus` gäbe einem kompromittierten Overlay den Weg, den
    Mitschnitt wieder anzuschalten."""
    for typen in ipc.PRODUZENTEN.values():
        assert not [t for t in typen if t.startswith("privatmodus")
                    and t != "privatmodus"]


# -- Der Zulauf: verarbeitet der Hub die Zeile auch -----------------------

class _Log:
    def __init__(self) -> None:
        self.zeilen = []

    def info(self, text, **kw): self.zeilen.append(("info", text, kw))
    def warn(self, text, **kw): self.zeilen.append(("warn", text, kw))
    def error(self, text, **kw): self.zeilen.append(("error", text, kw))


def _hub(tmp_path) -> D.Hub:
    h = D.Hub.__new__(D.Hub)          # ohne __init__: kein Socket, kein Thread
    h.runtime_dir = tmp_path
    h.log = _Log()
    h.diag = type("D", (), {"verworfen": lambda self, was: None})()
    return h


def test_DER_DISPATCH_ruft_privatmodus_auch_auf():
    """Der Zulauf des Zulaufs -- und ohne ihn waeren alle Tests darunter
    wertlos.

    Gemessen beim Bauen dieser Datei: mit gekapptem `elif event.type ==
    "privatmodus"` im Hub blieb JEDER Test hier gruen, weil sie
    `_privatmodus()` selbst rufen. Genau der Fehler, vor dem CLAUDE.md warnt,
    im Pruefstand fuer einen Befund derselben Gestalt.

    Am Baum gemessen und nicht per Textsuche: der Name steht in dieser Datei
    und im Modulkopf des Hubs oft genug, dass ein `grep` immer faendig wird.
    """
    import ast

    baum = ast.parse((REPO / "daimon" / "hub" / "daemon.py")
                     .read_text(encoding="utf-8"))
    gefunden = False
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.If):
            continue
        # Ein Zweig, der auf den Typ vergleicht UND `_privatmodus` ruft.
        vergleicht = any(
            isinstance(k, ast.Constant) and k.value == "privatmodus"
            for k in ast.walk(knoten.test))
        ruft = any(
            isinstance(k, ast.Call)
            and getattr(k.func, "attr", "") == "_privatmodus"
            for stmt in knoten.body for k in ast.walk(stmt))
        gefunden = gefunden or (vergleicht and ruft)
    assert gefunden, (
        "keine Stelle im Hub verzweigt auf `privatmodus` und ruft "
        "`_privatmodus` -- die Meldung des Face liefe ins Leere, und der "
        "Tonpfad haette wieder keine einschaltbare Sperre")


def test_der_hub_setzt_den_modus(tmp_path):
    """DER ZULAUF, und der Kern des Befunds: vorher endete die Kette hier."""
    h = _hub(tmp_path)
    assert R.privat_bis(tmp_path) == 0.0, "vorher läuft nichts"
    h._privatmodus()
    ablauf = R.privat_bis(tmp_path)
    assert ablauf > time.time(), "der Modus läuft nicht"
    assert ablauf <= time.time() + D.PRIVAT_DAUER_S + 1


def test_die_dauer_kommt_aus_dem_hub_nicht_aus_der_nachricht(tmp_path):
    """`_privatmodus` nimmt kein Argument -- und das ist die Zusage.

    Ein Absender, der die Dauer wählt, kann auch `0` wählen und hätte einen
    Privatmodus angefordert, der nichts tut, aber im Journal steht.
    """
    import inspect
    sig = inspect.signature(D.Hub._privatmodus)
    assert list(sig.parameters) == ["self"], sig
    assert D.PRIVAT_DAUER_S >= 60.0, "eine Frist unter einer Minute ist keine"


def test_ein_fehlschlag_ist_laut(tmp_path, monkeypatch):
    """Ein Privatmodus, der nicht greift, ist der gefährlichste Zustand hier:
    der Nutzer glaubt, es sei still, und spricht weiter."""
    h = _hub(tmp_path)
    monkeypatch.setattr(R, "privat_setzen",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("voll")))
    h._privatmodus()
    assert any(art == "error" for art, _, _ in h.log.zeilen), h.log.zeilen
    assert R.privat_bis(tmp_path) == 0.0


# -- Bis zur Wirkung: das Tonurteil ---------------------------------------

def test_der_ton_wird_unter_dem_modus_nicht_abgelegt(tmp_path):
    """DAS ENDE DER NAHT. Ein Modus, der ankommt und nichts bewirkt, wäre
    derselbe Befund eine Station weiter."""
    h = _hub(tmp_path)
    red = R.Redaktion(runtime_dir=tmp_path, kennungen={})

    vorher = red.urteil_ton()
    assert vorher.grund != R.GRUND_PRIVAT, "der Kanarienvogel: vorher legt er ab"

    h._privatmodus()
    nachher = red.urteil_ton()
    assert nachher.grund == R.GRUND_PRIVAT
    assert nachher.stufe == R.STUFE_TRANSIENT, "transient heißt: nicht auf Platte"


def test_auch_das_BILD_pausiert(tmp_path):
    """Der Menüpunkt verspricht "Bild und Ton". Prüfte das niemand, könnte
    die Beschriftung mehr sagen als der Code tut."""
    h = _hub(tmp_path)
    red = R.Redaktion(runtime_dir=tmp_path, kennungen={},
                      wahrnehmung_an=lambda: True)
    assert red.urteil("org.kde.kate").grund != R.GRUND_PRIVAT
    h._privatmodus()
    assert red.urteil("org.kde.kate").grund == R.GRUND_PRIVAT


def test_nach_ablauf_wird_wieder_abgelegt(tmp_path):
    """Die Gegenrichtung, und sie ist die Zusage der Frist: ein Privatmodus,
    den man einschaltet und vergisst, wäre ein abgeschalteter Mitschnitt mit
    einem beruhigenden Namen."""
    R.privat_setzen(tmp_path, 900.0, uhr=lambda: 1000.0)
    spaet = R.Redaktion(runtime_dir=tmp_path, kennungen={},
                        uhr=lambda: 1000.0 + 901.0)
    assert spaet.urteil_ton().grund != R.GRUND_PRIVAT


# -- Der Rust-Teil, am Quelltext ------------------------------------------

def test_das_menue_hat_den_eintrag():
    """Ohne ihn ist die ganze Kette darunter unerreichbar -- genau der
    Zustand, den diese Datei beendet."""
    text = FACE_MENU.read_text(encoding="utf-8")
    assert "Aktion::Privatmodus" in text
    assert '"privatmodus" => Some(Self::Privatmodus)' in text
    assert re.search(r'fest\("Mitschnitt pausieren[^"]*", Some\(Aktion::Privatmodus\)\)',
                     text), "kein Menüeintrag, der die Aktion trägt"


def test_die_gemeldete_zeile_passt_zum_erwarteten_typ():
    """Rust schreibt die Zeile, Python liest sie. Der Typname ist die eine
    Zeichenkette, an der beide hängen -- und ein Tippfehler dort wäre ein
    Menüpunkt, der still nichts tut."""
    text = FACE_HUB.read_text(encoding="utf-8")
    # Die Zeile steht in Rust mit escapten Anfuehrungszeichen im Quelltext.
    # Erst entescapen, dann als JSON lesen -- geprueft wird der INHALT, nicht
    # eine Zeichenkette, die zufaellig richtig aussieht.
    zeilen = [z for z in text.splitlines() if 'privatmodus\\"' in z]
    assert zeilen, "keine gesendete Zeile mit dem Typ `privatmodus` gefunden"
    roh = zeilen[0][zeilen[0].index('{'):zeilen[0].rindex('}') + 1]
    daten = json.loads(roh.replace('\\"', '"'))
    assert daten["type"] == "privatmodus"
    assert daten["payload"] == {}, "die Nutzlast muss leer sein"
    ipc.pruefe_typ("face", daten["type"])          # würde sonst werfen


def test_das_face_schreibt_die_datei_NICHT_selbst():
    """Die Entscheidung, festgenagelt: das Format des Ablaufs steht an genau
    einer Stelle, in `redaktion.privat_setzen`. Schriebe das Face die Datei,
    stünde es zweimal -- einmal in Rust, einmal in Python -- und wer eines
    von beiden anfasst, merkt es nicht."""
    # Nicht ueber den NAMEN gesucht: `PRIVAT_DATEI` heisst selbst
    # "privatmodus" und damit genauso wie der Nachrichtentyp -- eine
    # Namenssuche kann beides nicht unterscheiden und meldete den Menuepunkt
    # selbst als Verstoss. (Erste Fassung tat genau das.)
    #
    # Gesucht wird stattdessen nach dem VORGANG: eine Zeile, die in derselben
    # Zeile schreibt und den Privatmodus nennt.
    schreibend = ("fs::write", "fs::File", "create(", "OpenOptions")
    for datei in (FACE_MENU, FACE_HUB, REPO / "face" / "src" / "main.rs"):
        for nr, zeile in enumerate(
                datei.read_text(encoding="utf-8").splitlines(), 1):
            nackt = zeile.strip()
            if nackt.startswith("//"):
                continue
            if "privatmodus" in nackt.lower() and any(w in nackt
                                                      for w in schreibend):
                raise AssertionError(
                    f"{datei.name}:{nr} schreibt den Privatmodus selbst -- "
                    "das Format des Ablaufs gehoert an EINE Stelle, und das "
                    "ist `redaktion.privat_setzen`")

    # Die Gegenprobe: der Handler geht wirklich ueber den Meldeweg. Ohne sie
    # bestuende die Pruefung oben auch, wenn es den Menuepunkt gar nicht gibt.
    haupt = (REPO / "face" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "hub.privatmodus_melden()" in haupt


# -- Der alte Wächter darf jetzt nicht mehr melden ------------------------

def test_der_waechter_aus_T_5_9b_ist_beantwortet():
    """`tests/test_gate_zulauf.py` hielt seit dem 17.08. fest, dass
    `privat_setzen` keinen Aufrufer hat. Jetzt hat es einen -- und dieser
    Test sagt, wo: falls jemand den Hub-Aufruf entfernt, steht hier, dass
    das die Lücke wieder aufmacht, statt nur einen Wächter grün zu machen.
    """
    quelle = (REPO / "daimon" / "hub" / "daemon.py").read_text(encoding="utf-8")
    assert "privat_setzen(self.runtime_dir, PRIVAT_DAUER_S)" in quelle, (
        "der Hub setzt den Privatmodus nicht mehr -- der Tonpfad hat damit "
        "wieder KEINE einschaltbare Sperre (Karte 62b90c95)")
