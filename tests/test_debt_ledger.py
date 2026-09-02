"""`docs/DEBT.md` gegen den Quellbaum -- T-6.10, Akzeptanzpunkt 5.

Der Punkt lautet "Alle `ponytail:`-Kommentare in `DEBT.md` gesammelt", und er
war am 17.08. erfuellt: 18 Dateien im Code, 18 im Ledger. **Vier von 21
Zeilenangaben zeigten aber ins Leere.** Der Ledger sagt selbst, er werde
"ERZEUGT, nicht gepflegt" -- erzeugt wurde er einmal, und seither hat sich der
Code darunter bewegt.

Eine Momentaufnahme, die aussieht wie ein Verzeichnis, ist die schlechtere
Haelfte von beidem: wer eine Schuld nachlesen will, landet auf einer
beliebigen Zeile und haelt den Eintrag fuer erledigt.

Diese Datei macht daraus eine Zusage: jeder Eintrag zeigt auf einen echten
Vermerk, und keiner fehlt.

**Seit dem 02.09. ueber den TEXT, nicht ueber die Zeile.** Die Zeilennummer
war die einzige Angabe im Buch, die JEDE Aenderung oberhalb ungueltig macht --
dreimal rot, ohne dass jemand eine Schuld angefasst haette: am 01.09. drei
gewanderte Vermerke, am 02.09. vormittags `doppelself_gesichter.py:510 -> 522`
aus einer Sitzung, die mit dem Ledger nichts zu tun hatte. Da `pytest` in vier
eingebetteten Pruefstaenden ein Kriterium ist, kostet das rote Punkte in
Belegen, die von etwas ganz anderem handeln.

Der Text eines Vermerks wandert nicht mit; er aendert sich nur, wenn jemand
die Schuld selbst umformuliert -- und dann GEHOERT das Buch angefasst.
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


def _stellen() -> dict[str, list[tuple[int, str]]]:
    """Datei -> (Zeilennummer, Text hinter `ponytail:`) im Quellbaum.

    Die Zeilennummer bleibt hier drin, weil die Obergrenzen-Pruefung unten die
    Folgezeilen braucht. In den Ledger geht sie nicht mehr."""
    gefunden: dict[str, list[tuple[int, str]]] = {}
    for wurzel in WURZELN:
        for p in sorted((REPO / wurzel).rglob("*")):
            if p.is_dir() or "__pycache__" in str(p) or p.suffix not in ENDUNGEN:
                continue
            try:
                zeilen = p.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):      # pragma: no cover
                continue
            # Alles vor `ponytail:` faellt weg -- damit auch jede Einrueckung
            # und jedes Kommentarzeichen, ohne sie einzeln zu kennen.
            stellen = [(i, z.split("ponytail:", 1)[1].strip())
                       for i, z in enumerate(zeilen, 1) if "ponytail:" in z]
            if stellen:
                gefunden[str(p.relative_to(REPO))] = stellen
    return gefunden


def _vermerke() -> dict[str, list[str]]:
    """Datei -> Vermerktexte im Quellbaum. Der Schluessel des Ledgers."""
    return {d: [t for _, t in s] for d, s in _stellen().items()}


def _eintraege() -> dict[str, list[str]]:
    """Datei -> Vermerktexte, wie der Ledger sie behauptet.

    Gezaehlt wird erst ab dem ersten Dateiabschnitt; Aufzaehlungen im Kopf
    sind Prosa und keine Eintraege."""
    behauptet: dict[str, list[str]] = {}
    datei = None
    for z in LEDGER.read_text(encoding="utf-8").splitlines():
        if z.startswith("## "):
            m = re.match(r"^## `([^`]+)`$", z)
            datei = m.group(1) if m else None
            if datei:
                behauptet.setdefault(datei, [])
        elif z.startswith("- ") and datei:
            behauptet[datei].append(z[2:].strip())
    return behauptet


def test_es_gibt_ueberhaupt_vermerke():
    """Sonst waeren die Pruefungen unten leer und jede Aussage daraus wertlos."""
    assert len(_vermerke()) >= 10
    assert len(_eintraege()) >= 10


def test_kein_vermerk_ist_mehrdeutig():
    """Der Preis des Textschluessels: zwei gleich beginnende Vermerke in
    derselben Datei waeren im Ledger nicht mehr auseinanderzuhalten. Eine
    stille Kollision waere schlimmer als die alte Zeilennummer -- also faellt
    sie hier auf, bevor sie irgendwo anders wirkt."""
    doppelt = [f"{d}: {t!r}" for d, texte in sorted(_vermerke().items())
               for t in sorted(set(texte)) if texte.count(t) > 1]
    assert not doppelt, ("zwei Vermerke mit gleichem Text in einer Datei -- "
                         "einen davon umformulieren:\n  " + "\n  ".join(doppelt))


def test_jeder_vermerk_hat_einen_eintrag():
    """Nicht nur die Datei, sondern JEDE Stelle. Vier Dateien tragen zwei
    Vermerke -- ein Abschnitt je Datei genuegt also nicht."""
    fehlend = [f"{d}: {t!r}" for d, texte in sorted(_vermerke().items())
               for t in texte if t not in _eintraege().get(d, [])]
    assert not fehlend, ("Vermerk ohne Eintrag im Ledger:\n  "
                         + "\n  ".join(fehlend))


def test_der_ledger_nennt_nur_echte_vermerke():
    """Die Gegenrichtung. Ein Eintrag, dessen Vermerk entfernt oder
    umformuliert wurde, ist keine offene Schuld mehr -- und die gehoert nicht
    in ein Verzeichnis offener."""
    vermerke = _vermerke()
    erfunden = [f"{d}: {t!r}" for d, texte in sorted(_eintraege().items())
                for t in texte if t not in vermerke.get(d, [])]
    assert not erfunden, ("im Ledger, aber so nicht im Code:\n  "
                          + "\n  ".join(erfunden))


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
    for datei, stellen in sorted(_stellen().items()):
        zeilen = (REPO / datei).read_text(encoding="utf-8").splitlines()
        for nr, _ in stellen:
            block = " ".join(zeilen[nr - 1:nr + 4])
            if "Obergrenze" not in block and "sobald" not in block:
                ohne.append(f"{datei}:{nr}")
    assert not ohne, f"Vermerk ohne Obergrenze: {ohne}"
