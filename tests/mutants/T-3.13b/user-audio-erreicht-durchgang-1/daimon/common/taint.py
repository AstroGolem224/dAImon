"""Gut-Muster T-3.13b: die Senkentabelle als Code, aus dem Vertrag gebaut
(§2/§3, API gepinnt in §6, Stand 05.08.). Keine Zeile aus der echten
Implementierung.

Die zwei Fehlerfälle des Vertrags, die nicht verwechselt werden dürfen:

* Eine Senke bekommt eine ROHE Zeichenkette: sie gilt als `tainted`, und der
  Aufruf wird protokolliert (`marke_fehlt`). KEIN Wurf — kein eingefrorener
  Prüfstand wird an einer geänderten Signatur rot. Vorgabe ist Misstrauen,
  nicht Verweigerung.
* Eine Senke bekommt MARKIERTES Material, das sie laut Tabelle nicht nehmen
  darf: `SenkenFehler` — es wirft. Nicht still filtern.

In einem Satz: fehlendes Wissen wird konservativ behandelt, falsches Wissen
wird zurückgewiesen.
"""

from __future__ import annotations

import hashlib
import sys
from typing import Any

from daimon.common.protocol import Mark, Marked

# Streng aufsteigend. `verketten` nimmt das Maximum — die strengste Marke.
RANG = {
    Mark.TRUSTED: 0,
    Mark.USER_PTT: 1,
    Mark.USER_AUDIO: 2,
    Mark.TAINTED: 3,
}

# Die Senkentabelle aus Vertrag §2 (Design §5.2): je Senke und Marke ein
# Eintrag; `True` heißt erlaubt. `kurzzeitgedaechtnis`,
# `langzeitgedaechtnis` und `proaktive_ausloeser` existieren in Phase 3
# nicht — die Regel steht trotzdem schon hier: wer sie baut, findet sie vor
# und muss sie nicht neu erfinden.
SENKEN: dict[str, dict[Mark, bool]] = {
    "durchgang1": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                   Mark.TRUSTED: True, Mark.TAINTED: False},
    "durchgang2": {Mark.USER_PTT: True, Mark.USER_AUDIO: True,
                   Mark.TRUSTED: True, Mark.TAINTED: True},
    "auth_vorschau": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                      Mark.TRUSTED: True, Mark.TAINTED: True},
    "tts_auf_anfrage": {Mark.USER_PTT: True, Mark.USER_AUDIO: True,
                        Mark.TRUSTED: True, Mark.TAINTED: True},
    "tts_ungefragt": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                      Mark.TRUSTED: True, Mark.TAINTED: False},
    "audit_klartext": {Mark.USER_PTT: True, Mark.USER_AUDIO: True,
                       Mark.TRUSTED: True, Mark.TAINTED: False},
    "kurzzeitgedaechtnis": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                            Mark.TRUSTED: True, Mark.TAINTED: False},
    "langzeitgedaechtnis": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                            Mark.TRUSTED: True, Mark.TAINTED: False},
    "proaktive_ausloeser": {Mark.USER_PTT: False, Mark.USER_AUDIO: False,
                            Mark.TRUSTED: True, Mark.TAINTED: False},
}


class SenkenFehler(TypeError):
    """Markiertes Material an einer Senke, die diese Marke nicht nehmen
    darf. Es wirft — ein stiller Durchlass ließe den Fehler unbemerkt.

    `TypeError`: die Marke ist Teil des Typs, keine Eigenschaft des Werts.
    """


class Protokoll:
    """Der schmalste Logger, der hier gebraucht wird: ein Strom, eine Zeile.

    Kein `common.logging` — dieses Modul wird auch aus Prozessen gerufen,
    die ohne das venv laufen; ein Import, der dort fehlschlägt, nähme die
    ganze Markierung mit.
    """

    def __init__(self, strom: Any = None) -> None:
        self._strom = strom if strom is not None else sys.stderr

    def marke_fehlt(self, senke: str, wert: Any) -> None:
        print(f"marke_fehlt senke={senke} typ={type(wert).__name__} "
              f"laenge={len(str(wert))}", file=self._strom, flush=True)


_VORGABE_PROTOKOLL = Protokoll()


def markiere(wert: Any, marke: Mark = Mark.TAINTED) -> Marked:
    """Ein Wert mit Marke. Ein bereits markierter behält seine."""
    return wert if isinstance(wert, Marked) else Marked(wert, marke)


def verketten(*teile: Any) -> Marked:
    """Verkettung nimmt die STRENGSTE Marke.

    Gilt für Verkettung, Formatierung und jede Ableitung: die Ausgabe eines
    Durchgangs, der `tainted` gesehen hat, ist selbst `tainted`. Ein nackter
    Teil ist `tainted` — wer nichts sagt, bekommt kein Vertrauen.
    """
    stuecke = [markiere(t) for t in teile]
    if not stuecke:
        return Marked("", Mark.TRUSTED)
    strengste = stuecke[0].mark
    for s in stuecke:
        if RANG[s.mark] > RANG[strengste]:
            strengste = s.mark
    return Marked("".join(str(s.value) for s in stuecke), strengste)


def pruefe_senke(wert: Any, *, senke: str,
                 log: Protokoll | None = None) -> Marked:
    """Der eine Eingang an jeder Senke. Gibt den markierten Wert zurück.

    Rohe Zeichenkette: gilt als `tainted` und wird protokolliert — kein
    Wurf. Markiertes Material, das die Senke nicht nehmen darf:
    `SenkenFehler`. Rückgabe ist immer das `Marked`, damit der Aufrufer mit
    `.value` sichtbar und absichtlich aus der Verfolgung aussteigt.
    """
    erlaubt = SENKEN.get(senke)
    if erlaubt is None:
        # Eine unbekannte Senke ist ein Fehler, kein Freibrief — sonst wäre
        # ein Tippfehler im Senkennamen die bequemste Umgehung der Tabelle.
        raise SenkenFehler(f"unbekannte Senke {senke!r}")
    if not isinstance(wert, Marked):
        # Fehlendes Wissen, konservativ behandelt: tainted + Protokoll.
        (log or _VORGABE_PROTOKOLL).marke_fehlt(senke, wert)
        return Marked(wert, Mark.TAINTED)
    if not erlaubt.get(wert.mark, False):
        # Falsches Wissen, zurückgewiesen: es wirft.
        raise SenkenFehler(
            f"{wert.mark.value} darf nicht in die Senke {senke!r}. Erlaubt: "
            + ", ".join(m.value for m, ok in erlaubt.items() if ok))
    return wert


def audit_redigiert(wert: Any) -> Marked:
    """Markiertes Material für das Audit: Hash und Länge, kein Klartext.

    Die Tabelle sagt für `audit_klartext` bei `tainted` „nur Hash und
    Länge". Das ist keine Erlaubnis für die Senke — sie wirft weiterhin —,
    sondern der Weg daran vorbei, als eigene Funktion: wer sie ruft, hat
    entschieden, den Inhalt nicht aufzuheben. Das Ergebnis ist `trusted` —
    ein Hash und eine Zahl sind keine angreiferbeeinflusste Zeichenkette
    mehr.
    """
    roh = wert.value if isinstance(wert, Marked) else wert
    text = roh if isinstance(roh, str) else repr(roh)
    return Marked({"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "laenge": len(text)}, Mark.TRUSTED)
