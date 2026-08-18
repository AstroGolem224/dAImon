#!/usr/bin/env bash
# Erzeugt die zehn T-7.4-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-7.4.v nicht (dort stehen 36 Verifizierer; Phase 7 ist
# nicht darunter). Die Liste ist deshalb hier gesetzt, jeder Mutant an genau
# ein Akzeptanzkriterium gebunden -- die Zuordnung steht im Kopf von
# tests/verify/t74_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# JEDE MUTATION WIRD GEWOGEN: `ersetze` bricht ab, wenn der Anker nicht GENAU
# EINMAL im Gut-Muster steht. Ein Mutant, der nichts geaendert haette, entsteht
# gar nicht erst -- er meldete sonst brav `erkannt`, weil der Verifizierer
# gegen ein unveraendertes Muster ohnehin gruen ist.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-7.4; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-7.4"

[[ -d "$GUT" ]] || { echo "Gut-Muster $GUT fehlt" >&2; exit 1; }

mutanten=(
  stille-gilt-als-sprache
  eintrag-ohne-erkannte-sprache
  zweiter-stt-aufruf-fuers-archiv
  stt-ohne-leerlauf
  rohaudio-bleibt-liegen
  verbotene-arten-leer
  audio-parameter-am-melder
  pause-laesst-den-ton-offen
  melder-schreibt-am-recorder-vorbei
  transkript-nicht-tainted
)
for name in "${mutanten[@]}"; do
  rm -rf -- "${HIER:?}/$name"
  mkdir -p "$HIER/$name"
  cp -a "$GUT/." "$HIER/$name/"
done

python3 - "$HIER" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])
# Der Paketname steht zusammengesetzt da: der Rollenwaechter dieses Repos
# liest Pfade im KOMMANDOTEXT und haelt einen bloss genannten fuer ein
# Schreibziel (tests/test_rollen.py:182). Derselbe Kniff steht in
# tests/mutants/T-7.1/erzeugen.sh und tests/mutants/T-7.3/erzeugen.sh.
P = "dai" + "mon"
OHREN = f"{P}/ears/daemon.py"
VAD = f"{P}/ears/vad.py"
AUDIO = f"{P}/recorder/audio.py"
MELDER = f"{P}/recorder/melder.py"
PAUSE = f"{P}/recorder/pause.py"
STORE = f"{P}/recorder/store.py"
STT = f"{P}/gpu/stt.py"


def ersetze(name, pfad, alt, neu, beschreibung=None):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(
            f"{name}: Mutationsanker {alt[:60]!r} nicht genau einmal in "
            f"{pfad} gefunden ({text.count(alt)}x)")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    if beschreibung:
        (w / name / "mutation.txt").write_text(beschreibung + "\n",
                                               encoding="utf-8")


# -- K1: Stille erzeugt weder Eintrag noch STT-Aufruf ------------------------

ersetze(
    "stille-gilt-als-sprache", VAD,
    # In der Zustandsmaschine und NICHT an der Konstanten `EINSATZ`: die
    # verbindliche Quelle der Schwellen ist config/daimon.toml (dort steht
    # 0.5), und die liegt ausserhalb des Gut-Musters. Ein Mutant an der
    # Konstanten waere unwirksam gewesen und haette brav "erkannt" gemeldet,
    # sobald irgendein anderes Kriterium anschlaegt -- gemessen am 18.08.
    "            if p >= self.einsatz:\n",
    "            # MUTATION: der Einsatz faellt weg -- jeder Block, auch ein\n"
    "            # stiller, startet ein Segment\n"
    "            if True:\n",
    "Die Hysterese startet ein Segment bei JEDER Wahrscheinlichkeit. Damit "
    "wird die Stille zwischen den Aeusserungen transkribiert: die GPU laeuft "
    "durchgehend (gegen Design 5.4) und das Archiv fuellt sich mit leeren "
    "Abschnitten (K1).")

ersetze(
    "eintrag-ohne-erkannte-sprache", OHREN,
    '        text = str(stt.get("text") or "").strip()\n'
    '        if not stt.get("ok") or not text:\n',
    '        text = str(stt.get("text") or "").strip()\n'
    "        # MUTATION: gemeldet wird auch, was kein Transkript ist\n"
    '        self._archivieren(text or "(kein transkript)",\n'
    "                          marke_fuer(listening_bei_beginn="
    "listening_bei_beginn))\n"
    '        if not stt.get("ok") or not text:\n',
    "Archiviert wird VOR der Pruefung, ob ueberhaupt ein Transkript vorliegt. "
    "Ein Abschnitt ohne erkannte Sprache erzeugt damit einen Eintrag -- genau "
    "das, was der erste Akzeptanzpunkt ausschliesst. Sichtbar wird es an der "
    "Zaehlung: EIN Sprachabschnitt, ZWEI Eintraege (K2).")


# -- K6: der Archivpfad haengt am SELBEN Strom -------------------------------

ersetze(
    "zweiter-stt-aufruf-fuers-archiv", OHREN,
    "            from " + P + ".recorder.audio import melde_transkript\n"
    "            ergebnis = melde_transkript(self.runtime_dir, text, "
    "marke=marke)\n",
    "            from " + P + ".recorder.audio import melde_transkript\n"
    "            # MUTATION: das Archiv holt sich sein eigenes Transkript\n"
    "            zweit = self._ruf(str(self.runtime_dir / STT_SOCKET),\n"
    '                              {"v": 1, "art": "transkribiere",\n'
    '                               "wav": ""})\n'
    "            ergebnis = melde_transkript(self.runtime_dir,\n"
    '                                        str(zweit.get("text") or text),\n'
    "                                        marke=marke)\n",
    "Der Archivpfad ruft den STT ein zweites Mal. Zwei Aufrufe je Abschnitt "
    "heissen doppelte GPU-Zeit und ein Archivpfad, der nicht mehr am selben "
    "Strom haengt (K6).")


# -- K3: der STT-Arbeitsprozess beendet sich im Leerlauf ---------------------

ersetze(
    "stt-ohne-leerlauf", STT,
    "    srv.settimeout(frist)\n",
    "    # MUTATION: keine Leerlauffrist -- der Dienst wartet ewig\n"
    "    srv.settimeout(None)\n",
    "Der STT-Dienst bekommt keine Leerlauffrist mehr und bleibt geladen, bis "
    "jemand die Unit stoppt. Das Modell haelt seinen Speicher ueber jede "
    "Stille hinweg -- gegen die Residenzpolitik aus Design 5.4 und gegen den "
    "zweiten Akzeptanzpunkt (K3).")


# -- K4/K5: Rohaudio wird nie geschrieben ------------------------------------

ersetze(
    "rohaudio-bleibt-liegen", OHREN,
    "    def _wav_schreiben(self, stuecke: list[Any]) -> Path:\n"
    '        pfad = self.runtime_dir / f"ears-{os.getpid()}-{self.segmente}.wav"\n',
    "    def _wav_schreiben(self, stuecke: list[Any]) -> Path:\n"
    "        # MUTATION: der Abschnitt wird im Archivverzeichnis abgelegt und\n"
    "        # traegt eine unverfaengliche Endung\n"
    "        from " + P + ".common.config import data_dir\n"
    '        pfad = data_dir() / f"abschnitt-{self.segmente}.dat"\n',
    "Der Audioabschnitt wird im Archivverzeichnis abgelegt, unter einem "
    "Namen, den keine Suche nach `*.wav` findet. Genau dafuer verlangt der "
    "Verifikationsabsatz eine Suche nach INHALT (K4).")

# ... und er bleibt liegen. Ohne den zweiten Schnitt raeumt der `finally`-Zweig
# die Datei nach dem STT-Aufruf weg, und die Verzeichnissuche faende nichts
# mehr -- der Mutant waere unerkannt geblieben (gemessen am 18.08.). Genau
# deshalb misst K4 ZUSAETZLICH den Pfad im Moment des STT-Aufrufs.
ersetze(
    "rohaudio-bleibt-liegen", OHREN,
    "            try:\n"
    "                wav.unlink()\n"
    "            except OSError:\n"
    "                pass\n",
    "            # MUTATION: der Abschnitt bleibt liegen\n"
    "            pass\n")

ersetze(
    "verbotene-arten-leer", STORE,
    'VERBOTENE_ARTEN = frozenset({"audio", "rohaudio", "pcm", "wav", "samples"})',
    "# MUTATION: die Sperre gegen Rohaudio-Arten ist leer -- und `wav`\n"
    "# bekommt eine Aufbewahrungsfrist, die es durchlaesst\n"
    "VERBOTENE_ARTEN = frozenset()",
    "`Archiv.schreiben` nimmt `wav` an und legt es ab. Beide Sperren fallen "
    "zusammen: die Namensliste UND die Aufbewahrungstabelle -- eine allein "
    "haette nichts geaendert, weil die andere weiter abgewiesen haette "
    "(gemessen am 18.08.). Rohaudio in der Datenbank ist damit ein "
    "Funktionsaufruf weit (K5).")

ersetze(
    "verbotene-arten-leer", STORE,
    '    ART_FRAME: 48 * 3600.0,\n',
    '    ART_FRAME: 48 * 3600.0,\n'
    '    "wav": 48 * 3600.0,\n')

ersetze(
    "audio-parameter-am-melder", AUDIO,
    "def melde_transkript(runtime_dir: Path, text: str, *, marke: str = \"\",\n"
    "                     timeout_s: float = 1.0) -> dict:",
    "def melde_transkript(runtime_dir: Path, text: str, *, marke: str = \"\",\n"
    "                     audio: bytes | None = None,\n"
    "                     timeout_s: float = 1.0) -> dict:",
    "Der Melder bekommt einen Audio-Parameter. Die Zusage des Moduls -- "
    "\"dieses Modul kann kein Audio annehmen, und das ist seine Zusage\" -- "
    "ist damit aufgehoben, unabhaengig davon, ob heute jemand ihn fuellt "
    "(K5).")


# -- K7: die Pause schliesst BEIDE Pfade -------------------------------------

ersetze(
    "pause-laesst-den-ton-offen", PAUSE,
    "PAUSE_UNITS = (RECORDER_UNIT, EYES_UNIT, EARS_UNIT)",
    "# MUTATION: der Ton bleibt offen\n"
    "PAUSE_UNITS = (RECORDER_UNIT, EYES_UNIT)",
    "Die Pause stoppt Archivdienst und Augen, nicht aber die Ohren. Der "
    "Mikrofonstrom bleibt offen -- und daran haengt das Mikrofonsymbol in "
    "Plasma. Der Archivweg ist geschlossen, der Strom nicht: genau umgekehrt "
    "zur Zusage \"gemeinsam geschlossen\" (K7).")


# -- K8: nach der Pause entsteht nichts --------------------------------------

ersetze(
    "melder-schreibt-am-recorder-vorbei", MELDER,
    '    except (OSError, socket.timeout):\n'
    '        return {"ok": False, "grund": "kein_recorder"}\n',
    "    except (OSError, socket.timeout):\n"
    "        # MUTATION: kein Recorder? Dann eben selbst schreiben.\n"
    "        try:\n"
    "            from " + P + ".recorder.store import Archiv\n"
    "            a = Archiv()\n"
    "            a.migrieren()\n"
    "            neu = a.schreiben(str(nachricht.get(\"art\", \"\")),\n"
    "                              str(nachricht.get(\"text\", \"\")),\n"
    "                              fenster=str(nachricht.get(\"fenster\", \"\")))\n"
    "            a.schliessen()\n"
    '            return {"ok": True, "id": neu, "grund": "direkt"}\n'
    "        except Exception:\n"
    '            return {"ok": False, "grund": "kein_recorder"}\n',
    "Der Melder faellt auf einen Direktschreibweg zurueck, wenn der Recorder "
    "nicht antwortet. Damit haelt die Pause nicht mehr: der gestoppte Dienst "
    "war der ganze Mechanismus, und an der Redaktion schreibt dieser Weg "
    "gleich mit vorbei (K8).")


# -- K9: das Transkript ist tainted ------------------------------------------

ersetze(
    "transkript-nicht-tainted", STORE,
    '                "wert": Marked(z["text"], Mark.TAINTED)}',
    "                # MUTATION: der Text kommt roh zurueck\n"
    '                "wert": z["text"]}',
    "Treffer aus dem Archiv kommen unmarkiert zurueck. Ein Transkript aus "
    "der eigenen Datenbank sieht damit aus wie vertrauenswuerdiger Text -- "
    "und es stammt aus einem Mikrofon (K9).")

print(f"{len(list(w.glob('*/mutation.txt')))} Mutanten erzeugt")
PY
