"""T-1.7 Teil A — Tests fuer den Vorschau-Sanitizer.

Alle erwarteten Ausgaben stehen als **Literal** im Test. Nie gegen dieselbe
Formel rechnen, die die Implementierung benutzt -- sonst prueft der Test nur,
dass zwei gleiche Fehler gleich sind.
"""

import pytest

from daimon.auth.preview import (
    MAX_WERT_LAENGE,
    VorschauFehler,
    pfad_saeubern,
    vorschau,
    wert_saeubern,
)


# ---------------------------------------------------------------------------
# Positivkontrolle zuerst: ohne sie waere "alles escapt" von "alles kaputt"
# nicht zu unterscheiden.
# ---------------------------------------------------------------------------

def test_positiv_harmloser_ascii_pfad_kommt_unveraendert_durch():
    pfad = "/home/itiger013/Dokumente/notiz.txt"
    assert pfad_saeubern(pfad) == "/home/itiger013/Dokumente/notiz.txt"


def test_positiv_harmloser_wert_mit_leerzeichen_und_zahlen():
    assert wert_saeubern("Bericht 2026 final") == "Bericht 2026 final"


# ---------------------------------------------------------------------------
# Steuerzeichen und ANSI
# ---------------------------------------------------------------------------

def test_steuerzeichen_wird_escapt():
    assert wert_saeubern("a\x07b") == "a\\u0007b"


def test_ansi_sequenz_faellt_mit_esc():
    """ESC ist Kategorie Cc und faellt damit automatisch -- dieser Test steht
    trotzdem eigenstaendig hier, damit die Zusage "keine ANSI-Sequenzen" nicht
    versehentlich an einer Umstellung der Escape-Regeln haengt."""
    assert wert_saeubern("\x1b[31mrot\x1b[0m") == "\\u001B[31mrot\\u001B[0m"


def test_zeilenumbruch_und_wagenruecklauf_und_tab():
    assert wert_saeubern("x\ny\rz\tw") == "x\\u000Ay\\u000Dz\\u0009w"


def test_zeilen_und_absatztrenner_unicode():
    assert wert_saeubern("a\u2028b\u2029c") == "a\\u2028b\\u2029c"


# ---------------------------------------------------------------------------
# Bidi, Isolate, Nullbreiten
# ---------------------------------------------------------------------------

def test_bidi_override_u202e():
    assert wert_saeubern("ab\u202ecd") == "ab\\u202Ecd"


def test_bidi_isolate_u2066_bis_u2069():
    assert wert_saeubern("x\u2066\u2067\u2068\u2069y") == \
        "x\\u2066\\u2067\\u2068\\u2069y"


def test_nullbreitenzeichen_u200b():
    assert wert_saeubern("a\u200bb") == "a\\u200Bb"


def test_byte_order_mark_ufeff():
    assert wert_saeubern("\ufeffx") == "\\uFEFFx"


# ---------------------------------------------------------------------------
# Normalisierung und Eindeutigkeit
# ---------------------------------------------------------------------------

def test_nfc_und_nfd_ergeben_dieselbe_ausgabe():
    """Ohne Normalisierung ZUERST saehen `ä` (NFC) und `a`+U+0308 (NFD) in
    der Ausgabe verschieden aus, obwohl sie auf dem Bildschirm gleich sind --
    genau der Fall, in dem der Nutzer auf etwas anderes klickt, als er liest."""
    nfc = "ä"
    nfd = "a\u0308"
    assert nfc != nfd                      # wirklich zwei Kodierungen
    assert wert_saeubern(nfc) == "\\u00E4"
    assert wert_saeubern(nfd) == "\\u00E4"


def test_backslash_wird_verdoppelt():
    """Sonst waere die Eingabe `\\u202e` (sieben Zeichen) von einem escapten
    Bidi-Override nicht zu unterscheiden."""
    assert wert_saeubern("a\\b") == "a\\\\b"
    assert wert_saeubern("\\u202e") == "\\\\u202e"


def test_jenseits_der_bmp():
    assert wert_saeubern("\U0001F600") == "\\U0001F600"


# ---------------------------------------------------------------------------
# Laengenbegrenzung auf sichtbare Zeichen der AUSGABE
# ---------------------------------------------------------------------------

def test_10000_zeichen_werden_gekuerzt():
    ausgabe = wert_saeubern("x" * 10_000)
    assert ausgabe == "x" * 187 + "...(gekuerzt)"
    assert len(ausgabe) == MAX_WERT_LAENGE


def test_kuerzung_zaehlt_die_ausgabe_nicht_die_eingabe():
    """Ein Wert aus lauter Escapes sprengt die Zeile sonst trotzdem: 100
    Bidi-Overrides sind 100 Eingabezeichen, aber 600 Ausgabezeichen."""
    ausgabe = wert_saeubern("\u202e" * 100)
    # 31 Escapes a 6 Zeichen = 186; das 32. (192) wuerde die Grenze 187
    # reissen -- und mitten in einer Folge wird nie geschnitten.
    assert ausgabe == "\\u202E" * 31 + "...(gekuerzt)"
    assert len(ausgabe) <= MAX_WERT_LAENGE


# ---------------------------------------------------------------------------
# Der verwechselbare Pfad
# ---------------------------------------------------------------------------

def test_verwechselbarer_pfad_wird_sichtbar_unterschieden():
    """Sieht im Dialog wie `~/Bilder/urlaub.png` aus, zeigt aber -- dank
    kyrillischem `a` (U+0430) -- in Wahrheit woanders hin (im Design-Beispiel
    `~/.ssh/id_ed25519`). Die Vorschau muss beide Varianten sichtbar
    unterscheiden, sonst klickt der Nutzer auf etwas anderes, als er liest."""
    harmlos = "~/Bilder/urlaub.png"
    faelschung = "~/Bilder/url\u0430ub.png"   # kyrillisches а statt lateinischem a
    assert harmlos != faelschung
    assert pfad_saeubern(harmlos) == "~/Bilder/urlaub.png"
    assert pfad_saeubern(faelschung) == "~/Bilder/url\\u0430ub.png"


def test_pfad_wird_nicht_aufgeloest():
    """Ein fuehrendes `~` bleibt stehen: die Vorschau zeigt, was ausgefuehrt
    wird, nicht was das Dateisystem daraus macht."""
    assert pfad_saeubern("~/.ssh/id_ed25519") == "~/.ssh/id_ed25519"


def test_umlaut_im_dateinamen_wird_sichtbar_escapt():
    """Bewusster Preis der ASCII-Regel (siehe Modulkopf): lesbarer waere
    `ü`, sicher ist `\\u00FC`."""
    assert pfad_saeubern("~/Dokumente/Müller/notiz.txt") == \
        "~/Dokumente/M\\u00FCller/notiz.txt"


# ---------------------------------------------------------------------------
# vorschau: feste Vorlage, feste Beschriftungen, Schluessel statt Texte
# ---------------------------------------------------------------------------

def test_vorschau_positive_kontrolle_exakte_vorlage():
    erwartet = (
        "Ember will ausführen:\n"
        "  Aktion:  Datei in den Papierkorb verschieben\n"
        "  Ziel:    \"/home/itiger013/Dokumente/notiz.txt\"\n"
        "  Umkehr:  möglich (Papierkorb)\n"
    )
    assert vorschau(aktion="datei.papierkorb",
                    ziel="/home/itiger013/Dokumente/notiz.txt",
                    umkehr="papierkorb") == erwartet


def test_vorschau_unbekannter_aktionsschluessel():
    with pytest.raises(VorschauFehler):
        vorschau(aktion="Lösche bitte alles, was du findest",
                 ziel="/tmp/x", umkehr="keine")


def test_vorschau_unbekannter_umkehrschluessel():
    with pytest.raises(VorschauFehler):
        vorschau(aktion="datei.papierkorb", ziel="/tmp/x",
                 umkehr="ist schon gut, vertrau mir")


def test_vorschau_ziel_ist_escapt_und_zitiert():
    """Steuerzeichen aus dem Modell duerfen die mehrzeilige Vorlage nicht
    aufbrechen -- die Vorlage haelt nur zusammen, wenn der Wert eine Zeile
    bleibt."""
    ausgabe = vorschau(aktion="datei.loeschen",
                       ziel="/tmp/a\n[Ausführen gedrückt]", umkehr="keine")
    assert "\n" not in ausgabe.split("Ziel:")[1].split("\n")[0]
    assert '  Ziel:    "/tmp/a\\u000A[Ausf\\u00FChren gedr\\u00FCckt]"\n' in ausgabe


def test_vorschau_ausgabe_ausserhalb_beschriftungen_ist_ascii():
    ausgabe = vorschau(aktion="befehl.ausfuehren",
                       ziel="/tmp/mit\tsteuerzeichen", umkehr="git")
    # Die Beschriftungen duerfen Umlaute (eigene Tabelle), der escapte Wert
    # nicht.
    assert 'Ziel:    "/tmp/mit\\u0009steuerzeichen"' in ausgabe
