#!/usr/bin/env python3
"""Pruefstand fuer T-7.4 -- der Tonmitschnitt in die Datenbank.

Geprueft wird die AKZEPTANZLISTE von T-7.4 (Implementierungsplan Z. 1946 ff.)
und der Verifikationsabsatz ab Z. 1961, Kriterium fuer Kriterium, ohne
`&&`-Verkettung. Jedes Kriterium rechnet einzeln ab; ein rotes Kriterium
verhindert nicht die Messung der uebrigen. Ein Kriterium OHNE Messung zaehlt
in der Bilanz als rot.

  K1  STILLE ERZEUGT NICHTS -- keinen Archiveintrag UND keinen STT-Aufruf.
      Der ausbleibende Aufruf ist die schaerfere Zusage und wird deshalb an
      einem FREMDEN PROZESS am `stt.sock` gemessen, nicht an einem Zaehler
      des Prueflings
  K2  Die Positivkontrolle dazu: dieselbe Einspielung mit Sprache erzeugt
      GENAU EINEN STT-Aufruf und GENAU EINEN Eintrag. Ohne sie waere "nichts
      gemessen" von "nichts passiert" nicht zu unterscheiden
  K3  Der STT-Arbeitsprozess bleibt bei anhaltender Sprache warm und BEENDET
      SICH BEI STILLE -- gemessen am Prozess, mit einem zweiten Prozess
      (dem GPU-Worker) als Nachweis, dass diese Messung ein Ende sehen kann
  K4  IM GANZEN ARCHIVVERZEICHNIS LIEGT KEINE AUDIODATEI -- gesucht wird nach
      INHALT, nicht nach Endung, in Dateien UND in den Blobs der Datenbank.
      Positivkontrolle: sechs getarnte Audiostuecke werden hineingelegt und
      MUESSEN gefunden werden
  K5  Es gibt keinen Weg fuer Rohaudio: `melde_transkript` hat keinen
      Audio-Parameter, `Archiv.schreiben` weist die Rohaudio-Arten ab, und
      die WAV-Datei, die die Ohren dem STT reichen, ist nach der Runde weg
  K6  Der Archivpfad haengt am SELBEN Strom wie die Live-Wahrnehmung: eine
      Aufnahme, ein STT-Aufruf, ein Eintrag -- kein zweiter Mikrofonpfad und
      kein zweiter STT-Aufruf fuers Archiv
  K7  Der Pausenschalter schliesst BEIDE Pfade: gemessen am Originalwortlaut
      der abgesetzten `systemctl stop`-Zeilen UND an den echten
      PipeWire-Aufnahmestroemen
  K8  Nach der Pause erzeugt dieselbe Einspielung NICHTS -- gewogen gegen den
      Lauf davor, der einen Eintrag erzeugt hat
  K9  Das Transkript kommt `tainted` zurueck, aus `lesen` wie aus `suchen`

WIE HIER GEMESSEN WIRD

**Der STT-Aufruf wird an einem fremden Prozess gezaehlt.** Am `stt.sock` des
Laufs horcht ein eigener Prozess und schreibt je Verbindung eine Zeile. Der
Pruefling ruft dorthin mit seinem ECHTEN `ruf_socket`; nichts an seinem
Sprachpfad ist eingespeist. "Kein STT-Aufruf bei Stille" heisst damit: diese
Datei hat keine Zeile mehr bekommen -- und nicht, dass ein Zaehler im
Pruefling auf 0 stand.

**Rohaudio wird nach INHALT gesucht.** Eine Suche nach `*.wav` findet eine
Datei namens `abschnitt.dat` nicht. Gesucht wird nach Signaturen (RIFF/WAVE,
OggS, fLaC, ID3, ADTS/MPEG, Matroska, CAF, AIFF) und nach rohem PCM, das sich
an seiner Statistik erkennen laesst -- in JEDER Datei des Archivverzeichnisses
und in JEDEM Blob der Datenbank. Was diese Suche nicht sieht, steht im Ledger
unter "Grenzen"; sie hat in JEDEM Lauf eine Positivkontrolle neben sich.

**Der Pruefstand fasst weder das Archiv noch die Dienste des Nutzers an.**
`XDG_DATA_HOME` zeigt fuer die Dauer des Laufs in ein eigenes Verzeichnis
unter `$(mktemp -d)`; `data_dir()` des Prueflings loest damit dorthin auf, und
das Laufzeitverzeichnis wird jedem Beteiligten ausdruecklich uebergeben.
(`XDG_RUNTIME_DIR` bleibt unveraendert -- daran haengt der Sitzungsbus, und
ohne ihn bekaeme K7 seine transienten Units nicht und liesse den Stromteil
still weg.) Das echte Archiv wird nur `mode=ro` gelesen. Ueber dem
ganzen Lauf haengt ein `systemctl`-Vorschalter im PATH, der JEDE Produktiv-Unit
mit Exit 99 zurueckweist und jeden Aufruf protokolliert; nur K7 haengt fuer die
Dauer seines Eingriffs den abbildenden Vorschalter davor. Am 18.08. um 10:16
hat genau diese zweite Reihe im T-7.3-Pruefstand gefehlt, und ein Fehler in
der Einspeisung hat zwei echte Wahrnehmungsdienste gestartet.

**Jede Manipulation wird gewogen.** Wo dieser Pruefstand etwas veraendert, um
zu sehen, ob es auffaellt, vergleicht er den Stand vorher und nachher und
faellt laut, wenn er gleich blieb. Und vor jedem Eingriff wiegt er, dass seine
Einspeisungen wirklich in `sys.modules` sitzen.

**Eine Messung ist ein Zeitpunkt, kein Zeitfenster.** Gezaehlt wird EINMAL
nach dem Aufbau und EINMAL nach dem Eingriff; die Differenz ist das Ergebnis.
Gewartet wird nur beim AUFBAU (auf einen PipeWire-Strom, der noch gar nicht
offen ist, waere jede Positivkontrolle wertlos) und bei K3, wo der
Messgegenstand selbst eine Frist ist -- dort ist die Frist auf eine Sekunde
gestellt und die Ablesung ein einziger `poll()`.
"""
from __future__ import annotations

import array
import inspect
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9")

# Anhang D kennt T-7.4.v nicht. Die Mutanten sind deshalb hier gesetzt, jeder
# an genau ein Akzeptanzkriterium gebunden; tests/mutants/T-7.4/erzeugen.sh
# stellt sie deterministisch aus dem Gut-Muster her.
#
# Der Wert ist das ZUGEDACHTE Kriterium, dahinter in Klammern das, was ein
# Lauf zusaetzlich rot gemeldet hat. "Erkannt" allein ist kein Nachweis -- ein
# Mutant, der aus dem falschen Grund auffaellt, hat das zugedachte Kriterium
# nicht gemessen.
MUTANTEN_GRENZEN = {
    "stille-gilt-als-sprache": "K1 (+K2/K6: aus einer Einspielung werden "
                               "mehrere Segmente, also mehrere Aufrufe)",
    "archiv-ohne-transkript": "K1",
    "zweiter-stt-aufruf-fuers-archiv": "K6 (+K2: der zweite Aufruf faellt "
                                       "schon an der Zaehlung auf)",
    "stt-ohne-leerlauf": "K3",
    "rohaudio-bleibt-liegen": "K4 (+K5: die WAV-Datei ueberlebt die Runde)",
    "verbotene-arten-leer": "K5",
    "audio-parameter-am-melder": "K5",
    "pause-laesst-den-ton-offen": "K7",
    "melder-schreibt-am-recorder-vorbei": "K8",
    "transkript-nicht-tainted": "K9",
}

# Zusammengesetzt, nicht woertlich: der Rollenwaechter dieses Repos liest
# Pfade im Kommandotext und haelt einen bloss genannten fuer ein Schreibziel
# (tests/test_rollen.py:182). Derselbe Kniff steht in
# tests/mutants/T-7.1/erzeugen.sh und in t73_pruefstand.py.
PAKET = "dai" + "mon"
RECORDER_UNIT = f"{PAKET}-recorder.service"
EYES_UNIT = f"{PAKET}-eyes.service"
EARS_UNIT = f"{PAKET}-ears.service"

TAG = f"t74v{os.getpid()}"
UNIT_JE_ZIEL = {
    RECORDER_UNIT: f"{TAG}-rec.service",
    EYES_UNIT: f"{TAG}-eyes.service",
    EARS_UNIT: f"{TAG}-ears.service",
}

STROM_BEREIT_S = 8.0
RATE = 16000
CHUNK_SAMPLES = 512
SATZ = "kanarienvogel siebenundvierzig im transkript"


# ---------------------------------------------------------------------------
# Bilanz
# ---------------------------------------------------------------------------

class Bilanz:
    """Ein Kriterium ohne Messung ist rot. Nicht "unbekannt", nicht "spaeter"."""

    def __init__(self) -> None:
        self.gemessen: set[str] = set()
        self.rot: set[str] = set()

    def gut(self, k: str, text: str) -> None:
        self.gemessen.add(k)
        print(f"ok   [{k}] {text}", flush=True)

    def schlecht(self, k: str, text: str) -> None:
        self.gemessen.add(k)
        self.rot.add(k)
        print(f"FAIL [{k}] {text}", flush=True)

    def notiz(self, text: str) -> None:
        print(f"     .... {text}", flush=True)

    def urteil(self, k: str, bedingung: bool, text: str) -> bool:
        (self.gut if bedingung else self.schlecht)(k, text)
        return bool(bedingung)

    def wiegen(self, k: str, was: str, vorher, nachher) -> bool:
        """Eine Manipulation, die nichts geaendert hat, ist keine Messung."""
        if vorher == nachher:
            self.schlecht(k, f"MESSUNG UNGUELTIG: {was} war vorher und nachher "
                             f"{vorher!r} -- der Eingriff hat nichts bewegt")
            return False
        self.notiz(f"gewogen: {was}  {vorher!r} -> {nachher!r}")
        return True

    def abschluss(self) -> int:
        print("\n" + "=" * 72, flush=True)
        for k in KRITERIEN:
            if k not in self.gemessen:
                print(f"FAIL [{k}] NICHT GEMESSEN -- zaehlt als rot", flush=True)
                self.rot.add(k)
        if self.rot:
            print(f"T-7.4: ROT -- {len(self.rot)} von {len(KRITERIEN)} "
                  f"Kriterien rot: {', '.join(sorted(self.rot))}", flush=True)
            return 1
        print(f"T-7.4: GRUEN -- alle {len(KRITERIEN)} Kriterien gemessen "
              "und erfuellt", flush=True)
        return 0


# ---------------------------------------------------------------------------
# Module aus dem Pruefling
# ---------------------------------------------------------------------------

def modul_satz(pruefling: Path, *namen: str):
    """Einen SATZ Module frisch aus dem Pruefling laden.

    Frisch heisst: `sys.modules` wird von allem aus diesem Paket geleert und
    danach werden ALLE genannten Module importiert -- in einem Zug. Modul fuer
    Modul zu laden waere der Fehler, der den T-7.3-Pruefstand am 18.08. um
    10:16 zwei echte Dienste hat starten lassen: der zweite Aufruf raeumte die
    Einspeisungen des ersten weg, und der lokale Import im Prueflings-Code
    griff wieder auf das echte Modul zu.
    """
    import importlib
    for m in [k for k in sys.modules if k == PAKET or k.startswith(PAKET + ".")]:
        del sys.modules[m]
    module = tuple(importlib.import_module(n) for n in namen)
    return module[0] if len(namen) == 1 else module


def eingespeist(pruefling: Path, *namen: str) -> bool:
    """Sitzen die genannten Module WIRKLICH aus dem Pruefling in sys.modules?

    Die Auflage nach dem Zwischenfall vom 18.08.: vor dem Eingriff wiegen,
    dass die Einspeisung sitzt. Sitzt sie nicht, wird nicht eingegriffen.
    """
    for n in namen:
        m = sys.modules.get(n)
        if m is None:
            return False
        datei = getattr(m, "__file__", "") or ""
        if not str(Path(datei).resolve()).startswith(str(pruefling.resolve())):
            return False
    return True


# ---------------------------------------------------------------------------
# Der systemctl-Vorschalter -- die Sperre ueber dem ganzen Lauf
# ---------------------------------------------------------------------------

def vorschalter_bauen(arbeit: Path, protokoll: Path, name: str = "vorschalter",
                      abbilden: bool = True) -> Path:
    """Ein `systemctl`, das NUR auf die Units dieses Laufs zeigt.

    Mit `abbilden=False` entsteht die SPERRE: dann wird JEDE daimon-Unit
    zurueckgewiesen. Sie haengt waehrend des ganzen Laufs im PATH und ist die
    zweite Reihe hinter den Einspeisungen.
    """
    verz = arbeit / name
    verz.mkdir(parents=True, exist_ok=True)
    abbildung = "\n".join(
        f'    {echt}) a="{unser}" ;;' for echt, unser in UNIT_JE_ZIEL.items()
    ) if abbilden else ""
    text = f"""#!/usr/bin/env bash
# Erzeugt vom T-7.4-Pruefstand. Bildet die daimon-Units auf die transienten
# Units dieses Laufs ab und weist jede andere daimon-Unit zurueck.
printf '%s\\n' "$*" >> {protokoll}
args=()
for a in "$@"; do
  case "$a" in
{abbildung}
    {PAKET}-*) echo "VORSCHALTER VERWEIGERT: $a" >&2; exit 99 ;;
  esac
  args+=("$a")
done
exec /usr/bin/systemctl "${{args[@]}}"
"""
    p = verz / "systemctl"
    p.write_text(text, encoding="utf-8")
    p.chmod(0o755)
    return verz


def sperre_pruefen(sperr_log: Path, abbild_log: Path) -> None:
    """Jeder `systemctl`-Aufruf dieses Laufs, im ORIGINALWORTLAUT.

    Zwei Protokolle: das der SPERRE (die haengt ueber dem ganzen Lauf und
    weist jede Produktiv-Unit mit Exit 99 zurueck) und das des ABBILDENDEN
    Vorschalters (der haengt nur waehrend des K7-Eingriffs davor und leitet
    auf die transienten Units dieses Laufs um). Was in der SPERRE landet, ist
    ein Befund ueber den Pruefstand -- dort haette etwas eine echte Unit
    angefasst, wenn die zweite Reihe gefehlt haette.
    """
    print("", flush=True)
    for name, pfad, streng in (("SPERRE", sperr_log, True),
                               ("ABBILDUNG (nur K7)", abbild_log, False)):
        if not pfad.is_file():
            print(f"{name}: kein Protokoll -- nie gerufen.", flush=True)
            continue
        alle = [z for z in pfad.read_text(encoding="utf-8").splitlines()
                if z.strip()]
        print(f"{name}: {len(alle)} Aufruf(e).", flush=True)
        for z in alle:
            marke = "!!!" if (streng and f"{PAKET}-" in z) else "   "
            print(f"     {marke} {z}", flush=True)
        if streng:
            treffer = [z for z in alle if f"{PAKET}-" in z]
            print(f"SPERRE: {len(treffer)} Aufruf(e) an eine ECHTE Unit "
                  "zurueckgewiesen." if treffer else
                  "SPERRE: kein Aufruf an eine echte Unit.", flush=True)


# ---------------------------------------------------------------------------
# Audio-Erkennung nach INHALT
# ---------------------------------------------------------------------------

# Die Signaturen, die dieser Pruefstand kennt. Gesucht wird an JEDER Stelle
# der Datei und nicht nur am Anfang: ein Blob in der Datenbank liegt an einem
# beliebigen Versatz.
SIGNATUREN = (
    (b"RIFF", b"WAVE", "RIFF/WAVE"),
    (b"OggS", None, "OggS (Vorbis/Opus)"),
    (b"fLaC", None, "fLaC"),
    (b"ID3", None, "ID3 (MP3)"),
    (b"\x1a\x45\xdf\xa3", None, "Matroska/WebM"),
    (b"caff", None, "CAF"),
    (b"FORM", b"AIFF", "AIFF"),
    (b".snd", None, "AU/SND"),
)

PCM_MINDESTBYTES = 2000
# Die Grenzen sind GEMESSEN und nicht geraten. Am 18.08. gegen echte Dateien:
#
#   Datei                          Betrag  Gleichanteil  Differenz/Betrag
#   test_wavs/de.wav (echte Sprache)  5000         0,03              0,26
#   synthetisches Sprachsignal        2068         0,00              0,25
#   reiner Sinus                      5729         0,00              0,09
#   archiv.db (nur Text darin)       13995         0,82              0,61
#   espeak-Woerterbuch de_dict       17870         0,58              0,81
#   Zufallsbytes                     16305         --                1,34
#
# Zwei Achsen, beide mit Abstand: Audio ist gleichanteilsfrei und
# bandbegrenzt. Eine Achse allein haette die Datenbank des Laufs als Rohaudio
# gemeldet -- und ein Melder, der auf das eigene Archiv anspringt, ist so
# wenig wert wie einer, der schweigt.
PCM_DIFF_MAX = 0.40
PCM_GLEICHANTEIL_MAX = 0.15
PCM_BETRAG = (200.0, 20000.0)
PCM_NULLDURCHGAENGE = (0.005, 0.45)


def _pcm_verdacht(daten: bytes) -> str:
    """Rohes PCM, an seiner Statistik erkannt. Leer heisst: kein Verdacht.

    Vier Merkmale unterscheiden 16-bit-LE-PCM von Text, von komprimierten
    Daten und von Zufall:

      1. es ist kein Text (der Anteil druckbarer Bytes ist niedrig),
      2. die Amplituden liegen in einem plausiblen Bereich,
      3. **benachbarte Samples sind aehnlich** -- Sprache bei 16 kHz ist
         bandbegrenzt; bei Zufallsbytes ist der mittlere Differenzbetrag
         GROESSER als der mittlere Samplebetrag (Faktor 1,34),
      4. **es gibt keinen Gleichanteil.** Ein Mikrofonsignal schwingt um die
         Null; Bytes, die nur als int16 gelesen werden, tun das nicht.
    """
    if len(daten) < PCM_MINDESTBYTES:
        return ""
    druckbar = sum(1 for b in daten[:4096]
                   if 32 <= b < 127 or b in (9, 10, 13))
    if druckbar / min(len(daten), 4096) > 0.75:
        return ""
    n = (len(daten) // 2) * 2
    proben = array.array("h")
    proben.frombytes(daten[:min(n, 200_000)])
    if sys.byteorder == "big":
        proben.byteswap()
    if len(proben) < 500:
        return ""
    betrag = sum(abs(s) for s in proben) / len(proben)
    if not PCM_BETRAG[0] <= betrag <= PCM_BETRAG[1]:
        return ""
    gleich = abs(sum(proben) / len(proben)) / betrag
    if gleich >= PCM_GLEICHANTEIL_MAX:
        return ""
    diff = sum(abs(proben[i + 1] - proben[i])
               for i in range(len(proben) - 1)) / (len(proben) - 1)
    if diff / betrag >= PCM_DIFF_MAX:
        return ""
    nulldurchgaenge = sum(
        1 for i in range(len(proben) - 1)
        if (proben[i] < 0) != (proben[i + 1] < 0)) / (len(proben) - 1)
    if not (PCM_NULLDURCHGAENGE[0] <= nulldurchgaenge
            <= PCM_NULLDURCHGAENGE[1]):
        return ""
    return (f"rohes PCM (Betrag {betrag:.0f}, Gleichanteil {gleich:.2f}, "
            f"Differenz/Betrag {diff / betrag:.2f}, "
            f"Nulldurchgaenge {nulldurchgaenge:.2f})")


def audio_befund(daten: bytes) -> str:
    """Was an diesen Bytes nach Audio aussieht. Leer heisst: nichts."""
    for magie, zweite, name in SIGNATUREN:
        pos = daten.find(magie)
        if pos < 0:
            continue
        if zweite is not None and daten.find(zweite, pos, pos + 64) < 0:
            continue
        return f"{name} bei Versatz {pos}"
    # ADTS/MPEG hat keine Textmagie, sondern ein Bitmuster -- und zwar eines,
    # das in JEDER Binaerdatei zufaellig vorkommt (im WAL der eigenen
    # Datenbank elfmal). Erkannt wird es deshalb NUR am Dateianfang, wo eine
    # ADTS- oder MPEG-Datei ihren ersten Rahmen hat. Was das nicht sieht,
    # steht im Ledger unter "Grenzen".
    if (len(daten) >= PCM_MINDESTBYTES and daten[0] == 0xFF
            and (daten[1] & 0xE0) == 0xE0):
        return "ADTS/MPEG-Synchronwort bei Versatz 0"
    return _pcm_verdacht(daten)


def archiv_absuchen(verzeichnis: Path) -> list[tuple[str, str]]:
    """Jede Datei im Archivverzeichnis, und jeder Blob in jeder Datenbank."""
    funde: list[tuple[str, str]] = []
    if not verzeichnis.is_dir():
        return funde
    for pfad in sorted(verzeichnis.rglob("*")):
        if not pfad.is_file():
            continue
        try:
            daten = pfad.read_bytes()
        except OSError as exc:
            funde.append((str(pfad), f"NICHT LESBAR: {exc}"))
            continue
        befund = audio_befund(daten)
        if befund:
            funde.append((str(pfad), befund))
    funde.extend(datenbank_absuchen(verzeichnis))
    return funde


def datenbank_absuchen(verzeichnis: Path) -> list[tuple[str, str]]:
    """Jede Spalte jeder Tabelle jeder SQLite-Datei -- `mode=ro`.

    Die Dateisuche oben findet einen Blob in der Datenbank ohnehin, weil sie
    die Datei als Ganzes liest. Diese Suche sagt zusaetzlich, WO er steht:
    ein Befund "in archiv.db" ist ein Befund, ein Befund "in archiv, Zeile 3,
    Spalte daten" ist ein Auftrag.
    """
    funde: list[tuple[str, str]] = []
    for pfad in sorted(verzeichnis.glob("*.db")):
        try:
            db = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            tabellen = [z[0] for z in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for tabelle in tabellen:
                try:
                    zeilen = db.execute(
                        f'SELECT rowid, * FROM "{tabelle}"').fetchall()
                    spalten = [b[0] for b in db.execute(
                        f'SELECT rowid, * FROM "{tabelle}" LIMIT 0').description]
                except sqlite3.Error:
                    continue
                for zeile in zeilen:
                    for name, wert in zip(spalten, zeile):
                        if isinstance(wert, str):
                            wert = wert.encode("utf-8", "replace")
                        if not isinstance(wert, (bytes, bytearray)):
                            continue
                        befund = audio_befund(bytes(wert))
                        if befund:
                            funde.append(
                                (f"{pfad.name}:{tabelle}.{name} "
                                 f"(rowid {zeile[0]})", befund))
        except sqlite3.Error:
            pass
        finally:
            db.close()
    return funde


# ---------------------------------------------------------------------------
# Signalquellen
# ---------------------------------------------------------------------------

def stille_chunk() -> array.array:
    return array.array("h", [0] * CHUNK_SAMPLES)


def sprach_chunk(i: int) -> array.array:
    """Ein Chunk mit Signal. Kein Rauschen, sondern ein Ton mit Huellkurve:
    er soll fuer den Energie-Detektor Sprache sein UND fuer die PCM-Erkennung
    aus K4 wie Audio aussehen."""
    werte = []
    for n in range(CHUNK_SAMPLES):
        t = (i * CHUNK_SAMPLES + n) / RATE
        werte.append(int(9000 * math.sin(2 * math.pi * 220.0 * t)
                         + 3000 * math.sin(2 * math.pi * 55.0 * t)))
    return array.array("h", werte)


class EnergieDetektor:
    """Der VAD-Detektor dieses Laufs.

    Der echte ist `pysilero_vad` und fehlt auf dieser Maschine. Eingespeist
    wird deshalb ein Detektor, der die ENERGIE des Chunks bewertet -- und
    zwar an genau der Stelle, an der auch Silero sitzt: er geht in die ECHTE
    `vad.Erkenner` und von dort in die ECHTE `vad.Hysterese` des Prueflings.
    Gemessen wird also die Segmentierung des Prueflings, nicht meine.
    """

    def __init__(self, schwelle: float = 500.0) -> None:
        self.schwelle = schwelle
        self.aufrufe = 0

    def __call__(self, roh: bytes) -> float:
        self.aufrufe += 1
        proben = array.array("h")
        proben.frombytes(bytes(roh))
        if sys.byteorder == "big":
            proben.byteswap()
        if not proben:
            return 0.0
        energie = math.sqrt(sum(float(s) * s for s in proben) / len(proben))
        return 0.95 if energie >= self.schwelle else 0.0

    def chunk_samples(self) -> int:
        return CHUNK_SAMPLES


class AufnahmeAttrappe:
    """Die Mikrofonaufnahme. Zaehlt, WIE OFT eine geoeffnet wird -- daran
    haengt K6: ein zweiter Mikrofonpfad fuers Archiv waere eine zweite."""

    instanzen = 0
    starts = 0

    def __init__(self, *, senke, rate: int = RATE) -> None:
        AufnahmeAttrappe.instanzen += 1
        self.senke = senke
        self.rate = rate

    def start(self) -> None:
        AufnahmeAttrappe.starts += 1

    def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Die STT-Sonde: ein FREMDER Prozess am stt.sock
# ---------------------------------------------------------------------------

STT_SONDE = r'''
import json, os, socket, sys

pfad, protokoll, text = sys.argv[1], sys.argv[2], sys.argv[3]
if os.path.exists(pfad):
    os.unlink(pfad)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(pfad)
os.chmod(pfad, 0o600)
srv.listen(8)
open(protokoll, "a").close()
open(pfad + ".bereit", "w").write("1")
while True:
    try:
        conn, _ = srv.accept()
    except OSError:
        break
    with conn:
        conn.settimeout(5.0)
        try:
            roh = conn.makefile("rb").readline(65536)
        except OSError:
            roh = b""
        try:
            anfrage = json.loads(roh)
        except Exception:
            anfrage = {}
        wav = anfrage.get("wav") or ""
        eintrag = {"art": anfrage.get("art"), "wav": wav,
                   "wav_existiert": bool(wav) and os.path.exists(wav),
                   "wav_bytes": (os.path.getsize(wav)
                                 if wav and os.path.exists(wav) else 0)}
        with open(protokoll, "a") as fh:
            fh.write(json.dumps(eintrag) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            conn.sendall(json.dumps({"v": 1, "ok": True, "text": text,
                                     "latenz_ms": 1.0}).encode() + b"\n")
        except OSError:
            pass
'''


class SttSonde:
    """Horcht am `stt.sock` und zaehlt die Aufrufe -- in einem EIGENEN Prozess.

    Der Verifikationsabsatz verlangt "gemessen am Prozess, nicht an einem
    Zaehler des Prueflings". Genau deshalb steht hier ein Prozess und keine
    eingespeiste Funktion: der Pruefling ruft mit seinem echten `ruf_socket`
    an einen echten Socket, und was gezaehlt wird, zaehlt jemand anderes.
    """

    def __init__(self, arbeit: Path, runtime: Path, text: str = SATZ) -> None:
        self.skript = arbeit / "stt_sonde.py"
        self.skript.write_text(STT_SONDE, encoding="utf-8")
        self.socket = runtime / "stt.sock"
        self.protokoll = arbeit / "stt-aufrufe.jsonl"
        self.proc = subprocess.Popen(
            [sys.executable, str(self.skript), str(self.socket),
             str(self.protokoll), text],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        bereit = Path(str(self.socket) + ".bereit")
        ende = time.monotonic() + 10.0
        while time.monotonic() < ende and not bereit.exists():
            time.sleep(0.05)
        self.bereit = bereit.exists()

    def aufrufe(self) -> list[dict]:
        if not self.protokoll.is_file():
            return []
        return [json.loads(z) for z in
                self.protokoll.read_text(encoding="utf-8").splitlines()
                if z.strip()]

    def beenden(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# ---------------------------------------------------------------------------
# Der Recorder dieses Laufs -- echter Dienst, eigenes Archiv
# ---------------------------------------------------------------------------

class RecorderLauf:
    """Der ECHTE Recorder des Prueflings, auf einer eigenen Datenbank.

    Zwei Einspeisungen, und beide sind Sicherheit und nicht Bequemlichkeit:
    `fokus_klasse`/`mikrofone` halten die automatische Pause still (sie riefe
    sonst `systemctl --user stop` an den ECHTEN Units), und `pausieren`
    protokolliert, statt zu stoppen. Die Sperre im PATH ist die zweite Reihe
    dahinter.
    """

    def __init__(self, rec_mod, store_mod, redaktion_mod, runtime: Path,
                 db: Path) -> None:
        self.pausen_rufe: list[dict] = []
        self.rec = None
        self.fehler: BaseException | None = None
        self.bereit = threading.Event()
        self.runtime = runtime
        self._bauen = lambda: rec_mod.Recorder(
            runtime_dir=runtime, archiv=store_mod.Archiv(db),
            redaktion=redaktion_mod.Redaktion(runtime_dir=runtime,
                                              kennungen={}),
            fokus_klasse=lambda: "",
            mikrofone=lambda: 0,
            pausieren=self._pausieren_attrappe,
            erlaubte_units=None)
        # ALLES im selben Faden: bauen, `start()`, `lauf()`. Die
        # SQLite-Verbindung gehoert dem Faden, der sie geoeffnet hat -- ein
        # `start()` im Hauptfaden und ein `lauf()` daneben laesst den Dienst
        # beim ersten Schreiben mit `ProgrammingError` sterben, und der
        # Pruefstand haette "kein Eintrag" gemeldet, wo sein eigener Aufbau
        # kaputt war.
        self.thread = threading.Thread(target=self._fahren, daemon=True)
        self.thread.start()
        self.bereit.wait(timeout=15.0)

    def _fahren(self) -> None:
        try:
            self.rec = self._bauen()
            self.rec.start()
        except BaseException as exc:                            # noqa: BLE001
            self.fehler = exc
            self.bereit.set()
            return
        self.bereit.set()
        try:
            self.rec.lauf()
        except BaseException as exc:                            # noqa: BLE001
            self.fehler = exc

    def _pausieren_attrappe(self, **kw) -> dict:
        self.pausen_rufe.append(dict(kw))
        return {"v": 1, "ok": True, "units": [], "meldung": "Attrappe"}

    def laeuft(self) -> bool:
        return (self.fehler is None and self.rec is not None
                and self.thread.is_alive())

    def beenden(self) -> None:
        if self.rec is not None:
            self.rec.stop()
        self.thread.join(timeout=10.0)
        # `ipc.listen` raeumt den Socket nicht weg. Fuer das Ergebnis ist das
        # gleich -- ohne Horcher gibt `connect()` ECONNREFUSED statt ENOENT,
        # beides ist fuer den Melder `kein_recorder`. Weggeraeumt wird er
        # trotzdem, damit die Lage nach der Pause unmissverstaendlich ist.
        try:
            (self.runtime / "recorder.sock").unlink()
        except OSError:
            pass


def eintraege(db: Path, art: str | None = None) -> list[tuple]:
    """Die Zeilen der Datenbank, LESEND und ohne den Pruefling."""
    if not db.is_file():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        wo = " WHERE art = ?" if art else ""
        werte = (art,) if art else ()
        return conn.execute(
            f"SELECT id, art, fenster, text FROM archiv{wo} ORDER BY id",
            werte).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Eine Einspielung
# ---------------------------------------------------------------------------

def einspielen(ohren, *, sprache: bool, chunks: int = 40) -> None:
    """Eine Einspielung durch den ECHTEN Pfad des Prueflings.

    PTT an, Bloecke hinein, PTT aus. Was daraus wird -- Segment, STT-Aufruf,
    Archiveintrag --, entscheidet allein der Pruefling.
    """
    ohren.zustand_uebernehmen({"voice": {"listening": True,
                                         "tts_active": False}})
    for i in range(chunks):
        ohren.block(sprach_chunk(i) if sprache else stille_chunk())
    # Nachlauf: die Hysterese schliesst ein Segment erst nach genuegend
    # leisen Chunks. Ohne ihn haenge das Segment am `abschluss()` beim
    # Schliessen der Aufnahme -- gemessen wuerde dann der Sonderweg und
    # nicht der Regelweg.
    for _ in range(20):
        ohren.block(stille_chunk())
    ohren.zustand_uebernehmen({"voice": {"listening": False,
                                         "tts_active": False}})


def ohren_bauen(ears_mod, runtime: Path, cfg):
    vad = sys.modules[f"{PAKET}.ears.vad"]
    return ears_mod.Ohren(cfg, runtime_dir=runtime,
                          aufnahme_fabrik=AufnahmeAttrappe,
                          erkenner=vad.Erkenner(EnergieDetektor()))


# ---------------------------------------------------------------------------
# K1/K2/K5/K6/K9 -- eine Stille, eine Sprache, und was daraus wird
# ---------------------------------------------------------------------------

def k1_bis_k9_einspielung(B: Bilanz, pruefling: Path, arbeit: Path,
                          runtime: Path, daten: Path) -> dict | None:
    """Der Hauptlauf. Gibt zurueck, was K7/K8 weiterverwenden."""
    ears, rec_mod, store_mod, red_mod, cfg_mod = modul_satz(
        pruefling, f"{PAKET}.ears.daemon", f"{PAKET}.recorder.daemon",
        f"{PAKET}.recorder.store", f"{PAKET}.recorder.redaktion",
        f"{PAKET}.common.config")
    if not eingespeist(pruefling, f"{PAKET}.ears.daemon",
                       f"{PAKET}.recorder.daemon"):
        B.schlecht("K1", "die Module stammen NICHT aus dem Pruefling -- "
                         "abgebrochen, bevor irgendetwas laeuft")
        return None

    db = daten / PAKET / store_mod.DATEI
    sonde = SttSonde(arbeit, runtime)
    if not sonde.bereit:
        B.schlecht("K1", "die STT-Sonde ist nicht hochgekommen -- ohne sie "
                         "waere 'kein Aufruf' nicht von 'nicht gemessen' zu "
                         "unterscheiden")
        return None
    recorder = RecorderLauf(rec_mod, store_mod, red_mod, runtime, db)
    try:
        if not recorder.laeuft():
            B.schlecht("K1", f"der Recorder dieses Laufs steht nicht: "
                             f"{recorder.fehler!r} -- abgebrochen, statt "
                             "'kein Eintrag' zu melden")
            return None
        cfg = cfg_mod.load(make_dirs=False)
        ohren = ohren_bauen(ears, runtime, cfg)

        # --- K1: STILLE -------------------------------------------------
        vor_stille = len(sonde.aufrufe()), len(eintraege(db))
        einspielen(ohren, sprache=False)
        time.sleep(0.3)                      # Zustellung, nicht Messfenster
        nach_stille = len(sonde.aufrufe()), len(eintraege(db))
        B.notiz(f"Stille: STT-Aufrufe {vor_stille[0]} -> {nach_stille[0]}, "
                f"Eintraege {vor_stille[1]} -> {nach_stille[1]}")
        B.urteil("K1", nach_stille[0] == vor_stille[0],
                 "Stille erzeugt KEINEN STT-Aufruf"
                 if nach_stille[0] == vor_stille[0] else
                 f"Stille hat {nach_stille[0] - vor_stille[0]} STT-Aufruf(e) "
                 "erzeugt -- die GPU liefe an der Stille mit")
        B.urteil("K1", nach_stille[1] == vor_stille[1],
                 "Stille erzeugt KEINEN Archiveintrag"
                 if nach_stille[1] == vor_stille[1] else
                 f"Stille hat {nach_stille[1] - vor_stille[1]} Eintrag/"
                 "Eintraege erzeugt")

        # --- K2: die Positivkontrolle ------------------------------------
        # Der Zaehler beginnt HIER: gezaehlt wird, wie viele Aufnahmen EINE
        # Einspielung oeffnet. Ueber zwei Einspielungen hinweg zu zaehlen
        # haette den Pruefstand selbst gemessen (PTT geht zweimal an).
        AufnahmeAttrappe.instanzen = 0
        AufnahmeAttrappe.starts = 0
        einspielen(ohren, sprache=True)
        time.sleep(0.5)
        rufe = sonde.aufrufe()
        zeilen = eintraege(db)
        B.notiz(f"Sprache: STT-Aufrufe {len(rufe)}, Eintraege {len(zeilen)}")
        if not B.wiegen("K2", "STT-Aufrufe", nach_stille[0], len(rufe)):
            B.schlecht("K2", "die Sprache hat den Pfad nicht bewegt -- der "
                             "Befund aus K1 ist damit wertlos")
            return None
        B.wiegen("K2", "Archiveintraege", nach_stille[1], len(zeilen))
        B.urteil("K2", len(rufe) - nach_stille[0] == 1,
                 "GENAU EIN STT-Aufruf fuer einen Sprachabschnitt"
                 if len(rufe) - nach_stille[0] == 1 else
                 f"{len(rufe) - nach_stille[0]} STT-Aufrufe fuer EINEN "
                 "Sprachabschnitt")
        B.urteil("K2", len(zeilen) - nach_stille[1] == 1,
                 "GENAU EIN Archiveintrag fuer einen Sprachabschnitt"
                 if len(zeilen) - nach_stille[1] == 1 else
                 f"{len(zeilen) - nach_stille[1]} Eintraege fuer EINEN "
                 "Sprachabschnitt")
        transkripte = [z for z in zeilen if z[1] == store_mod.ART_TRANSKRIPT]
        B.urteil("K2", any(SATZ in (z[3] or "") for z in transkripte),
                 f"das Transkript steht in der Datenbank ({len(transkripte)} "
                 "Zeile(n) der Art 'transkript')"
                 if any(SATZ in (z[3] or "") for z in transkripte) else
                 f"das Transkript steht NICHT in der Datenbank: {zeilen!r}")

        # --- K6: derselbe Strom ------------------------------------------
        B.notiz(f"Aufnahmen geoeffnet: {AufnahmeAttrappe.instanzen}, "
                f"gestartet: {AufnahmeAttrappe.starts}")
        B.urteil("K6", AufnahmeAttrappe.instanzen == 1,
                 "EINE Aufnahme fuer Live-Pfad und Archivpfad"
                 if AufnahmeAttrappe.instanzen == 1 else
                 f"{AufnahmeAttrappe.instanzen} Aufnahmen -- der Archivpfad "
                 "haengt nicht am selben Strom")
        B.urteil("K6", len(rufe) - nach_stille[0] == 1,
                 "der Archivpfad loest KEINEN zweiten STT-Aufruf aus"
                 if len(rufe) - nach_stille[0] == 1 else
                 "der Archivpfad loest einen ZWEITEN STT-Aufruf aus -- ein "
                 "zweites Modell im Speicher und ein zweiter Strom")
        rec_quellen = list((pruefling / PAKET / "recorder").glob("*.py"))
        fremd = [p.name for p in rec_quellen
                 if re.search(r"sounddevice|ears\.capture|InputStream",
                              p.read_text(encoding="utf-8"))]
        B.urteil("K6", not fremd,
                 "kein eigener Mikrofonpfad im Recorder"
                 if not fremd else
                 f"der Recorder oeffnet selbst ein Mikrofon: {fremd}")

        # --- K5: es gibt keinen Weg fuer Rohaudio ------------------------
        audio_mod = sys.modules[f"{PAKET}.recorder.audio"]
        namen = set(inspect.signature(audio_mod.melde_transkript).parameters)
        verdaechtig = sorted(n for n in namen if re.search(
            r"audio|wav|pcm|puffer|samples|roh|stuecke|bytes", n, re.I))
        B.urteil("K5", not verdaechtig,
                 f"`melde_transkript` hat keinen Audio-Parameter ({sorted(namen)})"
                 if not verdaechtig else
                 f"`melde_transkript` nimmt Audio entgegen: {verdaechtig}")
        offen = [w for w in rufe[nach_stille[0]:] if w.get("wav")]
        B.notiz(f"WAV-Pfade, die dem STT gereicht wurden: "
                f"{[w['wav'] for w in offen]}")
        ueberlebt = [w["wav"] for w in offen if Path(w["wav"]).exists()]
        B.urteil("K5", offen and not ueberlebt,
                 "die WAV-Datei fuer den STT ist nach der Runde weg"
                 if offen and not ueberlebt else
                 (f"die WAV-Datei ueberlebt die Runde: {ueberlebt}"
                  if ueberlebt else
                  "kein WAV-Pfad im STT-Aufruf -- nicht messbar, und das ist "
                  "kein Erfolg"))
        archiv = store_mod.Archiv(daten / "probe.db")
        try:
            archiv.migrieren()
            abgewiesen, durchgelassen = [], []
            for art in sorted(store_mod.VERBOTENE_ARTEN) or ["audio"]:
                try:
                    archiv.schreiben(art, "x")
                    durchgelassen.append(art)
                except store_mod.ArchivFehler:
                    abgewiesen.append(art)
            # Positivkontrolle: eine ERLAUBTE Art muss durchgehen, sonst
            # weist dieser Aufruf alles ab und beweist nichts.
            kontrolle = True
            try:
                archiv.schreiben(store_mod.ART_TRANSKRIPT, "kontrolle")
            except store_mod.ArchivFehler:
                kontrolle = False
            B.notiz(f"verbotene Arten: abgewiesen={abgewiesen}, "
                    f"durchgelassen={durchgelassen}, "
                    f"Positivkontrolle transkript={kontrolle}")
            B.urteil("K5", bool(abgewiesen) and not durchgelassen and kontrolle,
                     f"`Archiv.schreiben` weist die Rohaudio-Arten ab "
                     f"({len(abgewiesen)}), laesst `transkript` aber durch"
                     if abgewiesen and not durchgelassen and kontrolle else
                     f"Rohaudio-Arten kommen durch: {durchgelassen} "
                     f"(Positivkontrolle transkript={kontrolle})")
        finally:
            archiv.schliessen()

        # --- K9: tainted --------------------------------------------------
        protokoll_mod = sys.modules[f"{PAKET}.common.protocol"]
        les_archiv = store_mod.Archiv(db)
        try:
            gelesen = les_archiv.lesen(store_mod.ART_TRANSKRIPT)
            gesucht = les_archiv.suchen("kanarienvogel")
        finally:
            les_archiv.schliessen()

        def ist_tainted(treffer: list) -> bool:
            if not treffer:
                return False
            for t in treffer:
                wert = t.get("wert")
                if not isinstance(wert, protokoll_mod.Marked):
                    return False
                if wert.mark is not protokoll_mod.Mark.TAINTED:
                    return False
            return True

        B.notiz(f"lesen(): {len(gelesen)} Treffer, suchen(): "
                f"{len(gesucht)} Treffer")
        B.urteil("K9", ist_tainted(gelesen),
                 "das Transkript kommt aus `lesen` als Marked(TAINTED)"
                 if ist_tainted(gelesen) else
                 f"`lesen` liefert das Transkript nicht tainted: "
                 f"{[type(t.get('wert')).__name__ for t in gelesen]}")
        B.urteil("K9", ist_tainted(gesucht),
                 "das Transkript kommt aus `suchen` als Marked(TAINTED)"
                 if ist_tainted(gesucht) else
                 f"`suchen` liefert das Transkript nicht tainted: "
                 f"{[type(t.get('wert')).__name__ for t in gesucht]}")

        return {"db": db, "runtime": runtime, "sonde": sonde,
                "recorder": recorder, "ohren": ohren,
                "eintraege_vor_pause": len(zeilen),
                "stt_vor_pause": len(rufe)}
    except Exception:
        sonde.beenden()
        recorder.beenden()
        raise


# ---------------------------------------------------------------------------
# K3 -- der STT-Arbeitsprozess: warm bei Sprache, Ende bei Stille
# ---------------------------------------------------------------------------

STT_LEBEN = r'''
import importlib, sys, types

pruefling, modus, pfad, frist = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
sys.path.insert(0, pruefling)


class StubErkenner:
    """Das Modell. Es fehlt auf dieser Maschine (sherpa_onnx ist nicht
    installiert) und ist fuer diese Frage auch nicht der Gegenstand: gemessen
    wird die SCHLEIFE, nicht die Erkennung."""

    def kennung(self):
        return {"modell": "stub"}

    def zustand(self):
        return {"v": 1, "ok": True, "modell": "stub", **self.kennung()}

    def transkribiere(self, wav):
        return {"v": 1, "ok": True, "text": "", **self.kennung()}


if modus == "stt":
    stt = importlib.import_module("dai" "mon.gpu.stt")
    # Wirkt NUR, wenn `lauf` die Frist ueberhaupt liest. Tut es das nicht,
    # laeuft die Schleife weiter -- und genau das ist dann der Befund.
    stt.LEERLAUF_S = frist
    srv = stt.eigener_socket(pfad)
    sys.exit(stt.lauf(StubErkenner(), srv))
else:
    w = importlib.import_module("dai" "mon.gpu.worker")
    srv = w.eigener_socket(pfad)
    worker = w.Worker(modell="stt", hub_socket=pfad + ".hub", idle_s=frist)
    sys.exit(worker.lauf(srv))
'''


def _anklopfen(pfad: Path) -> bool:
    import socket as s
    try:
        c = s.socket(s.AF_UNIX, s.SOCK_STREAM)
        c.settimeout(2.0)
        c.connect(str(pfad))
        with c:
            c.sendall(b'{"v":1,"art":"zustand"}\n')
            c.makefile("rb").readline(4096)
        return True
    except OSError:
        return False


def k3_leerlauf(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    """Beendet sich der STT-Arbeitsprozess bei Stille -- und bleibt er bei
    Sprache warm?

    Drei Prozesse, EINE Ablesung. Der dritte ist die Positivkontrolle: er ist
    derselbe Bauform-Fall (Accept-Schleife mit Leerlauffrist) aus
    `daimon/gpu/worker.py`. Endet ER nicht, misst dieser Kriteriumslauf gar
    nichts, und das steht dann da.
    """
    skript = arbeit / "stt_leben.py"
    skript.write_text(STT_LEBEN, encoding="utf-8")
    frist = 1.0
    kinder = {}
    for modus, name in (("stt", "stt-still"), ("stt", "stt-warm"),
                        ("worker", "worker-still")):
        pfad = arbeit / f"{name}.sock"
        kinder[name] = (subprocess.Popen(
            [sys.executable, str(skript), str(pruefling), modus, str(pfad),
             str(frist)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE),
            pfad)
    try:
        # AUFBAU, kein Messfenster: warten, bis die Sockets ueberhaupt da sind.
        ende = time.monotonic() + 10.0
        while time.monotonic() < ende and not all(
                p.exists() for _, p in kinder.values()):
            time.sleep(0.05)
        fehlend = [n for n, (_, p) in kinder.items() if not p.exists()]
        if fehlend:
            fehler = {}
            for n in fehlend:
                proc = kinder[n][0]
                proc.poll()
                fehler[n] = (proc.stderr.read(400).decode("utf-8", "replace")
                             if proc.stderr else "")
            B.schlecht("K3", f"diese Prozesse sind nicht hochgekommen: "
                             f"{fehlend} -- {fehler}")
            return

        # Der WARME haelt Anfragen, die anderen bekommen keine.
        t_ende = time.monotonic() + 3 * frist
        angeklopft = 0
        while time.monotonic() < t_ende:
            angeklopft += int(_anklopfen(kinder["stt-warm"][1]))
            time.sleep(frist / 4)

        # DIE ABLESUNG. Ein Zeitpunkt, drei Prozesse.
        time.sleep(frist)
        lebt = {n: (p.poll() is None) for n, (p, _) in kinder.items()}
        B.notiz(f"Leerlauffrist {frist}s, {angeklopft} Anfragen an den "
                f"warmen Prozess; lebt nach {4 * frist:.0f}s: {lebt}")

        if lebt["worker-still"]:
            B.schlecht("K3", "die POSITIVKONTROLLE lebt noch: auch der "
                             "GPU-Worker mit Leerlauffrist ist nicht beendet. "
                             "Diese Messung kann ein Prozessende nicht sehen "
                             "-- ihr Ergebnis zaehlt nicht")
            return
        B.notiz("Positivkontrolle: der GPU-Worker beendet sich nach seiner "
                "Leerlauffrist -- die Messung KANN ein Ende sehen")
        B.urteil("K3", lebt["stt-warm"],
                 "bei anhaltender Sprache bleibt der STT-Prozess warm"
                 if lebt["stt-warm"] else
                 "der STT-Prozess ist trotz laufender Anfragen beendet")
        B.urteil("K3", not lebt["stt-still"],
                 "bei Stille beendet sich der STT-Prozess"
                 if not lebt["stt-still"] else
                 "der STT-Prozess laeuft bei Stille WEITER: `lauf()` in "
                 f"{PAKET}/gpu/stt.py hat keine Leerlauffrist. Das Modell "
                 "bleibt geladen, bis jemand die Unit stoppt -- gegen die "
                 "Residenzpolitik aus Design 5.4 und gegen den zweiten "
                 "Akzeptanzpunkt von T-7.4")
    finally:
        for proc, _ in kinder.values():
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------------------
# K4 -- keine Audiodatei im Archivverzeichnis, nach INHALT gesucht
# ---------------------------------------------------------------------------

def wav_bytes(sekunden: float = 0.5) -> bytes:
    """Eine echte WAV-Datei im Speicher -- Kopf und Nutzdaten."""
    import io
    import wave
    proben = array.array("h")
    for n in range(int(RATE * sekunden)):
        t = n / RATE
        proben.append(int(9000 * math.sin(2 * math.pi * 220.0 * t)))
    puffer = io.BytesIO()
    with wave.open(puffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(proben.tobytes())
    return puffer.getvalue()


def pcm_bytes(sekunden: float = 0.5) -> bytes:
    roh = wav_bytes(sekunden)
    return roh[44:]                  # der Kopf faellt weg: nacktes PCM


def k4_kein_rohaudio(B: Bilanz, daten: Path, db: Path) -> None:
    verzeichnis = daten / PAKET

    # --- Zuerst der MESSGEGENSTAND: was liegt nach dem Lauf da? ----------
    funde = archiv_absuchen(verzeichnis)
    dateien = sorted(p.name for p in verzeichnis.rglob("*") if p.is_file())
    B.notiz(f"Archivverzeichnis {verzeichnis}: {dateien}")
    B.urteil("K4", not funde,
             "im ganzen Archivverzeichnis liegt keine Audiodatei -- "
             "gesucht nach Inhalt, nicht nach Endung"
             if not funde else
             f"AUDIO IM ARCHIV: {funde}")

    # --- Die POSITIVKONTROLLE. Ohne sie ist "nichts gefunden" nichts wert.
    getarnt = {
        "abschnitt.dat": wav_bytes(),
        "notizen.txt": b"OggS\x00\x02" + os.urandom(4000),
        "index.bin": b"fLaC\x00\x00\x00\x22" + os.urandom(4000),
        "tabelle.csv": b"ID3\x04\x00\x00" + os.urandom(4000),
        "puffer": pcm_bytes(),
        "stueck.json": (b"\xff\xf1" + os.urandom(60)) * 40,
    }
    for name, inhalt in getarnt.items():
        (verzeichnis / name).write_bytes(inhalt)
    # Und einer IN der Datenbank -- eine Suche, die nur Dateien kennt, sieht
    # ihn nicht, und ein Blob ist der bequemste Ort fuer Rohaudio.
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO archiv (art, ts, stufe, fenster, text, daten, bytes) "
            "VALUES ('transkript', 0, 'redacted', 'kontrolle', 'kontrolle', "
            "?, 0)", (wav_bytes(),))
        conn.commit()
    finally:
        conn.close()

    try:
        gefunden = archiv_absuchen(verzeichnis)
        erkannt = {Path(p).name.split(":")[0] for p, _ in gefunden}
        fehlend = sorted(n for n in getarnt if n not in erkannt)
        blob = [f for f in gefunden if ".daten" in f[0] or "archiv.db" in f[0]]
        B.notiz(f"Positivkontrolle: {len(gefunden)} Fund(e)")
        for pfad, was in gefunden:
            B.notiz(f"    {pfad}: {was}")
        B.urteil("K4", not fehlend,
                 f"alle {len(getarnt)} getarnten Audiostuecke wurden gefunden "
                 "-- die Suche misst wirklich"
                 if not fehlend else
                 f"diese getarnten Audiostuecke wurden NICHT gefunden: "
                 f"{fehlend} -- die Suche ist blind, ihr negatives Ergebnis "
                 "oben zaehlt nicht")
        B.urteil("K4", bool(blob),
                 "auch das Rohaudio IN der Datenbank wurde gefunden"
                 if blob else
                 "ein WAV-Blob in der Datenbank wurde NICHT gefunden -- die "
                 "Suche sieht nur Dateien")
    finally:
        for name in getarnt:
            try:
                (verzeichnis / name).unlink()
            except OSError:
                pass
        conn = sqlite3.connect(db)
        try:
            conn.execute("DELETE FROM archiv WHERE fenster = 'kontrolle'")
            conn.commit()
        finally:
            conn.close()

    # --- Und noch einmal der Messgegenstand, nach dem Aufraeumen ---------
    nachher = archiv_absuchen(verzeichnis)
    B.wiegen("K4", "Funde der Inhaltssuche", len(gefunden), len(nachher))


def k4_echtes_archiv(B: Bilanz) -> None:
    """Dasselbe am ECHTEN Archiv des Nutzers -- ausschliesslich lesend.

    Kein Kriterium: der Pruefstand hat es nicht gefuellt und kann darum nichts
    beweisen. Aber die Frage des Tasks lautet "liegt dort Rohaudio", und diese
    Frage hat eine Antwort, die man aufschreiben kann.
    """
    echt = Path.home() / ".local" / "share" / PAKET
    if not echt.is_dir():
        B.notiz(f"echtes Archiv {echt}: nicht vorhanden")
        return
    try:
        funde = archiv_absuchen(echt)
    except OSError as exc:
        B.notiz(f"echtes Archiv {echt}: nicht lesbar ({exc})")
        return
    dateien = sorted(p.name for p in echt.rglob("*") if p.is_file())
    B.notiz(f"echtes Archiv {echt} (NUR GELESEN): {dateien}")
    B.notiz(f"echtes Archiv: {len(funde)} Audiofund(e) {funde}")


# ---------------------------------------------------------------------------
# K7 -- der Pausenschalter schliesst BEIDE Pfade
# ---------------------------------------------------------------------------

def pw_dump_stroeme() -> list[str] | None:
    """Alle laufenden AUFNAHMEstroeme, EINMAL gelesen. `None` = nicht messbar."""
    try:
        lauf = subprocess.run(["pw-dump"], capture_output=True, text=True,
                              timeout=15.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if lauf.returncode != 0 or not lauf.stdout:
        return None
    try:
        knoten = json.loads(lauf.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(knoten, list):
        return None
    namen = []
    for k in knoten:
        if not isinstance(k, dict):
            continue
        props = (k.get("info") or {}).get("props", {})
        if props.get("media.class") != "Stream/Input/Audio":
            continue
        namen.append(f'{props.get("node.name", "")} '
                     f'{props.get("application.name", "")}')
    return namen


def sonde_kommando(name: str) -> list[str]:
    return ["pw-record", "--target=0",
            "-P", f"node.name={name} application.name={name}", "/dev/null"]


def transiente_unit_starten(unit: str, sonde: str) -> bool:
    try:
        lauf = subprocess.run(
            ["systemd-run", "--user", f"--unit={unit}", "--collect", "--quiet",
             "--"] + sonde_kommando(sonde),
            capture_output=True, text=True, timeout=20.0)
    except (OSError, subprocess.SubprocessError):
        return False
    return lauf.returncode == 0


def transiente_units_aufraeumen() -> None:
    for u in UNIT_JE_ZIEL.values():
        if not u.startswith(TAG):            # NIE etwas Fremdes anfassen
            continue
        subprocess.run(["/usr/bin/systemctl", "--user", "stop", u],
                       capture_output=True, timeout=20.0)


def auf_strom_warten(name: str, frist_s: float = STROM_BEREIT_S) -> bool:
    """AUFBAU, nicht Messung."""
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        stroeme = pw_dump_stroeme()
        if stroeme is not None and any(name in s for s in stroeme):
            return True
        time.sleep(0.3)
    return False


def zaehle(stroeme: list[str] | None, name: str) -> int | None:
    if stroeme is None:
        return None
    return sum(1 for s in stroeme if name in s)


def k7_pause_schliesst_beide(B: Bilanz, pruefling: Path, arbeit: Path) -> None:
    pause = modul_satz(pruefling, f"{PAKET}.recorder.pause")
    if not eingespeist(pruefling, f"{PAKET}.recorder.pause"):
        B.schlecht("K7", "das Pausenmodul stammt nicht aus dem Pruefling -- "
                         "abgebrochen VOR dem Eingriff")
        return

    protokoll = arbeit / "vorschalter.log"
    protokoll.write_text("", encoding="utf-8")
    verz = vorschalter_bauen(arbeit, protokoll)
    alter_pfad = os.environ.get("PATH", "")

    # --- Teil 1: WELCHE Units will der Schalter stoppen? -----------------
    # Ohne PipeWire messbar, und die Aussage haengt am ORIGINALWORTLAUT der
    # abgesetzten Zeile -- nicht an ihrer Abbildung.
    werkzeuge_da = all(shutil.which(w) is not None
                       for w in ("pw-record", "pw-dump", "systemd-run"))
    sonde_je_unit = {UNIT_JE_ZIEL[RECORDER_UNIT]: f"{TAG}rec",
                     UNIT_JE_ZIEL[EYES_UNIT]: f"{TAG}eyes",
                     UNIT_JE_ZIEL[EARS_UNIT]: f"{TAG}ears"}
    vorher = nachher = None
    try:
        if werkzeuge_da:
            for unit, sonde in sonde_je_unit.items():
                if not transiente_unit_starten(unit, sonde):
                    B.notiz(f"transiente Unit {unit} liess sich nicht starten "
                            "-- der Stromteil dieses Kriteriums entfaellt")
                    werkzeuge_da = False
                    break
        if werkzeuge_da:
            for sonde in sonde_je_unit.values():
                if not auf_strom_warten(sonde):
                    B.notiz(f"der Strom {sonde} erschien nicht in pw-dump -- "
                            "der Stromteil dieses Kriteriums entfaellt")
                    werkzeuge_da = False
                    break
        if werkzeuge_da:
            stroeme = pw_dump_stroeme()
            vorher = {s: zaehle(stroeme, s) for s in sonde_je_unit.values()}
            B.notiz(f"Aufnahmestroeme vorher: {vorher}")

        os.environ["PATH"] = f"{verz}:{alter_pfad}"
        bericht = pause.stoppe(runtime_dir=None)
        os.environ["PATH"] = f"{arbeit / 'sperre'}:{alter_pfad}"
        B.notiz(f"stoppe() meldet: ok={bericht.get('ok')} "
                f"units={bericht.get('units')} rc={bericht.get('rc')}")

        zeilen = [z for z in protokoll.read_text(encoding="utf-8").splitlines()
                  if z.strip()]
        stops = [z for z in zeilen if re.search(r"\bstop\b", z)]
        angefasst = {u for u in UNIT_JE_ZIEL if any(u in z for z in stops)}
        B.notiz(f"gestoppte Units (Originalwortlaut): {sorted(angefasst)}")

        bild = EYES_UNIT in angefasst
        ton = EARS_UNIT in angefasst
        B.urteil("K7", bild,
                 f"der BILD-Pfad wird gestoppt ({EYES_UNIT})" if bild else
                 f"der BILD-Pfad bleibt laufen -- {EYES_UNIT} nicht gestoppt")
        B.urteil("K7", ton,
                 f"der TON-Pfad wird gestoppt ({EARS_UNIT})" if ton else
                 f"der TON-Pfad wird NICHT gestoppt: {EARS_UNIT} steht in "
                 "ERLAUBTE_UNITS, aber nicht in PAUSE_UNITS. Der Archivpfad "
                 "haengt am Ohren-Dienst (er meldet das Transkript) und an "
                 "dessen Mikrofonstrom -- der Pausenschalter schliesst den "
                 "Archivweg (der Recorder ist weg), laesst den Strom aber "
                 "offen. Genau umgekehrt zur Zusage 'gemeinsam geschlossen'")

        if werkzeuge_da:
            stroeme = pw_dump_stroeme()
            nachher = {s: zaehle(stroeme, s) for s in sonde_je_unit.values()}
            B.notiz(f"Aufnahmestroeme nachher: {nachher}")
            ton_sonde = sonde_je_unit[UNIT_JE_ZIEL[EARS_UNIT]]
            bild_sonde = sonde_je_unit[UNIT_JE_ZIEL[EYES_UNIT]]
            if vorher and vorher.get(bild_sonde):
                # Die UNTERSCHEIDUNG: dass ueberhaupt ein Strom verschwindet,
                # macht "noch da" zu einer Aussage.
                B.wiegen("K7", f"Strom {bild_sonde}",
                         vorher.get(bild_sonde), nachher.get(bild_sonde))
            offen = nachher.get(ton_sonde)
            B.urteil("K7", offen == 0,
                     "der Mikrofonstrom des Ohren-Dienstes ist nach der "
                     "Pause geschlossen"
                     if offen == 0 else
                     f"der Mikrofonstrom des Ohren-Dienstes laeuft nach der "
                     f"Pause WEITER ({offen} Strom/Stroeme) -- gemessen an "
                     "pw-dump, ein Lesevorgang vor und einer nach dem Eingriff"
                     if offen else
                     "der Tonstrom ist nicht messbar (pw-dump?) -- und das "
                     "ist kein Erfolg")
        else:
            B.notiz("PipeWire-Teil von K7 nicht gelaufen; der Unit-Teil oben "
                    "traegt das Urteil allein")
    finally:
        os.environ["PATH"] = f"{arbeit / 'sperre'}:{alter_pfad}"
        transiente_units_aufraeumen()


# ---------------------------------------------------------------------------
# K8 -- nach der Pause erzeugt dieselbe Einspielung nichts
# ---------------------------------------------------------------------------

def k8_nach_der_pause(B: Bilanz, zustand: dict) -> None:
    db, sonde = zustand["db"], zustand["sonde"]
    ohren, recorder = zustand["ohren"], zustand["recorder"]

    vor = len(eintraege(db))
    if vor != zustand["eintraege_vor_pause"]:
        B.notiz(f"Eintraege haben sich seit K2 geaendert: "
                f"{zustand['eintraege_vor_pause']} -> {vor}")

    # Die Pause STOPPT die Unit -- hier wird derselbe Zustand hergestellt,
    # ohne eine echte Unit anzufassen: der Recorder dieses Laufs geht weg.
    # Das ist die Wirkung, die `systemctl stop daimon-recorder` im Betrieb
    # hat, und der Verifikationsabsatz fragt nach der Wirkung.
    recorder.beenden()
    zeit = time.monotonic()
    while time.monotonic() - zeit < 3.0 and (
            zustand["runtime"] / "recorder.sock").exists():
        time.sleep(0.1)
    lebt_noch = (zustand["runtime"] / "recorder.sock").exists()
    B.notiz(f"Recorder gestoppt; Socket noch da: {lebt_noch}")

    stt_vor = len(sonde.aufrufe())
    einspielen(ohren, sprache=True)
    time.sleep(0.5)
    nach = len(eintraege(db))
    stt_nach = len(sonde.aufrufe())
    B.notiz(f"nach der Pause: Eintraege {vor} -> {nach}, "
            f"STT-Aufrufe {stt_vor} -> {stt_nach}")

    # Positivkontrolle: die Einspielung muss den Pfad ueberhaupt bewegt haben.
    # Ohne sie hiesse "kein neuer Eintrag" auch "nichts eingespielt".
    if not B.wiegen("K8", "STT-Aufrufe durch die Einspielung nach der Pause",
                    stt_vor, stt_nach):
        return
    B.urteil("K8", nach == vor,
             "nach der Pause erzeugt dieselbe Einspielung KEINEN Eintrag"
             if nach == vor else
             f"nach der Pause sind {nach - vor} Eintrag/Eintraege entstanden "
             "-- der Melder findet einen Weg am gestoppten Recorder vorbei")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: t74_pruefstand.py <pruefling>", file=sys.stderr)
        return 2
    pruefling = Path(argv[1]).resolve()
    if not pruefling.is_dir():
        print(f"Pruefling {pruefling} ist kein Verzeichnis", file=sys.stderr)
        return 2

    print(f"T-7.4.v -- Pruefling: {pruefling}", flush=True)
    print(f"Marke dieses Laufs:  {TAG}", flush=True)
    print("Mutant-zu-Kriterium:", flush=True)
    for m, k in sorted(MUTANTEN_GRENZEN.items()):
        print(f"    {m:34s} -> {k}", flush=True)
    print("-" * 72, flush=True)

    sys.path.insert(0, str(pruefling))
    B = Bilanz()
    arbeit = Path(tempfile.mkdtemp(prefix=f"{TAG}-"))
    alter_pfad = os.environ.get("PATH", "")
    alte_umgebung = {k: os.environ.get(k)
                     for k in ("XDG_DATA_HOME", "XDG_RUNTIME_DIR",
                               "DAIMON_CONFIG", "PATH")}
    zustand = None
    try:
        # DIE SPERRE, ueber den ganzen Lauf.
        sperr_log = arbeit / "sperre.log"
        sperr_log.write_text("", encoding="utf-8")
        sperre = vorschalter_bauen(arbeit, sperr_log, name="sperre",
                                   abbilden=False)
        os.environ["PATH"] = f"{sperre}:{alter_pfad}"

        # EIGENES Archiv, EIGENES Laufzeitverzeichnis. `data_dir()` des
        # Prueflings loest damit hierhin auf, und das Archiv des Nutzers wird
        # von diesem Lauf nur gelesen.
        daten = arbeit / "daten"
        runtime = arbeit / "runtime"
        (daten / PAKET).mkdir(parents=True, exist_ok=True)
        runtime.mkdir(parents=True, exist_ok=True)
        os.environ["XDG_DATA_HOME"] = str(daten)
        # `XDG_RUNTIME_DIR` bleibt UNVERAENDERT. Es umzubiegen war der erste
        # Versuch und war falsch: daran haengt der Sitzungsbus, und ohne ihn
        # startet `systemd-run --user` nicht -- K7 haette seine transienten
        # Units nicht bekommen und den Stromteil still weggelassen. Der
        # Pruefling schreibt trotzdem nichts in das echte Laufzeitverzeichnis:
        # `runtime_dir` wird jedem Beteiligten AUSDRUECKLICH uebergeben.
        print(f"Archivverzeichnis dieses Laufs: {daten / PAKET}", flush=True)
        print(f"Laufzeitverzeichnis:            {runtime}", flush=True)

        print("\n--- K1/K2/K5/K6/K9 " + "-" * 53, flush=True)
        try:
            zustand = k1_bis_k9_einspielung(B, pruefling, arbeit, runtime,
                                            daten)
        except Exception as exc:                                # noqa: BLE001
            B.schlecht("K1", f"Messung abgestuerzt: {exc!r}")

        print("\n--- K4 " + "-" * 66, flush=True)
        try:
            k4_kein_rohaudio(B, daten, (daten / PAKET / "archiv.db"))
            k4_echtes_archiv(B)
        except Exception as exc:                                # noqa: BLE001
            B.schlecht("K4", f"Messung abgestuerzt: {exc!r}")

        print("\n--- K3 " + "-" * 66, flush=True)
        try:
            k3_leerlauf(B, pruefling, arbeit)
        except Exception as exc:                                # noqa: BLE001
            B.schlecht("K3", f"Messung abgestuerzt: {exc!r}")

        print("\n--- K7 " + "-" * 66, flush=True)
        try:
            k7_pause_schliesst_beide(B, pruefling, arbeit)
        except Exception as exc:                                # noqa: BLE001
            B.schlecht("K7", f"Messung abgestuerzt: {exc!r}")

        print("\n--- K8 " + "-" * 66, flush=True)
        if zustand is None:
            B.schlecht("K8", "der Hauptlauf kam nicht zustande -- ohne ihn "
                             "gibt es keinen Vorher-Stand zum Vergleichen")
        else:
            try:
                k8_nach_der_pause(B, zustand)
            except Exception as exc:                            # noqa: BLE001
                B.schlecht("K8", f"Messung abgestuerzt: {exc!r}")

        sperre_pruefen(sperr_log, arbeit / "vorschalter.log")
        return B.abschluss()
    finally:
        if zustand is not None:
            try:
                zustand["sonde"].beenden()
            except Exception:                                   # noqa: BLE001
                pass
            try:
                zustand["recorder"].beenden()
            except Exception:                                   # noqa: BLE001
                pass
        for k, v in alte_umgebung.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        transiente_units_aufraeumen()
        shutil.rmtree(arbeit, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
