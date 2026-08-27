"""T-3.13b — die Senkentabelle aus Design 5.2, ausfuehrbar.

Warum die Tabelle Code wird
----------------------------------------------------------------------------
Sie stand bisher in Prosa, und Prosa setzt nichts durch. Design 5.2 sagt es
selbst: die Aufzaehlung ist eine **Ausnahmeliste, keine Definition** -- ein neu
hinzugefuegtes Feld ist automatisch markiert, bis jemand begruendet, warum
nicht. Genau deshalb steht hier eine Tabelle mit einem Eintrag je Senke und
nicht eine Handvoll `if`-Zweige, die man beim naechsten Feld vergisst.

Die zwei Fehlerfaelle, die nicht verwechselt werden duerfen
----------------------------------------------------------------------------
Das ist die Entscheidung, an der dieser Task haengt (05.08.):

  * Eine Senke bekommt eine **rohe Zeichenkette** -> sie gilt als `tainted`,
    und der Aufruf wird protokolliert. **Kein Wurf.** Sonst wuerde jede
    Signaturaenderung einen der 26 eingefrorenen Pruefstaende rot machen, und
    die Zusage waere gegen ihre eigenen Waechter durchgesetzt worden.
  * Eine Senke bekommt **markiertes Material, das sie nicht nehmen darf** ->
    **es wirft.** Nicht still filtern: ein stiller Filter ist von "es kam nie
    etwas Verbotenes" nicht zu unterscheiden, und dann fehlt die Messung.

In einem Satz: fehlendes Wissen wird konservativ behandelt, falsches Wissen
wird zurueckgewiesen.

Warum die Raenge eine Vereinfachung sind, und wo sie eine bleibt
----------------------------------------------------------------------------
`user_audio` und `tainted` sind eigentlich **unvergleichbar**: das eine ist
spoofbar, das andere angreiferbeeinflusst. Ihre Verkettung wird hier `tainted`,
weil das an jeder Senke der Tabelle mindestens so streng ist wie `user_audio`.
Die einzige Stelle, an der das Genauigkeit kostet, waere eine Senke, die
`tainted` erlaubt und `user_audio` verbietet -- eine solche gibt es nicht. Wer
eine hinzufuegt, liest diesen Absatz noch einmal.
"""

from __future__ import annotations

import sys
from typing import Any

from daimon.common.protocol import Mark, Marked

# Streng aufsteigend. `verketten` nimmt das Maximum -- siehe Modulkopf.
RANG = {
    Mark.TRUSTED: 0,
    Mark.USER_PTT: 1,
    Mark.USER_AUDIO: 2,
    Mark.TAINTED: 3,
}

# Design 5.2, Zeile fuer Zeile. `True` heisst erlaubt; die Bedingungen in den
# Kommentaren sind KEINE Erlaubnis auf Zuruf, sondern Auflagen, die die Senke
# selbst einhaelt (die Auth-Vorschau escapt, der TTS validiert nach 8.3).
SENKEN: dict[str, dict[Mark, bool]] = {
    # Werkzeugfaehig: sieht keine angreiferkontrollierten Zeichenketten.
    "durchgang1": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                   Mark.TRUSTED: True, Mark.TAINTED: False},
    # Werkzeuglos: darf alles sehen, kann dafuer nichts auswaehlen.
    "durchgang2": {Mark.USER_PTT: True, Mark.USER_AUDIO: True,
                   Mark.TRUSTED: True, Mark.TAINTED: True},
    # `tainted` nur escapt und laengenbegrenzt -- das tut `pfad_saeubern()`
    # seit T-1.7. Neu ist die Pruefung, dass es passiert ist.
    "auth_vorschau": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                      Mark.TRUSTED: True, Mark.TAINTED: True},
    # `tainted` nur durch den Validator aus 8.3.
    "tts_auf_anfrage": {Mark.USER_PTT: True, Mark.USER_AUDIO: True,
                        Mark.TRUSTED: True, Mark.TAINTED: True},
    # Ungefragt: nur kuratierte Vorlagen. Die Luecke, die v3.3 hatte -- ein per
    # OCR erfasstes Passwort waere sonst vorgelesen worden.
    #
    # `user_ptt` stand hier bis zum 26.08. auf `True` und widersprach damit
    # `hub/sprechtext.aus_vorlage`, das seit T-3.9 ausschliesslich `trusted`
    # nimmt (Design §8.3: "Variable Anteile sind ausschliesslich
    # trusted-Werte"). Zwei Fassungen einer Regel sind eine Regel und eine
    # Attrappe: diese Zeile hatte keinen Aufrufer, der Zeitplaner schrieb
    # `user_ptt` in `trusted` um und liess damit vorlesen, was das Mikrofon
    # am gehaltenen Taster aufnahm. Gueltig ist die strengere Fassung, und
    # `aus_vorlage` ruft jetzt genau diese Zeile.
    "tts_ungefragt": {Mark.USER_PTT: False, Mark.USER_AUDIO: False,
                      Mark.TRUSTED: True, Mark.TAINTED: False},
    # Audit im Klartext: `tainted` nur als Hash und Laenge.
    "audit_klartext": {Mark.USER_PTT: True, Mark.USER_AUDIO: True,
                       Mark.TRUSTED: True, Mark.TAINTED: False},

    # -- Senken, die es in Phase 3 NOCH NICHT GIBT ------------------------
    # Sie stehen hier, damit die Regel schon dasteht, wenn jemand sie baut.
    # Der Pruefstand misst sie nicht am laufenden System -- er prueft nur, dass
    # die Tabelle sie kennt.
    "kurzzeitgedaechtnis": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                            Mark.TRUSTED: True, Mark.TAINTED: False},
    # Nur die woertliche Spanne aus einer `user_ptt`-Aeusserung.
    "langzeitgedaechtnis": {Mark.USER_PTT: True, Mark.USER_AUDIO: False,
                            Mark.TRUSTED: True, Mark.TAINTED: False},
    "proaktive_ausloeser": {Mark.USER_PTT: False, Mark.USER_AUDIO: False,
                            Mark.TRUSTED: True, Mark.TAINTED: False},
}


class SenkenFehler(TypeError):
    """Markiertes Material an einer Senke, die es nicht nehmen darf.

    `TypeError` und nicht `ValueError`: Design 5.2 nennt den Aufruf einen
    Typfehler, und das ist er auch -- die Marke ist Teil des Typs und nicht
    eine Eigenschaft des Werts.
    """


class Protokoll:
    """Der schmalste Logger, der hier gebraucht wird.

    Kein `common.logging`: dieses Modul wird auch aus dem Auth-Agenten benutzt,
    und der laeuft unter System-Python ohne das venv (siehe Uebergabe, zwei
    Interpreter). Ein Import, der dort fehlschlaegt, nimmt die ganze
    Markierung mit.
    """

    def __init__(self, strom: Any = None) -> None:
        self._strom = strom if strom is not None else sys.stderr

    def marke_fehlt(self, senke: str, wert: Any) -> None:
        print(f"marke_fehlt senke={senke} typ={type(wert).__name__} "
              f"laenge={len(str(wert))}", file=self._strom, flush=True)


_VORGABE_PROTOKOLL = Protokoll()


def markiere(wert: Any, marke: Mark = Mark.TAINTED) -> Marked:
    """Ein Wert mit Marke. Ein bereits markierter behaelt seine."""
    return wert if isinstance(wert, Marked) else Marked(wert, marke)


def verketten(*teile: Any) -> Marked:
    """Verkettung nimmt die STRENGSTE Marke.

    Gilt fuer Verkettung, Formatierung und jede Ableitung: die Ausgabe eines
    Durchgangs, der `tainted` gesehen hat, ist selbst `tainted`. Ein nackter
    Teil ist `tainted` -- wer nichts sagt, bekommt kein Vertrauen.
    """
    stuecke = [markiere(t) for t in teile]
    if not stuecke:
        return Marked("", Mark.TRUSTED)
    schaerfste = max(stuecke, key=lambda m: RANG[m.mark]).mark
    return Marked("".join(str(s.value) for s in stuecke), schaerfste)


def pruefe_senke(wert: Any, *, senke: str,
                 log: Protokoll | None = None) -> Marked:
    """Der Waechter vor jeder Senke. Gibt den markierten Wert zurueck.

    Nicht `bool`: wer hier durchkommt, hat den markierten Wert in der Hand und
    kann nicht versehentlich mit dem rohen weiterarbeiten.
    """
    erlaubt = SENKEN.get(senke)
    if erlaubt is None:
        # Eine unbekannte Senke ist ein Fehler und kein Freibrief. Sonst waere
        # ein Tippfehler im Senkennamen die bequemste Umgehung der Tabelle.
        raise SenkenFehler(f"unbekannte Senke {senke!r}. Bekannt: "
                           + ", ".join(sorted(SENKEN)))

    if not isinstance(wert, Marked):
        # Fehlendes Wissen: konservativ behandeln UND sichtbar machen -- und
        # dann ganz normal durch die Tabelle schicken.
        #
        # "Kein Wurf" heisst: es fliegt kein Fehler, WEIL die Marke fehlt. Es
        # heisst nicht, dass ein Wert ohne Marke mehr darf als einer mit. Der
        # erste Entwurf gab hier direkt zurueck, und damit war das Weglassen
        # der Marke die bequemste Umgehung der ganzen Tabelle: `tainted` an
        # Durchgang 1 wurde abgewiesen, ein roher String ging durch. Gemessen
        # am 05.08. beim Nachstellen des Pruefstandsbefundes.
        (log or _VORGABE_PROTOKOLL).marke_fehlt(senke, wert)
        wert = Marked(wert, Mark.TAINTED)

    if not erlaubt.get(wert.mark, False):
        raise SenkenFehler(
            f"{wert.mark.value} darf nicht in die Senke {senke!r}. "
            f"Erlaubt: "
            + ", ".join(m.value for m, ok in erlaubt.items() if ok))
    return wert


def audit_redigiert(wert: Any) -> Marked:
    """Markiertes Material fuer das Audit: Hash und Laenge, kein Klartext.

    Design 5.2 sagt fuer die Audit-Senke "tainted: nur Hash und Laenge". Das
    ist KEINE Erlaubnis fuer die Senke -- `audit_klartext` wirft weiterhin --,
    sondern der WEG daran vorbei. Er steht hier als eigene Funktion, damit die
    Umwandlung im Code sichtbar ist und nicht in einer Fussnote der Tabelle:
    wer sie ruft, hat entschieden, den Inhalt nicht aufzuheben.

    Das Ergebnis ist `trusted` -- ein Hash und eine Zahl sind keine
    angreiferbeeinflusste Zeichenkette mehr.
    """
    import hashlib

    roh = wert.value if isinstance(wert, Marked) else wert
    text = roh if isinstance(roh, str) else repr(roh)
    return Marked({"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "laenge": len(text)}, Mark.TRUSTED)
