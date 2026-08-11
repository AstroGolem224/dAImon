"""T-3.15 — der Kill-Switch der Ohren.

Vertrag: `UMBRA-Notes/DDs/dAImon/T-3.15-Ohren-Killswitch-Plan.md` §4.

Die Zusage, um die es geht: **`ok` heisst nicht "systemctl hat 0 geliefert",
sondern "danach nimmt nichts mehr auf"**. Ein Dienst, der auf SIGTERM den
Aufnahmestrom stehen laesst, meldet rc=0 und hoert weiter zu -- und genau
diese Verwechslung ist der Mutant `stream-nur-pausiert`.
"""

import json
import subprocess

import pytest

from daimon.ears import killswitch as ks


class Lauf:
    """Ersatz fuer `subprocess.run`. Merkt sich die Aufrufe."""

    def __init__(self, rc: int = 0, stderr: str = "", aktiv: str = "active") -> None:
        self.rc = rc
        self.stderr = stderr
        self.aktiv = aktiv
        self.aufrufe: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.aufrufe.append(list(argv))
        if "is-active" in argv:
            return subprocess.CompletedProcess(argv, 0 if self.aktiv == "active" else 3,
                                               stdout=self.aktiv + "\n", stderr="")
        return subprocess.CompletedProcess(argv, self.rc, stdout="",
                                           stderr=self.stderr)


def stroeme_folge(*werte):
    """Gibt bei jedem Aufruf den naechsten Wert -- vorher, nachher."""
    rest = list(werte)

    def lesen():
        return rest.pop(0) if rest else werte[-1]

    return lesen


# -- Der Normalfall -------------------------------------------------------

def test_stoppen_meldet_ok_wenn_danach_kein_strom_mehr_laeuft():
    lauf = Lauf()
    e = ks.stoppe(lauf=lauf, stroeme=stroeme_folge(1, 0))
    assert e["ok"] is True
    assert e["war_aktiv"] is True
    assert e["aufnahmestroeme_vorher"] == 1
    assert e["aufnahmestroeme_nachher"] == 0
    assert ["systemctl", "--user", "stop", ks.EARS_UNIT] in lauf.aufrufe


def test_ein_weiterlaufender_strom_ist_ein_fehlschlag_trotz_rc_null():
    """Der Mutant `stream-nur-pausiert` in einem Test: systemctl sagt ja,
    das Mikrofon laeuft weiter. `ok` muss falsch sein."""
    e = ks.stoppe(lauf=Lauf(rc=0), stroeme=stroeme_folge(1, 1))
    assert e["ok"] is False
    assert e["rc"] == 0
    assert "strom" in e["meldung"].lower()


def test_fehlgeschlagenes_systemctl_ist_nicht_ok():
    e = ks.stoppe(lauf=Lauf(rc=5, stderr="Unit not loaded"),
                  stroeme=stroeme_folge(0, 0))
    assert e["ok"] is False
    assert e["rc"] == 5
    assert "Unit not loaded" in e["meldung"]


def test_unbekannte_stromzahl_gilt_nicht_als_erfolg():
    """`None` heisst "nicht gemessen". Wer das als 0 zaehlt, macht aus einem
    fehlenden pw-dump eine Zusage."""
    e = ks.stoppe(lauf=Lauf(), stroeme=stroeme_folge(None, None))
    assert e["ok"] is False
    assert e["aufnahmestroeme_nachher"] is None


def test_ein_bereits_gestoppter_dienst_ist_kein_fehler():
    lauf = Lauf(aktiv="inactive")
    e = ks.stoppe(lauf=lauf, stroeme=stroeme_folge(0, 0))
    assert e["war_aktiv"] is False
    assert e["ok"] is True


def test_die_unit_ist_nicht_frei_waehlbar():
    """Ein Kill-Switch, der jede Unit stoppt, ist ein Abschaltknopf fuer das
    ganze System. Nur die Wahrnehmungs-Units stehen zur Wahl."""
    with pytest.raises(ValueError):
        ks.stoppe("daimon-auth.service", lauf=Lauf(), stroeme=stroeme_folge(0, 0))


# -- pw-dump lesen --------------------------------------------------------

def test_aufnahmestroeme_zaehlt_nur_eingangsstroeme():
    dump = json.dumps([
        {"info": {"props": {"media.class": "Stream/Input/Audio"}}},
        {"info": {"props": {"media.class": "Stream/Output/Audio"}}},
        {"info": {"props": {"media.class": "Audio/Source"}}},
        {"info": {"props": {"media.class": "Stream/Input/Audio"}}},
    ])
    assert ks.aufnahmestroeme(dump_text=dump) == 2


def test_aufnahmestroeme_ohne_pw_dump_ist_none():
    assert ks.aufnahmestroeme(dump_text=None) is None
    assert ks.aufnahmestroeme(dump_text="kein JSON") is None
