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

    Hier steht die Zusage als Struktur: in `aktion_anfrage` (Socketweg) darf
    `quelle` nicht aus der Nachricht kommen. Seit T-4.16 K3 gibt es einen
    ZWEITEN Aufrufer, `parser_anfrage` -- er ruft den Koordinator DIREKT,
    nicht ueber `aktion.sock`, und setzt `quelle="parser"` (siehe den
    WAECHTER unten). Beide sind Konstanten, keiner liest die Nachricht.
    """
    quelle = (REPO / "daimon" / "hub" / "daemon.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    gefunden: dict[str, ast.expr] = {}
    for funktion in ast.walk(baum):
        if not isinstance(funktion, ast.FunctionDef):
            continue
        for k in ast.walk(funktion):
            if not (isinstance(k, ast.Call)
                    and isinstance(k.func, ast.Attribute)
                    and k.func.attr == "ausfuehren"):
                continue
            for kw in k.keywords:
                if kw.arg == "quelle":
                    gefunden[funktion.name] = kw.value
    assert gefunden, "kein Aufruf von `ausfuehren(quelle=...)` gefunden"
    for name, fund in gefunden.items():
        assert isinstance(fund, ast.Constant), (
            f"{name}: die `quelle` kommt nicht als Konstante -- wer sie aus "
            "der Nachricht nimmt, gibt dem Absender die Direktbefehl-"
            "Ausnahme (T-4.4 K8)")
    assert gefunden.get("aktion_anfrage") is not None, (
        "aktion_anfrage (Socketweg) ruft `ausfuehren` nicht mehr mit "
        "`quelle=...`")
    assert gefunden["aktion_anfrage"].value == "modell"
    assert gefunden.get("parser_anfrage") is not None, (
        "parser_anfrage (Hub-Parser) ruft `ausfuehren` nicht mehr mit "
        "`quelle=...`")
    assert gefunden["parser_anfrage"].value == "parser"


# -- Der Waechter fuer den Tag, an dem es einen Parser gibt ---------------

def test_der_produktive_parser_ruft_den_koordinator_direkt():
    """T-4.16 K3: der Tag ist da. `daimon/hub/daemon.py: parser_anfrage`
    erzeugt jetzt `quelle="parser"` -- legitim, weil er den Koordinator
    DIREKT ruft (`teile.ausfuehren(...)`) und nicht ueber `aktion.sock`
    (dessen `aktion_anfrage` bleibt bei `quelle="modell"`, siehe der Test
    oben). Dieser Test ersetzt den vorigen WAECHTER, der eine leere Liste
    erwartete -- und verlangt jetzt GENAU eine Fundstelle, keine zweite."""
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
    assert len(treffer) == 1 and treffer[0].startswith("daimon/hub/daemon.py:"), (
        "genau eine Fundstelle wird erwartet (parser_anfrage) -- "
        f"gefunden: {treffer}. Ein zweiter Erzeuger von `quelle=parser` "
        "braucht seine eigene Pruefung, kein stillschweigendes Mitlaufen "
        "(T-4.4 Akzeptanzpunkt 8)")
