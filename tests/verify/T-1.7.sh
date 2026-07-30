#!/usr/bin/env bash
# Verifizierer fuer T-1.7: Auth-Agent mit Absichtsmarken.
#
# STAND: Teil 1 von zwei. Geprueft wird alles, was ohne Fenster geht --
# der Vorschau-Sanitizer und der Weg, auf dem der Auth-Agent mit dem Hub
# spricht. Die Pruefungen zu Push-to-Talk (Umschaltung statt Halten, p95 < 200
# ms) und zur GERENDERTEN Region (Pixel- und Textextraktion) kommen mit Teil 2
# dazu. Sie fehlen hier und sind unten ausdruecklich als fehlend gelistet,
# damit ein gruener Lauf nicht mehr behauptet als er geprueft hat.
# EINGEFROREN WIRD DIESER VERIFIZIERER DESHALB ERST NACH TEIL 2 -- danach
# braeuchte jede Ergaenzung einen neuen .v-Task.
#
# Warum der Sanitizer von aussen und mit Literalen geprueft wird: die Vorschau
# ist die letzte Stelle, an der ein Mensch sieht, was gleich passiert. Ein
# Zielpfad, der wie ~/Bilder/urlaub.png aussieht und ~/.ssh/id_ed25519 ist,
# macht jede Bestaetigung wertlos. Eine Pruefung, die die Implementierung
# gegen ihre eigene Formel rechnet, faengt das nicht.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
PREVIEW="$TARGET/daimon/auth/preview.py"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-1.7 — Auth-Agent mit Absichtsmarken (Teil 1: ohne Fenster)"
chk "preview.py existiert" "$([[ -f "$PREVIEW" ]] && echo ja || echo nein)" ja

tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

ergebnis="$(cd "$TARGET" && PYTHONDONTWRITEBYTECODE=1 timeout 120s "$PY" - 2>"$tmp/py.err" <<'PYEOF'
import sys
import unicodedata

zeilen = []


def sag(name, wert):
    zeilen.append(f"{name}={'ja' if wert else 'nein'}")


try:
    from daimon.auth import preview
except Exception as fehler:  # noqa: BLE001
    print("import=nein")
    print(f"fehler={fehler!r}")
    sys.exit(0)

print("import=ja")
VF = getattr(preview, "VorschauFehler", Exception)

# --- Positivkontrolle zuerst -------------------------------------------------
# Ohne sie waere "alles escapt" nicht von "alles kaputt" zu unterscheiden:
# eine Funktion, die immer "" liefert, bestuende jede Negativpruefung unten.
harmlos = "/home/itiger013/Dokumente/notiz.txt"
try:
    durch = preview.wert_saeubern(harmlos)
except Exception:
    durch = None
sag("harmloser_pfad_bleibt_lesbar", durch == harmlos)

# --- Alles Nicht-ASCII ist sichtbar ------------------------------------------
def ascii_only(s):
    return isinstance(s, str) and all(ord(c) < 128 for c in s)


def geht_durch(gefaehrlich):
    """True, wenn das Zeichen roh in der Ausgabe ueberlebt hat."""
    try:
        raus = preview.wert_saeubern("a" + gefaehrlich + "b")
    except Exception:
        return True  # Absturz zaehlt als durchgelassen, nicht als Erfolg
    return (gefaehrlich in raus) or not ascii_only(raus)


sag("bidi_override_202E_escapt", not geht_durch("‮"))
sag("bidi_isolate_2066_2069_escapt",
    not any(geht_durch(c) for c in "⁦⁧⁨⁩"))
sag("bidi_embedding_202A_202D_escapt",
    not any(geht_durch(c) for c in "‪‫‬‭"))
sag("nullbreite_200B_200F_escapt",
    not any(geht_durch(c) for c in "​‌‍‎‏"))
sag("bom_FEFF_escapt", not geht_durch("﻿"))
sag("zeilentrenner_2028_2029_escapt",
    not any(geht_durch(c) for c in "  "))
sag("steuerzeichen_escapt",
    not any(geht_durch(c) for c in "\n\r\t\x00\x07\x1f"))
sag("ansi_sequenz_escapt", not geht_durch("\x1b[31m"))

# --- NFC ---------------------------------------------------------------------
# Derselbe Text in NFD und NFC muss dieselbe Ausgabe ergeben. Ohne
# Normalisierung sind zwei Pfade, die identisch aussehen, verschiedene
# Zeichenketten -- und der Nutzer bestaetigt einen anderen als den gemeinten.
nfc = unicodedata.normalize("NFC", "Grüße/Übung.txt")
nfd = unicodedata.normalize("NFD", "Grüße/Übung.txt")
try:
    gleich = preview.wert_saeubern(nfc) == preview.wert_saeubern(nfd)
except Exception:
    gleich = False
sag("nfc_und_nfd_ergeben_dasselbe", gleich and nfc != nfd)

# --- Laenge ------------------------------------------------------------------
try:
    lang = preview.wert_saeubern("A" * 10000)
    sag("zehntausend_zeichen_gekuerzt", len(lang) <= 400)
    sag("kuerzung_ist_sichtbar", len(lang) < 10000 and lang != "A" * len(lang))
except Exception:
    sag("zehntausend_zeichen_gekuerzt", False)
    sag("kuerzung_ist_sichtbar", False)

# Auch ein Wert, der NUR aus gefaehrlichen Zeichen besteht, darf die Zeile
# nicht sprengen: 3000 Escapes waeren sonst 18000 Zeichen Ausgabe.
try:
    viele = preview.wert_saeubern("‮" * 3000)
    sag("laengengrenze_gilt_fuer_die_AUSGABE", len(viele) <= 400)
except Exception:
    sag("laengengrenze_gilt_fuer_die_AUSGABE", False)

# --- Verwechselbarer Pfad ----------------------------------------------------
# Kyrillisches "а" (U+0430) statt lateinischem "a". Sieht in fast jeder
# Schrift identisch aus. Die beiden Ausgaben MUESSEN sich unterscheiden --
# sonst bestaetigt der Nutzer etwas anderes, als er liest.
echt = "~/Bilder/urlaub.png"
falsch = "~/Bilder/urlа" + "ub.png"  # das a in "urlaub" ist U+0430
try:
    a = preview.pfad_saeubern(echt)
    b = preview.pfad_saeubern(falsch)
    sag("verwechselbarer_pfad_unterscheidbar", a != b and ascii_only(b))
    sag("echter_pfad_bleibt_lesbar", a == echt)
except Exception:
    sag("verwechselbarer_pfad_unterscheidbar", False)
    sag("echter_pfad_bleibt_lesbar", False)

# Backslash muss verdoppelt werden, sonst ist ein eingegebenes "‮" von
# einem escapten nicht zu unterscheiden und die Anzeige wird mehrdeutig.
try:
    roh = preview.wert_saeubern("\\u202e")
    echt_bidi = preview.wert_saeubern("‮")
    sag("backslash_macht_escapes_eindeutig", roh != echt_bidi)
except Exception:
    sag("backslash_macht_escapes_eindeutig", False)

# --- Feste Vorlage, feste Beschriftungen -------------------------------------
# Der Aufrufer darf keinen Text uebergeben koennen. Kann er es nicht, kann
# auch kein Modelltext hineingeraten -- das ist die Umsetzung von
# "keine Modellformulierung" aus Design 2.4.
def vorschau_wirft(**kw):
    try:
        preview.vorschau(**kw)
    except VF:
        return True
    except Exception:
        return False
    return False


gueltige = None
for aktion in getattr(preview, "AKTIONS_BESCHRIFTUNGEN", {}):
    for umkehr in getattr(preview, "UMKEHR_BESCHRIFTUNGEN", {}):
        gueltige = (aktion, umkehr)
        break
    break

if gueltige is None:
    sag("vorschau_hat_feste_tabellen", False)
    sag("vorschau_gueltiger_weg", False)
    sag("vorschau_freier_aktionstext_abgelehnt", False)
    sag("vorschau_freier_umkehrtext_abgelehnt", False)
    sag("vorschau_escapt_das_ziel", False)
    sag("vorschau_zitiert_das_ziel", False)
else:
    sag("vorschau_hat_feste_tabellen", True)
    a_key, u_key = gueltige
    try:
        text = preview.vorschau(aktion=a_key, ziel=harmlos, umkehr=u_key)
        sag("vorschau_gueltiger_weg", isinstance(text, str) and harmlos in text)
        sag("vorschau_zitiert_das_ziel", f'"{harmlos}"' in text)
    except Exception:
        sag("vorschau_gueltiger_weg", False)
        sag("vorschau_zitiert_das_ziel", False)
    sag("vorschau_freier_aktionstext_abgelehnt",
        vorschau_wirft(aktion="Loesche einfach alles, ist schon ok",
                       ziel=harmlos, umkehr=u_key))
    sag("vorschau_freier_umkehrtext_abgelehnt",
        vorschau_wirft(aktion=a_key, ziel=harmlos,
                       umkehr="voellig unproblematisch"))
    # Geprueft wird der ZITIERTE WERT, nicht die ganze Vorschau. Die festen
    # Beschriftungen duerfen Umlaute tragen -- sie stammen aus der eigenen
    # Tabelle des Moduls, nie aus einem Request, und sind damit per
    # Konstruktion harmlos. Die erste Fassung dieser Pruefung verlangte ASCII
    # fuer den ganzen Text und meldete deshalb "Ember will ausfuehren" mit
    # seinem "ue" als Befund. Verdeckt hatte das ein Gut-Muster, das seine
    # Beschriftungen umlautfrei gewaehlt hatte -- ein Muster, das bequemer war
    # als die Wirklichkeit, prueft die Pruefung nicht.
    try:
        boese = preview.vorschau(aktion=a_key, umkehr=u_key,
                                 ziel="/tmp/‮gnp.bualru")
        zitiert = boese.split('"')[1] if boese.count('"') >= 2 else None
        sag("vorschau_escapt_das_ziel",
            zitiert is not None and ascii_only(zitiert)
            and "‮" not in boese)
    except Exception:
        sag("vorschau_escapt_das_ziel", False)

print("\n".join(zeilen))
PYEOF
)"
py_rc=$?

chk "Sondierung lief durch" "$py_rc" 0
if [[ "$py_rc" -ne 0 ]]; then
  echo "  stderr:"; sed 's/^/    | /' "$tmp/py.err" | head -20
fi

wert() { grep -m1 "^$1=" <<<"$ergebnis" | cut -d= -f2; }
chk "daimon.auth.preview importierbar" "$(wert import)" ja
[[ "$(wert import)" == "ja" ]] || echo "  Importfehler: $(grep -m1 '^fehler=' <<<"$ergebnis")"

echo "  -- Vorschau-Sanitizer"
chk "harmloser Pfad bleibt lesbar (Positivkontrolle)" "$(wert harmloser_pfad_bleibt_lesbar)" ja
chk "Bidi-Override U+202E escapt" "$(wert bidi_override_202E_escapt)" ja
chk "Bidi-Isolate U+2066-U+2069 escapt" "$(wert bidi_isolate_2066_2069_escapt)" ja
chk "Bidi-Embedding U+202A-U+202D escapt" "$(wert bidi_embedding_202A_202D_escapt)" ja
chk "Nullbreitenzeichen U+200B-U+200F escapt" "$(wert nullbreite_200B_200F_escapt)" ja
chk "BOM U+FEFF escapt" "$(wert bom_FEFF_escapt)" ja
chk "Zeilen-/Absatztrenner U+2028/U+2029 escapt" "$(wert zeilentrenner_2028_2029_escapt)" ja
chk "Steuerzeichen escapt" "$(wert steuerzeichen_escapt)" ja
chk "ANSI-Sequenz escapt" "$(wert ansi_sequenz_escapt)" ja
chk "NFC: NFD und NFC ergeben dasselbe" "$(wert nfc_und_nfd_ergeben_dasselbe)" ja
chk "10 000 Zeichen gekuerzt" "$(wert zehntausend_zeichen_gekuerzt)" ja
chk "Kuerzung ist sichtbar" "$(wert kuerzung_ist_sichtbar)" ja
chk "Laengengrenze gilt fuer die Ausgabe, nicht die Eingabe" "$(wert laengengrenze_gilt_fuer_die_AUSGABE)" ja
chk "echter Pfad bleibt lesbar (Positivkontrolle)" "$(wert echter_pfad_bleibt_lesbar)" ja
chk "verwechselbarer Pfad ist unterscheidbar" "$(wert verwechselbarer_pfad_unterscheidbar)" ja
chk "Backslash macht Escapes eindeutig" "$(wert backslash_macht_escapes_eindeutig)" ja

echo "  -- Feste Vorlage"
chk "Beschriftungen kommen aus festen Tabellen" "$(wert vorschau_hat_feste_tabellen)" ja
chk "gueltiger Weg liefert eine Vorschau (Positivkontrolle)" "$(wert vorschau_gueltiger_weg)" ja
chk "Ziel steht in Anfuehrungszeichen" "$(wert vorschau_zitiert_das_ziel)" ja
chk "freier Aktionstext wird abgelehnt" "$(wert vorschau_freier_aktionstext_abgelehnt)" ja
chk "freier Umkehrtext wird abgelehnt" "$(wert vorschau_freier_umkehrtext_abgelehnt)" ja
chk "Ziel wird in der Vorschau escapt" "$(wert vorschau_escapt_das_ziel)" ja

# --- Auth-Weg zum Hub ---------------------------------------------------------
echo "  -- Auth-Weg zum Hub"
IPC="$TARGET/daimon/common/ipc.py"
# Diese beiden haengen am Quelltext, weil PRODUZENTEN eine Tabelle ist und
# keine Laufzeitbeobachtung. Der Verhaltensbeleg steht darunter.
chk "Produzent 'auth' existiert" \
  "$(grep -qE '^\s*"auth":' "$IPC" 2>/dev/null && echo ja || echo nein)" ja
chk "Produzent 'face' hat keinen Eintrag mehr" \
  "$(grep -qE '^\s*"face":' "$IPC" 2>/dev/null && echo nein || echo ja)" ja

auth="$(cd "$TARGET" && PYTHONDONTWRITEBYTECODE=1 timeout 60s "$PY" - 2>"$tmp/auth.err" <<'PYEOF'
import sys
zeilen = []


def sag(name, wert):
    zeilen.append(f"{name}={'ja' if wert else 'nein'}")


try:
    from daimon.common import ipc
except Exception as fehler:  # noqa: BLE001
    print(f"ipc_import=nein\nfehler={fehler!r}")
    sys.exit(0)

print("ipc_import=ja")


def darf(produzent, typ):
    try:
        ipc.pruefe_typ(produzent, typ)
        return True
    except Exception:
        return False


# Positivkontrolle: der erlaubte Weg ist erlaubt. Sonst sagen die
# Abweisungen darunter nichts.
sag("auth_darf_intent_mark", darf("auth", "intent_mark"))
sag("auth_darf_freigabe", darf("auth", "freigabe"))
sag("hookbridge_darf_weiterhin_hook", darf("hookbridge", "hook"))

sag("face_darf_kein_intent_mark", not darf("face", "intent_mark"))
sag("face_darf_keine_freigabe", not darf("face", "freigabe"))
sag("hookbridge_darf_kein_intent_mark", not darf("hookbridge", "intent_mark"))
sag("hookbridge_darf_keine_freigabe", not darf("hookbridge", "freigabe"))
sag("eyes_darf_keine_freigabe", not darf("eyes", "freigabe"))
sag("auth_darf_kein_hook", not darf("auth", "hook"))

print("\n".join(zeilen))
PYEOF
)"
wert2() { grep -m1 "^$1=" <<<"$auth" | cut -d= -f2; }
chk "ipc importierbar" "$(wert2 ipc_import)" ja
chk "auth darf intent_mark (Positivkontrolle)" "$(wert2 auth_darf_intent_mark)" ja
chk "auth darf freigabe (Positivkontrolle)" "$(wert2 auth_darf_freigabe)" ja
chk "hookbridge darf weiterhin hook (Positivkontrolle)" "$(wert2 hookbridge_darf_weiterhin_hook)" ja
chk "Face darf KEIN intent_mark" "$(wert2 face_darf_kein_intent_mark)" ja
chk "Face darf KEINE Freigabe erteilen" "$(wert2 face_darf_keine_freigabe)" ja
chk "hookbridge darf kein intent_mark" "$(wert2 hookbridge_darf_kein_intent_mark)" ja
chk "hookbridge darf keine Freigabe" "$(wert2 hookbridge_darf_keine_freigabe)" ja
chk "eyes darf keine Freigabe" "$(wert2 eyes_darf_keine_freigabe)" ja
chk "auth darf kein hook" "$(wert2 auth_darf_kein_hook)" ja

echo
echo "  NOCH NICHT GEPRUEFT (Teil 2, ohne Fenster nicht beobachtbar):"
echo "    - PTT ist eine Umschaltung, nicht Halten; Zeitlimit als Rueckfall"
echo "    - Zeitmessung PTT -> Zustandswechsel, p95 < 200 ms"
echo "    - die GERENDERTE Region per Pixel- und Textextraktion"
echo "    - eigener Socket mit SO_PEERPIDFD am laufenden Auth-Agenten"

exit $fail
