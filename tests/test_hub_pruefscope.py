"""Der Prueflauf-Eintrag auf `hookbridge.sock` -- und seine zwei Grenzen.

`daemon.VERIFY_SCOPE` ist eine ausdrueckliche Tuer: ein Pruefstand haengt sich
mit `systemd-run --user --scope --unit=daimon-verify.scope` in eine Scope
dieses Namens und kommt damit an `hookbridge.sock`. Ohne sie laeuft er unter
der Unit seiner Sitzung und wird abgewiesen (gemessen:
`('fremde_unit', 'app-com.anthropic.Claude-2954.scope', 88208)`).

Diese Datei bewacht den Eintrag in BEIDE Richtungen:

  (a) `_horche_produzent` muss seine Liste weiterhin aus `PRODUZENT_UNITS`
      beziehen. Faellt der Zulauf weg, ist die Tabelle Zierde -- genau das
      Muster aus CLAUDE.md, und in `_horche_produzent` schon einmal passiert
      (bis 19.08. rief sie `ipc.accept` ganz ohne `erlaubte_units`).
  (b) Der Eintrag darf bei KEINEM anderen Produzenten auftauchen. `auth` ist
      die einzige Typenmenge mit Faehigkeit statt Sichtbarkeit; `plan`
      schreibt in den Terminspeicher; `face` und `ears` melden Zustand des
      Nutzers. Die Begruendung steht bei `VERIFY_SCOPE` in `daemon.py`.

Gemessen wird (a) nicht am Quelltext, sondern am AUFRUF: `ipc.accept` wird
ersetzt und die uebergebene Liste eingesammelt. Die Positivkontrolle steht
gleich daneben -- wurde gar nicht aufgerufen, ist der Test rot und nicht
still gruen.
"""
from __future__ import annotations

import socket
import threading
import types

import pytest

from daimon.common import ipc
from daimon.hub import daemon as D


def _liste_beim_accept(produzent: str, tmp_path) -> tuple[bool, object]:
    """Was `_horche_produzent` an `ipc.accept` uebergibt -- echt eingesammelt.

    Rueckgabe `(gemessen, liste)`. `gemessen=False` heisst: `ipc.accept` wurde
    nie gerufen. Das ist die Positivkontrolle -- ohne sie liesse sich
    "keine Liste" nicht von "nichts gelaufen" unterscheiden.
    """
    gesehen: list[object] = []
    stop = threading.Event()

    def accept_attrappe(srv, prod, **kw):
        gesehen.append(kw.get("erlaubte_units"))
        stop.set()               # eine Runde reicht
        raise socket.timeout

    ns = types.SimpleNamespace(
        runtime_dir=tmp_path, _server=[], _stop=stop,
        log=types.SimpleNamespace(info=lambda *a, **k: None))
    echt = ipc.accept
    ipc.accept = accept_attrappe
    try:
        D.Hub._horche_produzent(ns, produzent)
    finally:
        ipc.accept = echt
        for srv in ns._server:
            srv.close()
    return (bool(gesehen), gesehen[0] if gesehen else None)


@pytest.mark.parametrize("produzent", sorted(D.PRODUZENT_UNITS))
def test_horche_produzent_zieht_seine_liste_aus_der_tabelle(produzent, tmp_path):
    """(a) DER ZULAUF. Ohne diese Uebergabe ist `PRODUZENT_UNITS` Zierde."""
    gemessen, liste = _liste_beim_accept(produzent, tmp_path)
    assert gemessen, f"ipc.accept wurde fuer {produzent!r} nie gerufen"
    assert liste == D.PRODUZENT_UNITS[produzent], (
        f"_horche_produzent uebergibt fuer {produzent!r} {liste!r} statt "
        f"{D.PRODUZENT_UNITS[produzent]!r}")


def test_ohne_lauf_meldet_die_messung_ausdruecklich_nichts(tmp_path):
    """Die Kontrolle der Kontrolle: laeuft die Schleife keine Runde, muss
    `gemessen=False` herauskommen -- sonst haette der Test oben auch bei
    einer toten Vorrichtung gruen gemeldet."""
    gesehen: list[object] = []
    stop = threading.Event()
    stop.set()                   # Schleife laeuft keine einzige Runde

    def accept_attrappe(srv, prod, **kw):
        gesehen.append(kw)
        raise socket.timeout

    ns = types.SimpleNamespace(
        runtime_dir=tmp_path, _server=[], _stop=stop,
        log=types.SimpleNamespace(info=lambda *a, **k: None))
    echt = ipc.accept
    ipc.accept = accept_attrappe
    try:
        D.Hub._horche_produzent(ns, "hookbridge")
    finally:
        ipc.accept = echt
        for srv in ns._server:
            srv.close()
    assert gesehen == [], "die Attrappe lief, obwohl _stop gesetzt war"


def test_der_prueflauf_eintrag_steht_nur_bei_hookbridge():
    """(b) Die Tuer bleibt eine einzelne. Wer sie woanders aufmacht, macht
    sie mit einer eigenen Begruendung in `daemon.py` auf -- und dieser Test
    wird dann bewusst geaendert, nicht beilaeufig."""
    assert D.VERIFY_SCOPE in D.PRODUZENT_UNITS["hookbridge"]
    for produzent, liste in D.PRODUZENT_UNITS.items():
        if produzent == "hookbridge":
            continue
        assert not ipc.unit_erlaubt(D.VERIFY_SCOPE, liste), (
            f"{produzent!r} laesst {D.VERIFY_SCOPE} durch -- siehe die "
            f"Begruendung bei VERIFY_SCOPE in daemon.py")


def test_der_eintrag_oeffnet_nur_sichtbarkeit_keine_faehigkeit():
    """Der Handel, festgenagelt: `hookbridge` darf genau `hook`. Waechst
    diese Menge, waechst mit ihr das, was ein Prueflauf ausloesen kann."""
    assert ipc.PRODUZENTEN["hookbridge"] == frozenset({"hook"})


def test_der_scope_name_wird_exakt_verglichen():
    """Kein `@`-Template, kein Beinahe-Treffer. `unit_erlaubt` haette sonst
    eine zweite Tuer geoeffnet, die niemand aufgeschrieben hat."""
    assert D.VERIFY_SCOPE.endswith(".scope") and "@" not in D.VERIFY_SCOPE
    liste = D.PRODUZENT_UNITS["hookbridge"]
    assert ipc.unit_erlaubt(D.VERIFY_SCOPE, liste)
    for fremd in (D.VERIFY_SCOPE + ".boese", "daimon-verify",
                  "daimon-verify.service", "xdaimon-verify.scope"):
        assert not ipc.unit_erlaubt(fremd, liste), fremd
