"""T-4.12 — der modale Dialog: was nicht umkehrbar ist, wird nicht wegge-poppt.

Warum der Dialog im Auth-Agenten liegt
----------------------------------------------------------------------------
Design 2.2: das Face rendert Modelltext und erteilt deshalb **keine**
Freigaben. Der Auth-Agent rendert ausschliesslich feste Vorlagen (T-1.7,
`daimon/auth/preview.py`) und ist damit der einzige Prozess, der eine
Bestaetigung entgegennehmen darf. Eine Freigabe, die vom Face kaeme, wird
verworfen -- nicht weil das Face boese waere, sondern weil in seinem
Adressraum fremder Text gelandet ist.

Wann ein Dialog Pflicht ist
----------------------------------------------------------------------------
`destructive: true` **ohne verifiziertes Undo-Artefakt**. Beides gehoert
zusammen: eine zerstoererische Aktion MIT geprueftem Artefakt (T-4.8) ist
umkehrbar und darf die leisere Rueckfrage nehmen; ohne Artefakt ist sie
endgueltig, und Endgueltiges wird nicht per Benachrichtigung freigegeben,
die neben dem Bildschirmrand verschwindet.

Nicht unterdrueckbar
----------------------------------------------------------------------------
Benachrichtigungen lassen sich per `Inhibit()` abschalten -- "Bitte nicht
stoeren" ist ein normaler Zustand eines Arbeitstages. Ein Fenster laesst sich
so nicht abschalten. Genau deshalb ist der modale Dialog fuer diesen Fall
gewaehlt und nicht als schoenere Benachrichtigung gebaut.

Was hier NICHT passiert
----------------------------------------------------------------------------
Kein Text aus einer Modellausgabe, keine Auszeichnungssprache, kein freier
Fliesstext. Der Dialog zeigt `preview.vorschau()` -- feste Beschriftungen,
escapte Werte, in Anfuehrungszeichen. GTK bekommt den Text als reines Label,
nicht als Markup: `set_use_markup(False)`, sonst waere ein `<b>` im Pfad ein
Formatierungsbefehl.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from daimon.auth.preview import VorschauFehler, vorschau

# Wer eine Freigabe erteilen darf. Eine Menge mit genau einem Eintrag -- und
# das Face steht ausdruecklich nicht darin (Design 2.2).
ERLAUBTE_ERTEILER = frozenset({"auth"})

ANTWORT_AUSFUEHREN = "granted"
ANTWORT_ABLEHNEN = "declined"
ANTWORT_ABBRUCH = "cancelled"


class ModalFehler(RuntimeError):
    """Der Dialog kam nicht zustande. Ohne Dialog keine Ausfuehrung."""


@dataclass(frozen=True)
class Vorlage:
    """Was gezeigt wird -- fertig gesaeubert, bevor GTK es sieht."""

    text: str
    aktion: str
    umkehr: str


def braucht_modal(eintrag: dict, *, undo_verifiziert: bool) -> bool:
    """Die eine Regel, als eigene Funktion und damit pruefbar.

    `destructive` UND kein verifiziertes Artefakt. Wer hier `or` schriebe,
    zwaenge bei jeder umkehrbaren Aktion ein Fenster auf -- und ein Dialog,
    der staendig kommt, wird weggeklickt, ohne gelesen zu werden.
    """
    return bool(eintrag.get("destructive")) and not undo_verifiziert


def vorlage_bauen(*, aktion: str, ziel: Any, umkehr: str) -> Vorlage:
    """Der Text. Wirft, wenn ein Schluessel unbekannt ist.

    Ein unbekannter Aktionsschluessel wird NICHT durch den rohen Wert
    ersetzt: dann stuende Modellformulierung im Dialog, und die Vorlage waere
    keine.
    """
    try:
        return Vorlage(text=vorschau(aktion=aktion, ziel=ziel, umkehr=umkehr),
                       aktion=aktion, umkehr=umkehr)
    except VorschauFehler as fehler:
        raise ModalFehler(str(fehler)) from fehler


def freigabe_annehmen(*, erteiler: str, antwort: str) -> str:
    """Wer darf, und was zaehlt.

    Eine Freigabe vom Face wird verworfen -- und zwar als `cancelled`, nicht
    als `declined`: abgelehnt hat niemand, es hat nur der Falsche geantwortet.
    """
    if erteiler not in ERLAUBTE_ERTEILER:
        raise ModalFehler(
            f"{erteiler!r} darf keine Freigabe erteilen; erlaubt ist "
            + ", ".join(sorted(ERLAUBTE_ERTEILER)))
    if antwort not in (ANTWORT_AUSFUEHREN, ANTWORT_ABLEHNEN, ANTWORT_ABBRUCH):
        raise ModalFehler(f"unbekannte Antwort {antwort!r}")
    return antwort


def zeigen(vorlage: Vorlage, *, gtk: Any = None) -> str:
    """Der Dialog. Gibt `granted`, `declined` oder `cancelled` zurueck.

    `gtk` ist injizierbar, damit die Regeln oben ohne Bildschirm pruefbar
    sind. Ohne Injektion wird GTK4 hier importiert -- nicht am Modulkopf: der
    Hub importiert dieses Modul nur fuer `braucht_modal`, und ein
    GTK-Import im Hub waere eine Abhaengigkeit ohne Anlass.
    """
    if gtk is not None:
        return gtk(vorlage)

    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    ergebnis = {"antwort": ANTWORT_ABBRUCH}

    dialog = Gtk.Window(title="dAImon — Bestaetigung erforderlich")
    dialog.set_modal(True)
    dialog.set_deletable(False)  # Wegklicken ist kein Nein und kein Ja
    kasten = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    kasten.set_margin_top(16)
    kasten.set_margin_bottom(16)
    kasten.set_margin_start(16)
    kasten.set_margin_end(16)

    label = Gtk.Label(label=vorlage.text)
    # KEIN Markup: ein `<b>` im Pfad waere sonst ein Formatierungsbefehl und
    # der Pfad saehe anders aus, als er ist.
    label.set_use_markup(False)
    label.set_selectable(False)
    label.set_wrap(True)
    kasten.append(label)

    knoepfe = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    ablehnen = Gtk.Button(label="Abbrechen")
    ausfuehren = Gtk.Button(label="Ausführen")
    # Der zerstoererische Knopf traegt die Warnfarbe der Umgebung, damit er
    # sich nicht wie der Abbrechen-Knopf anfuehlt.
    ausfuehren.add_css_class("destructive-action")

    def antwort(_knopf, wert):
        ergebnis["antwort"] = wert
        dialog.close()

    ablehnen.connect("clicked", antwort, ANTWORT_ABLEHNEN)
    ausfuehren.connect("clicked", antwort, ANTWORT_AUSFUEHREN)
    knoepfe.append(ablehnen)
    knoepfe.append(ausfuehren)
    kasten.append(knoepfe)
    dialog.set_child(kasten)
    dialog.present()

    schleife = __import__("gi.repository.GLib", fromlist=["GLib"]).MainLoop()
    dialog.connect("close-request", lambda *_: (schleife.quit(), False)[1])
    schleife.run()
    return ergebnis["antwort"]
