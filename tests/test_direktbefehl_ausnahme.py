"""Die Direktbefehl-Ausnahme gehoert dem Hub -- und der Socketweg ist nie sie.

BEFUND T-4.4 K8, gemessen von der Reviewer-Sitzung am 18.08. ueber den echten
Socket (Ledger-Ausgang produktdefekt-rot):

    ehrlich:   verdikt=ask   direkt=False  (Vorschau, vom Nutzer abbrechbar)
    behauptet: verdikt=allow direkt=True   (keine Vorschau)

Der Unterschied war ein Feld in der Nachricht: `quelle: "parser"`. Damit war
die Nutzerbestaetigung fuer JEDE Aktion mit `direct: true` im Katalog
umgehbar.

**Der Modulkopf von `policy.py` kannte die Regel und wandte sie nicht an.**
Er sagt: "Ein Feld, das der Absender setzt, sagt nichts" -- und zaehlt
`initiator` und `params_hash` auf. Die `quelle` stand nicht dabei, obwohl sie
als einzige eine Schranke OEFFNET. Zwei Absaetze weiter steht: "Die
Direktbefehl-Ausnahme gehoert dem Hub."

Die Antwort ist strukturell und keine zusaetzliche Pruefung: ein
deterministischer Hub-Parser laeuft IM Hub und ruft den Koordinator direkt.
Was durch `aktion.sock` kommt, ist per Definition eine fremde Anfrage. Der
Socketweg setzt deshalb `quelle="modell"`, fest.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from daimon.hub.policy import Anfrage, Policy

REPO = Path(__file__).resolve().parents[1]
# `foreground` traegt das Verdikt je Initiator -- `entscheide` liest
# `eintrag.get(initiator, "deny")`. Ohne diesen Schluessel endet jede Anfrage
# bei `katalog:foreground`, und die Direktausnahme kaeme nie zum Zug.
KATALOG = {
    "licht.an": {"audience": "dbus", "direct": True,
                 "foreground": "allow", "background": "deny"},
}


def _policy() -> Policy:
    return Policy(katalog=KATALOG, ebenen=[])


def _anfrage(quelle: str) -> Anfrage:
    # MIT gueltiger Rundenmarke -- ohne sie ist der `initiator` `background`,
    # und der Katalog verbietet das schon eine Stufe frueher. Geprueft werden
    # soll die Direktausnahme, nicht die Markenpflicht.
    return Anfrage(action_id="licht.an", params={}, session_id="s",
                   request_id="r", quelle=quelle, jetzt=0.0,
                   marke={"id": "t-1", "gueltig_bis": float("inf")})


# -- Die Ausnahme selbst bleibt, wie sie gedacht ist ----------------------

def test_die_ausnahme_gilt_fuer_den_parser():
    """Sie wird NICHT gestrichen -- sie ist die Zusage aus T-4.4
    Akzeptanzpunkt 8. Nur erreichbar ist sie kuenftig allein von innen."""
    e = _policy().entscheide(_anfrage("parser"))
    assert e.verdikt == "allow", e.grund


def test_und_nicht_fuer_eine_modellausgabe():
    """"Jede aus einer Modellausgabe stammende Aktion geht durch die
    Vorschau, unabhaengig von ihrem Katalogflag." """
    e = _policy().entscheide(_anfrage("modell"))
    assert e.verdikt == "ask"
    assert e.grund == "vorschau_pflicht"


@pytest.mark.parametrize("quelle", ["scheduler", "", "PARSER", "parser "])
def test_nur_genau_parser_mildert(quelle):
    """Kein Beinahe-Treffer. Ein Vergleich, der Grossschreibung oder ein
    Leerzeichen verzeiht, ist eine zweite Tuer.

    Geprueft wird "nicht `allow`" und nicht "`ask`": `scheduler` gilt als
    Hintergrund und endet schon eine Stufe frueher bei `deny`. Strenger als
    die Zusage verlangt -- und ein Test, der auf `ask` besteht, wuerde diese
    Strenge kuenftig verbieten.
    """
    assert _policy().entscheide(_anfrage(quelle)).verdikt != "allow"


# -- DER BEFUND: der Socketweg kann sie nicht mehr ausloesen --------------

def test_der_socketweg_setzt_die_quelle_selbst():
    """Gemessen am Quelltext und nicht am Verhalten, weil der Weg dorthin
    einen laufenden Hub samt Broker braucht -- der Verifizierer T-4.4 misst
    ihn ueber den echten Socket, und genau das ist seine Aufgabe.

    Hier steht die Zusage als Struktur: in `aktion_anfrage` darf `quelle`
    nicht aus der Nachricht kommen.
    """
    quelle = (REPO / "daimon" / "hub" / "daemon.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    fund = None
    for k in ast.walk(baum):
        if not (isinstance(k, ast.Call)
                and isinstance(k.func, ast.Attribute)
                and k.func.attr == "ausfuehren"):
            continue
        for kw in k.keywords:
            if kw.arg == "quelle":
                fund = kw.value
    assert fund is not None, "kein Aufruf von `ausfuehren(quelle=...)` gefunden"
    assert isinstance(fund, ast.Constant), (
        "die `quelle` kommt nicht als Konstante -- wer sie aus der Nachricht "
        "nimmt, gibt dem Absender die Direktbefehl-Ausnahme (T-4.4 K8)")
    assert fund.value == "modell", fund.value


# -- Der Waechter fuer den Tag, an dem es einen Parser gibt ---------------

def test_WAECHTER_es_gibt_keinen_produktiven_parser():
    """Der Verifizierer hat dafuer einen Waechter gebaut, und er meldet eine
    leere Liste: es gibt heute KEINEN legitimen Erzeuger von
    `quelle="parser"`. Die Ausnahme existierte damit ausschliesslich als
    Umgehungsweg.

    Wer einen Parser baut, ruft den Koordinator DIREKT -- nicht ueber
    `aktion.sock`. Dieser Test faellt auf, sobald jemand es anders macht,
    und verweist auf die Stelle.
    """
    treffer = []
    for datei in sorted((REPO / "daimon").rglob("*.py")):
        if "__pycache__" in str(datei):
            continue
        text = datei.read_text(encoding="utf-8")
        for nr, zeile in enumerate(text.splitlines(), 1):
            if '"parser"' in zeile or "'parser'" in zeile:
                nackt = zeile.strip()
                if nackt.startswith("#") or "quelle ==" in nackt:
                    continue          # der Vergleich in der Policy selbst
                if "quelle" in nackt:
                    treffer.append(f"{datei.relative_to(REPO)}:{nr}")
    assert not treffer, (
        "jemand erzeugt jetzt `quelle=parser`: " + ", ".join(treffer)
        + " -- er muss den Koordinator DIREKT rufen, und die Direktausnahme "
          "gehoert dann samt Zulauf geprueft (T-4.4 Akzeptanzpunkt 8)")
