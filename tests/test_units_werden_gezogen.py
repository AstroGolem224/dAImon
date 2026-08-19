"""Jede Unit braucht jemanden, der sie startet.

**Der Befund, der diese Datei ausgeloest hat.** `daimon-mind.service` hatte
kein `[Install]`, keine `daimon-mind.socket` und keine andere Unit, die sie
zieht -- `PartOf=` propagiert nur Stopp und Neustart, nie einen Start. Der
Dienst lief nicht, und das fiel erst auf, als zwei Messungen des
Abschlussreviews auf `unbelegt` standen: nicht weil eine Zusage gebrochen
war, sondern weil der Dienst gar nicht da war. Die Fussnote der Unit sagte
„gestartet wird ueber den Socket" und widersprach damit ihrem eigenen Kopf,
der Socket-Aktivierung ausdruecklich ablehnt.

Das ist wieder die Gestalt, die dieses Projekt bisher fuenfmal getroffen hat:
das Stueck ist gebaut und geprueft, sein ZULAUF fehlt -- Ticketbuch ohne
Verbraucher, Gate ohne Aufrufer, Kontextspeicher ohne `laden()`, DRM-Gatter
ohne Eingabe, jetzt ein Dienst ohne Starter. Ein Test je Stueck findet das
nie; dieser hier fragt nach dem Zulauf.

Geprueft werden die Dateien im Repo, nicht die Sitzung: eine Unit, die nur
auf DIESER Maschine von Hand gestartet wurde, ist fuer den naechsten Neustart
genauso weg.
"""
from __future__ import annotations

from pathlib import Path

import pytest

UNITS = Path(__file__).resolve().parents[1] / "config" / "systemd"
DIENSTE = sorted(UNITS.glob("*.service"))


def _hat(datei: Path, abschnitt: str) -> bool:
    return any(z.strip() == abschnitt
               for z in datei.read_text(encoding="utf-8").splitlines())


def test_es_gibt_ueberhaupt_units():
    """Sonst waere die Schleife unten leer und jede Aussage daraus wertlos."""
    assert len(DIENSTE) >= 15


@pytest.mark.parametrize("unit", DIENSTE, ids=lambda p: p.name)
def test_jede_unit_wird_von_etwas_gezogen(unit: Path):
    """`[Install]`, oder eine gleichnamige `.socket`/`.timer`. Nichts sonst.

    `PartOf=` und `Wants=` in der Unit selbst zaehlen NICHT: das erste
    propagiert nur Stopp und Neustart, das zweite zieht andere und nicht
    sich.
    """
    ziehende = [p.name for p in (unit.with_suffix(".socket"),
                                 unit.with_suffix(".timer")) if p.exists()]
    assert _hat(unit, "[Install]") or ziehende, (
        f"{unit.name} hat kein [Install] und keine .socket/.timer -- "
        "nach einem Neustart startet sie niemand")


@pytest.mark.parametrize("timer", sorted(UNITS.glob("*.timer")),
                         ids=lambda p: p.name)
def test_jeder_timer_wird_aktiviert(timer: Path):
    """Dieselbe Frage eine Ebene weiter, aufgefallen am 17.08. beim Bau des
    Audit-Timers: die Regel oben prueft nur `*.service`.

    Ein Timer ohne `[Install]` zieht seinen Dienst nie -- und dann ist auch
    der Dienst nie gezogen, obwohl die Regel oben ihn fuer versorgt haelt.
    Eine `.timer`-Datei, die niemand aktiviert, ist genau die Attrappe, gegen
    die diese Datei geschrieben wurde.
    """
    assert _hat(timer, "[Install]"), (
        f"{timer.name} hat kein [Install] -- niemand aktiviert ihn, und der "
        "Dienst dahinter laeuft nie")
    text = timer.read_text(encoding="utf-8")
    assert "Unit=" in text, f"{timer.name} nennt keinen Dienst"


def test_der_audit_pruefer_darf_nicht_schreiben():
    """Die Zusage dieser einen Unit, und sie steht in ihrer Abwesenheit.

    Ein Pruefer mit Schreibrecht koennte einen Befund wegschreiben, statt ihn
    zu melden. `ProtectSystem=strict` und `ProtectHome=read-only` ohne ein
    einziges `ReadWritePaths=` ist die ganze Bauart -- und weil eine fehlende
    Zeile in keinem Diff auffaellt, steht sie hier als Pruefung.
    """
    text = (UNITS / "daimon-audit-verify.service").read_text(encoding="utf-8")
    ohne_kommentar = "\n".join(z for z in text.splitlines()
                               if not z.strip().startswith("#"))
    assert "ProtectSystem=strict" in ohne_kommentar
    assert "ProtectHome=read-only" in ohne_kommentar
    assert "ReadWritePaths" not in ohne_kommentar
    assert "StateDirectory" not in ohne_kommentar


def test_POSITIVKONTROLLE_die_regel_kann_ueberhaupt_reissen(tmp_path):
    """Ohne diese Zeile bestuende der Test oben auch eine Fassung, die immer
    wahr sagt -- und genau so eine haette den Befund nicht gefunden."""
    verwaist = tmp_path / "daimon-verwaist.service"
    verwaist.write_text("[Unit]\nDescription=x\nPartOf=graphical-session.target\n"
                        "[Service]\nExecStart=/bin/true\n")
    ziehende = [p for p in (verwaist.with_suffix(".socket"),
                            verwaist.with_suffix(".timer")) if p.exists()]
    assert not (_hat(verwaist, "[Install]") or ziehende)


def test_die_mind_unit_widerspricht_sich_nicht_mehr():
    """Der konkrete Befund, festgenagelt: der Kopf lehnt Socket-Aktivierung
    ab, also darf die Fussnote nicht auf einen Socket verweisen."""
    text = (UNITS / "daimon-mind.service").read_text(encoding="utf-8")
    assert "[Install]" in text
    assert not (UNITS / "daimon-mind.socket").exists()
    assert "Kein `[Install]`: gestartet wird ueber den Socket" not in text


# -- Wer darf ins Archiv schreiben (T-7.1 K1) -----------------------------
#
# BEFUND vom 18.08.: `daimon-fs.service` hatte keine `ProtectHome`-Zeile --
# der Block war am 09.08. auskommentiert worden, weil `ReadWritePaths=` unter
# `tmpfs` scheiterte. Die Abwaegung war richtig; ihre Nebenwirkung hat
# niemand nachverfolgt. `ProtectSystem=strict` schuetzt /usr, nicht $HOME,
# und das Archiv liegt unter $XDG_DATA_HOME.
#
# Der Verifizierer mass es, indem er drei Units schreiben liess:
# recorder SCHRIEB (richtig), eyes VERWEIGERT (richtig), fs SCHRIEB (falsch).
# Damit gab es zwei schreibende Dienste statt einem -- und der zweite ist der
# modellgetriebene. Wer ihn erreicht, aendert die Aufzeichnung dessen, was
# wahrgenommen wurde, also die Grundlage der Archivsuche aus T-7.5.
#
# Hier stehen ZWEI Pruefungen und nicht eine. Die erste haelt die Zeile fest,
# die gefehlt hat. Die zweite faengt den Weg, den die erste offenliesse:
# `ProtectHome=read-only` sperrt das Schreiben, aber ein `BindPaths=` auf
# einen $HOME-Pfad holt es wieder herein -- und genau so bekommt der Recorder
# sein Archiv zurueck. Eine Unit, die es ihm nachmacht, waere derselbe Befund
# mit einer anderen Zeile.

ARCHIV_MARKE = "share/daimon"
SCHREIBENDER_DIENST = "daimon-recorder.service"


@pytest.mark.parametrize("unit", DIENSTE, ids=lambda p: p.name)
def test_jede_unit_sagt_etwas_ueber_HOME(unit: Path):
    """Eine fehlende Zeile faellt in keinem Diff auf -- deshalb steht sie
    hier als Pruefung. Ausgeschrieben und nicht geraten: was `ProtectHome`
    nicht nennt, ist offen."""
    zeilen = [z.strip() for z in unit.read_text(encoding="utf-8").splitlines()
              if not z.strip().startswith("#")]
    assert any(z.startswith("ProtectHome=") for z in zeilen), (
        f"{unit.name} sagt nichts ueber $HOME -- ProtectSystem=strict "
        "schuetzt /usr, nicht das Archiv (T-7.1 K1)")


def test_nur_EIN_dienst_holt_sich_das_archiv_zurueck():
    """Der zweite Weg hinein, und der schwerer zu sehende.

    Gesucht wird in den wirksamen Zeilen, nicht im ganzen Text: der Befund
    steht als Kommentar in genau diesen Dateien, und eine nackte Textsuche
    faende ihn und meldete Ruhe.
    """
    holen = []
    for unit in DIENSTE:
        for nr, zeile in enumerate(unit.read_text(encoding="utf-8").splitlines(), 1):
            nackt = zeile.strip()
            if nackt.startswith("#") or ARCHIV_MARKE not in nackt:
                continue
            if nackt.startswith(("BindPaths=", "ReadWritePaths=",
                                 "StateDirectory=", "ExecStartPre=")):
                holen.append(unit.name)
                break
    assert holen == [SCHREIBENDER_DIENST], (
        "genau ein Dienst darf ins Archiv schreiben (T-7.1 Akzeptanzpunkt 1), "
        f"hier sind es: {holen or 'keiner'}")


def test_POSITIVKONTROLLE_der_schreibende_dienst_wird_auch_gefunden():
    """Ohne diese Zeile bestuende der Test darueber auch dann, wenn die Suche
    ins Leere greift -- `holen == []` waere nie gleich `[recorder]`, aber ein
    Tippfehler in ARCHIV_MARKE machte beide Listen leer und den Vergleich
    stumm. Vier Falschbefunde an einem Tag kamen genau daher."""
    text = (UNITS / SCHREIBENDER_DIENST).read_text(encoding="utf-8")
    assert any(z.strip().startswith("BindPaths=") and ARCHIV_MARKE in z
               for z in text.splitlines()), (
        "der Recorder holt sich das Archiv nicht mehr per BindPaths -- "
        "dann misst die Pruefung darueber nichts")
