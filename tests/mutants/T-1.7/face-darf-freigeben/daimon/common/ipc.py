"""Gut-Muster fuer T-1.7 Teil 1: nur die Produzententabelle.

Ein Fixture ist ein Ersatzbaum, kein zweites Projekt -- von der echten
IPC-Schicht (T-0.7, eingefroren) steht hier nur, was T-1.7 prueft: welcher
Produzent welchen Nachrichtentyp senden darf.

Der Punkt fuer T-1.7: **`face` hat keinen Eintrag.** Das Face ist ein
Anzeigeprozess. Solange es auf einem eigenen Socket `intent_mark` senden
duerfte, waere "alle Bestaetigungen liegen im Auth-Agenten" nur behauptet.
"""

from __future__ import annotations

PRODUZENTEN: dict[str, frozenset[str]] = {
    "hookbridge": frozenset({"hook"}),
    "ears": frozenset({"utterance", "intent_mark"}),
    "eyes": frozenset({"screen"}),
    "kwin": frozenset({"window"}),
    "auth": frozenset({"intent_mark", "freigabe"}),
    "face": frozenset({"intent_mark", "freigabe"}),  # MUTANT
}


class MessageTypeError(PermissionError):
    """Nachrichtentyp gehoert nicht zu diesem Produzenten."""


def pruefe_typ(produzent: str, typ: str) -> None:
    erlaubt = PRODUZENTEN.get(produzent, frozenset())
    if typ not in erlaubt:
        raise MessageTypeError(
            f"Produzent {produzent!r} darf {typ!r} nicht senden"
        )
