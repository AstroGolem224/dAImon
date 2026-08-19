"""Eine gerissene Audit-Kette erreicht den Bildschirm, nicht nur das Journal.

BEFUND T-4.6 K5 (Reviewer-Sitzung 19.08.):

    daimon/hub/daemon.py:1176 schreibt `log.warn("AUDIT-KETTE GERISSEN")`.
    docs/DESIGN.md:1034 verlangt "meldet eine Abweichung als Bubble mit
    hoher Dringlichkeit". Es gab auf diesem Weg keinen `set_bubble`-Aufruf.

**Fehlerszenario:** jemand ändert eine Zeile in
`$XDG_STATE_HOME/daimon/audit/`. Beim nächsten Hub-Start steht eine Warnung
im Journal, die niemand liest; das Overlay bleibt ruhig.

Das ist das Muster aus CLAUDE.md an der Stelle, an der es am meisten weh tut:
das Audit ist die Vorrichtung, die BEZEUGT, was passiert ist. Wenn ihre
Verletzung nur an eine Stelle gemeldet wird, die man erst liest, nachdem man
schon Verdacht geschöpft hat, bezeugt sie nichts.

Diese Datei fährt die Naht: manipulierte Kette -> `_audit_pruefen` ->
`state.warnblase` -> Schnappschuss. Kein Glied davon wird ersetzt.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from daimon.hub import daemon as D
from daimon.hub.state import HubState


class _Log:
    def __init__(self) -> None:
        self.zeilen = []

    def info(self, text, **kw): self.zeilen.append(("info", text, kw))
    def warn(self, text, **kw): self.zeilen.append(("warn", text, kw))
    def error(self, text, **kw): self.zeilen.append(("error", text, kw))


def _hub(tmp_path) -> D.Hub:
    h = D.Hub.__new__(D.Hub)          # ohne __init__: kein Socket, kein Thread
    h.runtime_dir = tmp_path
    h.log = _Log()
    h.state = HubState()
    h._audit = None

    class Cfg:
        state_dir = tmp_path / "state"
    h.cfg = Cfg()
    return h


def _kette_bauen(h: D.Hub, saetze: int = 3) -> Path:
    """Echte Saetze durch das echte Buch -- keine von Hand gebaute Datei.

    `schreiben` verlangt alle Pflichtfelder und berechnet `prev_hash` selbst;
    eine nachgebaute Kette waere genau die Attrappe, an der `pruefe` nichts
    finden koennte.
    """
    buch = h.audit_buch()
    for i in range(saetze):
        buch.schreiben(action_id=f"probe.{i}", outcome="ok",
                       prompt_shown="", params_hash="sha256:0", mark_id="m",
                       initiator="foreground", turn_id="t", tool_use_id=f"u{i}")
    return buch.verzeichnis


# -- Die Positivkontrolle zuerst -----------------------------------------

def test_eine_heile_kette_erzeugt_KEINE_blase(tmp_path):
    """Ohne diese Zeile bestünde alles unten auch eine Fassung, die immer
    warnt -- und die hätte den Nutzer nach dem dritten Start abgestumpft."""
    h = _hub(tmp_path)
    _kette_bauen(h)
    h.audit_verankern()
    assert h.state.snapshot().get("bubble") is None
    assert not [z for art, z, _ in h.log.zeilen if art == "warn"]


def test_der_allererste_lauf_warnt_nicht(tmp_path):
    """Beim allerersten Start gibt es noch keine Kettendatei -- `pruefe`
    meldet dann „Datei fehlt", und das ist KEIN Fund.

    Gefunden beim Bau dieser Datei: solange der Befund nur ins Journal ging,
    störte der Falschalarm niemanden. Als Blase wäre er bei jeder
    Neuinstallation aufgeploppt, und eine dringende Warnung, die dreimal
    grundlos kommt, klickt man beim vierten Mal weg -- auch wenn sie dann
    stimmt. Wer eine Meldung sichtbar macht, erbt ihre Falschalarme.
    """
    h = _hub(tmp_path)
    h.audit_buch()                      # legt das Verzeichnis an, schreibt nichts
    h.audit_verankern()
    assert h.state.snapshot().get("bubble") is None


def test_eine_GELOESCHTE_kette_ist_sehr_wohl_ein_fund(tmp_path, monkeypatch):
    """Die Gegenrichtung, und sie ist der Grund, warum die Ausnahme oben an
    den ANKERN hängt und nicht am Wort „Datei fehlt".

    Eine gelöschte Kette sieht auf der Platte aus wie eine nie geschriebene.
    Der zweite Strom unterscheidet sie: gibt es Anker im Journal, hat die
    Kette einmal existiert. Genau dafür wurde er gebaut.
    """
    from daimon.hub import audit as A

    h = _hub(tmp_path)
    verzeichnis = _kette_bauen(h)
    for datei in verzeichnis.rglob("audit.jsonl*"):
        datei.unlink()
    h._audit = None

    echt = A.pruefe
    monkeypatch.setattr(
        A, "pruefe",
        lambda v: {**echt(v), "anker_gefunden": 3, "anker_getroffen": 0})
    h.audit_verankern()

    blase = h.state.snapshot().get("bubble")
    assert blase is not None, (
        "eine gelöschte Kette mit vorhandenen Ankern muss auffallen -- sonst "
        "ist Löschen der einfachste Weg, das Audit loszuwerden")
    assert blase["urgent"] is True


# -- DER BEFUND: die gerissene Kette -------------------------------------

def _kette_zerreissen(verzeichnis: Path) -> None:
    """Eine Zeile ändern, wie es ein Angreifer täte -- nicht die Datei
    löschen. Der Hash bleibt stehen, der Inhalt nicht."""
    dateien = sorted(p for p in verzeichnis.rglob("audit.jsonl*")
                     if p.is_file())
    assert dateien, f"keine Kettendatei unter {verzeichnis} gefunden"
    for datei in dateien:
        zeilen = datei.read_text(encoding="utf-8").splitlines()
        if not zeilen:
            continue
        satz = json.loads(zeilen[0])
        satz["action_id"] = "etwas.anderes"
        zeilen[0] = json.dumps(satz, ensure_ascii=False, sort_keys=True)
        datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        return
    pytest.fail("keine Zeile zum Ändern gefunden")


def test_die_gerissene_kette_erreicht_den_bildschirm(tmp_path):
    """DER BEFUND. Vorher endete die Kette bei `log.warn`."""
    h = _hub(tmp_path)
    verzeichnis = _kette_bauen(h)
    h._audit = None                     # neues Buch, wie beim Neustart
    _kette_zerreissen(verzeichnis)

    h.audit_verankern()

    blase = h.state.snapshot().get("bubble")
    assert blase is not None, (
        "die gerissene Kette meldet sich nur im Journal -- Design 7.5 "
        "verlangt eine Blase (T-4.6 K5)")
    assert blase["urgent"] is True, (
        "eine Blase ohne Dringlichkeit steht neben den beiläufigen und wird "
        "übersehen")
    assert "Audit" in blase["title"]


def test_die_blase_verraet_keinen_inhalt(tmp_path):
    """Sie steht auf dem BILDSCHIRM. Was in der Kette steht, ist genau das,
    was dorthin nicht gehört -- Aktionsnamen, Pfade, Zeitpunkte."""
    h = _hub(tmp_path)
    verzeichnis = _kette_bauen(h)
    h._audit = None
    _kette_zerreissen(verzeichnis)
    h.audit_verankern()

    blase = h.state.snapshot()["bubble"]
    ganz = f"{blase['title']} {blase['body']}"
    assert "probe." not in ganz and "etwas.anderes" not in ganz, ganz
    assert str(tmp_path) not in ganz, "die Blase nennt einen Pfad"
    assert "--verify" in ganz, "sie sagt nicht, wo nachzusehen ist"


def test_das_journal_meldet_weiter(tmp_path):
    """Die Blase ERSETZT die Journalzeile nicht. Sie ist flüchtig -- wer sie
    wegklickt, soll den Befund später noch finden."""
    h = _hub(tmp_path)
    verzeichnis = _kette_bauen(h)
    h._audit = None
    _kette_zerreissen(verzeichnis)
    h.audit_verankern()
    assert [z for art, z, _ in h.log.zeilen
            if art == "warn" and "GERISSEN" in z]


# -- Die Vorrichtung selbst ----------------------------------------------

def test_warnblase_braucht_keine_sitzung():
    """Der Grund, warum es diese Methode gibt: `apply()` verlangt eine
    `session_id` und eine Stimmung, und der Hub hat beim Start keine von
    beiden."""
    s = HubState()
    s.warnblase("Titel", "Text")
    schnapp = s.snapshot()
    assert schnapp["bubble"] == {"title": "Titel", "body": "Text",
                                 "urgent": True}
    assert schnapp["sessions"] == 0 or not schnapp.get("sessions")


def test_warnblase_bewegt_die_revision():
    """Ohne `rev++` erfährt das Overlay nichts -- es fragt nach Änderungen,
    nicht nach dem Inhalt. Eine Blase, die den Zähler nicht bewegt, wird erst
    beim nächsten fremden Ereignis sichtbar."""
    s = HubState()
    vorher = s.snapshot()["rev"]
    s.warnblase("Titel", "Text")
    assert s.snapshot()["rev"] > vorher
