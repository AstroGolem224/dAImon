"""Die Sprechblase zeigt keine technischen Felder, sondern Marken dafuer.

Gefahren wird die NAHT des Betriebs, nicht `platzhalter_setzen` allein:
Hook-Nutzlast -> `Hub._on_event` -> `mood_of` -> `wert_saeubern` ->
`HubState.snapshot`. Genau in dieser Reihenfolge -- der Sanitizer laeuft NACH
dem Ersetzen, und er darf die Marke nicht zerlegen (`[` und `]` sind ASCII,
aber das steht in `preview.py` und nicht hier).

Ein Test nur auf `platzhalter_setzen` waere der Fehler aus CLAUDE.md: er
riefe die Funktion selbst auf und saehe nie, ob sie im Betrieb jemand ruft.
"""
from __future__ import annotations

import threading

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
