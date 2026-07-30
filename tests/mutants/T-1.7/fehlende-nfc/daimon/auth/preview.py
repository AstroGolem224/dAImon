"""Gut-Muster fuer T-1.7 Teil 1: der Vorschau-Sanitizer.

Unabhaengig von der Implementierung geschrieben. Ein Gut-Muster, das aus dem
Produktivcode kopiert waere, bewiese nur, dass der Code der Code ist.

Die Vorschau ist die letzte Stelle, an der ein Mensch sieht, was gleich
passiert. Deshalb die strenge Regel: **die Ausgabe ist reines ASCII**. Das ist
schaerfer als Design 2.4 verlangt ("verwechselbare Glyphen werden markiert"),
und zwar mit Absicht -- eine Tabelle verwechselbarer Glyphen ist unvollstaendig,
sobald Unicode waechst, und eine unvollstaendige Sperrliste ist bei einer
Sicherheitsanzeige die falsche Fehlerrichtung. Es kostet Lesbarkeit bei
Umlauten in Dateinamen und schliesst dafuer die ganze Klasse.
"""

from __future__ import annotations

import unicodedata

MAX_WERT_LAENGE = 200
_GEKUERZT = "...(gekuerzt)"


class VorschauFehler(Exception):
    """Unbekannte Beschriftung oder nicht darstellbarer Wert."""


# Feste Beschriftungen. Der Aufrufer uebergibt SCHLUESSEL, keine Texte --
# wer keinen Text uebergeben kann, kann auch keinen Modelltext hineinreichen.
AKTIONS_BESCHRIFTUNGEN = {
    "datei.papierkorb": "Datei in den Papierkorb verschieben",
    "datei.oeffnen": "Datei öffnen",
    "fenster.fokus": "Fenster in den Vordergrund holen",
}

UMKEHR_BESCHRIFTUNGEN = {
    "papierkorb": "möglich (Papierkorb)",
    "keine": "nicht möglich",
}


def _escape(zeichen: str) -> str:
    nummer = ord(zeichen)
    if nummer > 0xFFFF:
        return f"\\U{nummer:08x}"
    return f"\\u{nummer:04x}"


def _darstellbar(zeichen: str) -> bool:
    """Nur druckbares ASCII ueberlebt roh. Der Backslash nicht -- er wird
    verdoppelt, sonst waere ein eingegebenes \\u202e von einem escapten
    Bidi-Override nicht zu unterscheiden und die Anzeige mehrdeutig."""
    nummer = ord(zeichen)
    return 0x20 <= nummer <= 0x7E and zeichen != "\\"


def wert_saeubern(wert: str, *, max_laenge: int = MAX_WERT_LAENGE) -> str:
    if not isinstance(wert, str):
        raise VorschauFehler("Wert muss eine Zeichenkette sein")
    # NFC ZUERST. Sonst escapt man Zeichen, die nach der Normalisierung
    # anders aussehen -- und zwei optisch gleiche Pfade ergaeben zwei
    # verschiedene Anzeigen.
    normal = wert  # MUTANT: keine NFC-Normalisierung
    teile = []
    for zeichen in normal:
        if zeichen == "\\":
            teile.append("\\\\")
        elif _darstellbar(zeichen):
            teile.append(zeichen)
        else:
            teile.append(_escape(zeichen))
    raus = "".join(teile)
    # Die Grenze gilt fuer die AUSGABE. Ein Wert aus 3000 Bidi-Zeichen waere
    # sonst 18000 Zeichen lang, obwohl die Eingabe unter der Grenze lag.
    if len(raus) > max_laenge:
        raus = raus[:max_laenge] + _GEKUERZT
    if any(ord(c) > 127 for c in raus):
        raise VorschauFehler("Ausgabe ist nicht rein ASCII")
    return raus


def pfad_saeubern(pfad: str, *, max_laenge: int = MAX_WERT_LAENGE) -> str:
    """Wie wert_saeubern. Der Pfad wird NICHT aufgeloest: die Vorschau zeigt,
    was ausgefuehrt wird, nicht was das Dateisystem daraus macht."""
    return wert_saeubern(pfad, max_laenge=max_laenge)


def vorschau(*, aktion: str, ziel: str, umkehr: str) -> str:
    if aktion not in AKTIONS_BESCHRIFTUNGEN:
        raise VorschauFehler(f"unbekannte Aktion: {aktion!r}")
    if umkehr not in UMKEHR_BESCHRIFTUNGEN:
        raise VorschauFehler(f"unbekannte Umkehr: {umkehr!r}")
    return (
        "Ember will ausführen:\n"
        f"  Aktion:  {AKTIONS_BESCHRIFTUNGEN[aktion]}\n"
        f'  Ziel:    "{pfad_saeubern(ziel)}"\n'
        f"  Umkehr:  {UMKEHR_BESCHRIFTUNGEN[umkehr]}"
    )
