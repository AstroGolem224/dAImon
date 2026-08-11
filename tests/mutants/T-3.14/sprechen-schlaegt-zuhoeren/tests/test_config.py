"""T-0.5 — Tests fuer die Konfiguration."""

import os
import stat
from pathlib import Path

import pytest

from daimon.common import config as C


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    for name, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state"),
                      ("XDG_RUNTIME_DIR", "run")):
        p = tmp_path / sub
        p.mkdir()
        monkeypatch.setenv(name, str(p))
    return tmp_path


def test_ohne_datei_gelten_die_vorgaben(xdg):
    cfg = C.load()
    assert cfg.quelle is None
    assert cfg.get("face.poll_ms") == 250
    assert cfg.get("persona.name") == "Ember"


def test_datei_ueberschreibt_nur_das_genannte(xdg):
    d = C.config_dir()
    d.mkdir(parents=True)
    (d / "daimon.toml").write_text('[face]\npoll_ms = 100\n')
    cfg = C.load()
    assert cfg.get("face.poll_ms") == 100
    # Die Palette stand nicht in der Datei und darf nicht verschwinden.
    assert cfg.get("face.palette.idle") == "#3a2418"
    assert cfg.get("persona.name") == "Ember"


def test_fehlerhafte_datei_nennt_die_zeile(xdg):
    d = C.config_dir()
    d.mkdir(parents=True)
    (d / "daimon.toml").write_text('[face]\npoll_ms = = 100\n')
    with pytest.raises(C.ConfigError) as ex:
        C.load()
    text = str(ex.value)
    assert "daimon.toml" in text
    assert "line" in text.lower() or "zeile" in text.lower()


def test_state_dir_bekommt_modus_0700(xdg):
    cfg = C.load()
    modus = stat.S_IMODE(cfg.state_dir.stat().st_mode)
    assert modus == 0o700, f"war {modus:o}"


def test_zu_offenes_state_dir_wird_korrigiert(xdg):
    """Ein Verzeichnis aus einem frueheren Lauf darf nicht offen bleiben."""
    st = C.state_dir()
    st.mkdir(parents=True)
    st.chmod(0o755)
    cfg = C.load()
    assert stat.S_IMODE(cfg.state_dir.stat().st_mode) == 0o700


def test_runtime_dir_bekommt_modus_0700(xdg):
    cfg = C.load()
    assert stat.S_IMODE(cfg.runtime_dir.stat().st_mode) == 0o700


def test_ohne_xdg_runtime_dir_klare_meldung(xdg, monkeypatch):
    """Auf /tmp auszuweichen waere eine stille Verschlechterung."""
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    with pytest.raises(C.ConfigError) as ex:
        C.load()
    assert "XDG_RUNTIME_DIR" in str(ex.value)


def test_kein_globaler_zustand(xdg):
    """Zwei Aufrufe liefern unabhaengige Objekte."""
    a = C.load()
    b = C.load()
    assert a is not b
    assert a.data is not b.data


def test_get_mit_punktpfad_und_fallback(xdg):
    cfg = C.load()
    assert cfg.get("gibt.es.nicht") is None
    assert cfg.get("gibt.es.nicht", 7) == 7
    # Ein Blattknoten darf nicht wie ein dict weiterdurchsucht werden.
    assert cfg.get("face.poll_ms.tiefer", "fallback") == "fallback"
