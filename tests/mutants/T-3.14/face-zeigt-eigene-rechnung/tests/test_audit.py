"""T-4.6 — die Kette macht Manipulation erkennbar, nicht unmoeglich.

Gemessen wird an der Datei, nicht an einer Selbstauskunft: jede Probe
veraendert echte Bytes und laesst dann `pruefe` darauf los.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from daimon.hub import audit as A


def schreibe(a: A.Audit, n: int, *, ab: int = 0, **extra) -> None:
    for i in range(ab, ab + n):
        a.schreiben(ts=1_000_000.0 + i,
                    prompt_shown=f"Lautstaerke auf {i} setzen?",
                    params_hash=f"sha256:{i:064d}", mark_id=f"m{i}",
                    initiator="foreground", turn_id=f"r{i}",
                    tool_use_id=f"t{i}", outcome="ok", **extra)


def test_hundert_saetze_ergeben_eine_stimmige_kette(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 100)
    a.verankern(journal=lambda text: None)
    befund = A.pruefe(tmp_path / "audit", anker={a.kopf})
    assert befund["saetze"] == 100
    assert befund["ok"], befund["fehler"]


def test_eine_geaenderte_zeile_faellt_auf(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 20)
    pfad = a.datei
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    satz = json.loads(zeilen[9])
    satz["outcome"] = "failed"
    zeilen[9] = json.dumps(satz, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    befund = A.pruefe(tmp_path / "audit", anker={a.kopf})
    assert not befund["ok"]
    assert any("prev_hash" in f for f in befund["fehler"])


def test_eine_geloeschte_zeile_faellt_auf(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 20)
    zeilen = a.datei.read_text(encoding="utf-8").splitlines()
    del zeilen[5]
    a.datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    befund = A.pruefe(tmp_path / "audit", anker={a.kopf})
    assert not befund["ok"]
    assert any("seq" in f for f in befund["fehler"])


def test_zwei_getauschte_zeilen_fallen_auf(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 20)
    zeilen = a.datei.read_text(encoding="utf-8").splitlines()
    zeilen[7], zeilen[8] = zeilen[8], zeilen[7]
    a.datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    befund = A.pruefe(tmp_path / "audit", anker={a.kopf})
    assert not befund["ok"]


def test_eine_neu_gerechnete_datei_faellt_an_den_ankern_auf(tmp_path):
    """Der eigentliche Fall: in sich stimmig, und trotzdem falsch.

    Ohne den zweiten Strom waere diese Datei nicht von der echten zu
    unterscheiden -- sie ist gegen sich selbst geprueft fehlerfrei.
    """
    echt = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(echt, 30)
    anker = {echt.kopf}

    # Angreifer: Verzeichnis leeren und eine eigene, stimmige Kette schreiben.
    for datei in (tmp_path / "audit").iterdir():
        datei.unlink()
    # Der Angreifer schneidet einen Satz heraus und rechnet neu -- genau
    # deshalb ist die Kette danach in sich stimmig.
    gefaelscht = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(gefaelscht, 12)
    schreibe(gefaelscht, 17, ab=13)

    ohne_anker = A.pruefe(tmp_path / "audit", anker=set())
    assert ohne_anker["saetze"] == 29
    # In sich stimmig: der einzige Fehler ist der fehlende zweite Strom.
    assert ohne_anker["fehler"] == [
        "keine Journal-Anker gefunden; die Kette ist nur gegen sich selbst "
        "geprueft und damit gegen eine Neuberechnung blind"]

    mit_anker = A.pruefe(tmp_path / "audit", anker=anker)
    assert not mit_anker["ok"]
    assert any("ersetzt" in f for f in mit_anker["fehler"])


def test_rotation_traegt_den_letzten_hash_in_die_neue_datei(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 5)
    kopf_vorher = a.kopf
    alt = a.rotieren(journal=lambda text: None)
    assert alt.is_file()
    erste = json.loads(a.datei.read_text(encoding="utf-8").splitlines()[0])
    assert erste["prev_hash"] == kopf_vorher
    assert erste["rotation_von"] == alt.name
    # Und die Kette laeuft weiter, statt neu zu beginnen.
    schreibe(a, 3)
    assert A.pruefe(tmp_path / "audit", anker={a.kopf})["ok"]


def test_ein_neustart_setzt_die_kette_fort(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 4)
    kopf = a.kopf
    b = A.Audit.oeffnen(tmp_path / "audit")
    assert b.kopf == kopf
    schreibe(b, 2)
    assert A.pruefe(tmp_path / "audit", anker={b.kopf})["ok"]


# --------------------------------------------------------------------------
# Redaktion nach Herkunft
# --------------------------------------------------------------------------

def test_tainted_wird_redigiert_auch_ohne_katalogflag(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    satz = a.schreiben(prompt_shown="egal", params_hash="sha256:0",
                       mark_id="m", initiator="foreground", turn_id="r",
                       tool_use_id="t", outcome="ok",
                       fenstertitel="Passwort: hunter2",
                       tainted=["fenstertitel"])
    assert "hunter2" not in json.dumps(satz)
    assert satz["fenstertitel"].startswith("<redacted:sha256:")
    assert "len=17" in satz["fenstertitel"]


def test_der_vorschautext_steht_nie_im_klartext(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    satz = a.schreiben(prompt_shown="Datei /home/geheim.txt loeschen?",
                       params_hash="sha256:0", mark_id="m",
                       initiator="foreground", turn_id="r", tool_use_id="t",
                       outcome="denied")
    assert "geheim" not in json.dumps(satz)
    assert satz["prompt_shown"].startswith("<redacted:")
    # Der Abdruck ist trotzdem vergleichbar: derselbe Text ergibt denselben.
    assert satz["prompt_shown"] == A.redigieren(
        "Datei /home/geheim.txt loeschen?")


def test_ablehnungen_und_abbrueche_werden_geschrieben(tmp_path):
    """Design 7.6: IMMER protokolliert."""
    a = A.Audit.oeffnen(tmp_path / "audit")
    for ausgang in ("denied", "cancelled", "timeout", "unknown"):
        a.schreiben(prompt_shown="x", params_hash="sha256:0", mark_id="m",
                    initiator="background", turn_id="r", tool_use_id="t",
                    outcome=ausgang)
    assert A.pruefe(tmp_path / "audit", anker={a.kopf})["saetze"] == 4


# --------------------------------------------------------------------------
# Die Zusagen, die ein halber Datensatz brechen wuerde
# --------------------------------------------------------------------------

def test_ein_fehlendes_pflichtfeld_wird_nicht_geschrieben(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    with pytest.raises(A.AuditFehler) as f:
        a.schreiben(prompt_shown="x", params_hash="sha256:0", mark_id="m",
                    initiator="foreground", turn_id="r", outcome="ok")
    assert "tool_use_id" in str(f.value)
    assert not a.datei.exists()


def test_ein_erfundener_ausgang_wird_abgewiesen(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    with pytest.raises(A.AuditFehler):
        a.schreiben(prompt_shown="x", params_hash="sha256:0", mark_id="m",
                    initiator="foreground", turn_id="r", tool_use_id="t",
                    outcome="halb")


def test_unknown_ist_ein_erlaubter_ausgang():
    assert "unknown" in A.AUSGAENGE


def test_die_rechte_sind_eng(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 1)
    assert stat.S_IMODE(os.stat(a.verzeichnis).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(a.datei).st_mode) == 0o600


def test_eine_kaputte_letzte_zeile_haelt_das_anhaengen_an(tmp_path):
    """Anhaengen wuerde die Beschaedigung ueberschreiben."""
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 3)
    with open(a.datei, "a", encoding="utf-8") as fh:
        fh.write("{kaputt\n")
    with pytest.raises(A.AuditFehler):
        A.Audit.oeffnen(tmp_path / "audit")


def test_der_anker_traegt_kopf_und_nummer(tmp_path):
    a = A.Audit.oeffnen(tmp_path / "audit")
    schreibe(a, 2)
    gesehen = []
    text = a.verankern(journal=gesehen.append)
    assert gesehen == [text]
    assert text.startswith(A.ANKER_PRAEFIX)
    assert f"head={a.kopf}" in text
    assert "seq=2" in text
