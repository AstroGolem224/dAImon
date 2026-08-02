#!/usr/bin/env bash
# Verifizierer fuer T-1.7: Auth-Agent mit Absichtsmarken.
#
# Deckt T-1.7 vollstaendig ab: Teil 1 (Sanitizer, Auth-Weg) und Teil 2
# (PTT-Umschaltautomat, gerenderter Dialog, systemd-Unit).
#
# Die Pixelprobe laeuft NICHT ueber grim: KWin implementiert wlr-screencopy
# nicht, und org.kde.KWin.ScreenShot2 weist einen beliebigen python3 mit
# "not authorized to take a screenshot" ab. spectacle ist autorisiert.
#
#
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

echo "T-1.7 — Auth-Agent mit Absichtsmarken"
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
# T-1.7 verlangte urspruenglich, dass `face` GAR KEINEN Eintrag hat. Seit
# T-2.2 hat es einen -- aber einen engen: die Blase, die der Nutzer
# weggeklickt hat, darf gemeldet werden. Die Grenze, um die es T-1.7 ging,
# ist damit unveraendert: keine Absichtsmarke, keine Freigabe.
#
# Geprueft wird deshalb die MENGE, nicht die Abwesenheit. Ein Textvergleich
# auf `"face":` waere ausserdem an Anfuehrungszeichen zu umgehen -- genau das
# ist beim Bau von T-2.2 passiert, und es ist der Grund fuer diese Fassung
# (T-1.7.v3).
#
# T-1.7.v4: Seit T-2.7 darf das Face zusaetzlich `wahrnehmung_aus` senden --
# den Wahrnehmungs-Kill-Switch aus dem Kontextmenue. Diese Erweiterung ist
# eine bewusste Entscheidung, keine Aufweichung durch die Hintertuer: sie ist
# EINSEITIG (es gibt kein Gegenstueck zum Einschalten, in KEINEM Produzenten)
# und sie benennt kein Ziel selbst (der Unit-Name kommt aus der Konfiguration
# des Hubs, nie aus der Nachricht -- das prueft T-2.7.sh am laufenden Hub).
#
# Geprueft wird hier eine OBERGRENZE, keine exakte Menge. Zwei Gruende:
# das Gut-Muster stammt aus der Zeit vor T-2.2 und hat gar keinen
# face-Eintrag, und die Sicherheitsfrage lautet ohnehin "waechst die Menge?",
# nicht "steht genau das Erwartete drin". Die exakte Menge prueft T-2.7.sh.
ERLAUBT_FUER_FACE="bubble_dismiss wahrnehmung_aus"
face_menge="$(cd "$TARGET" && "$PY" -c '
from daimon.common import ipc
print(",".join(sorted(ipc.PRODUZENTEN.get("face", []))))' 2>/dev/null)"
echo "  face darf senden: ${face_menge:-<nichts>}"
ueber_der_grenze=""
for t in ${face_menge//,/ }; do
  case " $ERLAUBT_FUER_FACE " in *" $t "*) ;; *) ueber_der_grenze="$ueber_der_grenze $t";; esac
done
chk "face sendet nichts ausserhalb {bubble_dismiss, wahrnehmung_aus}" \
  "${ueber_der_grenze:-<nichts>}" "<nichts>"

# Die Einseitigkeit, und zwar fuer ALLE Produzenten. Das ist die eigentliche
# Zusage hinter T-2.7: der schlimmste Fall eines uebernommenen Overlays ist,
# dass Wahrnehmung AUSGEHT. Gaebe es irgendwo ein Gegenstueck, waere aus dem
# Kill-Switch ein Schalter geworden -- und ein Schalter laesst sich auch
# gegen den Nutzer umlegen.
einschalt="$(cd "$TARGET" && "$PY" -c '
from daimon.common import ipc
verdaechtig = ("wahrnehmung_an", "wahrnehmung_ein", "wahrnehmung_start",
               "perception_on", "unit_start", "unit_starten", "ears_an", "eyes_an")
treffer = [f"{p}:{t}" for p, m in ipc.PRODUZENTEN.items() for t in m if t in verdaechtig]
print(",".join(sorted(treffer)))' 2>/dev/null)"
chk "kein Produzent darf Wahrnehmung EINSCHALTEN" "${einschalt:-<nichts>}" "<nichts>"

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

# --- Push-to-Talk: Umschaltung, nicht Halten ---------------------------------
# Der Automat ist bewusst von der Tastenbindung getrennt und reines stdlib --
# nur deshalb ist dieses Kriterium ohne Plasma-Sitzung und ohne synthetische
# Tastendruecke pruefbar. (ydotool positioniert auf dieser Maschine
# nachweislich nicht; auf echte Eingabe zu bauen waere hier eine Messung des
# Zufalls gewesen.)
echo "  -- Push-to-Talk"
ptt="$(cd "$TARGET" && PYTHONDONTWRITEBYTECODE=1 timeout 60s "$PY" - 2>"$tmp/ptt.err" <<'PYEOF'
import sys
zeilen = []


def sag(name, wert):
    zeilen.append(f"{name}={'ja' if wert else 'nein'}")


class Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def vor(self, s):
        self.t += s


class Protokoll:
    def __init__(self):
        self.zeilen = []

    def _merken(self, *a, **kw):
        self.zeilen.append((a, kw))

    info = warning = error = debug = _merken


try:
    from daimon.auth import ptt as modul
except Exception as fehler:  # noqa: BLE001
    print(f"ptt_import=nein\nfehler={fehler!r}")
    sys.exit(0)

print("ptt_import=ja")

# Der Automat darf gi NICHT hereinziehen -- sonst laeuft er nicht im venv,
# und genau dafuer wurde er abgetrennt.
sag("ptt_ohne_gi", "gi" not in sys.modules)

uhr, log = Uhr(), Protokoll()
a = modul.PTTAutomat(zeitlimit_s=100.0, jetzt=uhr, log=log)

# Positivkontrolle: einmal umschalten macht ihn aktiv. Ohne sie waere
# "nach zweimal aus" auch mit einem Automaten gruen, der nie angeht.
sag("ptt_einmal_ist_an", a.umschalten() is True and a.ist_aktiv())
# DAS Kriterium: Umschaltung, nicht Halten.
sag("ptt_zweimal_ist_aus", a.umschalten() is False and not a.ist_aktiv())

# Zeitlimit als Rueckfall. Ohne verlaessliches Loslassen ist es der einzige
# Weg, ein offenes Mikrofonfenster wieder zu schliessen -- ein PTT, das
# versehentlich anbleibt, waere der Dauermitschnitt, den 1.1 ausschliesst.
a.umschalten()
sag("ptt_vor_ablauf_noch_aktiv", a.ist_aktiv())
uhr.vor(101.0)
sag("ptt_zeitlimit_schaltet_ab", not a.ist_aktiv())
# Nach dem Ablauf muss die naechste Umschaltung wieder ANschalten, nicht aus.
sag("ptt_nach_ablauf_wieder_anschaltbar", a.umschalten() is True)

# Eine Auskunft veraendert nichts.
b = modul.PTTAutomat(zeitlimit_s=100.0, jetzt=Uhr(), log=Protokoll())
b.umschalten()
vorher = b.ist_aktiv()
for _ in range(5):
    b.ist_aktiv()
sag("ptt_auskunft_veraendert_nichts", vorher and b.ist_aktiv())

sag("ptt_audit_nichtleer", len(log.zeilen) > 0)
print("\n".join(zeilen))
PYEOF
)"
wert3() { grep -m1 "^$1=" <<<"$ptt" | cut -d= -f2; }
chk "daimon.auth.ptt importierbar" "$(wert3 ptt_import)" ja
[[ "$(wert3 ptt_import)" == "ja" ]] || echo "  Importfehler: $(grep -m1 '^fehler=' <<<"$ptt")"
chk "Automat zieht kein gi herein" "$(wert3 ptt_ohne_gi)" ja
chk "einmal umschalten ist an (Positivkontrolle)" "$(wert3 ptt_einmal_ist_an)" ja
chk "zweimal umschalten ist AUS (Umschaltung, nicht Halten)" "$(wert3 ptt_zweimal_ist_aus)" ja
chk "vor Ablauf noch aktiv (Positivkontrolle)" "$(wert3 ptt_vor_ablauf_noch_aktiv)" ja
chk "Zeitlimit schaltet ab" "$(wert3 ptt_zeitlimit_schaltet_ab)" ja
chk "nach Ablauf wieder anschaltbar" "$(wert3 ptt_nach_ablauf_wieder_anschaltbar)" ja
chk "Auskunft veraendert nichts" "$(wert3 ptt_auskunft_veraendert_nichts)" ja
chk "Audit nichtleer" "$(wert3 ptt_audit_nichtleer)" ja

# --- Die GERENDERTE Region ----------------------------------------------------
# Ab hier wird der echte Agent gestartet. Nur gegen das echte Repo: ein
# Fixture ist ein Ersatzbaum ohne GTK-Prozess.
if [[ "$TARGET" != "$REPO" ]]; then
  echo "  INFO Fixture-Lauf: Fenster-, OCR- und Live-Pruefungen uebersprungen"
  exit $fail
fi

echo "  -- Der gerenderte Dialog"
AGENT="$REPO/daimon/auth/agent.py"
SYSPY="/usr/bin/python3"
chk "agent.py existiert" "$([[ -f "$AGENT" ]] && echo ja || echo nein)" ja
chk "System-Python vorhanden" "$([[ -x "$SYSPY" ]] && echo ja || echo nein)" ja
# Nicht grim: KWin implementiert wlr-screencopy nicht ("compositor doesn't
# support the screen capture protocol"). Und die native Schnittstelle
# org.kde.KWin.ScreenShot2 weist einen beliebigen python3 mit "The process is
# not authorized to take a screenshot" ab -- sie prueft das aufrufende
# Programm. spectacle ist autorisiert; geschossen wird der ganze Schirm und
# anschliessend auf die AT-SPI-Geometrie zugeschnitten.
chk "spectacle vorhanden" "$(command -v spectacle >/dev/null && echo ja || echo nein)" ja
chk "magick vorhanden (Zuschnitt)" "$(command -v magick >/dev/null && echo ja || echo nein)" ja
chk "tesseract vorhanden" "$(command -v tesseract >/dev/null && echo ja || echo nein)" ja
chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja

agent_pid=""
aufraeumen_agent() { [[ -n "$agent_pid" ]] && kill "$agent_pid" 2>/dev/null; }
trap 'aufraeumen_agent; rm -rf -- "$tmp"' EXIT

if [[ -f "$AGENT" && -x "$SYSPY" && -n "${WAYLAND_DISPLAY:-}" ]]; then
  DAIMON_MAX_SECS=40 PYTHONPATH="$REPO" "$SYSPY" "$AGENT" \
    --diag-socket "$tmp/adiag.sock" --control-socket "$tmp/actl.sock" \
    --shortcut "" >"$tmp/agent.out" 2>"$tmp/agent.err" &
  agent_pid=$!

  bereit=nein
  for _ in $(seq 1 150); do
    [[ -S "$tmp/actl.sock" && -S "$tmp/adiag.sock" ]] && { bereit=ja; break; }
    kill -0 "$agent_pid" 2>/dev/null || break
    sleep 0.1
  done
  chk "Agent startet und legt beide Sockets an" "$bereit" ja
  chk "diag.sock hat Modus 0600" "$(stat -c '%a' "$tmp/adiag.sock" 2>/dev/null)" 600
  chk "control.sock hat Modus 0600" "$(stat -c '%a' "$tmp/actl.sock" 2>/dev/null)" 600


  actl() { "$PY" - "$tmp/actl.sock" "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
c.sendall(sys.argv[2].encode() + b"\n")
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
  }
  adiag() { "$PY" - "$tmp/adiag.sock" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
  }

  # Positivkontrolle des Steuerkanals: Unsinn muss abgewiesen werden, sonst
  # beweist ein "ok" auf einen echten Befehl nichts.
  chk "Steuer-Socket weist Unsinn mit err ab" "$(actl 'quatsch mit sosse')" err

  if [[ "$bereit" == ja ]]; then
    # Der boesartige Zielpfad: sieht aus wie ~/Bilder/urlaub.png, ist es aber
    # nicht. Base64, damit weder Shell noch Zeilenprotokoll ihn unterwegs
    # veraendern -- die Bidi- und Nullbreitenzeichen sind der ganze Punkt.
    ziel_b64="$("$PY" -c '
import base64
# U+202E dreht die Leserichtung, U+200B ist unsichtbar, das a ist kyrillisch.
boese = "~/Bilder/urlа​ub.png‮ gnp.5952de_di/hss./"
print(base64.b64encode(boese.encode()).decode())')"
    a_key="$("$PY" -c 'import sys; sys.path.insert(0,"'"$REPO"'")
from daimon.auth import preview; print(sorted(preview.AKTIONS_BESCHRIFTUNGEN)[0])')"
    u_key="$("$PY" -c 'import sys; sys.path.insert(0,"'"$REPO"'")
from daimon.auth import preview; print(sorted(preview.UMKEHR_BESCHRIFTUNGEN)[0])')"

    chk "unbekannter Aktionsschluessel wird abgewiesen" \
      "$(actl "zeige gibt.es.nicht $u_key $ziel_b64")" err
    chk "Dialog anzeigen wird bestaetigt" \
      "$(actl "zeige $a_key $u_key $ziel_b64")" ok
    sleep 1.5
    chk "Diagnose meldet den sichtbaren Dialog" \
      "$(adiag | jq -r '.dialog_sichtbar' 2>/dev/null)" true

    # --- AT-SPI: der Textbaum und die Fenstergeometrie ------------------------
    # GTK4 exportiert den Baum ohne Zutun -- anders als Qt, das laut T-1.11 im
    # Auslieferungszustand GAR KEINEN Baum liefert. Die Extents daraus sind
    # zugleich der Ausschnitt fuer den Screenshot: so wird genau das Fenster
    # geknipst und nicht der halbe Schirm.
    atspi="$("$SYSPY" - "$agent_pid" "$tmp/geom.txt" 2>"$tmp/atspi.err" <<'PYEOF'
import sys
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

pid = int(sys.argv[1])
Atspi.init()
texte = []
geom = None


def durchgehen(knoten, tiefe=0):
    global geom
    if tiefe > 12:
        return
    try:
        n = knoten.get_child_count()
    except Exception:
        return
    try:
        name = knoten.get_name() or ""
        if name.strip():
            texte.append(name)
        iface = knoten.get_text_iface()
        if iface is not None:
            texte.append(iface.get_text(0, iface.get_character_count()))
    except Exception:
        pass
    for i in range(n):
        try:
            durchgehen(knoten.get_child_at_index(i), tiefe + 1)
        except Exception:
            continue


desktop = Atspi.get_desktop(0)
for i in range(desktop.get_child_count()):
    try:
        app = desktop.get_child_at_index(i)
        if app.get_process_id() != pid:
            continue
    except Exception:
        continue
    for j in range(app.get_child_count()):
        try:
            fenster = app.get_child_at_index(j)
            komp = fenster.get_component_iface()
            if komp is not None and geom is None:
                e = komp.get_extents(Atspi.CoordType.SCREEN)
                if e.width > 0 and e.height > 0:
                    geom = (e.x, e.y, e.width, e.height)
        except Exception:
            pass
        durchgehen(fenster)

with open(sys.argv[2], "w", encoding="utf-8") as fh:
    if geom:
        fh.write("%d,%d %dx%d\n" % geom)
print("ATSPI_TEXT<<")
print("\n".join(texte))
print(">>ATSPI_TEXT")
PYEOF
)"
    baum="$(sed -n '/ATSPI_TEXT<</,/>>ATSPI_TEXT/p' <<<"$atspi")"
    # Positivkontrolle: der Baum ist ueberhaupt da. Ohne sie waere
    # "kein Bidi-Zeichen gefunden" auch bei gar keinem Baum gruen -- genau die
    # Sorte Nullaussage, die dieses Projekt schon mehrfach gekostet hat.
    chk "AT-SPI liefert ueberhaupt einen Baum (Positivkontrolle)" \
      "$([[ $(wc -c <<<"$baum") -gt 40 ]] && echo ja || echo nein)" ja
    chk "AT-SPI zeigt die feste Beschriftung 'Aktion:'" \
      "$(grep -q 'Aktion:' <<<"$baum" && echo ja || echo nein)" ja
    chk "AT-SPI zeigt die Schaltflaeche 'Ausführen'" \
      "$(grep -qi 'ausf' <<<"$baum" && echo ja || echo nein)" ja
    chk "AT-SPI zeigt die Schaltflaeche 'Ablehnen'" \
      "$(grep -qi 'ablehnen' <<<"$baum" && echo ja || echo nein)" ja
    # Das eigentliche Kriterium: kein rohes Bidi-, Nullbreiten- oder
    # Nicht-ASCII-Zeichen im ANGEZEIGTEN Zielpfad.
    chk "kein rohes U+202E im angezeigten Baum" \
      "$(grep -q $'‮' <<<"$baum" && echo nein || echo ja)" ja
    chk "kein rohes U+200B im angezeigten Baum" \
      "$(grep -q $'​' <<<"$baum" && echo nein || echo ja)" ja
    chk "kein kyrillisches a im angezeigten Baum" \
      "$(grep -q $'а' <<<"$baum" && echo nein || echo ja)" ja
    chk "der Zielpfad erscheint escapt (\\u-Folge sichtbar)" \
      "$(grep -q '\\u0' <<<"$baum" && echo ja || echo nein)" ja

    # --- Pixel: was wirklich auf dem Schirm steht ------------------------------
    if true; then
      # --- Die eigentliche Pixelprobe: sieht der Mensch einen Unterschied? ----
      #
      # ZWEI Umwege stecken hier drin, beide teuer gelernt:
      #
      # 1. NICHT ueber Koordinaten. AT-SPI meldet fuer das Fenster (0, 0) --
      #    ein Wayland-Client kennt seine eigene Bildschirmposition NICHT, das
      #    Protokoll gibt sie ihm nicht. Ein Zuschnitt auf diese Extents
      #    erwischt die linke obere Bildschirmecke, und weil die sich nicht
      #    aendert, kommt zweimal dasselbe heraus: Rauschen 0, Unterschied 0.
      #    Das sah nach "Sanitizer kaputt" aus und war "falsche Stelle
      #    fotografiert". Deshalb `spectacle -a` -- das aktive Fenster,
      #    ohne dass jemand Koordinaten kennen muss.
      #
      # 2. NICHT ueber OCR als Kriterium. /usr/share/tessdata haelt hier nur
      #    `afr` und `osd`, keine lateinischen Sprachdaten; die
      #    OCR-Positivkontrolle scheitert. Eine Negativaussage ohne
      #    Positivkontrolle ist wertlos -- "kein kyrillisches Zeichen erkannt"
      #    waere von "gar nichts erkannt" nicht zu unterscheiden.
      #
      # Gemessen wird stattdessen die Zusage selbst: ein Zielpfad, der wie
      # ~/Bilder/urlaub.png AUSSIEHT, aber woandershin zeigt, darf nicht
      # aussehen wie das Harmlose. Also beide rendern und die Pixel
      # vergleichen -- keine Sprachdaten noetig, unabhaengig von der Schrift.
      pixel_unterschied() {
        # magick compare liefert die Zahl in wissenschaftlicher Schreibweise
        # ("2.46591e+08"). Ein grep auf ^[0-9]+ machte daraus eine 2.
        magick compare -metric AE "$1" "$2" null: 2>&1 | tr -d '\n' \
          | grep -oE '^[0-9.e+-]+' | awk '{printf "%.0f", $1+0}'
      }
      # Gehoert das AKTIVE Fenster dem Agenten?
      #
      # `spectacle -a` nimmt das aktive Fenster. Wandert der Fokus zwischen
      # zwei Aufnahmen -- Benachrichtigung, anderer Prozess, Spectacle selbst
      # --, wird ein fremdes Fenster geknipst, und der Vergleich misst zwei
      # verschiedene Dinge. Genau das hat die Positivkontrolle etwa jeden
      # zweiten Lauf reissen lassen: Rauschen normalerweise 0, gelegentlich
      # 13 331 100. Das Kriterium stimmte, die Kamera nicht.
      #
      # AT-SPI kennt den ACTIVE-Zustand jedes Fensters und die PID seiner
      # Anwendung. Das ist der richtige Anker. Ein Vergleich der BILDMASSE
      # waere es nicht: AT-SPI meldet die Client-Flaeche (hier 612x173),
      # spectacle liefert Rahmen und Schatten mit (742x331) -- die weichen
      # legitim voneinander ab.
      #
      # Bewusst NICHT: den Fokus selbst erzwingen. Ein Test, der sich seine
      # Bedingungen herstellt, prueft weniger als einer, der sie vorfindet.
      aktiv_gehoert_agent() { "$SYSPY" - "$agent_pid" <<'AKTIVEOF'
import sys
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

pid = int(sys.argv[1])
Atspi.init()
desktop = Atspi.get_desktop(0)
for i in range(desktop.get_child_count()):
    try:
        app = desktop.get_child_at_index(i)
    except Exception:
        continue
    for j in range(app.get_child_count()):
        try:
            fenster = app.get_child_at_index(j)
            if not fenster.get_state_set().contains(Atspi.StateType.ACTIVE):
                continue
            print("ja" if app.get_process_id() == pid else "nein")
            sys.exit(0)
        except Exception:
            continue
print("nein")
AKTIVEOF
      }

      aufnehmen() {
        local ziel="$1" versuch
        for versuch in 1 2 3 4 5; do
          if [[ "$(aktiv_gehoert_agent)" == ja ]]; then
            timeout 60 spectacle -a -b -n -o "$tmp/$ziel.png" >/dev/null 2>&1 || return 1
            # Nach der Aufnahme noch einmal: der Fokus koennte waehrend des
            # Schusses gewandert sein.
            [[ "$(aktiv_gehoert_agent)" == ja ]] && return 0
          fi
          sleep 0.8
        done
        echo "  INFO Aufnahme '$ziel': das aktive Fenster gehoert nicht dem Agenten" >&2
        return 1
      }

      actl 'schliessen' >/dev/null; sleep 0.6
      harmlos_b64="$("$PY" -c '
import base64; print(base64.b64encode("~/Bilder/urlaub.png".encode()).decode())')"
      chk "harmlosen Pfad anzeigen" "$(actl "zeige $a_key $u_key $harmlos_b64")" ok
      sleep 2
      aufnehmen harmlos
      chk "Aufnahme des harmlosen Dialogs gelingt" "$?" 0
      chk "Aufnahme ist nichtleer (Positivkontrolle)" \
        "$([[ "$(stat -c '%s' "$tmp/harmlos.png" 2>/dev/null || echo 0)" -gt 3000 ]] \
          && echo ja || echo nein)" ja

      # Positivkontrolle des Vergleichs: dasselbe Fenster zweimal aufgenommen
      # muss gleich sein. Ohne sie waere jeder Unterschied unten auch blosses
      # Rauschen -- Mauszeiger, Uhr in der Leiste, Neuzeichnen.
      sleep 0.8
      aufnehmen harmlos2
      rausch="$(pixel_unterschied "$tmp/harmlos.png" "$tmp/harmlos2.png")"
      [[ "$rausch" =~ ^[0-9]+$ ]] || rausch=999999999
      echo "  Rauschen zwischen zwei Aufnahmen desselben Dialogs: $rausch"
      chk "zwei Aufnahmen desselben Dialogs sind gleich (Positivkontrolle)" \
        "$([[ "$rausch" -lt 5000 ]] && echo ja || echo nein)" ja

      actl 'schliessen' >/dev/null; sleep 0.6
      chk "verwechselbaren Pfad anzeigen" "$(actl "zeige $a_key $u_key $ziel_b64")" ok
      sleep 2
      aufnehmen boese
      chk "Aufnahme des verwechselbaren Dialogs gelingt" "$?" 0
      # Auf gleiche Groesse bringen, sonst vergleicht magick gar nicht erst.
      # Dass sie sich UEBERHAUPT unterscheidet, ist schon ein Befund fuer sich:
      # der escapte Pfad ist laenger, das Fenster wird breiter.
      masse_h="$(magick identify -format '%wx%h' "$tmp/harmlos.png" 2>/dev/null)"
      masse_b="$(magick identify -format '%wx%h' "$tmp/boese.png" 2>/dev/null)"
      echo "  Fenstermasse harmlos $masse_h, verwechselbar $masse_b"
      magick "$tmp/boese.png" -resize "${masse_h}!" "$tmp/boese_norm.png" 2>/dev/null
      unterschied="$(pixel_unterschied "$tmp/harmlos.png" "$tmp/boese_norm.png")"
      [[ "$unterschied" =~ ^[0-9]+$ ]] || unterschied=0
      echo "  Unterschied harmlos gegen verwechselbar: $unterschied"
      chk "der verwechselbare Pfad sieht sichtbar anders aus" \
        "$([[ "$unterschied" -gt $(( rausch + 20000 )) ]] && echo ja || echo nein)" ja

      # OCR nur als Hinweis, ausdruecklich nicht als Kriterium.
      sprachdaten="$(ls /usr/share/tessdata/*.traineddata 2>/dev/null \
        | grep -cE '/(eng|deu)\.traineddata')"
      [[ "$sprachdaten" =~ ^[0-9]+$ ]] || sprachdaten=0
      if [[ "$sprachdaten" -gt 0 ]]; then
        tesseract "$tmp/boese.png" "$tmp/ocr" --psm 6 >/dev/null 2>&1
        ocr="$(cat "$tmp/ocr.txt" 2>/dev/null)"
        chk "OCR liest die feste Beschriftung (Positivkontrolle)" \
          "$(grep -qiE 'aktion|ziel|umkehr' <<<"$ocr" && echo ja || echo nein)" ja
        chk "OCR findet kein rohes kyrillisches Zeichen" \
          "$(grep -qP '\x{0430}' <<<"$ocr" && echo nein || echo ja)" ja
      else
        echo "  INFO OCR uebersprungen: /usr/share/tessdata hat keine eng/deu-Daten."
        echo "       Nachruestbar mit: pacman -S tesseract-data-eng"
        echo "       Der Pixelvergleich oben deckt die Zusage auch ohne sie."
      fi
    fi

    # --- Entscheidung ---------------------------------------------------------
    chk "Ablehnen wird bestaetigt" "$(actl 'klick ablehnen')" ok
    sleep 1
    d="$(adiag)"
    # Kein `// empty` in diesen Abfragen: jq behandelt false wie null, ein
    # Vergleich auf "false" waere damit immer leer -- und ein Dialog, der
    # offen bleibt, faellt nie auf. Die Falle hat der Builder gefunden.
    chk "Diagnose meldet die Ablehnung" \
      "$(jq -r '.letzte_entscheidung' <<<"$d" 2>/dev/null)" abgelehnt
    chk "Dialog ist danach zu" \
      "$(jq -r '.dialog_sichtbar' <<<"$d" 2>/dev/null)" false
    chk "Ablehnen sendet KEINE Freigabe an den Hub" \
      "$(jq -r '.freigaben_gesendet' <<<"$d" 2>/dev/null)" 0
    chk "Diagnose verraet keinen Zielpfad" \
      "$(grep -qiE 'ssh|id_ed25519|urlaub' <<<"$d" && echo nein || echo ja)" ja

    # PTT ueber den Steuerkanal, am laufenden Prozess.
    chk "ptt einmal wird bestaetigt" "$(actl 'ptt')" ok
    sleep 0.3
    chk "Diagnose meldet ptt aktiv" \
      "$(adiag | jq -r '.ptt_aktiv' 2>/dev/null)" true
    chk "ptt zweimal wird bestaetigt" "$(actl 'ptt')" ok
    sleep 0.3
    chk "Diagnose meldet ptt wieder aus (Umschaltung)" \
      "$(adiag | jq -r '.ptt_aktiv' 2>/dev/null)" false

    # Kein Log-Spam: ein Agent, der im Sekundentakt meldet, ist im Journal
    # nicht mehr von einem Vorfall zu unterscheiden.
    stderr_zeilen="$(grep -c . "$tmp/agent.err" 2>/dev/null)"
    [[ "$stderr_zeilen" =~ ^[0-9]+$ ]] || stderr_zeilen=0
    echo "  stderr-Zeilen des Agenten: $stderr_zeilen"
    chk "kein Log-Spam (hoechstens 20 Zeilen)" \
      "$([[ "$stderr_zeilen" -le 20 ]] && echo ja || echo nein)" ja
  fi

  kill "$agent_pid" 2>/dev/null; agent_pid=""
fi

# --- systemd-Unit -------------------------------------------------------------
echo "  -- systemd-Unit"
UNIT="$REPO/config/systemd/daimon-auth.service"
chk "Unit existiert" "$([[ -f "$UNIT" ]] && echo ja || echo nein)" ja
if [[ -f "$UNIT" ]]; then
  chk "ExecStart nutzt System-Python, nicht das venv" \
    "$(grep -qE '^ExecStart=.*/usr/bin/python3' "$UNIT" && echo ja || echo nein)" ja
  chk "ExecStart zeigt NICHT auf .venv" \
    "$(grep -qE '^ExecStart=.*\.venv' "$UNIT" && echo nein || echo ja)" ja
  chk "RestrictAddressFamilies=AF_UNIX gesetzt" \
    "$(grep -qE '^RestrictAddressFamilies=.*AF_UNIX' "$UNIT" && echo ja || echo nein)" ja
  chk "kein AF_INET in der Unit" \
    "$(grep -qE 'AF_INET' "$UNIT" && echo nein || echo ja)" ja
  chk "NoNewPrivileges gesetzt" \
    "$(grep -qE '^NoNewPrivileges=(yes|true|1)' "$UNIT" && echo ja || echo nein)" ja
fi

exit $fail
