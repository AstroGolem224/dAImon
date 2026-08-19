"""Eine Zeile in `redaktion.yaml` haelt den Mitschnitt an -- oder sie tut nichts.

BEFUND T-7.3 K5, gemessen von der Reviewer-Sitzung am 18.08.:

    Die Konferenzliste ist im Betrieb nicht konfigurierbar. `main()` nennt
    `konferenz` nirgends; `config/redaktion.yaml` hat keinen
    `konferenz`-Schluessel, obwohl `pause.py` dorthin verweist.

Der Parameter stand seit jeher in `Recorder.__init__`, und die Tests, die ihn
benutzten, reichten ihn selbst herein. Genau das Muster aus CLAUDE.md: ein
Stueck ist gebaut, dokumentiert und gruen belegt -- und im Betrieb fuellt es
niemand. Ein Nutzer, der Element eintrug, wurde weiter mitgeschnitten.

Diese Datei prueft die NAHT und nicht das Stueck: von der Zeile in der Datei
bis zur ausgeloesten Pause.

    redaktion.yaml -> konferenz_laden -> main() -> Recorder.konferenz
                   -> pause_grund()   -> automatik() pausiert

Kein Glied davon wird ersetzt ausser dem Dienst selbst, der sonst blockiert.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from daimon.common.config import ConfigError, konferenz_laden
from daimon.recorder import daemon as D
from daimon.recorder import pause

ZUSATZ = "element"                  # nicht in der Vorgabe, absichtlich
# Vor jedem monkeypatch festgehalten: waehrend des Laufs zeigt `D.Recorder`
# auf die Attrappe, und die baute sich sonst selbst.
ECHTER_RECORDER = D.Recorder


def _yaml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "redaktion.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_der_zusatz_steht_nicht_schon_in_der_vorgabe():
    """Sonst waere jede Aussage unten auch ohne den Fix wahr."""
    assert not pause.ist_konferenz(ZUSATZ)


# -- Der Lader --------------------------------------------------------------

def test_die_liste_wird_gelesen(tmp_path):
    eintraege, herkunft = konferenz_laden([_yaml(tmp_path, "konferenz:\n  - element\n")])
    assert eintraege == ["element"]
    assert herkunft == tmp_path / "redaktion.yaml"


def test_eine_datei_ohne_den_schluessel_ist_kein_fehler(tmp_path):
    """Die mitgelieferte Datei hatte den Schluessel bis zum 19.08. nicht, und
    eine eigene Kopie von damals hat ihn weiterhin nicht."""
    assert konferenz_laden([_yaml(tmp_path, "denylist:\n  - keepassxc\n")])[0] == []


def test_eine_kaputte_datei_scheitert_laut(tmp_path):
    """Dieselbe Haltung wie bei der Denylist: still auf die Vorgabe
    zurueckfallen hiesse, eine Einstellung wirkt, die nicht wirkt."""
    with pytest.raises(ConfigError):
        konferenz_laden([_yaml(tmp_path, "konferenz: [unbalanced\n")])


# -- DER ZULAUF: fuellt `main()` den Parameter auch ------------------------

class _Attrappe:
    """Nur der Dienst selbst -- sonst blockiert `main()` in `lauf()`."""

    zuletzt: dict = {}

    def __init__(self, **kw):
        _Attrappe.zuletzt = kw
        # Der echte Recorder, mit denselben Argumenten. So wird geprueft, was
        # WIRKT, und nicht nur, was uebergeben wurde.
        self.echt = ECHTER_RECORDER(**kw)

    def start(self): pass
    def stop(self): pass
    def lauf(self): pass


@pytest.fixture
def gefahren(tmp_path, monkeypatch):
    """`main()` einmal durchlaufen lassen, mit einer eigenen Konfiguration."""
    def fahren(yaml_text: str):
        pfad = _yaml(tmp_path, yaml_text)
        monkeypatch.setattr(D, "denylist_pfade", lambda: [pfad])
        monkeypatch.setattr(D, "Recorder", _Attrappe)
        D.main(["--runtime-dir", str(tmp_path / "rt"),
                "--archiv", str(tmp_path / "archiv")])
        return _Attrappe.zuletzt
    (tmp_path / "rt").mkdir()
    return fahren


def test_main_reicht_die_datei_bis_zum_dienst_durch(gefahren):
    """DER BEFUND in einer Zeile: vorher kam hier die Vorgabe an, egal was
    in der Datei stand."""
    kw = gefahren("konferenz:\n  - element\n")
    assert ZUSATZ in kw["konferenz"], (
        "die Zeile aus redaktion.yaml erreicht den Dienst nicht")


def test_die_vorgabe_geht_dabei_nicht_verloren(gefahren):
    """Ergaenzt, nicht ersetzt. Wer Element eintraegt, soll Zoom nicht
    verlieren -- und merkt den Verlust erst, wenn er mitgeschnitten ist."""
    kw = gefahren("konferenz:\n  - element\n")
    for vorgabe in pause.KONFERENZ_VORGABE:
        assert vorgabe in kw["konferenz"], vorgabe


def test_ohne_eintrag_gilt_genau_die_vorgabe(gefahren):
    """DIE POSITIVKONTROLLE. Ohne sie bestuende alles oben auch eine Fassung,
    die jede Fensterklasse fuer eine Konferenz haelt -- und die haette den
    Mitschnitt dauerhaft angehalten, also die Zusage von der anderen Seite
    gebrochen."""
    kw = gefahren("konferenz: []\n")
    assert tuple(kw["konferenz"]) == tuple(pause.KONFERENZ_VORGABE)


# -- Bis zur Wirkung -------------------------------------------------------

def test_der_eingetragene_klient_haelt_den_mitschnitt_an(gefahren, monkeypatch):
    """Das Ende der Naht. Ein Parameter, der ankommt und nichts bewirkt,
    waere derselbe Befund eine Station weiter.
    """
    gefahren("konferenz:\n  - element\n")
    dienst = ECHTER_RECORDER(**_Attrappe.zuletzt)
    berichte = []
    monkeypatch.setattr(dienst, "_fokus_klasse", lambda: "Element")
    monkeypatch.setattr(dienst, "_mikrofone", lambda: 0)
    monkeypatch.setattr(dienst, "_pausieren",
                        lambda **kw: berichte.append(kw) or {"ok": True})
    assert dienst.pause_grund().startswith("konferenz:")
    assert dienst.automatik().startswith("konferenz:")
    assert berichte, "es wurde nichts angehalten"


def test_ein_fremdes_fenster_haelt_nichts_an(gefahren, monkeypatch):
    """Die Gegenrichtung, sonst waere oben auch eine Totalpause gruen."""
    gefahren("konferenz:\n  - element\n")
    dienst = ECHTER_RECORDER(**_Attrappe.zuletzt)
    monkeypatch.setattr(dienst, "_fokus_klasse", lambda: "org.kde.kate")
    monkeypatch.setattr(dienst, "_mikrofone", lambda: 0)
    assert dienst.pause_grund() == ""


# -- Der Waechter ----------------------------------------------------------

def test_die_mitgelieferte_datei_traegt_den_schluessel():
    """Der Kommentar in `pause.py` verweist auf ihn. Fehlte er wieder, waere
    die Zusage wieder nur ein Kommentar -- und nichts wuerde rot."""
    pfad = Path(__file__).resolve().parents[1] / "config" / "redaktion.yaml"
    import yaml
    daten = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    assert "konferenz" in daten, (
        "config/redaktion.yaml nennt den Schluessel nicht mehr -- "
        "pause.py verweist aber weiter dorthin (T-7.3 K5)")
