"""Gezielte Tests fuer die mehrtaegige, atomare Phase-1-Aufzeichnung."""

import json
import os

import pytest

from tools.phase1_recorder import (
    Observation,
    ProcessSample,
    atomar_schreiben,
    aufzeichnen,
)


def beobachtung(
    datum="2026-07-31",
    uptime=100.0,
    ticks=100,
    *,
    laufend=True,
    toene=0,
    neustarts=0,
):
    services = {}
    if laufend:
        services["daimon-face.service"] = ProcessSample(123, 50, ticks)
    return Observation(
        datum=datum,
        uptime_s=uptime,
        services=services,
        restarts={"daimon-face.service": neustarts},
        face_pid=123 if laufend else None,
        face_tones=toene if laufend else None,
    )


def test_zwei_laeufe_am_selben_tag_ergeben_einen_tageseintrag(tmp_path):
    pfad = tmp_path / "phase1-usage.json"
    aufzeichnen(pfad, beobachtung())
    daten = aufzeichnen(pfad, beobachtung(uptime=200.0, ticks=120, toene=2))
    assert daten["days"] == 1
    assert len(daten["tage"]) == 1
    assert daten["tage"][0]["laufzeit_stichproben"] == 2
    assert daten["needs_input_events"] == 2


def test_lauf_ohne_laufenden_dienst_zaehlt_keinen_tag(tmp_path):
    daten = aufzeichnen(
        tmp_path / "phase1-usage.json", beobachtung(laufend=False)
    )
    assert daten["days"] == 0
    assert daten["tage"] == []


def test_verdict_wird_nie_ueberschrieben(tmp_path):
    pfad = tmp_path / "phase1-usage.json"
    pfad.write_text(
        json.dumps({"v": 1, "verdict": "mapping aendern und wiederholen"}),
        encoding="utf-8",
    )
    daten = aufzeichnen(pfad, beobachtung())
    assert daten["verdict"] == "mapping aendern und wiederholen"


def test_simulierter_abbruch_laesst_alte_datei_heil(tmp_path, monkeypatch):
    pfad = tmp_path / "phase1-usage.json"
    alt = {"v": 1, "verdict": "weiter", "marker": 17}
    atomar_schreiben(pfad, alt)

    def abbruch(_quelle, _ziel):
        raise RuntimeError("simulierter Abbruch vor replace")

    monkeypatch.setattr(os, "replace", abbruch)
    with pytest.raises(RuntimeError, match="simulierter Abbruch"):
        atomar_schreiben(pfad, {"v": 1, "marker": 99})
    assert json.loads(pfad.read_text(encoding="utf-8")) == alt


def test_p95_entsteht_aus_stichproben_nicht_aus_letztem_wert(tmp_path):
    pfad = tmp_path / "phase1-usage.json"
    # CLK_TCK ist auf Linux 100: ueber je 100 s entsprechen die Tick-Deltas
    # hier exakt den gewuenschten Prozentwerten 1..19, zuletzt 99.
    aufzeichnen(pfad, beobachtung(uptime=0.0, ticks=0))
    ticks = 0
    for nummer, prozent in enumerate([*range(1, 20), 99], start=1):
        ticks += prozent * 100
        daten = aufzeichnen(
            pfad, beobachtung(uptime=nummer * 100.0, ticks=ticks)
        )
    assert daten["idle_cpu_p95"] == 19.0
    assert daten["tage"][0]["idle_cpu_samples"][-1] == 99.0


def test_menschliche_zaehlfelder_bleiben_null_bis_ein_mensch_sie_setzt(tmp_path):
    daten = aufzeichnen(tmp_path / "phase1-usage.json", beobachtung())
    assert daten["fehlalarme"] is None
    assert daten["ablenkungen"] is None
