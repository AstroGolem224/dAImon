"""`docs/DEBT.md` gegen den Quellbaum -- T-6.10, Akzeptanzpunkt 5.

Der Punkt lautet "Alle `ponytail:`-Kommentare in `DEBT.md` gesammelt", und er
war am 17.08. erfuellt: 18 Dateien im Code, 18 im Ledger. **Vier von 21
Zeilenangaben zeigten aber ins Leere.** Der Ledger sagt selbst, er werde
"ERZEUGT, nicht gepflegt" -- erzeugt wurde er einmal, und seither hat sich der
Code darunter bewegt.

Eine Momentaufnahme, die aussieht wie ein Verzeichnis, ist die schlechtere
Haelfte von beidem: wer eine Schuld nachlesen will, landet auf einer
beliebigen Zeile und haelt den Eintrag fuer erledigt.

Diese Datei macht daraus eine Zusage. Sie prueft nicht den WORTLAUT der
Eintraege -- das waere Nachschreiben --, sondern dass jeder auf einen echten
Vermerk zeigt und keiner fehlt.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "DEBT.md"
# Dieselben Wurzeln, die der Ledger in seinem Kopf nennt. NICHT
# `tests/mutants/` und `tests/fixtures/known-good/`: das sind Kopien des
# Quellbaums, und wer sie mitzaehlt, bekommt dieselbe Schuld vielfach --
# der erste Lauf meldete so 295 Eintraege statt 21.
WURZELN = ("daimon", "face/src", "tools", "config")
ENDUNGEN = (".py", ".rs", ".js", ".toml", ".service")


def _vermerke() -> dict[str, list[int]]:
    """Datei -> Zeilennummern mit `ponytail:` im Quellbaum."""
    gefunden: dict[str, list[int]] = {}
    for wurzel in WURZELN:
        for p in sorted((REPO / wurzel).rglob("*")):
            if p.is_dir() or "__pycache__" in str(p) or p.suffix not in ENDUNGEN:
                continue
            try:
                zeilen = p.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):      # pragma: no cover
                continue
            nummern = [i for i, z in enumerate(zeilen, 1) if "ponytail:" in z]
            if nummern:
                gefunden[str(p.relative_to(REPO))] = nummern
    return gefunden


def _eintraege() -> dict[str, list[int]]:
    """Datei -> Zeilennummern, wie der Ledger sie behauptet."""
    behauptet: dict[str, list[int]] = {}
    datei = None
    for z in LEDGER.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^## `([^`]+)`", z):
            datei = m.group(1)
            behauptet.setdefault(datei, [])
        elif (m := re.match(r"^- \*\*Zeile (\d+)\*\*", z)) and datei:
            behauptet[datei].append(int(m.group(1)))
    return behauptet


def test_es_gibt_ueberhaupt_vermerke():
    """Sonst waeren die Pruefungen unten leer und jede Aussage daraus wertlos."""
    assert len(_vermerke()) >= 10


def test_jede_datei_mit_vermerk_steht_im_ledger():
    fehlt = sorted(set(_vermerke()) - set(_eintraege()))
    assert not fehlt, f"nicht im Ledger: {fehlt}"


def test_der_ledger_nennt_keine_datei_ohne_vermerk():
    """Die Gegenrichtung. Ein Eintrag, dessen Vermerk entfernt wurde, ist
    erledigte Schuld -- und die gehoert nicht in ein Verzeichnis offener."""
    zuviel = sorted(set(_eintraege()) - set(_vermerke()))
    assert not zuviel, f"im Ledger, aber kein Vermerk mehr im Code: {zuviel}"


def test_jede_zeilenangabe_zeigt_auf_einen_vermerk():
    """DER BEFUND vom 17.08.: vier von 21 zeigten ins Leere, weil der Code
    sich unter dem Ledger bewegt hat."""
    daneben = []
    for datei, nummern in sorted(_eintraege().items()):
        zeilen = (REPO / datei).read_text(encoding="utf-8").splitlines()
        for nr in nummern:
            ist = zeilen[nr - 1] if 0 < nr <= len(zeilen) else ""
            if "ponytail:" not in ist:
                daneben.append(f"{datei}:{nr} -> {ist.strip()[:50]!r}")
    assert not daneben, "Zeilenangaben zeigen ins Leere:\n  " + "\n  ".join(daneben)


def test_jeder_vermerk_hat_einen_eintrag():
    """Nicht nur die Datei, sondern JEDE Stelle. Drei Dateien tragen zwei
    Vermerke -- ein Abschnitt je Datei genuegt also nicht."""
    fehlend = []
    behauptet = _eintraege()
    for datei, nummern in sorted(_vermerke().items()):
        for nr in nummern:
            if nr not in behauptet.get(datei, []):
                fehlend.append(f"{datei}:{nr}")
    assert not fehlend, f"Vermerk ohne Eintrag: {fehlend}"


def test_die_zahl_im_kopf_stimmt():
    """Sie steht als Behauptung in der ersten Zeilen -- und eine Zahl, die
    niemand nachrechnet, ist die bequemste Art, falsch zu liegen."""
    kopf = LEDGER.read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+) Eintraege in (\d+) Dateien\.\*\*", kopf)
    assert m, "der Ledger nennt seine Zahl nicht"
    vermerke = _vermerke()
    assert int(m.group(1)) == sum(len(v) for v in vermerke.values())
    assert int(m.group(2)) == len(vermerke)


@pytest.mark.xfail(strict=True, reason=(
    "`daimon/face/echo.py:34` nennt keine Obergrenze, ab der die "
    "Vereinfachung zurueckzudrehen ist -- nur, dass ein echter AEC einen "
    "zustandsbehafteten Resampler braeuchte. Ein Vermerk ohne Ausloeser ist "
    "der, der still verrottet (Skill `ponytail-debt`: `no-trigger`)."))
def test_jeder_vermerk_nennt_eine_obergrenze():
    ohne = []
    for datei, nummern in sorted(_vermerke().items()):
        zeilen = (REPO / datei).read_text(encoding="utf-8").splitlines()
        for nr in nummern:
            block = " ".join(zeilen[nr - 1:nr + 4])
            if "Obergrenze" not in block and "sobald" not in block:
                ohne.append(f"{datei}:{nr}")
    assert not ohne, f"Vermerk ohne Obergrenze: {ohne}"
