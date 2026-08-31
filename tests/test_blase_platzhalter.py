"""Was in der Sprechblase steht: Marken statt technischer Felder, und
lesbarer Text statt Escapes.

Gefahren wird die NAHT des Betriebs, nicht die einzelnen Funktionen:
Hook-Nutzlast -> `Hub._on_event` -> `mood_of` (`platzhalter_setzen`) ->
`anzeige_saeubern` -> `HubState.snapshot`. Genau in dieser Reihenfolge -- der
Sanitizer laeuft NACH dem Ersetzen und darf die Marke nicht zerlegen.

Ein Test nur auf `platzhalter_setzen` waere der Fehler aus CLAUDE.md: er
riefe die Funktion selbst auf und saehe nie, ob sie im Betrieb jemand ruft.
"""
from __future__ import annotations

import threading
import unicodedata

import pytest

from daimon.hub import daemon as D
from daimon.hub.state import HubState


class _Log:
    def info(self, text, **kw): pass
    def warn(self, text, **kw): pass
    def error(self, text, **kw): pass


class _Diag:
    def verworfen(self, grund): pass
    def hop(self, name, ms): pass


@pytest.fixture
def hub(tmp_path) -> D.Hub:
    h = D.Hub.__new__(D.Hub)          # ohne __init__: kein Socket, kein Thread
    h.runtime_dir = tmp_path
    h.log = _Log()
    h.diag = _Diag()
    h.state = HubState()
    h._audit = None
    h._aktion_lock = threading.RLock()
    return h


def _body(hub: D.Hub, nachricht: str) -> str:
    hub._on_event(D.Event(type="hook", payload={
        "hook_event_name": "Stop",
        "session_id": "s1",
        "last_assistant_message": nachricht,
    }))
    bubble = hub.state.snapshot()["bubble"]
    assert bubble is not None
    return bubble["body"]


@pytest.mark.parametrize("nachricht, marke, weg", [
    ("Geaendert in /home/matthias/repo/face/src/bubble.rs, fertig.",
     "[Pfad]", "/home/matthias"),
    ("Siehe https://example.org/pfad?a=1 fuer Details.", "[Link]", "https://"),
    ("Ruf `cargo test --all -- --nocapture` auf.", "[Code]", "cargo test"),
    ("Der Token lautet ghp0123456789abcdefghij0123 nun.", "[Wert]", "ghp0123"),
    ("Ich habe bubble.rs angefasst.", "[Pfad]", "bubble.rs"),
])
def test_technisches_wird_zur_marke(hub, nachricht, marke, weg):
    body = _body(hub, nachricht)
    assert marke in body
    assert weg not in body


def test_positivkontrolle_normaler_satz_bleibt_unangetastet(hub):
    """Ohne diese Zeile waere ein Ersetzer, der ALLES zur Marke macht,
    genauso gruen wie einer, der genau das Richtige trifft."""
    satz = "Der Task ist durch, zwei Tests waren rot und sind jetzt gruen."
    assert _body(hub, satz) == satz


def test_und_oder_ist_kein_pfad(hub):
    """Die Pfadregel darf keinen Schraegstrich mitten im Wort greifen."""
    assert "[Pfad]" not in _body(hub, "Das gilt fuer Bild und/oder Ton.")


# --------------------------------------------------------------------------
# Umlaute: BEFUND 31.08.
#
# Die Blase zeigte `Prüfstand` statt `Prüfstand` -- sie lief durch
# `wert_saeubern`, den Sanitizer der AKTIONSVORSCHAU. Dort ist das Escapen
# ausdruecklich gewollt und bleibt es; die Blase bestaetigt aber nichts.
# Gefahren wird wieder die Naht, nicht `anzeige_saeubern` allein.
# --------------------------------------------------------------------------

def test_umlaute_kommen_lesbar_an(hub):
    satz = "Der Pr\u00fcfstand \u00e4ndert die Aufgabe \u2013 \u201eso\u201c hei\u00dft das."
    assert _body(hub, satz) == satz


@pytest.mark.parametrize("unsichtbar, name", [
    ("‮", "Bidi-Override"),
    ("⁦", "Isolate"),
    ("​", "Nullbreite"),
    ("﻿", "BOM"),
    (" ", "Zeilentrenner"),
    ("\x1b[31m", "ANSI-Folge"),
])
def test_unsichtbares_wird_entfernt_nicht_escapt(hub, unsichtbar, name):
    """Entfernt, nicht escapt: ein escapter Bidi-Override waere wieder
    unlesbarer Text, und entfernt kann er nichts mehr drehen."""
    body = _body(hub, f"vorne{unsichtbar}hinten")
    # Geprueft wird das unsichtbare Zeichen selbst. Bei der ANSI-Folge ist
    # das ESC; die Ziffern dahinter sind danach gewoehnlicher Text und
    # sollen es auch bleiben -- `wert_saeubern` laesst sie ebenfalls stehen.
    for zeichen in unsichtbar:
        if unicodedata.category(zeichen) in ("Cc", "Cf", "Zl", "Zp"):
            assert zeichen not in body, f"{name}: U+{ord(zeichen):04X} blieb"
    assert "\\u" not in body, f"{name} wurde escapt statt entfernt"
    assert "vorne" in body and "hinten" in body, name


def test_positivkontrolle_die_saeuberung_laeuft_ueberhaupt(hub):
    """Ohne diese Zeile waere ein Hub, der GAR NICHT saeubert, bei allen
    Pruefungen oben genauso gruen -- Umlaute kaemen ja auch dann durch."""
    assert _body(hub, "a​b") == "ab"


def test_die_aktionsvorschau_escapt_weiterhin():
    """Die Gegenrichtung, und der Grund fuer zwei Profile: was der Nutzer
    BESTAETIGT, muss verwechselbare Glyphen weiter sichtbar machen."""
    from daimon.auth.preview import wert_saeubern
    assert wert_saeubern("Prüfstand") == "Pr\\u00FCfstand"
