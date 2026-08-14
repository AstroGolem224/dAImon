"""T-7.3 -- der Pausenschalter, an der Wirkung gemessen.

Der teuerste Fehler waere ein `ok`, das nur den Rueckgabewert von
`systemctl` wiedergibt. Deshalb steht hier ein Test, der genau diesen Fall
herstellt: rc=0, Unit weg -- und der Strom laeuft weiter.
"""
from __future__ import annotations

import json

import pytest

from daimon.recorder.pause import (
    EYES_UNIT, HERZSCHLAG_FRIST_S, PAUSE_UNITS, RECORDER_UNIT,
    bildschirmstroeme, fortsetzen, fremde_mikrofonstroeme, herzschlag,
    herzschlag_loeschen, ist_konferenz, schneidet_mit, stoppe)


class Systemctl:
    """Ein `subprocess.run`-Ersatz, der Unit-Zustaende nachhaelt."""

    def __init__(self, aktiv: set[str] | None = None, *, rc: int = 0,
                 stur: set[str] | None = None) -> None:
        self.aktiv = set(aktiv or PAUSE_UNITS)
        self.rc = rc
        self.stur = set(stur or ())       # Units, die sich nicht stoppen lassen
        self.rufe: list[list[str]] = []

    def __call__(self, argv, **_):
        self.rufe.append(list(argv))
        befehl, unit = argv[2], argv[3]
        if befehl == "stop" and unit not in self.stur:
            self.aktiv.discard(unit)
        elif befehl == "start":
            self.aktiv.add(unit)
        text = ("active" if unit in self.aktiv else "inactive") \
            if befehl == "is-active" else ""
        return type("E", (), {"returncode": self.rc, "stdout": text,
                              "stderr": ""})()


class Uhr:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# -- Die Wirkung, nicht der Rueckgabewert ----------------------------------

def test_pause_belegt_dass_nichts_mehr_aufnimmt(tmp_path):
    sc = Systemctl()
    stroeme = iter([1, 0])              # vorher einer, danach keiner
    bericht = stoppe(lauf=sc, video=lambda: next(stroeme),
                     runtime_dir=tmp_path)
    assert bericht["ok"] is True, bericht["meldung"]
    assert bericht["bildschirmstroeme_vorher"] == 1   # Positivkontrolle
    assert bericht["bildschirmstroeme_nachher"] == 0
    assert bericht["beleg"] == "strom_gemessen"
    assert bericht["noch_aktiv"] == []
    # Erst der Schreiber, dann die Quelle.
    gestoppt = [r[3] for r in sc.rufe if r[2] == "stop"]
    assert gestoppt == [RECORDER_UNIT, EYES_UNIT]


def test_ohne_strom_vorher_ist_der_beleg_schwaecher(tmp_path):
    """Am 14.08. live passiert: ok=true, vorher=0 -- und niemand sah, dass
    der Nachweis leer war. Jetzt sagt der Bericht es selbst."""
    bericht = stoppe(lauf=Systemctl(), video=lambda: 0, runtime_dir=tmp_path)
    assert bericht["ok"] is True          # die Units sind weg, das gilt
    assert bericht["beleg"] == "nur_unit_zustand"


def test_bildschirmstrom_wird_am_kwin_knoten_gezaehlt():
    """Der Befund vom 14.08.: der Augendienst hat KEINEN eigenen Knoten --
    gezaehlt wird der, den kwin_wayland fuer die Portal-Sitzung erzeugt."""
    text = json.dumps([
        {"info": {"props": {"media.class": "Stream/Output/Video",
                            "node.name": "kwin_wayland"}}},
        {"info": {"props": {"media.class": "Video/Source",
                            "node.name": "webcam"}}},
    ])
    assert bildschirmstroeme(dump_text=text) == 1
    assert bildschirmstroeme(dump_text=None) is None


def test_rc_null_mit_laufendem_strom_ist_kein_erfolg(tmp_path):
    """Der teuerste Fehlermodus, hier hergestellt."""
    sc = Systemctl()
    bericht = stoppe(lauf=sc, video=lambda: 1, runtime_dir=tmp_path)
    assert bericht["rc"] == 0
    assert bericht["ok"] is False
    assert "laufen weiter" in bericht["meldung"]


def test_nicht_messbar_ist_kein_erfolg(tmp_path):
    bericht = stoppe(lauf=Systemctl(), video=lambda: None,
                     runtime_dir=tmp_path)
    assert bericht["ok"] is False
    assert "nicht messbar" in bericht["meldung"]


def test_unit_die_stehen_bleibt_faellt_auf(tmp_path):
    sc = Systemctl(stur={EYES_UNIT})
    bericht = stoppe(lauf=sc, video=lambda: 0, runtime_dir=tmp_path)
    assert bericht["ok"] is False
    assert bericht["noch_aktiv"] == [EYES_UNIT]


def test_fremde_unit_wird_verweigert():
    with pytest.raises(ValueError, match="Allowlist"):
        stoppe(["daimon-hub.service"], lauf=Systemctl(), video=lambda: 0)


def test_fortsetzen_startet_rueckwaerts():
    sc = Systemctl(aktiv=set())
    bericht = fortsetzen(lauf=sc)
    assert bericht["ok"] is True
    gestartet = [r[3] for r in sc.rufe if r[2] == "start"]
    assert gestartet == [EYES_UNIT, RECORDER_UNIT]


# -- Die Ausloeser der automatischen Pause ---------------------------------

def _dump(*stroeme: dict) -> str:
    return json.dumps([{"info": {"props": p}} for p in stroeme])


def test_eigener_mikrofonstrom_loest_nicht_aus():
    text = _dump({"media.class": "Stream/Input/Audio",
                  "application.name": "daimon-ears"})
    assert fremde_mikrofonstroeme(dump_text=text) == 0


def test_fremder_mikrofonstrom_zaehlt():
    text = _dump({"media.class": "Stream/Input/Audio",
                  "application.name": "daimon-ears"},
                 {"media.class": "Stream/Input/Audio",
                  "application.name": "Zoom"},
                 {"media.class": "Audio/Source",      # das GERAET, kein Strom
                  "application.name": "Mikrofon"})
    assert fremde_mikrofonstroeme(dump_text=text) == 1


def test_nicht_messbar_bleibt_none():
    assert fremde_mikrofonstroeme(dump_text=None) is None
    assert fremde_mikrofonstroeme(dump_text="kein json") is None


def test_konferenz_erkennung():
    assert ist_konferenz("zoom")
    assert ist_konferenz("Zoom Workplace")           # Teiltreffer
    assert ist_konferenz("teams-for-linux")
    assert not ist_konferenz("org.kde.kate")
    assert not ist_konferenz("")


# -- Der Herzschlag --------------------------------------------------------

def test_herzschlag_veraltet_und_erlischt(tmp_path):
    uhr = Uhr()
    assert schneidet_mit(tmp_path, uhr=uhr) is False   # noch nie geschlagen
    herzschlag(tmp_path, uhr=uhr)
    assert schneidet_mit(tmp_path, uhr=uhr) is True
    uhr.t += HERZSCHLAG_FRIST_S + 1.0
    assert schneidet_mit(tmp_path, uhr=uhr) is False, (
        "ein alter Herzschlag zeigt Mitschnitt an, wo keiner ist")


def test_pause_loescht_den_herzschlag(tmp_path):
    herzschlag(tmp_path)
    stoppe(lauf=Systemctl(), video=lambda: 0, runtime_dir=tmp_path)
    assert schneidet_mit(tmp_path) is False
    herzschlag_loeschen(tmp_path)          # zweimal loeschen ist kein Fehler


# -- Die Automatik im Dienst ----------------------------------------------

def _dienst(tmp_path, **kw):
    from daimon.recorder.daemon import Recorder
    from daimon.recorder.store import Archiv
    rt = tmp_path / "run"
    rt.mkdir(exist_ok=True)
    return Recorder(runtime_dir=rt, archiv=Archiv(tmp_path / "a.db"),
                    erlaubte_units=None, **kw)


def test_konferenz_im_fokus_pausiert_allein(tmp_path):
    berichte = []
    d = _dienst(tmp_path, fokus_klasse=lambda: "zoom",
                mikrofone=lambda: 0,
                pausieren=lambda **kw: berichte.append(kw) or {"ok": True})
    grund = d.automatik()
    assert grund.startswith("konferenz:")
    assert len(berichte) == 1
    assert d._halt is True


def test_fremdes_mikrofon_pausiert_allein(tmp_path):
    berichte = []
    d = _dienst(tmp_path, fokus_klasse=lambda: "org.kde.kate",
                mikrofone=lambda: 2,
                pausieren=lambda **kw: berichte.append(kw) or {"ok": True})
    assert d.automatik() == "fremdes_mikrofon:2"
    assert len(berichte) == 1


def test_ohne_ausloeser_wird_nicht_pausiert(tmp_path):
    """Die Positivkontrolle zu beiden Ausloesern."""
    berichte = []
    d = _dienst(tmp_path, fokus_klasse=lambda: "org.kde.kate",
                mikrofone=lambda: 0,
                pausieren=lambda **kw: berichte.append(kw) or {"ok": True})
    assert d.automatik() == ""
    assert berichte == []
    assert d._halt is False


def test_nicht_messbares_mikrofon_pausiert_nicht(tmp_path):
    """`None` heisst "wir wissen es nicht" -- und ein Dienst, der sich bei
    jeder unlesbaren Messung selbst abschaltet, ist unbenutzbar."""
    d = _dienst(tmp_path, fokus_klasse=lambda: "", mikrofone=lambda: None,
                pausieren=lambda **kw: {"ok": True})
    assert d.automatik() == ""


# -- Der Wahrnehmungs-Gatter (T-7.2, korrigiert am 14.08.) ----------------

def test_wahrnehmung_wird_am_strom_gemessen_nicht_am_etikett():
    """Zwei Fehler, beide live gefunden, beide hier festgenagelt.

    `lampe()` kennt drei Werte; `lampe() == "an"` faltet sie auf zwei und
    macht aus einem Werkzeugfehler ein "abgeschaltet". Und `systemctl
    --user` laeuft im Sandkasten des Recorders gar nicht. Gemessen wird
    deshalb der ScreenCast-Strom.
    """
    from daimon.recorder.daemon import _wahrnehmung_an
    from daimon.recorder import pause as p

    alt = p.bildschirmstroeme
    try:
        p.bildschirmstroeme = lambda: 1
        assert _wahrnehmung_an() is True
        p.bildschirmstroeme = lambda: 0
        assert _wahrnehmung_an() is False
        # Nicht messbar heisst NICHT "aus": sonst schaltet ein
        # Werkzeugfehler die ganze Funktion still ab.
        p.bildschirmstroeme = lambda: None
        assert _wahrnehmung_an() is True
    finally:
        p.bildschirmstroeme = alt
