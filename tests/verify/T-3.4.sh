#!/usr/bin/env bash
# Verifizierer fuer T-3.4: Rueckkopplungssperre -- die eigene Stimme kann sich
# nicht selbst reaktivieren.
#
# ============================================================================
# WAS HIER DIE MESSUNG IST -- UND WAS AUSDRUECKLICH NICHT
# ============================================================================
#
# Alle vier Kriterien der Akzeptanzliste lassen sich bequem falsch messen.
# Bequem waere: eine Konstante `500` im Quelltext suchen, `diagnose()["gesperrt"]`
# ablesen, `grep echo`. Alle drei messen, was der Pruefling ueber sich selbst
# sagt. Gemessen wird hier stattdessen die EINZIGE Groesse, die zaehlt:
#
#     GEHT DIESER CHUNK ZUR TRANSKRIPTION -- JA ODER NEIN?
#
#   Abschnitt 2  NACHLAUF -- DIE GRENZE VON BEIDEN SEITEN.
#                Nicht "es gibt eine Konstante 500". Unmittelbar nach dem Ende
#                der Wiedergabe (< 2 ms danach) muss gesperrt sein; nach 650 ms
#                muss durchgelassen werden; bei 400 ms noch nicht. Zusaetzlich
#                wird die Grenze durch Abtasten im 5-ms-Raster GEMESSEN und
#                muss in 400..700 ms fallen.
#                DIE GEGENPROBE STEHT IM SELBEN ABSCHNITT: eine Sperre, die
#                nie aufhoert, erfuellt "unmittelbar danach gesperrt" perfekt.
#                Deshalb ist "die Sperre endet ueberhaupt" eine eigene, harte
#                Pruefung -- und davor steht die Grundlinie, dass ein frisch
#                gebautes, unbeschaeftigtes Sperrwerk ueberhaupt durchlaesst.
#                Ohne die waere jede "gesperrt"-Messung die Nullaussage.
#
#   Abschnitt 3  SPERRE BEI GEDRUECKTEM PTT -- der Kern unter Plan C.
#                Positivkontrolle unmittelbar daneben, im selben Zustand: OHNE
#                laufende Wiedergabe und MIT gedruecktem PTT muss
#                durchgelassen werden. Sonst prueft dieser Abschnitt eine
#                Sperre, die immer sperrt, und waere bei genau der Umsetzung
#                gruen, die das Geraet unbrauchbar macht.
#
#   Abschnitt 4  ECHO-REFERENZ -- BEIDE RICHTUNGEN, GLEICHE LAENGE.
#                Ein eingespeister Ausgabepuffer, der im Aufgenommenen
#                vorkommt, wird verworfen; fremdes Material DERSELBEN LAENGE,
#                im selben Zustand, durch dieselbe Kette, wird NICHT verworfen.
#                Und davor die A/B-Kontrolle: DERSELBE Echo-Chunk geht durch,
#                solange keine Referenz eingespeist wurde. Erst damit ist
#                gezeigt, dass die Referenz die Ursache des Verwerfens ist und
#                nicht irgendein anderer Zustand.
#                Gemessen wird NACH dem Ende der Wiedergabe und NACH Ablauf des
#                Nachlaufs -- waehrend der Sperre waere alles verworfen und der
#                Abgleich nicht von ihr zu trennen. Das ist auch die reale
#                Lage: der Raumhall, den die Referenz fangen soll, kommt nach
#                dem Nachlauf.
#
#   Abschnitt 5  DIAGNOSE -- BELEG, NICHT MESSGRUNDLAGE (Regel 9).
#                `gesperrt`, `grund`, `bis` muessen im Diagnosebild stehen.
#                Gewertet wird, ob das Bild mit dem GEMESSENEN VERHALTEN
#                uebereinstimmt -- an zwei Zeitpunkten, einmal gesperrt, einmal
#                frei. Kein Kriterium aus 1-3 haengt an dieser Auskunft.
#                Der Mutant `sperre-nicht-bei-offener-runde` zeigt genau
#                deshalb weiter brav `gesperrt: true` an, waehrend er
#                durchlaesst -- er faellt in Abschnitt 3, nicht hier.
#
#   Abschnitt 6  MONOTONE ZEIT -- OHNE ROOT MESSBAR, und deshalb gemessen.
#                Die Uhren werden im Treiber ERSETZT, BEVOR der Pruefling
#                importiert wird: `time.time` und `time.monotonic` bekommen
#                einen einspeisbaren Versatz. Eine gestellte WANDUHR (+1 h)
#                darf die Sperre nicht aufheben; eine vorgestellte MONOTONE
#                Uhr (+5 s) muss sie beenden. Die zweite Haelfte ist die
#                Positivkontrolle der ersten: ohne sie waere "die Wanduhr
#                bewirkt nichts" auch dann gruen, wenn der Pruefling gar keine
#                Uhr liest.
#
#   Abschnitt 7  THREADSICHERHEIT -- der offene Punkt aus T-3.3.
#                `ring.py` traegt "nicht threadsicher" im Modulkopf. Die
#                Sperre wird aus zwei Richtungen beruehrt. Zwei Threads, 25
#                Wechsel, ein hammernder Leser: keine Ausnahme, und waehrend
#                einer laufenden Wiedergabe darf KEINE einzige Probe
#                durchgelassen werden.
#
# ============================================================================
# WAS HIER NICHT BELEGT WERDEN KANN -- ausgeschrieben, nicht kaschiert
# ============================================================================
#
# Ohne TTS (piper fehlt, T-3.9) und ohne echtes Mikrofon-Rueckkopplungsereignis
# ist die Kette "Lautsprecher -> Raum -> Mikrofon -> STT" UNGEMESSEN. Belegt
# wird die Logik. Der akustische Nachweis gehoert nach T-3.15. Siehe OFFEN.
#
# Aufruf:
#   tests/verify/T-3.4.sh
#   DAIMON_FIXTURE=<baum> tests/verify/T-3.4.sh   # Baum mit eigenem daimon/
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
TARGET="$(cd "$TARGET" || exit 1; pwd)"

MAX_SECS="${DAIMON_T34_MAX_SECS:-180}"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
laut() { echo "  !! $*"; }

# `jq -r '.feld // false'` liefert bei einem echten `false` dasselbe wie bei
# einem FEHLENDEN Feld -- HANDOVER.md, "Fallen dieser Maschine". Bei jeder
# Pruefung, die `false` ERWARTET -- und davon hat dieser Verifizierer viele --
# ist das der Unterschied zwischen "gemessen und widerlegt" und "gar nicht
# gemessen".
jqb() {  # $1 = JSON, $2 = Feldname -> true | false | FEHLT
  jq -r --arg f "$2" 'if has($f) then (.[$f]|tostring) else "FEHLT" end' <<<"$1"
}

echo "T-3.4 — Rueckkopplungssperre: die eigene Stimme reaktiviert sich nicht"
echo "  Baum: $TARGET"
echo "  Interpreter: $PY"

# =============================================================================
# 0. Voraussetzungen
# =============================================================================
echo
echo "--- 0. Voraussetzungen ---"
chk "jq vorhanden" "$(command -v jq >/dev/null && echo ja || echo nein)" ja
chk "timeout vorhanden" "$(command -v timeout >/dev/null && echo ja || echo nein)" ja
chk "Interpreter vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja
chk "der Baum bringt daimon/ears/interlock.py mit" \
  "$([[ -f "$TARGET/daimon/ears/interlock.py" ]] && echo ja || echo nein)" ja

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "T-3.4: FEHLGESCHLAGEN — Voraussetzungen fehlen, nichts gemessen."
  exit 1
fi

RT="$(mktemp -d)"
trap 'rm -rf -- "$RT"' EXIT INT TERM

# =============================================================================
# Der gemeinsame Vertragssucher
# =============================================================================
cat >"$RT/sperrkontrakt.py" <<'PYEOF'
"""Sucht den Einstiegspunkt von daimon.ears.interlock, statt ihn zu erraten.

Der Verifizierer ist blind gegen die Implementierung entstanden. Die
Akzeptanzliste nennt eine Sperre mit Nachlauf, einen PTT-Zustand, eine
einspeisbare Echo-Referenz und ein Diagnosebild -- Namen nennt sie nicht.

DAS AUSWAHLKRITERIUM IST NICHT DAS MESSKRITERIUM. Das ist bei T-3.4
wichtiger als bei jedem Verifizierer davor, weil hier alle drei Mutanten
Methoden mutieren, die der Sucher vorher finden muss:

  * `wiedergabe_beginnt` / `wiedergabe_endet` werden gewaehlt, weil sie
    ANGENOMMEN werden -- NICHT, weil danach gesperrt ist. Sonst waere
    `nachlauf-auf-null` nicht zu finden, sondern wegdefiniert.
  * Der Entscheider wird nach FORM gewaehlt: ein Name aus einer festen
    Liste, der einen `bool` zurueckgibt. NICHT danach, ob er in irgendeiner
    Lage `False` liefert. Sonst faende der Sucher bei
    `sperre-nicht-bei-offener-runde` einfach "keinen Entscheider", statt ihn
    auffallen zu lassen.
  * Die POLARITAET kommt aus dem NAMEN, nicht aus einer Messung. `durchlassen`
    heisst "True = geht durch", `gesperrt` heisst "True = geht nicht durch".
    Wuerde die Polaritaet aus dem Verhalten abgeleitet ("im Ruhezustand muss
    es durchlassen"), waere jede Sperre, die immer sperrt, einfach
    umgedreht -- und Abschnitt 2 koennte gar nicht mehr rot werden.
  * `referenz` wird gewaehlt, weil ein Puffer ANGENOMMEN wird -- NICHT, weil
    danach etwas verworfen wird. Sonst waere `echo-referenz-entfernt`
    wegdefiniert.

Was gegriffen hat und was mit welchem Grund verworfen wurde, steht im
Protokoll und wird ausgedruckt.
"""
import inspect

CHUNK_BYTES = 1024          # 512 Samples int16, wie in capture.py/ring.py

KLASSEN = ("Rueckkopplungssperre", "Rückkopplungssperre", "Sperre",
           "Interlock", "Verriegelung", "Sperrwerk", "Echosperre",
           "EchoSperre", "Rueckkopplung", "Rueckkopplungsschutz",
           "Torwaechter", "Audiosperre", "AudioSperre", "Mikrofonsperre",
           "Sperrlogik", "Gate", "Feedback", "FeedbackInterlock",
           "Duplexsperre", "Halbduplex")

FABRIKEN = ("sperre", "neu", "erzeuge", "erzeugen", "baue", "instanz",
            "hole_sperre", "interlock")

START = ("wiedergabe_beginnt", "wiedergabe_start", "wiedergabe_an",
         "wiedergabe_beginnen", "beginne_wiedergabe", "starte_wiedergabe",
         "wiedergabe_angemeldet", "wiedergabe_anmelden", "ausgabe_beginnt",
         "ausgabe_start", "ausgabe_an", "spricht_an", "sprechen_beginnt",
         "tts_start", "tts_beginnt", "playback_start", "start_wiedergabe",
         "anmelden", "beginnt", "start", "an")

ENDE = ("wiedergabe_endet", "wiedergabe_ende", "wiedergabe_aus",
        "wiedergabe_beendet", "wiedergabe_abmelden", "beende_wiedergabe",
        "stoppe_wiedergabe", "ausgabe_endet", "ausgabe_ende", "ausgabe_aus",
        "spricht_aus", "sprechen_endet", "tts_ende", "tts_endet",
        "playback_ende", "playback_stop", "abmelden", "endet", "ende", "aus")

# True bedeutet: DAS AUDIO GEHT ZUR TRANSKRIPTION.
PASS_NAMEN = ("durchlassen", "durchlaesst", "darf_durch", "darf_zum_stt",
              "zum_stt", "zulassen", "annehmen", "akzeptieren",
              "weiterleiten", "erlaubt", "durchlass", "passieren",
              "ist_frei", "frei", "offen")
# True bedeutet: DAS AUDIO WIRD ZURUECKGEHALTEN.
BLOCK_NAMEN = ("gesperrt", "ist_gesperrt", "sperrt", "blockiert",
               "blockiert_gerade", "unterdrueckt", "unterdruecken",
               "verwirft", "verwerfen", "ist_echo", "echo", "abweisen",
               "abgewiesen", "gesperrt_gerade", "zurueckhalten")

PTT_SETZER = ("ptt", "ptt_gedrueckt", "setze_ptt", "ptt_setzen", "taste",
              "taste_gedrueckt", "push_to_talk", "ptt_zustand", "runde",
              "runde_offen", "ptt_melden")
PTT_PAARE = (("ptt_an", "ptt_aus"), ("taste_an", "taste_aus"),
             ("ptt_gedrueckt", "ptt_losgelassen"),
             ("ptt_start", "ptt_ende"), ("runde_auf", "runde_zu"),
             ("runde_beginnt", "runde_endet"))
PTT_ATTR = ("ptt", "ptt_gedrueckt", "taste_gedrueckt", "push_to_talk",
            "_ptt")

REFERENZ = ("referenz", "echo_referenz", "referenz_einspeisen",
            "speise_referenz", "setze_referenz", "ausgabepuffer",
            "ausgabe_puffer", "melde_ausgabe", "ausgabe", "wiedergabe_puffer",
            "puffer", "gesprochen", "gespielt", "echo_puffer", "reference")

DIAGNOSE = ("diagnose", "zustand", "snapshot", "status", "als_dict",
            "bild", "diag", "auskunft")

CTOR = (
    ("()", (), {}),
    ("(nachlauf_ms=500)", (), {"nachlauf_ms": 500}),
    ("(nachlauf_s=0.5)", (), {"nachlauf_s": 0.5}),
    ("(nachlauf=0.5)", (), {"nachlauf": 0.5}),
    ("(500)", (500,), {}),
)


class KeinVertrag(Exception):
    pass


def angebot(o):
    return sorted(a for a in dir(o) if not a.startswith("_"))


def muster(seed, n):
    """Deterministische Bytes. Kein `random`, damit zwei Laeufe vergleichbar
    sind und ein Mutant nicht an einem Zufallstreffer haengt."""
    x = seed & 0x7FFFFFFF
    aus = bytearray()
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        aus.append((x >> 16) & 0xFF)
    return bytes(aus)


NEUTRAL = muster(3, CHUNK_BYTES)            # gewoehnliches Mikrofonmaterial

# DIE RICHTUNG, IN DER "VORKOMMEN" HIER LIEGT. Der Ausgabepuffer ist eine
# ganze Aeusserung (hier 0,5 s), das Aufgenommene ist ein Chunk von 32 ms.
# Der Chunk kommt IM Ausgabepuffer vor, nicht umgekehrt -- alles andere waere
# gar nicht die Lage, die dieser Task beschreibt.
#
# Das ist keine Kosmetik: eine Referenz, die KUERZER ist als der zu pruefende
# Chunk, ist nicht vergleichbar, und eine fail-closed gebaute Sperre verwirft
# dann (richtigerweise) ALLES. Ein Verifizierer, der so misst, haelt genau
# dieses korrekte Verhalten fuer "verwirft alles" und meldet einen Befund, wo
# keiner ist. Beim ersten Gegenlesen ist mir das passiert.
REF_LANG = muster(7, 16000)                 # 0,5 s Ausgabepuffer
FREMD_LANG = muster(99, 16000)              # 0,5 s fremdes Material
REF = REF_LANG                              # das, was eingespeist wird
ECHO_CHUNK = REF_LANG[4096:4096 + CHUNK_BYTES]     # ein Stueck DER Ausgabe
FREMD_CHUNK = FREMD_LANG[4096:4096 + CHUNK_BYTES]  # gleiche Laenge, fremd


# ---------------------------------------------------------------------------
# Entscheider
# ---------------------------------------------------------------------------
def finde_entscheider(fabrik):
    """Alle brauchbaren Entscheider, bester zuerst.

    Rang: PASS-Name mit Chunk < PASS-Name ohne Chunk < BLOCK-Name mit Chunk
    < BLOCK-Name ohne Chunk. Ein Entscheider MIT Chunk ist besser, weil nur
    er den Echo-Abgleich ueberhaupt sehen kann.
    """
    kandidaten, verworfen = [], []
    for pol, namen, basis in (("pass", PASS_NAMEN, 0), ("block", BLOCK_NAMEN, 2)):
        for n in namen:
            probe = fabrik()
            f = getattr(probe, n, None)
            if f is None:
                continue
            if not callable(f):
                if isinstance(f, bool):
                    kandidaten.append({"name": n, "polaritaet": pol,
                                       "nimmt_chunk": False, "art": "attribut",
                                       "rang": basis + 1})
                else:
                    verworfen.append(f"{n}: weder aufrufbar noch bool")
                continue
            gefunden = False
            for nimmt in (True, False):
                p2 = fabrik()
                g = getattr(p2, n)
                try:
                    r = g(NEUTRAL) if nimmt else g()
                except TypeError as exc:
                    verworfen.append(f"{n}({'chunk' if nimmt else ''}): "
                                     f"TypeError: {exc}"[:120])
                    continue
                except Exception as exc:
                    verworfen.append(f"{n}({'chunk' if nimmt else ''}): "
                                     f"{type(exc).__name__}: {exc}"[:120])
                    continue
                if not isinstance(r, bool):
                    verworfen.append(f"{n}({'chunk' if nimmt else ''}): "
                                     f"liefert {type(r).__name__}, kein bool")
                    continue
                kandidaten.append({"name": n, "polaritaet": pol,
                                   "nimmt_chunk": nimmt, "art": "methode",
                                   "rang": basis + (0 if nimmt else 1)})
                gefunden = True
                break
            if not gefunden:
                continue
    kandidaten.sort(key=lambda k: (k["rang"], k["name"]))
    return kandidaten, verworfen


def entscheidung(obj, bind, chunk, ptt_bind=None, ptt_wert=False):
    """True = dieser Chunk geht zur Transkription.

    `ptt_bind`/`ptt_wert` nur fuer die Argumentform: dort ist der PTT-Zustand
    kein Zustand, sondern wird bei jeder Entscheidung mitgegeben.
    """
    f = getattr(obj, bind["name"])
    kw = {}
    if (ptt_bind is not None and ptt_bind.get("art") == "kwarg"
            and bind["art"] == "methode"):
        kw[ptt_bind["name"]] = bool(ptt_wert)
    if bind["art"] == "attribut":
        r = f
    else:
        r = f(chunk, **kw) if bind["nimmt_chunk"] else f(**kw)
    return bool(r) if bind["polaritaet"] == "pass" else (not bool(r))


# ---------------------------------------------------------------------------
# Wiedergabe an/aus -- Auswahl NUR nach Annahme, nicht nach Wirkung
# ---------------------------------------------------------------------------
def finde_ruf(fabrik, namen, vorlauf=None):
    verworfen = []
    for n in namen:
        p = fabrik()
        if vorlauf:
            try:
                vorlauf(p)
            except Exception:
                pass
        f = getattr(p, n, None)
        if not callable(f):
            continue
        try:
            f()
        except TypeError:
            try:
                f(None)
            except Exception as exc:
                verworfen.append(f"{n}: {type(exc).__name__}: {exc}"[:120])
                continue
            return {"name": n, "arg": True}, verworfen
        except Exception as exc:
            verworfen.append(f"{n}: {type(exc).__name__}: {exc}"[:120])
            continue
        return {"name": n, "arg": False}, verworfen
    return None, verworfen


def rufe(obj, bind):
    f = getattr(obj, bind["name"])
    return f(None) if bind["arg"] else f()


# ---------------------------------------------------------------------------
# PTT
# ---------------------------------------------------------------------------
PTT_KWARGS = ("ptt_gedrueckt", "ptt", "gedrueckt", "taste_gedrueckt",
              "push_to_talk", "ptt_aktiv", "runde_offen", "taste")


def finde_ptt(fabrik, entsch=None):
    """Der PTT-Zustand kann auf zwei grundverschiedene Arten hereinkommen:
    als ZUSTAND (ein Setzer, ein Paar, ein Attribut) oder als ARGUMENT der
    Entscheidung selbst (`annehmen(chunk, ptt_gedrueckt=True)`).

    Die zweite Form ist die sauberere -- sie kann gar nicht veralten -- und
    genau sie hatte dieser Sucher beim ersten Gegenlesen uebersehen. Ein
    Verifizierer, der nur nach einem Setzer sucht, meldet dann "der
    PTT-Zustand ist nicht einspeisbar" an einem Pruefling, der ihn sehr wohl
    entgegennimmt. Deshalb wird die Argumentform ZUERST probiert.

    Auswahlkriterium bleibt die ANNAHME, nicht die Wirkung.
    """
    verworfen = []
    if entsch is not None and entsch["art"] == "methode":
        for n in PTT_KWARGS:
            p = fabrik()
            f = getattr(p, entsch["name"])
            try:
                if entsch["nimmt_chunk"]:
                    r = f(NEUTRAL, **{n: True})
                else:
                    r = f(**{n: True})
            except TypeError as exc:
                verworfen.append(f"{entsch['name']}(..., {n}=): "
                                 f"TypeError: {exc}"[:120])
                continue
            except Exception as exc:
                verworfen.append(f"{entsch['name']}(..., {n}=): "
                                 f"{type(exc).__name__}: {exc}"[:120])
                continue
            if not isinstance(r, bool):
                verworfen.append(f"{entsch['name']}(..., {n}=): kein bool")
                continue
            return {"art": "kwarg", "name": n}, verworfen
    for n in PTT_SETZER:
        p = fabrik()
        f = getattr(p, n, None)
        if not callable(f):
            continue
        try:
            f(True)
            f(False)
        except Exception as exc:
            verworfen.append(f"{n}(bool): {type(exc).__name__}: {exc}"[:120])
            continue
        return {"art": "setzer", "name": n}, verworfen
    for an, aus in PTT_PAARE:
        p = fabrik()
        fa, fb = getattr(p, an, None), getattr(p, aus, None)
        if not (callable(fa) and callable(fb)):
            continue
        try:
            fa()
            fb()
        except Exception as exc:
            verworfen.append(f"{an}/{aus}: {type(exc).__name__}: {exc}"[:120])
            continue
        return {"art": "paar", "an": an, "aus": aus}, verworfen
    for n in PTT_ATTR:
        p = fabrik()
        if not isinstance(getattr(p, n, None), bool):
            continue
        try:
            setattr(p, n, True)
            setattr(p, n, False)
        except Exception as exc:
            verworfen.append(f"{n} (Attribut): {type(exc).__name__}"[:120])
            continue
        return {"art": "attribut", "name": n}, verworfen
    return None, verworfen


def setze_ptt(obj, bind, wert):
    """Bei der Argumentform gibt es nichts zu setzen -- der Wert wird bei
    jeder Entscheidung mitgegeben. Der Treiber fuehrt ihn deshalb selbst."""
    if bind is None or bind["art"] == "kwarg":
        return bind is not None
    if bind["art"] == "setzer":
        getattr(obj, bind["name"])(bool(wert))
    elif bind["art"] == "paar":
        getattr(obj, bind["an"] if wert else bind["aus"])()
    else:
        setattr(obj, bind["name"], bool(wert))
    return True


# ---------------------------------------------------------------------------
# Referenz -- gewaehlt, weil ein Puffer ANGENOMMEN wird
# ---------------------------------------------------------------------------
def finde_referenz(fabrik):
    verworfen = []
    for n in REFERENZ:
        p = fabrik()
        f = getattr(p, n, None)
        if not callable(f):
            continue
        try:
            f(REF)
        except Exception as exc:
            verworfen.append(f"{n}: {type(exc).__name__}: {exc}"[:120])
            continue
        return {"name": n}, verworfen
    return None, verworfen


def finde_diagnose(fabrik):
    verworfen = []
    for n in DIAGNOSE:
        p = fabrik()
        f = getattr(p, n, None)
        if not callable(f):
            if isinstance(f, dict):
                return {"name": n, "art": "attribut"}, verworfen
            continue
        try:
            r = f()
        except Exception as exc:
            verworfen.append(f"{n}: {type(exc).__name__}: {exc}"[:120])
            continue
        if not isinstance(r, dict):
            verworfen.append(f"{n}: liefert {type(r).__name__}, kein dict")
            continue
        return {"name": n, "art": "methode"}, verworfen
    return None, verworfen


def hole_diagnose(obj, bind):
    f = getattr(obj, bind["name"])
    return f() if bind["art"] == "methode" else f


# ---------------------------------------------------------------------------
# Bindung
# ---------------------------------------------------------------------------
def binde(modul):
    versuche = []
    gewaehlt = None

    def baubar(Kl, a, kw):
        return Kl(*a, **dict(kw))

    for kn in KLASSEN:
        Kl = getattr(modul, kn, None)
        if not inspect.isclass(Kl):
            continue
        for besch, a, kw in CTOR:
            try:
                obj = baubar(Kl, a, kw)
            except Exception as exc:
                versuche.append(f"{kn}{besch}: {type(exc).__name__}: {exc}"[:160])
                continue
            gewaehlt = (kn + besch, lambda Kl=Kl, a=a, kw=kw: baubar(Kl, a, kw))
            break
        if gewaehlt:
            break

    if gewaehlt is None:
        for fn in FABRIKEN:
            f = getattr(modul, fn, None)
            if not callable(f) or inspect.isclass(f):
                continue
            try:
                f()
            except Exception as exc:
                versuche.append(f"{fn}(): {type(exc).__name__}: {exc}"[:160])
                continue
            gewaehlt = (fn + "()", lambda f=f: f())
            break

    if gewaehlt is None:
        raise KeinVertrag(
            "Keine Sperre in daimon.ears.interlock baubar.\n"
            "Das Modul bietet an: " + ", ".join(angebot(modul)) + "\n"
            "Versuche:\n  " + "\n  ".join(versuche))

    form, fabrik = gewaehlt
    protokoll = {"form": form, "verworfene_ctor": versuche,
                 "angebot_objekt": angebot(fabrik())}

    kandidaten, verw_e = finde_entscheider(fabrik)
    protokoll["entscheider_kandidaten"] = [
        f"{k['name']}({'chunk' if k['nimmt_chunk'] else ''}) [{k['polaritaet']}]"
        for k in kandidaten]
    protokoll["verworfene_entscheider"] = verw_e
    if not kandidaten:
        raise KeinVertrag(
            "Keine Methode entscheidet ueber einen Chunk.\n"
            "Das Objekt bietet an: " + ", ".join(protokoll["angebot_objekt"]) +
            "\nVerworfen:\n  " + "\n  ".join(verw_e))
    entsch = kandidaten[0]
    mit_chunk = next((k for k in kandidaten if k["nimmt_chunk"]), None)

    start, verw_s = finde_ruf(fabrik, START)
    ende = None
    verw_x = []
    if start:
        ende, verw_x = finde_ruf(
            fabrik, ENDE, vorlauf=lambda o: rufe(o, start))
        # Ein Name, der beides bedienen wuerde, ist kein Ende.
        if ende and ende["name"] == start["name"]:
            ende = None
            verw_x.append(f"{start['name']}: identisch mit dem Anfang")

    ptt, verw_p = finde_ptt(fabrik, entsch)
    ref, verw_r = finde_referenz(fabrik)
    diag, verw_d = finde_diagnose(fabrik)

    protokoll.update({
        "entscheider": f"{entsch['name']}"
                       f"({'chunk' if entsch['nimmt_chunk'] else ''})"
                       f" [{entsch['polaritaet']}]",
        "entscheider_mit_chunk": (mit_chunk or {}).get("name", "(keiner)"),
        "start": (start or {}).get("name", "(keiner)"),
        "verworfene_start": verw_s,
        "ende": (ende or {}).get("name", "(keines)"),
        "verworfene_ende": verw_x,
        "ptt": ptt or "(keine)",
        "verworfene_ptt": verw_p,
        "referenz": (ref or {}).get("name", "(keine)"),
        "verworfene_referenz": verw_r,
        "diagnose": (diag or {}).get("name", "(keine)"),
        "verworfene_diagnose": verw_d,
    })

    M = {"fabrik": fabrik, "entscheider": entsch, "entscheider_chunk": mit_chunk,
         "start": start, "ende": ende, "ptt": ptt, "referenz": ref,
         "diagnose": diag}
    return M, protokoll
PYEOF

# =============================================================================
# Treiber: Nachlauf, PTT, Echo, Diagnose, Threads
# =============================================================================
cat >"$RT/treiber.py" <<'PYEOF'
"""Misst am laufenden Objekt. Jede Zusage in einem eigenen Abschnitt, jeder
Abschnitt in einem eigenen Zustand -- damit ein Fehler an einer Stelle nicht
alle anderen mitfaerbt.

PTT wird ausser in Abschnitt 3 AUSDRUECKLICH LOSGELASSEN. Nicht aus Bequem-
lichkeit: nur so bleibt "die Sperre gilt auch bei gedruecktem PTT" eine eigene,
isolierte Messung, statt jeden anderen Abschnitt mitzureissen.
"""
import importlib
import json
import os
import sys
import threading
import time
import traceback

BAUM, RT = sys.argv[1], sys.argv[2]
sys.path.insert(0, BAUM)
sys.path.insert(0, RT)

import sperrkontrakt as K  # noqa: E402

ERG = {"ok": True}


def melde(**kw):
    ERG.update(kw)


try:
    modul = importlib.import_module("daimon.ears.interlock")
except Exception as exc:
    print(json.dumps({"ok": False, "phase": "import",
                      "fehler": f"{type(exc).__name__}: {exc}",
                      "spur": traceback.format_exc()[-1400:]}))
    raise SystemExit(0)

melde(datei=os.path.abspath(modul.__file__), angebot=K.angebot(modul))
melde(deklariert={n: getattr(modul, n) for n in
                  ("NACHLAUF_MS", "NACHLAUF_S", "NACHLAUF", "NACHLAUF_SEK",
                   "RATE", "CHUNK", "REFERENZ_FENSTER_S", "ECHO_FENSTER_S")
                  if isinstance(getattr(modul, n, None), (int, float, str))})

try:
    M, protokoll = K.binde(modul)
except Exception as exc:
    melde(ok=False, phase="vertrag", fehler=str(exc))
    print(json.dumps(ERG, default=str))
    raise SystemExit(0)
melde(vertrag=protokoll)

fabrik = M["fabrik"]
E = M["entscheider"]
EC = M["entscheider_chunk"]


# Bei der Argumentform ist der PTT-Zustand kein Zustand des Prueflings,
# sondern ein Argument jeder Entscheidung. Der Treiber fuehrt ihn dann selbst,
# damit `ptt(o, True)` und `durch(o)` im Text dieses Treibers dasselbe
# bedeuten wie bei einem Pruefling mit Setzer.
PTT_WERT = {"v": False}


def durch(o, chunk=K.NEUTRAL):
    return K.entscheidung(o, E, chunk, M["ptt"], PTT_WERT["v"])


def durch_chunk(o, chunk):
    """Fuer den Echo-Abgleich: der Entscheider MUSS den Chunk sehen."""
    return K.entscheidung(o, EC, chunk, M["ptt"], PTT_WERT["v"])


def ptt(o, wert):
    PTT_WERT["v"] = bool(wert)
    return K.setze_ptt(o, M["ptt"], wert)


def neu(ptt_wert=False):
    o = fabrik()
    ptt(o, ptt_wert)
    return o


# ===========================================================================
# GRUNDLINIE. Ohne sie ist jede "gesperrt"-Messung die Nullaussage: eine
# Sperre, die immer sperrt, erfuellt Abschnitt 2 und 3 muehelos.
# ===========================================================================
grund = {}
try:
    o = neu(False)
    grund["frisch_durchlaesst"] = durch(o)
    grund["frisch_zweimal"] = durch(o)
except Exception as exc:
    grund["fehler"] = f"{type(exc).__name__}: {exc}"
melde(grundlinie=grund)

# ===========================================================================
# ABSCHNITT 2: NACHLAUF -- die Grenze von beiden Seiten
# ===========================================================================
nach = {"runden": []}
if M["start"] is None or M["ende"] is None:
    nach["fehler"] = ("kein Anfang/Ende der Wiedergabe gefunden: "
                      f"start={protokoll['start']} ende={protokoll['ende']}")
else:
    try:
        for i in range(3):
            o = neu(False)
            K.rufe(o, M["start"])
            time.sleep(0.03)
            waehrend = durch(o)
            K.rufe(o, M["ende"])
            t0 = time.monotonic()
            sofort = durch(o)
            sofort_ms = round((time.monotonic() - t0) * 1000.0, 2)
            freigabe = None
            while (time.monotonic() - t0) < 2.0:
                if durch(o):
                    freigabe = round((time.monotonic() - t0) * 1000.0, 1)
                    break
                time.sleep(0.005)
            time.sleep(0.05)
            bleibt = durch(o)
            nach["runden"].append({
                "waehrend_wiedergabe_durch": waehrend,
                "sofort_danach_durch": sofort,
                "sofort_nach_ms": sofort_ms,
                "freigabe_ms": freigabe,
                "danach_frei": bleibt,
            })
        f = sorted(r["freigabe_ms"] for r in nach["runden"]
                   if r["freigabe_ms"] is not None)
        nach["freigaben_ms"] = f
        nach["median_ms"] = f[len(f) // 2] if f else None
        nach["alle_drei_endeten"] = (len(f) == 3)
        nach["waehrend_nie_durchgelassen"] = all(
            not r["waehrend_wiedergabe_durch"] for r in nach["runden"])
        nach["sofort_nie_durchgelassen"] = all(
            not r["sofort_danach_durch"] for r in nach["runden"])
        nach["sofort_max_ms"] = max(r["sofort_nach_ms"] for r in nach["runden"])
        nach["danach_immer_frei"] = all(r["danach_frei"] for r in nach["runden"])

        # Und die Grenze noch einmal als ZWEI-PUNKT-PROBE, ohne Abtasten:
        # 400 ms davor gesperrt, 650 ms danach frei. Zwei frische Objekte,
        # damit das Abtasten selbst nichts verschoben haben kann.
        for etikett, warte in (("bei_400ms", 0.40), ("bei_650ms", 0.65)):
            o = neu(False)
            K.rufe(o, M["start"])
            time.sleep(0.03)
            K.rufe(o, M["ende"])
            time.sleep(warte)
            nach[etikett + "_durch"] = durch(o)
    except Exception as exc:
        nach["fehler"] = f"{type(exc).__name__}: {exc}"
        nach["spur"] = traceback.format_exc()[-800:]
melde(nachlauf=nach)

# ===========================================================================
# ABSCHNITT 3: SPERRE BEI GEDRUECKTEM PTT
# ===========================================================================
p = {}
if M["ptt"] is None:
    p["fehler"] = ("keine PTT-Schnittstelle gefunden -- der Zustand 'Taste "
                   "gehalten' laesst sich nicht einspeisen, die Zusage ist "
                   "damit UNGEMESSEN")
    p["angebot"] = protokoll["angebot_objekt"]
elif M["start"] is None:
    p["fehler"] = "kein Anfang der Wiedergabe gefunden"
else:
    try:
        # POSITIVKONTROLLE ZUERST, im selben Zustand: keine Wiedergabe,
        # PTT gehalten -> es MUSS durchgehen. Sonst prueft der Rest dieses
        # Abschnitts eine Sperre, die immer sperrt.
        o = neu(True)
        p["ohne_wiedergabe_ptt_gedrueckt_durch"] = durch(o)
        # DIE ZUSAGE.
        K.rufe(o, M["start"])
        time.sleep(0.03)
        p["waehrend_wiedergabe_ptt_gedrueckt_durch"] = durch(o)
        # Zur Gegenueberstellung: dasselbe mit losgelassener Taste.
        ptt(o, False)
        p["waehrend_wiedergabe_ptt_los_durch"] = durch(o)
        # Und der PTT-Druck DARF die Sperre auch nicht nachtraeglich oeffnen.
        ptt(o, True)
        p["ptt_erneut_gedrueckt_durch"] = durch(o)
        if M["ende"]:
            K.rufe(o, M["ende"])
            time.sleep(0.70)
            p["nach_nachlauf_ptt_gedrueckt_durch"] = durch(o)
    except Exception as exc:
        p["fehler"] = f"{type(exc).__name__}: {exc}"
        p["spur"] = traceback.format_exc()[-800:]
melde(ptt=p)

# ===========================================================================
# ABSCHNITT 4: ECHO-REFERENZ -- beide Richtungen, gleiche Laenge
#
# Gemessen NACH dem Ende der Wiedergabe UND nach Ablauf des Nachlaufs. Waere
# die Sperre noch aktiv, waere alles verworfen und "die Referenz wirkt" nicht
# von "es ist ohnehin gesperrt" zu trennen.
# Der A/B-Aufbau ist der Kern: zwei Objekte, GLEICHER Ablauf, EIN Unterschied
# -- ob eine Referenz eingespeist wurde.
# ===========================================================================
e = {}
if EC is None:
    e["fehler"] = ("kein Entscheider nimmt einen Chunk entgegen -- ein "
                   "Echo-Abgleich ist damit nicht messbar")
elif M["referenz"] is None:
    e["fehler"] = ("keine Methode nimmt einen Ausgabepuffer entgegen: "
                   "die Referenz ist nicht einspeisbar")
    e["angebot"] = protokoll["angebot_objekt"]
else:
    try:
        e["chunk_bytes"] = len(K.ECHO_CHUNK)
        e["fremd_bytes"] = len(K.FREMD_CHUNK)
        e["ref_bytes"] = len(K.REF)
        e["echo_chunk_ist_stueck_der_referenz"] = K.ECHO_CHUNK in K.REF
        e["fremd_chunk_nicht_in_der_referenz"] = K.FREMD_CHUNK not in K.REF

        def lauf(mit_referenz):
            o = neu(False)
            if M["start"]:
                K.rufe(o, M["start"])
            if mit_referenz:
                getattr(o, M["referenz"]["name"])(K.REF)
            time.sleep(0.03)
            if M["ende"]:
                K.rufe(o, M["ende"])
            time.sleep(0.75)          # Nachlauf sicher vorbei
            return {"echo_durch": durch_chunk(o, K.ECHO_CHUNK),
                    "fremd_durch": durch_chunk(o, K.FREMD_CHUNK),
                    "neutral_durch": durch_chunk(o, K.NEUTRAL)}

        # A: OHNE Referenz -- die Positivkontrolle. Derselbe Chunk, derselbe
        #    Ablauf, kein Abgleich moeglich: er MUSS durchgehen.
        e["ohne_referenz"] = lauf(False)
        # B: MIT Referenz -- die Zusage.
        e["mit_referenz"] = lauf(True)
    except Exception as exc:
        e["fehler"] = f"{type(exc).__name__}: {exc}"
        e["spur"] = traceback.format_exc()[-800:]
melde(echo=e)

# ===========================================================================
# ABSCHNITT 5: DIAGNOSE -- Beleg, nicht Messgrundlage
# ===========================================================================
d = {}
if M["diagnose"] is None:
    d["fehler"] = "keine Diagnose gefunden"
    d["angebot"] = protokoll["angebot_objekt"]
elif M["start"] is None or M["ende"] is None:
    d["fehler"] = "kein Anfang/Ende der Wiedergabe gefunden"
else:
    try:
        o = neu(False)
        d["frisch"] = hole_dg = K.hole_diagnose(o, M["diagnose"])
        d["frisch_schluessel"] = sorted(hole_dg.keys())
        K.rufe(o, M["start"])
        time.sleep(0.03)
        bild1 = K.hole_diagnose(o, M["diagnose"])
        verh1 = durch(o)
        K.rufe(o, M["ende"])
        time.sleep(0.90)
        bild2 = K.hole_diagnose(o, M["diagnose"])
        verh2 = durch(o)
        d["bild_gesperrt"] = {k: str(v) for k, v in bild1.items()}
        d["bild_frei"] = {k: str(v) for k, v in bild2.items()}
        d["hat_gesperrt"] = "gesperrt" in bild1
        d["hat_grund"] = "grund" in bild1
        d["hat_bis"] = "bis" in bild1
        d["verhalten_gesperrt_durch"] = verh1
        d["verhalten_frei_durch"] = verh2
        d["stimmt_bei_sperre"] = (bool(bild1.get("gesperrt")) is True
                                  and verh1 is False)
        d["stimmt_bei_frei"] = (bool(bild2.get("gesperrt")) is False
                                and verh2 is True)
        g = bild1.get("grund")
        d["grund_bei_sperre_gesetzt"] = isinstance(g, str) and len(g) > 0
        b = bild1.get("bis")
        d["bis_ist_zahl_oder_none"] = (b is None or isinstance(b, (int, float)))
    except Exception as exc:
        d["fehler"] = f"{type(exc).__name__}: {exc}"
        d["spur"] = traceback.format_exc()[-800:]
melde(diagnose=d)

# ===========================================================================
# ABSCHNITT 7: THREADSICHERHEIT
#
# T-3.3 hat `ring.py` ausdruecklich als NICHT threadsicher benannt. Die Sperre
# wird aus mindestens zwei Richtungen beruehrt: dem Audio-Callback (liest,
# oft) und der Wiedergabe (schreibt, selten). Gemessen wird beides zugleich.
#
# Das Urteil haengt NICHT am Nachlauf: gewertet wird nur das Fenster ZWISCHEN
# Anfang und Ende einer Wiedergabe, mit 5 ms Schutzabstand an beiden Raendern.
# Sonst faerbte `nachlauf-auf-null` diesen Abschnitt mit ein und die Mutanten
# waeren nicht mehr getrennt.
# ===========================================================================
t = {}
if M["start"] is None or M["ende"] is None:
    t["fehler"] = "kein Anfang/Ende der Wiedergabe gefunden"
else:
    try:
        o = neu(False)
        fenster = []          # (t_start, t_ende)
        proben = []           # (t, durchgelassen)
        fehler_a, fehler_b = [], []
        halt = threading.Event()

        def schreiber():
            try:
                for _ in range(25):
                    ta = time.monotonic()
                    K.rufe(o, M["start"])
                    time.sleep(0.02)
                    K.rufe(o, M["ende"])
                    tb = time.monotonic()
                    fenster.append((ta, tb))
                    time.sleep(0.06)
            except Exception as exc:
                fehler_a.append(f"{type(exc).__name__}: {exc}")
            finally:
                halt.set()

        def leser():
            try:
                while not halt.is_set():
                    proben.append((time.monotonic(), durch(o)))
            except Exception as exc:
                fehler_b.append(f"{type(exc).__name__}: {exc}")

        a = threading.Thread(target=schreiber, name="wiedergabe")
        b = threading.Thread(target=leser, name="callback")
        a.start()
        b.start()
        a.join(timeout=30)
        b.join(timeout=10)

        GUARD = 0.005
        drin = lecks = 0
        for ts, tr in proben:
            for ta, tb in fenster:
                if ta + GUARD <= ts <= tb - GUARD:
                    drin += 1
                    if tr:
                        lecks += 1
                    break
        t["fenster"] = len(fenster)
        t["proben"] = len(proben)
        t["proben_in_wiedergabe"] = drin
        t["lecks_in_wiedergabe"] = lecks
        t["ausnahmen_schreiber"] = fehler_a
        t["ausnahmen_leser"] = fehler_b
        t["ausnahmen"] = len(fehler_a) + len(fehler_b)
        time.sleep(0.75)
        t["am_ende_frei"] = durch(o)
    except Exception as exc:
        t["fehler"] = f"{type(exc).__name__}: {exc}"
        t["spur"] = traceback.format_exc()[-800:]
melde(threads=t)

# ===========================================================================
# GEGENPROBE AM AUSWERTER SELBST
#
# Bis hierhin ist gezeigt, was der Pruefling tut. NICHT gezeigt ist, dass
# diese Messkette -- Vertragssucher, Polaritaet, `entscheidung()` -- beide
# Antworten ueberhaupt hervorbringen kann. Ein Sucher, der die Polaritaet
# verdreht, oder ein `entscheidung()`, das immer True liefert, waere oben
# vollstaendig gruen gewesen.
#
# Deshalb laufen zwei ATTRAPPEN durch DIESELBE Kette: eine, die alles
# durchlaesst, und eine, die alles sperrt. Beide sind hier im Treiber
# definiert, ihr Verhalten ist bekannt. Kaeme bei beiden dasselbe heraus,
# waere jede Zeile daraufueber wertlos, egal wie gruen sie aussieht.
# ===========================================================================
a = {}
try:
    class _Basis:
        def wiedergabe_beginnt(self):
            pass

        def wiedergabe_endet(self):
            pass

        def ptt(self, gedrueckt):
            pass

        def referenz(self, puffer):
            pass

        def diagnose(self):
            return {"gesperrt": False, "grund": None, "bis": None}

    class _Modul:
        pass

    def attrappe(antwort):
        class Sperre(_Basis):
            def durchlassen(self, chunk):
                return antwort
        m = _Modul()
        m.Sperre = Sperre
        MA, _ = K.binde(m)
        o = MA["fabrik"]()
        frisch = K.entscheidung(o, MA["entscheider"], K.NEUTRAL)
        K.rufe(o, MA["start"])
        waehrend = K.entscheidung(o, MA["entscheider"], K.NEUTRAL)
        return {"gewaehlt": MA["entscheider"]["name"],
                "polaritaet": MA["entscheider"]["polaritaet"],
                "frisch_durch": frisch, "waehrend_durch": waehrend}

    a["laesst_alles_durch"] = attrappe(True)
    a["sperrt_alles"] = attrappe(False)
    a["kette_unterscheidet"] = (
        a["laesst_alles_durch"]["frisch_durch"] is True
        and a["sperrt_alles"]["frisch_durch"] is False)
except Exception as exc:
    a["fehler"] = f"{type(exc).__name__}: {exc}"
    a["spur"] = traceback.format_exc()[-800:]
melde(attrappen=a)

print(json.dumps(ERG, default=str))
PYEOF

# =============================================================================
# Treiber: monotone Zeit -- die Uhren werden VOR dem Import ersetzt
# =============================================================================
cat >"$RT/treiber_uhr.py" <<'PYEOF'
"""Eine gestellte Wanduhr darf die Sperre nicht aufheben.

Ohne root ist die echte Systemuhr nicht zu verstellen -- also wird sie im
Prozess ersetzt, und zwar BEVOR `daimon.ears.interlock` importiert wird.
Damit greift der Versatz auch bei `from time import monotonic`.

DREI MESSUNGEN, UND DIE DRITTE IST DIE POSITIVKONTROLLE DER ERSTEN:

  (a) Wiedergabe LAEUFT, Wanduhr +1 h  -> muss gesperrt bleiben.
  (b) Wiedergabe BEENDET, im Nachlauf, Wanduhr +1 h -> muss gesperrt bleiben.
      Nur gewertet, wenn der Pruefling ueberhaupt einen Nachlauf hat -- sonst
      misst diese Zeile den Nachlauf und nicht die Uhr, und der Mutant
      `nachlauf-auf-null` fiele an zwei Stellen statt an einer.
  (c) Wiedergabe BEENDET, monotone Uhr +5 s -> muss FREIGEBEN.
      Ohne (c) waere (a)+(b) auch bei einem Pruefling gruen, der ueberhaupt
      keine Uhr liest.
  (d) Und die Messkette selbst: eine Frist im Treiber reagiert auf beide
      Versaetze so, wie sie soll.
"""
import json
import sys
import time
import traceback

BAUM, RT = sys.argv[1], sys.argv[2]

_mono, _wall = time.monotonic, time.time
V = {"m": 0.0, "w": 0.0}
time.monotonic = lambda: _mono() + V["m"]
time.perf_counter = lambda: _mono() + V["m"]
time.monotonic_ns = lambda: int((_mono() + V["m"]) * 1e9)
time.time = lambda: _wall() + V["w"]
time.time_ns = lambda: int((_wall() + V["w"]) * 1e9)

sys.path.insert(0, BAUM)
sys.path.insert(0, RT)

import importlib  # noqa: E402
import sperrkontrakt as K  # noqa: E402

ERG = {"ok": True}

# --- (d) die Messkette, bevor irgendetwas am Pruefling gemessen wird --------
frist = time.monotonic() + 0.5
ERG["kette_vor_sprung_abgelaufen"] = time.monotonic() > frist
V["m"] += 5.0
ERG["kette_nach_mono_sprung_abgelaufen"] = time.monotonic() > frist
V["m"] -= 5.0
w0 = time.time()
V["w"] += 3600.0
ERG["kette_wanduhr_sprung_s"] = round(time.time() - w0, 1)
V["w"] -= 3600.0

try:
    modul = importlib.import_module("daimon.ears.interlock")
    M, protokoll = K.binde(modul)
except Exception as exc:
    ERG.update(ok=False, fehler=f"{type(exc).__name__}: {exc}",
               spur=traceback.format_exc()[-900:])
    print(json.dumps(ERG, default=str))
    raise SystemExit(0)

E = M["entscheider"]


def durch(o):
    return K.entscheidung(o, E, K.NEUTRAL)


def neu():
    o = M["fabrik"]()
    K.setze_ptt(o, M["ptt"], False)
    return o


if M["start"] is None or M["ende"] is None:
    ERG.update(ok=False, fehler="kein Anfang/Ende der Wiedergabe gefunden",
               vertrag=protokoll)
    print(json.dumps(ERG, default=str))
    raise SystemExit(0)

try:
    # (a) Wiedergabe laeuft, Wanduhr springt.
    o = neu()
    K.rufe(o, M["start"])
    time.sleep(0.02)
    ERG["a_vor_sprung_durch"] = durch(o)
    V["w"] += 3600.0
    ERG["a_nach_wanduhrsprung_durch"] = durch(o)
    V["w"] -= 3600.0

    # Hat der Pruefling ueberhaupt einen Nachlauf? Das entscheidet, ob (b)
    # etwas ueber die Uhr aussagt oder nur ueber den Nachlauf.
    o = neu()
    K.rufe(o, M["start"])
    K.rufe(o, M["ende"])
    time.sleep(0.05)
    ERG["nachlauf_vorhanden"] = not durch(o)

    # (b) im Nachlauf, Wanduhr springt.
    o = neu()
    K.rufe(o, M["start"])
    K.rufe(o, M["ende"])
    V["w"] += 3600.0
    ERG["b_nach_wanduhrsprung_durch"] = durch(o)
    V["w"] -= 3600.0

    # (c) monotone Uhr springt -> muss freigeben.
    o = neu()
    K.rufe(o, M["start"])
    K.rufe(o, M["ende"])
    ERG["c_vor_sprung_durch"] = durch(o)
    V["m"] += 5.0
    ERG["c_nach_monosprung_durch"] = durch(o)
    V["m"] -= 5.0
except Exception as exc:
    ERG.update(ok=False, fehler=f"{type(exc).__name__}: {exc}",
               spur=traceback.format_exc()[-900:])

print(json.dumps(ERG, default=str))
PYEOF

lauf() {  # $1 = Skript, ab $2 = Argumente
  local s="$1"; shift
  timeout --foreground --signal=TERM --kill-after=5s "${MAX_SECS}s" \
    "$PY" -B -P "$RT/$s" "$@" 2>"$RT/${s%.py}.log"
}

# =============================================================================
# 1. Bindung
# =============================================================================
echo
echo "--- 1. Bindung ---"
lauf treiber.py "$TARGET" "$RT" >"$RT/kern.json"
if ! jq -e . >/dev/null 2>/dev/null <"$RT/kern.json"; then
  laut "Der Treiber hat keine auswertbare Antwort geliefert."
  laut "Protokoll:"; tail -30 "$RT/treiber.log"
  chk "1 Treiber laeuft" nein ja
  echo; echo "T-3.4: FEHLGESCHLAGEN"; exit 1
fi
S="$(cat "$RT/kern.json")"
echo "  Modul bietet an: $(jq -r '.angebot // [] | join(", ")' <<<"$S")"
if [[ "$(jq -r '.ok' <<<"$S")" != true ]]; then
  laut "Phase: $(jq -r '.phase // "?"' <<<"$S")"
  laut "$(jq -r '.fehler // "?"' <<<"$S")"
  [[ "$(jq -r '.spur // empty' <<<"$S")" ]] && jq -r '.spur' <<<"$S"
fi
chk "1 daimon.ears.interlock laedt und der Vertrag greift" "$(jq -r '.ok' <<<"$S")" true
if [[ "$(jq -r '.ok' <<<"$S")" != true ]]; then
  echo; echo "T-3.4: FEHLGESCHLAGEN — ohne Einstiegspunkt ist nichts gemessen."
  exit 1
fi
geladen="$(jq -r '.datei' <<<"$S")"
echo "  geladen aus: $geladen"
# Fall 12 in HANDOVER.md: eine Positivkontrolle, die nie gruen werden kann,
# meldet jeden Mutanten als "erkannt", ohne dessen Mutation zu messen.
chk "1 POSITIVKONTROLLE: der Pruefling stammt AUS DEM GEPRUEFTEN BAUM" \
  "$([[ "$geladen" == "$TARGET"/* ]] && echo ja || echo "aus_$geladen")" ja
V="$(jq -c '.vertrag' <<<"$S")"
echo "  Form:            $(jq -r '.form' <<<"$V")"
echo "  Entscheider:     $(jq -r '.entscheider' <<<"$V")"
echo "    Kandidaten:    $(jq -r '.entscheider_kandidaten | join(", ")' <<<"$V")"
echo "    verworfen:     $(jq -r '.verworfene_entscheider | join(" ; ")' <<<"$V")"
echo "  Wiedergabe an:   $(jq -r '.start' <<<"$V")   (verworfen: $(jq -r '.verworfene_start | join(" ; ")' <<<"$V"))"
echo "  Wiedergabe aus:  $(jq -r '.ende' <<<"$V")   (verworfen: $(jq -r '.verworfene_ende | join(" ; ")' <<<"$V"))"
echo "  PTT:             $(jq -c '.ptt' <<<"$V")   (verworfen: $(jq -r '.verworfene_ptt | join(" ; ")' <<<"$V"))"
echo "  Referenz:        $(jq -r '.referenz' <<<"$V")   (verworfen: $(jq -r '.verworfene_referenz | join(" ; ")' <<<"$V"))"
echo "  Diagnose:        $(jq -r '.diagnose' <<<"$V")   (verworfen: $(jq -r '.verworfene_diagnose | join(" ; ")' <<<"$V"))"
echo "  Objekt bietet an: $(jq -r '.angebot_objekt | join(", ")' <<<"$V")"
echo "  deklariert: $(jq -c '.deklariert' <<<"$S")"
chk "1 ein Entscheider ueber einen Chunk ist gefunden" \
  "$([[ "$(jq -r '.entscheider' <<<"$V")" != "(keiner)" ]] && echo ja || echo nein)" ja
chk "1 ein Anfang der Wiedergabe ist ansprechbar" \
  "$([[ "$(jq -r '.start' <<<"$V")" != "(keiner)" ]] && echo ja || echo nein)" ja
chk "1 ein Ende der Wiedergabe ist ansprechbar" \
  "$([[ "$(jq -r '.ende' <<<"$V")" != "(keines)" ]] && echo ja || echo nein)" ja

# --- Die Grundlinie. Ohne sie ist jede Sperrmessung die Nullaussage. --------
G="$(jq -c '.grundlinie' <<<"$S")"
echo "  Grundlinie: $G"
chk "1 GRUNDLINIE: ein frisches, unbeschaeftigtes Sperrwerk laesst durch" \
  "$(jqb "$G" frisch_durchlaesst)" true
chk "1 GRUNDLINIE: und beim zweiten Mal immer noch (kein Einmal-Effekt)" \
  "$(jqb "$G" frisch_zweimal)" true

# --- Gegenprobe am AUSWERTER selbst -----------------------------------------
# Zwei Attrappen mit bekanntem Verhalten durch DIESELBE Kette. Ohne sie waere
# jede Zeile darunter von einem verdrehten Vertragssucher nicht zu trennen.
A="$(jq -c '.attrappen // {}' <<<"$S")"
if [[ "$(jq -r 'has("fehler")' <<<"$A")" == true ]]; then
  laut "$(jq -r '.fehler' <<<"$A")"
  [[ "$(jq -r '.spur // empty' <<<"$A")" ]] && jq -r '.spur' <<<"$A"
  chk "1 die Gegenprobe am Auswerter laeuft" nein ja
else
  echo "  Attrappe 'laesst alles durch': $(jq -c '.laesst_alles_durch' <<<"$A")"
  echo "  Attrappe 'sperrt alles':       $(jq -c '.sperrt_alles' <<<"$A")"
  chk "1 GEGENPROBE: die Kette liest eine Attrappe, die alles durchlaesst, als DURCH" \
    "$(jqb "$(jq -c '.laesst_alles_durch' <<<"$A")" frisch_durch)" true
  chk "1 GEGENPROBE: und eine, die alles sperrt, als GESPERRT" \
    "$(jqb "$(jq -c '.sperrt_alles' <<<"$A")" frisch_durch)" false
  chk "1 GEGENPROBE: die Kette unterscheidet die beiden ueberhaupt" \
    "$(jqb "$A" kette_unterscheidet)" true
  chk "1 GEGENPROBE: auch waehrend laufender Wiedergabe bleibt die Attrappe lesbar" \
    "$(jqb "$(jq -c '.laesst_alles_durch' <<<"$A")" waehrend_durch)" true
fi

# =============================================================================
# 2. Nachlauf 500 ms -- die Grenze von BEIDEN Seiten
# =============================================================================
echo
echo "--- 2. Nachlauf 500 ms (die Grenze von beiden Seiten gemessen) ---"
N="$(jq -c '.nachlauf' <<<"$S")"
if [[ "$(jq -r 'has("fehler")' <<<"$N")" == true ]]; then
  laut "$(jq -r '.fehler' <<<"$N")"
  [[ "$(jq -r '.spur // empty' <<<"$N")" ]] && jq -r '.spur' <<<"$N"
  chk "2 der Nachlauf ist messbar" nein ja
else
  jq -r '.runden[] | "  Runde: waehrend_durch=\(.waehrend_wiedergabe_durch) sofort_durch=\(.sofort_danach_durch) (nach \(.sofort_nach_ms) ms) freigabe=\(.freigabe_ms) ms danach_frei=\(.danach_frei)"' <<<"$N"
  echo "  Freigaben: $(jq -c '.freigaben_ms' <<<"$N") ms, Median $(jq -r '.median_ms' <<<"$N") ms"
  chk "2 waehrend der Wiedergabe wird NICHTS durchgelassen" \
    "$(jqb "$N" waehrend_nie_durchgelassen)" true
  # Die Grenze von der einen Seite.
  chk "2 DIE ZUSAGE: unmittelbar nach dem Ende ist gesperrt" \
    "$(jqb "$N" sofort_nie_durchgelassen)" true
  echo "  'unmittelbar' war spaetestens $(jq -r '.sofort_max_ms' <<<"$N") ms nach dem Ende"
  chk "2 und 'unmittelbar' heisst hier auch wirklich unter 20 ms" \
    "$([[ "$(jq -r '.sofort_max_ms' <<<"$N" | cut -d. -f1)" -lt 20 ]] && echo ja || echo nein)" ja
  # Die Grenze von der anderen Seite -- ohne sie besteht auch eine Sperre,
  # die nie aufhoert.
  chk "2 GEGENPROBE: die Sperre endet ueberhaupt (alle drei Runden gaben frei)" \
    "$(jqb "$N" alle_drei_endeten)" true
  med="$(jq -r '.median_ms // -1' <<<"$N" | cut -d. -f1)"
  chk "2 DIE ZUSAGE: die gemessene Grenze liegt bei 500 ms (400..700)" \
    "$([[ "$med" -ge 400 && "$med" -le 700 ]] && echo ja || echo "war_${med}ms")" ja
  chk "2 nach der Freigabe bleibt es frei (kein Flattern)" \
    "$(jqb "$N" danach_immer_frei)" true
  # Zwei-Punkt-Probe an frischen Objekten, ohne Abtasten.
  echo "  Zwei-Punkt-Probe: 400 ms -> durch=$(jq -r '.bei_400ms_durch' <<<"$N")," \
       "650 ms -> durch=$(jq -r '.bei_650ms_durch' <<<"$N")"
  chk "2 ZWEI-PUNKT: 400 ms nach dem Ende ist noch gesperrt" \
    "$(jqb "$N" bei_400ms_durch)" false
  chk "2 ZWEI-PUNKT: 650 ms nach dem Ende ist frei" \
    "$(jqb "$N" bei_650ms_durch)" true
fi

# =============================================================================
# 3. Die Sperre gilt bei gedruecktem PTT -- der Kern unter Plan C
# =============================================================================
echo
echo "--- 3. Sperre gilt bei gedruecktem PTT (mit Positivkontrolle daneben) ---"
P="$(jq -c '.ptt' <<<"$S")"
if [[ "$(jq -r 'has("fehler")' <<<"$P")" == true ]]; then
  laut "$(jq -r '.fehler' <<<"$P")"
  laut "Das Objekt bietet an: $(jq -r '.angebot // [] | join(", ")' <<<"$P")"
  chk "3 der PTT-Zustand ist einspeisbar" nein ja
else
  echo "  $P"
  # ZUERST die Positivkontrolle. Eine Sperre, die immer sperrt, erfuellt die
  # Zusage darunter muehelos und macht das Geraet unbrauchbar.
  chk "3 POSITIVKONTROLLE: ohne Wiedergabe und mit gedruecktem PTT geht es DURCH" \
    "$(jqb "$P" ohne_wiedergabe_ptt_gedrueckt_durch)" true
  chk "3 DIE ZUSAGE: waehrend der Wiedergabe wird bei GEDRUECKTEM PTT nichts durchgelassen" \
    "$(jqb "$P" waehrend_wiedergabe_ptt_gedrueckt_durch)" false
  chk "3 dasselbe bei losgelassener Taste (der PTT-Zustand aendert daran nichts)" \
    "$(jqb "$P" waehrend_wiedergabe_ptt_los_durch)" false
  chk "3 erneutes Druecken oeffnet die laufende Sperre nicht nachtraeglich" \
    "$(jqb "$P" ptt_erneut_gedrueckt_durch)" false
  chk "3 GEGENPROBE: nach dem Nachlauf geht es bei gedruecktem PTT wieder durch" \
    "$(jqb "$P" nach_nachlauf_ptt_gedrueckt_durch)" true
fi

# =============================================================================
# 4. Echo-Referenz -- beide Richtungen, gleiche Laenge
# =============================================================================
echo
echo "--- 4. Echo-Referenz wirkt (beide Richtungen, gleiche Laenge) ---"
EJ="$(jq -c '.echo' <<<"$S")"
if [[ "$(jq -r 'has("fehler")' <<<"$EJ")" == true ]]; then
  laut "$(jq -r '.fehler' <<<"$EJ")"
  laut "Das Objekt bietet an: $(jq -r '.angebot // [] | join(", ")' <<<"$EJ")"
  chk "4 die Echo-Referenz ist einspeisbar und wird auf Chunks angewandt" nein ja
else
  echo "  Ausgabepuffer $(jq -r '.ref_bytes' <<<"$EJ") Byte (0,5 s)," \
       "Echo-Chunk $(jq -r '.chunk_bytes' <<<"$EJ") Byte," \
       "fremder Chunk $(jq -r '.fremd_bytes' <<<"$EJ") Byte"
  echo "  ohne Referenz: $(jq -c '.ohne_referenz' <<<"$EJ")"
  echo "  mit  Referenz: $(jq -c '.mit_referenz' <<<"$EJ")"
  chk "4 die beiden Chunks sind gleich lang (sonst misst der Abgleich die Laenge)" \
    "$(jq -r '.chunk_bytes' <<<"$EJ")" "$(jq -r '.fremd_bytes' <<<"$EJ")"
  chk "4 der Echo-Chunk ist woertlich ein Stueck des Ausgabepuffers" \
    "$(jqb "$EJ" echo_chunk_ist_stueck_der_referenz)" true
  chk "4 der fremde Chunk kommt im Ausgabepuffer NICHT vor" \
    "$(jqb "$EJ" fremd_chunk_nicht_in_der_referenz)" true
  A="$(jq -c '.ohne_referenz' <<<"$EJ")"
  B="$(jq -c '.mit_referenz' <<<"$EJ")"
  # A/B: gleicher Ablauf, ein Unterschied. Ohne A ist "verworfen" von
  # "verwirft ohnehin alles" nicht zu trennen.
  chk "4 POSITIVKONTROLLE: OHNE eingespeiste Referenz geht derselbe Echo-Chunk DURCH" \
    "$(jqb "$A" echo_durch)" true
  chk "4 POSITIVKONTROLLE: und der fremde Chunk auch" \
    "$(jqb "$A" fremd_durch)" true
  chk "4 DIE ZUSAGE: MIT eingespeister Referenz wird der Echo-Chunk VERWORFEN" \
    "$(jqb "$B" echo_durch)" false
  # Die Gegenrichtung. Ohne sie ist "verworfen" von "verwirft alles" nicht
  # zu unterscheiden.
  chk "4 GEGENRICHTUNG: fremdes Material derselben Laenge wird NICHT verworfen" \
    "$(jqb "$B" fremd_durch)" true
  chk "4 GEGENRICHTUNG: und gewoehnliches Mikrofonmaterial ebensowenig" \
    "$(jqb "$B" neutral_durch)" true
fi

# =============================================================================
# 5. Diagnose -- Beleg, nicht Messgrundlage (Regel 9)
# =============================================================================
echo
echo "--- 5. Sperrzustand in der Diagnose (Beleg, NICHT Messgrundlage) ---"
D="$(jq -c '.diagnose' <<<"$S")"
if [[ "$(jq -r 'has("fehler")' <<<"$D")" == true ]]; then
  laut "$(jq -r '.fehler' <<<"$D")"
  laut "Das Objekt bietet an: $(jq -r '.angebot // [] | join(", ")' <<<"$D")"
  chk "5 ein Diagnosebild ist abrufbar" nein ja
else
  echo "  Schluessel: $(jq -c '.frisch_schluessel' <<<"$D")"
  echo "  gesperrt:   $(jq -c '.bild_gesperrt' <<<"$D")  Verhalten: durch=$(jq -r '.verhalten_gesperrt_durch' <<<"$D")"
  echo "  frei:       $(jq -c '.bild_frei' <<<"$D")  Verhalten: durch=$(jq -r '.verhalten_frei_durch' <<<"$D")"
  chk "5 das Bild enthaelt 'gesperrt'" "$(jqb "$D" hat_gesperrt)" true
  chk "5 das Bild enthaelt 'grund'"    "$(jqb "$D" hat_grund)" true
  chk "5 das Bild enthaelt 'bis'"      "$(jqb "$D" hat_bis)" true
  chk "5 'grund' ist waehrend der Sperre eine nichtleere Zeichenkette" \
    "$(jqb "$D" grund_bei_sperre_gesetzt)" true
  chk "5 'bis' ist eine Zahl oder None (ein Zeitpunkt, keine Prosa)" \
    "$(jqb "$D" bis_ist_zahl_oder_none)" true
  # Und jetzt der Punkt: gewertet wird die UEBEREINSTIMMUNG mit dem
  # gemessenen Verhalten. Nicht das Bild allein -- Regel 9.
  chk "5 das Bild stimmt mit dem VERHALTEN ueberein, solange gesperrt ist" \
    "$(jqb "$D" stimmt_bei_sperre)" true
  chk "5 und ebenso, nachdem der Nachlauf abgelaufen ist" \
    "$(jqb "$D" stimmt_bei_frei)" true
  echo "  (kein Kriterium aus 1-3 haengt an dieser Auskunft; sie ist hier"
  echo "   ausschliesslich Beleg, dass der Pruefling dasselbe meint)"
fi

# =============================================================================
# 6. Monotone Zeit
# =============================================================================
echo
echo "--- 6. Monotone Zeit (Uhren im Treiber ersetzt, ohne root) ---"
lauf treiber_uhr.py "$TARGET" "$RT" >"$RT/uhr.json"
if ! jq -e . >/dev/null 2>/dev/null <"$RT/uhr.json"; then
  laut "Der Uhrentreiber hat keine auswertbare Antwort geliefert."
  tail -25 "$RT/treiber_uhr.log"
  chk "6 Uhrentreiber laeuft" nein ja
else
  U="$(cat "$RT/uhr.json")"
  echo "  $U"
  if [[ "$(jq -r '.ok' <<<"$U")" != true ]]; then
    laut "$(jq -r '.fehler // "?"' <<<"$U")"
    [[ "$(jq -r '.spur // empty' <<<"$U")" ]] && jq -r '.spur' <<<"$U"
  fi
  chk "6 Uhrentreiber laeuft" "$(jq -r '.ok' <<<"$U")" true
  # ZUERST die Messkette. Ohne sie sagt der Rest nichts.
  chk "6 POSITIVKONTROLLE: die eingesetzte monotone Uhr ist vor dem Sprung nicht abgelaufen" \
    "$(jqb "$U" kette_vor_sprung_abgelaufen)" false
  chk "6 POSITIVKONTROLLE: nach +5 s ist dieselbe Frist abgelaufen (der Versatz wirkt)" \
    "$(jqb "$U" kette_nach_mono_sprung_abgelaufen)" true
  chk "6 POSITIVKONTROLLE: die Wanduhr springt im Treiber um 3600 s" \
    "$(jq -r '.kette_wanduhr_sprung_s // -1' <<<"$U")" 3600.0
  # (a) laufende Wiedergabe, Wanduhr springt.
  chk "6 (a) waehrend laufender Wiedergabe ist gesperrt" \
    "$(jqb "$U" a_vor_sprung_durch)" false
  chk "6 DIE ZUSAGE (a): eine um 1 h gestellte WANDUHR hebt die Sperre nicht auf" \
    "$(jqb "$U" a_nach_wanduhrsprung_durch)" false
  # (b) und (c) messen BEIDE am Nachlauf -- der eine, dass die Wanduhr ihn
  # nicht verkuerzt, der andere, dass die monotone Uhr ihn beendet. Beide
  # setzen voraus, DASS es einen Nachlauf gibt. Ohne ihn saehe (c) "frei" und
  # (b) "frei", und beides haette mit der Uhr nichts zu tun.
  #
  # Deshalb sind sie hier ausdruecklich an `nachlauf_vorhanden` gebunden. Das
  # ist keine Nachsicht, sondern Trennung: ein fehlender Nachlauf ist ein
  # Befund von Abschnitt 2, und wenn er auch hier zuschluege, faellt ein
  # Mutant an zwei Stellen und man weiss nicht mehr, welche ihn gefangen hat.
  if [[ "$(jqb "$U" nachlauf_vorhanden)" == true ]]; then
    chk "6 DIE ZUSAGE (b): auch im Nachlauf hebt die gestellte Wanduhr nichts auf" \
      "$(jqb "$U" b_nach_wanduhrsprung_durch)" false
    chk "6 (c) unmittelbar nach dem Ende ist gesperrt" \
      "$(jqb "$U" c_vor_sprung_durch)" false
    # Die Positivkontrolle AM PRUEFLING: liest er ueberhaupt eine Uhr?
    chk "6 POSITIVKONTROLLE (c): eine um 5 s vorgestellte MONOTONE Uhr gibt frei" \
      "$(jqb "$U" c_nach_monosprung_durch)" true
  else
    laut "(b) UND (c) NICHT GEMESSEN: der Pruefling hat 50 ms nach dem Ende"
    laut "    der Wiedergabe schon freigegeben, also gibt es keinen Nachlauf,"
    laut "    an dem sich eine Uhr zeigen koennte. Damit ist von der monotonen"
    laut "    Zeit nur (a) belegt -- die laufende Wiedergabe. Das ist ein"
    laut "    Befund von Abschnitt 2, nicht von diesem hier."
    laut "    (Rohwerte, ungewertet: b=$(jqb "$U" b_nach_wanduhrsprung_durch)" \
         "c_vor=$(jqb "$U" c_vor_sprung_durch)" \
         "c_nach=$(jqb "$U" c_nach_monosprung_durch))"
  fi
fi

# =============================================================================
# 7. Threadsicherheit
# =============================================================================
echo
echo "--- 7. Threadsicherheit (offener Punkt aus T-3.3, hier entschieden) ---"
T="$(jq -c '.threads' <<<"$S")"
if [[ "$(jq -r 'has("fehler")' <<<"$T")" == true ]]; then
  laut "$(jq -r '.fehler' <<<"$T")"
  [[ "$(jq -r '.spur // empty' <<<"$T")" ]] && jq -r '.spur' <<<"$T"
  chk "7 der Nebenlauf ist messbar" nein ja
else
  echo "  $(jq -r '.fenster' <<<"$T") Wiedergaben," \
       "$(jq -r '.proben' <<<"$T") Proben," \
       "davon $(jq -r '.proben_in_wiedergabe' <<<"$T") mitten in einer Wiedergabe"
  echo "  Ausnahmen: $(jq -c '.ausnahmen_schreiber' <<<"$T") / $(jq -c '.ausnahmen_leser' <<<"$T")"
  chk "7 25 Wechsel sind tatsaechlich gelaufen" "$(jq -r '.fenster' <<<"$T")" 25
  # POSITIVKONTROLLE: ohne Proben INNERHALB der Fenster waere "0 Lecks" die
  # Nullaussage schlechthin.
  chk "7 POSITIVKONTROLLE: es gab ueberhaupt Proben mitten in einer Wiedergabe" \
    "$([[ "$(jq -r '.proben_in_wiedergabe' <<<"$T")" -ge 100 ]] && echo ja || echo "nur_$(jq -r '.proben_in_wiedergabe' <<<"$T")")" ja
  chk "7 DIE ZUSAGE: kein einziger Chunk kam waehrend einer Wiedergabe durch" \
    "$(jq -r '.lecks_in_wiedergabe' <<<"$T")" 0
  chk "7 keine Ausnahme in einem der beiden Threads" \
    "$(jq -r '.ausnahmen' <<<"$T")" 0
  chk "7 nach dem Sturm gibt die Sperre wieder frei (kein Zaehler verhakt)" \
    "$(jqb "$T" am_ende_frei)" true
fi

# =============================================================================
# 8. Die Konfigurationsdatei -- GEMELDET, NICHT GEWERTET
# =============================================================================
echo
echo "--- 8. Konfiguration (gemeldet, nicht gewertet) ---"
TOML="$TARGET/config/daimon.toml"
[[ -f "$TOML" ]] || TOML="$REPO/config/daimon.toml"
tomlwert="$("$PY" -B -c '
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    d = tomllib.load(f)
ears = d.get("ears") or {}
aus = []
for name in ("sperre", "interlock", "rueckkopplung", "echo"):
    abschnitt = ears.get(name)
    if isinstance(abschnitt, dict):
        aus.append(f"[ears.{name}] {abschnitt}")
print(" ; ".join(aus) if aus else "kein Abschnitt fuer die Sperre")
' "$TOML" 2>/dev/null)"
# Was in der Datei steht, ist eine Angabe ueber sich selbst -- gemessen ist
# oben die Wirkung. Und ein Fixture-Baum bringt seine eigene (womoeglich
# beschnittene) Konfiguration mit; daran duerfte ein Mutant nicht scheitern,
# dessen Mutation ganz woanders sitzt.
echo "  $TOML: ${tomlwert:-(nicht lesbar)}"

# =============================================================================
# OFFEN, und zwar benannt
# =============================================================================
echo
echo "--- OFFEN, und zwar benannt ---"
echo "  (1) DIE AKUSTISCHE KETTE IST UNGEMESSEN. 'Lautsprecher -> Raum ->"
echo "      Mikrofon -> STT' braucht TTS (piper fehlt, T-3.9) und ein echtes"
echo "      Rueckkopplungsereignis. Belegt ist hier die LOGIK: Sperrzustaende,"
echo "      Nachlaufgrenze, Echo-Abgleich an eingespeisten Puffern, Verhalten"
echo "      bei gedruecktem PTT. Der akustische Nachweis gehoert nach T-3.15."
echo "  (2) DER ECHO-ABGLEICH IST AN EINER WOERTLICHEN KOPIE gemessen. Ein"
echo "      realer Raumhall ist gedaempft, verzoegert und gefiltert; ob der"
echo "      Abgleich das noch faengt, sagt dieser Verifizierer NICHT. Was er"
echo "      sagt: die Referenz wirkt ueberhaupt, und sie wirkt nicht auf"
echo "      fremdes Material. Die Schwelle zwischen beidem ist ungemessen."
echo "  (3) DIE 400..700 MS SIND EIN KORRIDOR. Eine Umsetzung mit 450 ms und"
echo "      eine mit 650 ms sind hier beide gruen. Gemessen ist, DASS es eine"
echo "      Grenze gibt und wo sie ungefaehr liegt -- nicht, dass genau 500"
echo "      irgendwo im Quelltext steht. Der ausgedruckte Median sagt es."
echo "  (4) FAIL-CLOSED IST NUR ZUR HAELFTE GEPRUEFT. Gemessen: eine"
echo "      angemeldete, nie beendete Wiedergabe sperrt (Abschnitt 6a). NICHT"
echo "      gemessen: das Verhalten bei fehlender Referenz oder bei einem"
echo "      abgestuerzten Sprecher -- dafuer braucht es den Ears-Agenten aus"
echo "      T-3.7, hier gibt es nur den Baustein."
echo "  (5) THREADSICHERHEIT IST AN EINEM MUSTER GEMESSEN, nicht bewiesen."
echo "      Zwei Threads, 25 Wechsel, ein hammernder Leser. Ein Pruefling ohne"
echo "      Lock, dessen Pfade sich in genau diesem Muster nicht ueberschneiden,"
echo "      waere hier gruen. Was der Abschnitt ausschliesst, ist die grobe"
echo "      Form: Ausnahmen unter Nebenlauf und ein Chunk, der mitten in einer"
echo "      laufenden Wiedergabe durchkommt."
echo "  (6) DIE MONOTONE UHR IST IM PROZESS ERSETZT, nicht im Kernel. Eine"
echo "      echte NTP-Korrektur oder Zeitumstellung ist ohne root nicht"
echo "      herzustellen. Gemessen ist, dass ein Sprung der WANDUHR-Funktion"
echo "      nichts bewirkt und ein Sprung der MONOTONEN Funktion alles --"
echo "      also dass der Pruefling die richtige der beiden liest."
echo "  (7) DER EINSTIEGSPUNKT WIRD GESUCHT, nicht vorgeschrieben. Welche"
echo "      Klasse und welche Methoden gegriffen haben, steht oben im"
echo "      Protokoll -- samt der verworfenen Kandidaten und dem Grund. Die"
echo "      POLARITAET kommt aus dem NAMEN ('durchlassen' gegen 'gesperrt'),"
echo "      nicht aus einer Messung; anders liesse sich eine Sperre, die immer"
echo "      sperrt, nicht von einer unterscheiden, die nie sperrt."
echo "  (8) DIE KETTE TOML -> SPERRE IST NICHT GEMESSEN. Abschnitt 8 meldet"
echo "      nur, was in der Konfiguration steht. Dass beim Start genau dieser"
echo "      Wert ankommt, entscheidet die Verdrahtung im Ears-Agenten."

echo
if [[ "$fail" -eq 0 ]]; then
  echo "T-3.4: gruen. Waehrend der Wiedergabe und 500 ms darueber hinaus geht"
  echo "       kein Chunk zur Transkription -- auch nicht bei gehaltener"
  echo "       PTT-Taste --, danach wieder jeder; ein eingespeister"
  echo "       Ausgabepuffer laesst genau das Material verwerfen, in dem er"
  echo "       vorkommt, und fremdes derselben Laenge nicht; eine um eine"
  echo "       Stunde gestellte Wanduhr hebt nichts auf, eine vorgestellte"
  echo "       monotone Uhr alles; und das Diagnosebild sagt dasselbe wie das"
  echo "       gemessene Verhalten."
else
  echo "T-3.4: FEHLGESCHLAGEN"
fi
exit $fail
