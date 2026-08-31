r"""T-1.7 Teil A — Sanitizer fuer die Aktionsvorschau.

WARUM ES DIESES MODUL GIBT
--------------------------
Design 2.4: die Vorschau ist eine **Senke fuer markiertes Material**. Sie muss
den Zielpfad zeigen, sonst ist die Bestaetigung wertlos -- und sie darf keinen
Modelltext rendern. Aufloesung: feste Vorlage, feste Beschriftungen,
Parameterwerte escapt, laengenbegrenzt, in Anfuehrungszeichen.

Ein Verbot von Steuerzeichen reicht nicht. Ein Zielpfad, der wie
`~/Bilder/urlaub.png` aussieht und in Wahrheit `~/.ssh/id_ed25519` ist, macht
die Bestaetigung wertlos -- der Nutzer klickt auf etwas anderes, als er liest.

REINES ASCII -- EINE BEWUSSTE ABWAEGUNG
---------------------------------------
Design 2.4 verlangt nur, dass **verwechselbare Glyphen markiert** werden.
Dieses Modul ist strenger: es escapt **jedes** Nicht-ASCII-Zeichen sichtbar
als `\uXXXX`. Begruendung: eine Tabelle verwechselbarer Glyphen (lateinisch
`a` gegen kyrillisch `a`, griechisch `o`, ...) ist unvollstaendig, sobald
Unicode waechst -- und „unvollstaendige Sperrliste" ist bei einer
Sicherheitsanzeige die falsche Fehlerrichtung. Ein neuer verwechselbarer
Codepunkt waere dann still durchgekommen; hier ist er automatisch sichtbar.
Der Preis ist Lesbarkeit: Umlaute in Dateinamen erscheinen als `\u00E4`
statt `ä`. Das ist bewusst in Kauf genommen -- die Vorschau ist eine
Sicherheitsanzeige, keine schoene Anzeige.

PYTHON 3.12 UND 3.14
--------------------
Reines stdlib-Python, kein `gi`, kein GTK. Der Auth-Agent laeuft in Teil 2
unter System-Python 3.14 (dort liegt PyGObject), das Projekt-venv ist auf
3.12 festgenagelt. Dieses Modul muss unter beiden laufen: hier steht nichts,
was es in 3.12 nicht gibt.
"""

from __future__ import annotations

from daimon.common.taint import pruefe_senke

import unicodedata


class VorschauFehler(Exception):
    """Ein Wert oder Schluessel darf nicht in die Vorschau."""


MAX_WERT_LAENGE = 200   # sichtbare Zeichen je Parameterwert (der AUSGABE)

# ASCII, damit der Anhang nicht selbst escapt werden muss.
KUERZUNG_ANHANG = "...(gekuerzt)"


def _escapen(zeichen: str) -> str:
    """Ein Zeichen als sichtbare ASCII-Folge -- oder unveraendert, wenn es
    ohnehin harmloses druckbares ASCII ist.

    Die Regel ist absichtlich eine einzige: alles ausserhalb 0x20-0x7E wird
    escapt. Damit fallen Bidi-Steuerung (U+202A-U+202E), Isolate
    (U+2066-U+2069), Nullbreiten- und Formatzeichen (U+200B-U+200F, U+FEFF),
    Zeilen-/Absatztrenner (U+2028/U+2029), `\\n`, `\\r`, `\\t`, ESC (und mit
    ihm jede ANSI-Sequenz) sowie saemtliche Zeichen der Kategorien Cc, Cf,
    Cs, Co und Cn unter dieselbe Behandlung -- ohne Einzelliste, die
    veralten koennte. Der Backslash wird verdoppelt, damit ein eingegebenes
    `\\u202e` von einem escapten Bidi-Zeichen unterscheidbar bleibt.
    """
    if zeichen == "\\":
        return "\\\\"
    cp = ord(zeichen)
    if 0x20 <= cp <= 0x7E:
        return zeichen
    if cp <= 0xFFFF:
        return f"\\u{cp:04X}"
    return f"\\U{cp:08X}"


def wert_saeubern(wert: str, *, max_laenge: int = MAX_WERT_LAENGE) -> str:
    """Ein Parameterwert, sicher darstellbar. Rueckgabe OHNE
    Anfuehrungszeichen, reines ASCII.

    Reihenfolge: erst NFC-normalisieren (sonst escapt man Zeichen, die nach
    der Normalisierung anders aussehen), dann sichtbar escapen, dann auf
    `max_laenge` sichtbare Zeichen der **Ausgabe** kuerzen -- sonst sprengt
    ein Wert aus lauter Escapes die Zeile trotzdem.
    """
    if not isinstance(wert, str):
        raise VorschauFehler(f"Zeichenkette erwartet, war {type(wert).__name__}")
    if max_laenge < len(KUERZUNG_ANHANG) + 1:
        raise VorschauFehler(f"max_laenge {max_laenge} zu klein")

    wert = unicodedata.normalize("NFC", wert)
    teile = [_escapen(z) for z in wert]
    ausgabe = "".join(teile)

    if len(ausgabe) > max_laenge:
        grenze = max_laenge - len(KUERZUNG_ANHANG)
        stuecke: list[str] = []
        n = 0
        for t in teile:
            # Niemals mitten in einer Escape-Folge schneiden.
            if n + len(t) > grenze:
                break
            stuecke.append(t)
            n += len(t)
        ausgabe = "".join(stuecke) + KUERZUNG_ANHANG

    if not ausgabe.isascii():
        # Unmoeglich nach dem Aufbau oben -- aber eine Zusicherung, die sich
        # selbst prueft, ueberlebt eine spaetere Aenderung.
        raise VorschauFehler("Ausgabe ist nicht reines ASCII")
    return ausgabe


# Unsichtbares und Steuerndes, nach Unicode-Kategorie statt nach Einzelliste
# -- dieselbe Ueberlegung wie bei `_escapen`: eine Liste veraltet, sobald
# Unicode waechst. Cc/Cf decken Bidi-Overrides (U+202A-U+202E), Isolate
# (U+2066-U+2069), Nullbreiten (U+200B-U+200F, U+FEFF) und ANSI-Sequenzen ab;
# Zl/Zp die Zeilen- und Absatztrenner; Cs/Co/Cn Surrogate, Privatgebrauch und
# Unzugewiesenes.
_UNSICHTBAR = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

MAX_ANZEIGE_LAENGE = 240        # eine Sprechblase, nicht ein Textfenster


def anzeige_saeubern(wert: str, *, max_laenge: int = MAX_ANZEIGE_LAENGE) -> str:
    r"""Untrusted Text, sicher **und lesbar** fuer eine reine Anzeige.

    Das dritte Profil neben `wert_saeubern` (escapen) und
    `sprechtext.pruefe` (fuer die Stimme entfernen). Es gibt es, weil die
    Sprechblase seit T-1.7 durch `wert_saeubern` lief und damit die
    Abwaegung der **Aktionsvorschau** geerbt hat: dort ist `ä` statt
    `ä` ausdruecklich gewollt, weil eine Bestaetigung wertlos ist, wenn man
    einen verwechselbaren Codepunkt nicht sieht. Eine Sprechblase
    bestaetigt nichts. Sie war dadurch stellenweise schlicht unlesbar --
    „Prüfstand" statt „Prüfstand", und in einem deutschen Satz ist
    das jedes dritte Wort.

    Was bleibt: alles Druckbare, auch Umlaute, Gedankenstriche und
    typografische Anfuehrungszeichen. Was geht: Unsichtbares und Steuerndes
    (siehe `_UNSICHTBAR`), **entfernt statt escapt** -- ein escapter
    Bidi-Override waere hier wieder unlesbarer Text, und entfernt kann er
    nichts mehr drehen. Zeilenumbrueche und Tabulatoren werden zu
    Leerzeichen, mehrfacher Leerraum zu einem.

    DIE ABWAEGUNG, AUSDRUECKLICH: verwechselbare Glyphen (kyrillisches `а`
    fuer lateinisches `a`) kommen damit in der Blase durch. Das ist
    hinnehmbar, WEIL die Blase keine Senke fuer eine Entscheidung ist --
    sie zeigt an, sie fragt nicht. Jede Stelle, an der der Nutzer auf
    Grundlage des Textes zustimmt, benutzt weiter `wert_saeubern`; das ist
    die Aktionsvorschau, und die bleibt unveraendert.
    """
    if not isinstance(wert, str):
        raise VorschauFehler(f"Zeichenkette erwartet, war {type(wert).__name__}")
    if max_laenge < len(KUERZUNG_ANHANG) + 1:
        raise VorschauFehler(f"max_laenge {max_laenge} zu klein")

    # NFC zuerst, dann pruefen -- dieselbe Reihenfolge und derselbe Grund wie
    # bei `wert_saeubern`: sonst filtert man Zeichen, die nach der
    # Normalisierung andere sind.
    wert = unicodedata.normalize("NFC", wert)
    ausgabe = "".join(
        " " if z in "\n\r\t" else z
        for z in wert
        if z in "\n\r\t" or unicodedata.category(z) not in _UNSICHTBAR
    )
    ausgabe = " ".join(ausgabe.split())
    if len(ausgabe) > max_laenge:
        ausgabe = ausgabe[: max_laenge - len(KUERZUNG_ANHANG)] + KUERZUNG_ANHANG
    return ausgabe


def pfad_saeubern(pfad: str, *, max_laenge: int = MAX_WERT_LAENGE) -> str:
    """Wie `wert_saeubern`, aber fuer Zielpfade.

    Ein fuehrendes `~` bleibt stehen, und der Pfad wird **nicht** aufgeloest
    (kein `expanduser`, kein `realpath`): die Vorschau zeigt, was ausgefuehrt
    wird, nicht was das Dateisystem daraus macht.
    """
    return wert_saeubern(pfad, max_laenge=max_laenge)


# ---------------------------------------------------------------------------
# Feste Beschriftungen. `aktion` und `umkehr` sind Schluessel in diese
# Tabellen, keine freien Zeichenketten: wenn der Aufrufer keinen Text
# uebergeben *kann*, kann auch kein Modelltext in die Vorschau geraten.
# Umlaute sind hier erlaubt -- die Texte stammen aus dieser Datei, nicht aus
# einem Request.
# ---------------------------------------------------------------------------

AKTIONS_BESCHRIFTUNGEN: dict[str, str] = {
    "datei.papierkorb": "Datei in den Papierkorb verschieben",
    "datei.loeschen": "Datei unwiderruflich löschen",
    "datei.verschieben": "Datei verschieben",
    "befehl.ausfuehren": "Befehl ausführen",
    "prozess.beenden": "Prozess beenden",
    # config/actions/core.yaml -- der Katalog, den der Hub im Betrieb
    # tatsaechlich anfragt (Befund T-4.12 K6: ohne diese Eintraege faellt
    # `Hub._vorschau_bauen` auf einen selbstgebauten Text mit dem rohen
    # Aktionsschluessel zurueck, statt diese Vorlage zu nutzen).
    "media.playpause": "Wiedergabe pausieren oder fortsetzen",
    "media.next": "Zum naechsten Titel springen",
    "media.previous": "Zum vorigen Titel springen",
    "media.stop": "Wiedergabe stoppen",
    "media.seek.forward": "5 Sekunden vorspulen",
    "media.seek.backward": "5 Sekunden zurueckspulen",
    "audio.volume.up": "Lautstaerke erhoehen",
    "audio.volume.down": "Lautstaerke verringern",
    "audio.mute.toggle": "Stummschaltung umschalten",
    "audio.volume.set": "Lautstaerke auf einen Wert setzen",
    "kde.window.raise": "Fenster in den Vordergrund holen",
    "desktop.next": "Zur naechsten Arbeitsflaeche wechseln",
    "desktop.previous": "Zur vorigen Arbeitsflaeche wechseln",
    "window.to_next_desktop": "Fenster auf die naechste Arbeitsflaeche verschieben",
    "window.to_previous_desktop": "Fenster auf die vorige Arbeitsflaeche verschieben",
    "screenshot.fullscreen": "Bildschirmfoto des ganzen Schirms erstellen",
    "screenshot.region": "Bildschirmfoto eines Bereichs erstellen",
    # T-4.18 F-01 (21.08., high): T-4.16 K2 hat diese drei am 21.08. in den
    # Katalog aufgenommen, die Tabelle hier blieb stehen -- der Dialog fiel
    # auf "Ember will eine unbekannte Aktion ausfuehren. Bitte ablehnen."
    # zurueck und nannte damit nie, was er bestaetigen liess.
    "fs.file.read": "Datei lesen",
    "exec.launch.kcalc": "Taschenrechner starten",
    "input.type.kcalc": "Tastenfolge an den Taschenrechner senden",
}

UMKEHR_BESCHRIFTUNGEN: dict[str, str] = {
    "papierkorb": "möglich (Papierkorb)",
    "git": "möglich (git)",
    "keine": "nicht möglich",
    # Der Katalogeintrag nennt eine Gegenaktion (`reversible_by`), die
    # dieselbe Wirkung aufhebt -- z. B. media.next/media.previous.
    "gegenaktion": "möglich (Gegenaktion)",
    # Kein `reversible_by` und nicht destruktiv: die Aktion aendert keinen
    # Zustand, der eine Umkehr braucht (z. B. ein Bildschirmfoto).
    "unkritisch": "nicht nötig (folgenlos)",
}

_VORLAGE = (
    "Ember will ausführen:\n"
    "  Aktion:  {aktion}\n"
    "  Ziel:    \"{ziel}\"\n"
    "  Umkehr:  {umkehr}\n"
)


def vorschau(*, aktion: str, ziel: object, umkehr: str) -> str:
    """Die fertige, mehrzeilige Vorschau nach der festen Vorlage.

    `aktion` und `umkehr` sind Schluessel in `AKTIONS_BESCHRIFTUNGEN` bzw.
    `UMKEHR_BESCHRIFTUNGEN`; ein unbekannter Schluessel ist VorschauFehler.
    Nur `ziel` ist ein Parameterwert und geht durch `pfad_saeubern`, sichtbar
    in Anfuehrungszeichen.
    """
    try:
        aktion_text = AKTIONS_BESCHRIFTUNGEN[aktion]
    except KeyError:
        raise VorschauFehler(f"unbekannter Aktionsschluessel {aktion!r}") from None
    try:
        umkehr_text = UMKEHR_BESCHRIFTUNGEN[umkehr]
    except KeyError:
        raise VorschauFehler(f"unbekannter Umkehrschluessel {umkehr!r}") from None

    # T-3.13b: die Vorschau ist eine SENKE. `tainted` darf hinein -- aber nur
    # escapt und laengenbegrenzt, und genau das tut `pfad_saeubern()` seit
    # T-1.7. Neu ist die Pruefung, DASS es passiert ist: `user_audio` hat hier
    # nichts verloren (Design 5.2), und ein roher `str` gilt als `tainted`,
    # ohne zu werfen -- T-1.7 ist eingefroren und ruft mit rohen Zeichenketten
    # auf, und eine Zusage gegen ihre eigenen Waechter durchzusetzen waere die
    # falsche Reihenfolge.
    geprueft = pruefe_senke(ziel, senke="auth_vorschau")
    return _VORLAGE.format(aktion=aktion_text,
                           ziel=pfad_saeubern(str(geprueft.value)),
                           umkehr=umkehr_text)
