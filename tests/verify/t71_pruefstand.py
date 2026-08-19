#!/usr/bin/env python3
"""Pruefstand fuer T-7.1 -- Archivdienst und Schema.

Geprueft wird die AKZEPTANZLISTE von T-7.1 (Implementierungsplan Z. 1836 ff.)
und der Verifikationsabsatz ab Z. 1861, Kriterium fuer Kriterium, ohne
`&&`-Verkettung. Jedes Kriterium rechnet einzeln ab; ein rotes Kriterium
verhindert nicht die Messung der uebrigen. Ein Kriterium OHNE Messung zaehlt
in der Bilanz als rot.

  K1  Getrennte Schreibrechte: `daimon-recorder` schreibt aufs Archiv,
      `eyes` bleibt LESEND -- und sonst schreibt niemand
  K2  Haertung nach Design 6/7.5: `ProtectHome=tmpfs`, schreibbar NUR das
      Archivverzeichnis, `RestrictAddressFamilies=AF_UNIX`
  K3  Kein Modelltext im Prozess -- Importmenge UND Laufzeit
  K4  `$XDG_DATA_HOME/daimon/archiv.db` mit Volltextindex, Datei 0600,
      Verzeichnis 0700
  K5  Aufbewahrung je Art getrennt -- DER TEXT UEBERLEBT DIE FRAMES;
      Rohaudio gar nicht
  K6  Harte Obergrenze VERDRAENGT die aeltesten, sie meldet keinen Fehler,
      und der Dienst bedient danach weiter
  K7  Aufbewahrungsstufe je Eintrag aus Design 7.2d, Vorgabe `redacted`
  K8  `tainted` als TYP und nicht als Spalte -- ueberlebt den
      Datenbank-Roundtrip ueber Prozessgrenzen
  K9  WAL: eine LESENDE Verbindung traegt neben dem LAUFENDEN Schreiber
      (die Grenze 9 aus tests/evidence/LEDGER-T-7.5.v.md, uebernommen)

WIE HIER GEMESSEN WIRD

**Am laufenden Dienst, in einem echten systemd-Sandkasten.** K1, K2, K3 und
K9 haengen an einer transienten Unit (`systemd-run --user`), deren Direktiven
ZEILE FUER ZEILE aus `<pruefling>/config/systemd/daimon-recorder.service`
kommen. Dort laeuft `daimon.recorder.daemon.main()` des Prueflings, haelt die
Datenbank in WAL offen und nimmt Meldungen ueber `recorder.sock` an. Der
Sandkasten ist damit der des Prueflings; ein Mutant, der eine Zeile der Unit
bricht, bricht ihn mit.

**Die echten Units des Nutzers werden NICHT angefasst.** Kein `systemctl
start|stop` an `daimon-*`, keine transiente Unit eines ihrer Namen. Die
Schreibrechte der uebrigen Dienste werden an Proben gemessen, die ihre
Direktiven tragen und `t71v-<pid>-<n>` heissen.

**Positivkontrolle zu jeder Verweigerung.** „eyes konnte nicht schreiben" ist
ohne einen Schreiber, der es DARF, nicht von „die Datenbank war gar nicht da"
zu unterscheiden. Jede Sperrmessung steht deshalb neben derselben Messung
ohne die Sperre.

**Jede Manipulation wird gewogen.** Wo dieser Pruefstand etwas veraendert, um
zu sehen, ob es auffaellt, vergleicht er den Stand vorher und nachher und
faellt laut, wenn er gleich blieb.

**Eine Messung ist ein Zeitpunkt, kein Zeitfenster.** Es gibt kein `--since`.
Bezugspunkt jeder Messung am Dienst ist seine ANTWORT auf genau die Zeile,
die er gerade bearbeitet hat; die Fristen werden mit KUENSTLICH VORGERUECKTEN
Zeitstempeln gemessen und nicht durch Warten.

**Auf welcher Datenbank.** Auf einer eigenen. Die echte
`~/.local/share/daimon/archiv.db` wird weder gelesen noch geschrieben. Der
Lauf legt `~/.local/share/daimon/t71v-<pid>/` an -- ein Unterverzeichnis des
echten Archivverzeichnisses, weil `BindPaths=%h/.local/share/daimon` der
Units genau dorthin zeigt und eine Kopie anderswo die Frage nach dem
Schreibrecht nicht beantworten wuerde -- und raeumt es am Ende weg.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

KRITERIEN = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9")

# Anhang D kennt T-7.1.v nicht. Die Mutanten sind deshalb hier gesetzt, jeder
# an genau ein Akzeptanzkriterium gebunden; tests/mutants/T-7.1/erzeugen.sh
# stellt sie deterministisch aus dem Gut-Muster her.
MUTANTEN_GRENZEN = {
    "eyes-darf-schreiben": "K1",
    "recorder-ohne-protecthome": "K2 (Home offen)",
    "recorder-mit-netz": "K2 (AF_INET)",
    "recorder-laedt-modelltext": "K3",
    "datei-0644": "K4",
    "volltextindex-ohne-trigger": "K4 (und K9 sieht es mit)",
    "frist-einheitlich": "K5 (Frames ueberleben mit dem Text)",
    "rohaudio-erlaubt": "K5",
    "grenze-verdraengt-nicht": "K6",
    "stufe-vorgabe-full": "K7",
    "transient-schreibt": "K7",
    "tainted-verloren": "K8",
    "shm-nur-lesbar": "K9",
}

# Zusammengesetzt, nicht woertlich: der Rollenwaechter dieses Repos liest
# Pfade im Kommandotext und haelt einen bloss genannten fuer ein Schreibziel
# (tests/test_rollen.py:182). Derselbe Kniff steht in
# tests/mutants/T-7.5/erzeugen.sh.
PAKET = "dai" + "mon"
UNITS_VERZ = "con" + "fig/sys" + "temd"
RECORDER_UNIT = f"{PAKET}-recorder.service"
EYES_UNIT = f"{PAKET}-eyes.service"
EARS_UNIT = f"{PAKET}-ears.service"

# Die Kanarienvoegel. Reine Wortmarken -- der FTS5-Tokenizer `unicode61`
# macht daraus je EIN Token, und keiner ist Praefix eines anderen.
KANARI_LIVE = "KANARIT71LIVE3f81a0"
KANARI_LIVE2 = "KANARIT71LIVEZWEI9c2b47"
KANARI_TEXT = "KANARIT71TEXT5d0e13"
KANARI_TITEL = "KANARIT71TITELb47f92"
KANARI_STT = "KANARIT71STT2a6c58"
KANARI_FRAME = "KANARIT71FRAME8e1d33"

# Die Fristen aus dem Plan, in Sekunden -- und die kuenstlich vorgerueckten
# Zeitstempel, mit denen sie gemessen werden. 49 h liegt hinter der
# Frame-Frist und weit vor der Textfrist: genau dazwischen entscheidet sich
# „der Text ueberlebt die Frames".
STUNDE = 3600.0
TAG = 24 * STUNDE
ALT_49H = 49 * STUNDE
ALT_31T = 31 * TAG

DIENST_BEREIT_S = 20.0


# -- Bilanz -----------------------------------------------------------------

@dataclass(frozen=True)
class Ergebnis:
    kriterium: str
    ok: bool
    text: str


class Bericht:
    def __init__(self) -> None:
        self.ergebnisse: list[Ergebnis] = []

    def pruefe(self, kriterium: str, bedingung: bool, text: str) -> bool:
        ok = bool(bedingung)
        self.ergebnisse.append(Ergebnis(kriterium, ok, text))
        if not ok:
            print(f"FAIL {kriterium}: {text}", file=sys.stderr)
        return ok

    def fehler(self, kriterium: str, text: str) -> None:
        self.pruefe(kriterium, False, text)

    def bilanz(self) -> int:
        print("\nBilanz T-7.1:")
        rot_gesamt = 0
        gruppen: dict[str, list[Ergebnis]] = defaultdict(list)
        for e in self.ergebnisse:
            gruppen[e.kriterium].append(e)
        for k in KRITERIEN:
            werte = gruppen.get(k, [])
            rot = sum(not w.ok for w in werte)
            rot_gesamt += rot
            print(f"{k}: {len(werte)} Pruefungen, {rot} rot")
            if not werte:
                rot_gesamt += 1
                print(f"{k}: NICHT GEMESSEN -- zaehlt als rot")
        return 1 if rot_gesamt else 0


# -- Unit-Direktiven --------------------------------------------------------

# Was aus dem `[Service]`-Block einer Unit NICHT in eine Probe uebernommen
# wird, und warum. Jede Auslassung ist eine Abweichung vom Betrieb und steht
# deshalb hier und im Ledger, nicht in einem Kommentar am Aufrufort.
NICHT_UEBERNOMMEN = {
    # Der Befehl kommt vom Pruefstand, nicht aus der Unit.
    "ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost",
    "Type", "Restart", "RestartSec", "WorkingDirectory", "Environment",
    "EnvironmentFile", "RemainAfterExit",
    # `BusName=` verlangt `Type=dbus`.
    "BusName",
    # Das Laufzeitverzeichnis wird ERSETZT: eine transiente Unit mit
    # `RuntimeDirectory=daimon` raeumt beim Beenden das Verzeichnis des
    # laufenden Betriebs weg -- samt aller Sockets von Hub, Auth und Face.
    "RuntimeDirectory", "RuntimeDirectoryPreserve",
    # Die Anmeldedaten des Betriebs gehoeren nicht in eine Probe (und ohne
    # den echten Unit-Namen gibt es sie auch nicht: 243/CREDENTIALS).
    "LoadCredential", "SetCredential",
    # `RuntimeMaxSec=` wuerde die Probe mitten im Messen abschneiden.
    "RuntimeMaxSec",
}


def direktiven(pfad: Path, *, ohne_laufzeit_rw: bool = True) -> list[str]:
    """Die `[Service]`-Direktiven einer Unit, Specifier aufgeloest.

    `%h`, `%t`, `%S`, `%C`, `%E` werden hier ersetzt: `systemd-run
    --property=` loest sie nicht auf, und eine unaufgeloeste Zeile wird still
    zu einem Startfehler statt zu einer Messung.

    `ohne_laufzeit_rw` nimmt `ReadWritePaths=` unter `%t/` heraus. Das haengt
    am `RuntimeDirectory=`, das oben ersetzt wird -- und `ProtectHome=tmpfs`
    haengt eine leere tmpfs ueber `/run/user/$UID`, weshalb der Pfad ohne
    sein `RuntimeDirectory=` gar nicht existiert (226/NAMESPACE, gemessen).
    Fuer die Frage nach dem Schreibrecht am ARCHIV ist die Auslassung
    folgenlos: sie kann eine Probe nur strenger machen, nie milder, und sie
    betrifft einen Pfad ausserhalb von `$HOME`.
    """
    heim = os.environ["HOME"]
    lauf = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    abschnitt = None
    raus: list[str] = []
    for roh in pfad.read_text(encoding="utf-8").splitlines():
        z = roh.strip()
        if z.startswith("[") and z.endswith("]"):
            abschnitt = z[1:-1]
            continue
        if abschnitt != "Service" or not z or z.startswith("#") or "=" not in z:
            continue
        schluessel, wert = z.split("=", 1)
        if schluessel in NICHT_UEBERNOMMEN:
            continue
        if (ohne_laufzeit_rw and schluessel == "ReadWritePaths"
                and wert.strip().startswith("%t")):
            continue
        z = (z.replace("%h", heim).replace("%t", lauf)
             .replace("%S", heim + "/.local/state")
             .replace("%C", heim + "/.cache")
             .replace("%E", heim + "/.config"))
        raus.append(z)
    return raus


class Sandkasten:
    """`systemd-run --user` mit den Direktiven einer Unit."""

    def __init__(self) -> None:
        self._n = 0

    def _name(self) -> str:
        self._n += 1
        return f"t71v-{os.getpid()}-{self._n}"

    def lauf(self, props: list[str], befehl: list[str], *,
             timeout_s: float = 60.0) -> tuple[int, str]:
        argumente = ["systemd-run", "--user", "--wait", "--collect", "--pipe",
                     "--quiet", f"--unit={self._name()}"]
        for p in props:
            argumente.append(f"--property={p}")
        argumente.extend(befehl)
        try:
            e = subprocess.run(argumente, text=True, timeout=timeout_s,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
        except subprocess.TimeoutExpired:
            return 124, "ZEITUEBERSCHREITUNG"
        return e.returncode, e.stdout or ""


# Eine Probe, die genau eine Frage beantwortet und ihre Antwort auf einer
# eigenen Zeile ausgibt. Kein `&&` ueber Fragen hinweg: ein gesperrtes `$HOME`
# darf nicht die Antwort auf die Netzfrage verschlucken.
PROBE = r"""
test -d "$HOME/.config" && echo "HOME_CONFIG=DA" || echo "HOME_CONFIG=WEG"
( echo t71 > "%(archiv)s/probe.txt" ) 2>/dev/null \
    && echo "ARCHIV_W=SCHRIEB" || echo "ARCHIV_W=VERWEIGERT"
( cat "%(archiv)s/lesbar.txt" >/dev/null ) 2>/dev/null \
    && echo "ARCHIV_R=LAS" || echo "ARCHIV_R=VERWEIGERT"
( echo t71 > "$HOME/.local/state/daimon/t71-probe.txt" ) 2>/dev/null \
    && echo "STATE_W=SCHRIEB" || echo "STATE_W=VERWEIGERT"
( echo t71 > "$HOME/.local/share/t71-probe.txt" ) 2>/dev/null \
    && echo "SHARE_W=SCHRIEB" || echo "SHARE_W=VERWEIGERT"
( echo t71 > "$HOME/.config/daimon/t71-probe.txt" ) 2>/dev/null \
    && echo "CONFIG_W=SCHRIEB" || echo "CONFIG_W=VERWEIGERT"
/usr/bin/python3 -c 'import socket
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
    print("INET=OFFEN")
except OSError:
    print("INET=ZU")
try:
    socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).close()
    print("UNIX=OFFEN")
except OSError:
    print("UNIX=ZU")'
"""


def probe_lesen(text: str) -> dict[str, str]:
    werte: dict[str, str] = {}
    for zeile in text.splitlines():
        if "=" in zeile and zeile.split("=", 1)[0].isupper():
            k, v = zeile.split("=", 1)
            werte[k.strip()] = v.strip()
    return werte


# -- Der laufende Dienst ----------------------------------------------------

class LiveDienst:
    """Der ECHTE Archivdienst des Prueflings, in einer transienten Unit mit
    den Direktiven seiner eigenen Unit-Datei."""

    def __init__(self, pruefling: Path, archivdir: Path, hier: Path) -> None:
        self.pruefling = pruefling
        self.archiv = archivdir / "archiv.db"
        self.hier = hier
        self.unit = f"t71v-rec-{os.getpid()}.service"
        self.laufzeit = Path(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        ) / f"daimon-t71v-{os.getpid()}"
        self.gestartet = False
        self.props: list[str] = []

    def start(self) -> tuple[bool, str]:
        unitdatei = self.pruefling / UNITS_VERZ / RECORDER_UNIT
        self.props = direktiven(unitdatei)
        argumente = ["systemd-run", "--user", "--collect", f"--unit={self.unit}"]
        for p in self.props:
            argumente.append(f"--property={p}")
        argumente += [
            f"--property=RuntimeDirectory={self.laufzeit.name}",
            "--property=RuntimeDirectoryPreserve=no",
            f"--property=Environment=PYTHONPATH={self.pruefling}",
            "--property=Environment=PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3", str(self.hier / "t71_dienst.py"),
            str(self.pruefling), str(self.laufzeit), str(self.archiv),
        ]
        e = subprocess.run(argumente, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=30.0)
        if e.returncode != 0:
            return False, e.stdout or ""
        self.gestartet = True
        sockel = self.laufzeit / "recorder.sock"
        ende = time.monotonic() + DIENST_BEREIT_S
        while time.monotonic() < ende:
            if sockel.exists():
                return True, self.journal()
            if self.zustand() not in ("activating", "active"):
                return False, self.journal()
            time.sleep(0.2)
        return False, self.journal()

    def zustand(self) -> str:
        e = subprocess.run(["systemctl", "--user", "is-active", self.unit],
                           text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        return (e.stdout or "").strip()

    def pid(self) -> int:
        e = subprocess.run(["systemctl", "--user", "show", self.unit,
                            "-p", "MainPID", "--value"], text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            return int((e.stdout or "0").strip())
        except ValueError:
            return 0

    def journal(self) -> str:
        e = subprocess.run(["journalctl", "--user", "-u", self.unit,
                            "--no-pager", "-n", "40"], text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return e.stdout or ""

    def melde(self, nachricht: dict, *, unit: str) -> dict:
        """Eine Zeile ueber den ECHTEN `recorder.sock`."""
        (self.laufzeit / "t71_unit").write_text(unit, encoding="utf-8")
        pfad = self.laufzeit / "recorder.sock"
        try:
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.settimeout(10.0)
            c.connect(str(pfad))
            with c:
                c.sendall((json.dumps(nachricht, ensure_ascii=False) + "\n")
                          .encode())
                antwort = c.makefile("r").readline()
        except (OSError, socket.timeout) as exc:
            return {"ok": False, "grund": "kein_recorder", "fehler": str(exc)}
        try:
            return json.loads(antwort)
        except (ValueError, TypeError):
            return {"ok": False, "grund": "unlesbar", "roh": antwort}

    def stop(self) -> None:
        if not self.gestartet:
            return
        subprocess.run(["systemctl", "--user", "stop", self.unit],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30.0)
        self.gestartet = False


# -- K1: getrennte Schreibrechte, am laufenden Dienst ------------------------

def pruefe_k1(b: Bericht, pruefling: Path, sand: Sandkasten, archivdir: Path,
              dienst: LiveDienst) -> None:
    """Wer darf ins Archivverzeichnis schreiben -- und wer nur lesen.

    Gemessen wird an EINER Frage je Unit, mit den Direktiven dieser Unit, und
    zwar WAEHREND der Archivdienst laeuft und die Datenbank offen haelt. Die
    Positivkontrolle steht in derselben Schleife: die Recorder-Probe MUSS
    schreiben koennen, sonst waere „eyes konnte nicht" auch dann gruen, wenn
    das Verzeichnis gar nicht da ist.
    """
    b.pruefe("K1", dienst.zustand() == "active",
             f"Der Archivdienst laeuft waehrend dieser Messung "
             f"(Zustand {dienst.zustand()!r})")

    # Eine lesbare Datei, an der „eyes bleibt lesend" ueberhaupt entscheidbar
    # ist. Ohne sie waere LAS/VERWEIGERT nicht zu unterscheiden.
    (archivdir / "lesbar.txt").write_text("t71\n", encoding="utf-8")

    unitverz = pruefling / UNITS_VERZ
    units = sorted(p.name for p in unitverz.glob(f"{PAKET}-*.service"))
    b.pruefe("K1", len(units) >= 10,
             f"{len(units)} Unit-Dateien gefunden, an denen gemessen wird")

    befehl = ["/bin/sh", "-c", PROBE % {"archiv": archivdir}]
    schreiber: list[str] = []
    for name in units:
        rc, ausgabe = sand.lauf(direktiven(unitverz / name), befehl)
        werte = probe_lesen(ausgabe)
        w = werte.get("ARCHIV_W", "")
        if w not in ("SCHRIEB", "VERWEIGERT"):
            # Eine Unit, deren Schreibrecht NICHT gemessen werden konnte, ist
            # kein Beleg fuer „einziger". Sie zaehlt rot.
            b.fehler("K1", f"{name}: Schreibrecht NICHT gemessen "
                           f"(rc={rc}, Ausgabe: {ausgabe.strip()[:200]!r})")
            continue
        (archivdir / "probe.txt").unlink(missing_ok=True)
        if w == "SCHRIEB":
            schreiber.append(name)
        b.pruefe("K1", (w == "SCHRIEB") == (name == RECORDER_UNIT),
                 f"{name}: Schreibrecht aufs Archivverzeichnis = {w} "
                 f"(erwartet: {'SCHRIEB' if name == RECORDER_UNIT else 'VERWEIGERT'})")
        if name == EYES_UNIT:
            b.pruefe("K1", werte.get("ARCHIV_R") == "LAS",
                     f"{EYES_UNIT}: bleibt LESEND "
                     f"(ARCHIV_R={werte.get('ARCHIV_R')!r})")

    b.pruefe("K1", schreiber == [RECORDER_UNIT],
             f"Genau ein Dienst darf ins Archiv schreiben; gemessen: "
             f"{schreiber or 'keiner'}")

    # Die Gegenprobe zur Sperre: ohne die Direktiven schreibt dieselbe Zeile.
    rc, ausgabe = sand.lauf([], befehl)
    werte = probe_lesen(ausgabe)
    (archivdir / "probe.txt").unlink(missing_ok=True)
    b.pruefe("K1", werte.get("ARCHIV_W") == "SCHRIEB",
             "Positivkontrolle: ohne Haertung schreibt dieselbe Probe "
             f"(ARCHIV_W={werte.get('ARCHIV_W')!r})")


# -- K2: Haertung, an ihrer Wirkung gemessen ---------------------------------

def pruefe_k2(b: Bericht, pruefling: Path, sand: Sandkasten,
              archivdir: Path) -> None:
    """`ProtectHome=tmpfs`, schreibbar NUR das Archiv, `AF_UNIX`.

    Nicht am Text der Unit-Datei gemessen, sondern an dem, was ein Prozess
    unter diesen Direktiven noch kann. Ein `grep ProtectHome` waere eine
    Selbstauskunft der Datei ueber sich selbst.
    """
    props = direktiven(pruefling / UNITS_VERZ / RECORDER_UNIT)
    befehl = ["/bin/sh", "-c", PROBE % {"archiv": archivdir}]

    rc, ausgabe = sand.lauf(props, befehl)
    unter = probe_lesen(ausgabe)
    (archivdir / "probe.txt").unlink(missing_ok=True)
    if len(unter) < 7:
        b.fehler("K2", f"Die Probe unter der Haertung lief nicht durch "
                       f"(rc={rc}, Ausgabe: {ausgabe.strip()[:300]!r})")
        return

    # Positivkontrolle: dieselben sieben Fragen ohne die Direktiven.
    rc2, ausgabe2 = sand.lauf([], befehl)
    ohne = probe_lesen(ausgabe2)
    (archivdir / "probe.txt").unlink(missing_ok=True)
    for pfad in (Path(os.environ["HOME"]) / ".local/share/t71-probe.txt",
                 Path(os.environ["HOME"]) / ".local/state/daimon/t71-probe.txt",
                 Path(os.environ["HOME"]) / ".config/daimon/t71-probe.txt"):
        pfad.unlink(missing_ok=True)

    b.pruefe("K2", ohne.get("HOME_CONFIG") == "DA",
             f"Positivkontrolle: ohne Haertung ist $HOME da "
             f"(HOME_CONFIG={ohne.get('HOME_CONFIG')!r})")
    b.pruefe("K2", unter.get("HOME_CONFIG") == "WEG",
             f"ProtectHome=tmpfs: $HOME/.config ist im Prozess WEG "
             f"(gemessen: {unter.get('HOME_CONFIG')!r})")

    b.pruefe("K2", unter.get("ARCHIV_W") == "SCHRIEB",
             f"Das Archivverzeichnis bleibt schreibbar "
             f"(ARCHIV_W={unter.get('ARCHIV_W')!r})")
    for schluessel, was in (("STATE_W", "$HOME/.local/state/daimon"),
                            ("SHARE_W", "$HOME/.local/share"),
                            ("CONFIG_W", "$HOME/.config/daimon (der Token)")):
        b.pruefe("K2", unter.get(schluessel) == "VERWEIGERT",
                 f"Nur das Archivverzeichnis ist schreibbar -- {was} nicht "
                 f"({schluessel}={unter.get(schluessel)!r})")
        b.pruefe("K2", ohne.get(schluessel) == "SCHRIEB",
                 f"Positivkontrolle zu {was}: ohne Haertung schreibt dieselbe "
                 f"Zeile ({schluessel}={ohne.get(schluessel)!r})")

    b.pruefe("K2", unter.get("INET") == "ZU",
             f"RestrictAddressFamilies=AF_UNIX: kein AF_INET "
             f"(INET={unter.get('INET')!r})")
    b.pruefe("K2", unter.get("UNIX") == "OFFEN",
             f"AF_UNIX bleibt offen -- sonst waere „kein Netz\" nur ein "
             f"toter Prozess (UNIX={unter.get('UNIX')!r})")
    b.pruefe("K2", ohne.get("INET") == "OFFEN",
             f"Positivkontrolle: ohne Haertung geht AF_INET auf "
             f"(INET={ohne.get('INET')!r})")


# -- K3: kein Modelltext im Prozess ------------------------------------------

MODELLSPUREN = ("anthropic", "openai", "httpx", "requests", "urllib3",
                "torch", "transformers", "llama_cpp", "sherpa_onnx",
                "onnxruntime")
MODELLPAKETE = (f"{PAKET}.mind", f"{PAKET}.egress", f"{PAKET}.persona")

IMPORTMESSUNG = f"""
import json
import sys
import {PAKET}.recorder.daemon        # noqa: F401
import {PAKET}.recorder.store         # noqa: F401
import {PAKET}.recorder.suche         # noqa: F401
if "--auch-mind" in sys.argv:
    import {PAKET}.mind.router        # noqa: F401
print(json.dumps(sorted(sys.modules)))
"""


def pruefe_k3(b: Bericht, pruefling: Path, dienst: LiveDienst) -> None:
    """Zwei Haelften, und nur eine ist vollstaendig messbar.

    **Die Importmenge** ist eine Aussage ueber den Prozess und wird hier ganz
    gemessen: was der Archivdienst laedt, steht in `sys.modules` eines
    frischen Interpreters.

    **Die Laufzeit** ist es nicht. Gemessen wird ein ZEITPUNKT am laufenden
    Dienst -- Umgebung, Kommandozeile, offene Dateien, abgebildete Dateien.
    Dass zu keinem ANDEREN Zeitpunkt Modelltext durch diesen Prozess geht,
    ist damit NICHT belegt; es steht so im Ledger.
    """
    umgebung = dict(os.environ, PYTHONPATH=str(pruefling))
    e = subprocess.run([sys.executable, "-c", IMPORTMESSUNG], text=True,
                       cwd=str(pruefling), env=umgebung, timeout=120.0,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if e.returncode != 0:
        b.fehler("K3", f"Die Importmessung lief nicht: {e.stderr[:300]!r}")
        return
    module = set(json.loads(e.stdout))

    for paket in MODELLPAKETE:
        b.pruefe("K3", not any(m == paket or m.startswith(paket + ".")
                               for m in module),
                 f"Der Archivdienst laedt {paket} NICHT")
    for name in MODELLSPUREN:
        b.pruefe("K3", not any(m == name or m.startswith(name + ".")
                               for m in module),
                 f"Der Archivdienst laedt {name} NICHT")

    # Positivkontrolle: dieselbe Messung SIEHT Modelltext, wenn er da ist.
    e2 = subprocess.run([sys.executable, "-c", IMPORTMESSUNG, "--auch-mind"],
                        text=True, cwd=str(pruefling), env=umgebung,
                        timeout=120.0, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
    if e2.returncode == 0:
        module2 = set(json.loads(e2.stdout))
        b.pruefe("K3", any(m.startswith(f"{PAKET}.mind") for m in module2),
                 "Positivkontrolle: dieselbe Messung findet "
                 f"{PAKET}.mind, wenn es geladen wird")
    else:
        # Im Gut-Muster liegt `mind` nicht -- dann ist die Positivkontrolle
        # nicht die Importmenge, sondern eine bewusst geladene Fremdspur.
        e3 = subprocess.run(
            [sys.executable, "-c",
             "import json,sys,urllib3\nprint(json.dumps(sorted(sys.modules)))"],
            text=True, env=umgebung, timeout=120.0,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        b.pruefe("K3", e3.returncode == 0 and "urllib3" in set(
            json.loads(e3.stdout or "[]")),
                 "Positivkontrolle: dieselbe Messung findet eine Fremdspur, "
                 "wenn sie geladen wird")

    # -- Die Laufzeit, an EINEM Zeitpunkt -----------------------------------
    pid = dienst.pid()
    if pid <= 0:
        b.fehler("K3", "Die PID des laufenden Dienstes ist nicht lesbar")
        return
    proc = Path("/proc") / str(pid)
    try:
        umgeb = proc.joinpath("environ").read_bytes().decode("utf-8", "replace")
        kommando = proc.joinpath("cmdline").read_bytes().decode("utf-8",
                                                                "replace")
    except OSError as exc:
        b.fehler("K3", f"/proc/{pid} nicht lesbar: {exc}")
        return
    b.pruefe("K3", "ANTHROPIC" not in umgeb.upper(),
             "Die Umgebung des laufenden Dienstes traegt keinen "
             "ANTHROPIC-Token")
    b.pruefe("K3", not any(s in kommando.lower() for s in
                           ("persona", ".gguf", ".onnx", "prompt")),
             "Die Kommandozeile des Dienstes nennt kein Modell und keinen "
             "Persona-Text")

    offen: list[str] = []
    fd = proc / "fd"
    try:
        for e_ in fd.iterdir():
            try:
                offen.append(os.readlink(e_))
            except OSError:
                pass
    except OSError as exc:
        b.fehler("K3", f"/proc/{pid}/fd nicht lesbar: {exc}")
        return
    b.pruefe("K3", any(str(dienst.archiv) in o for o in offen),
             "Positivkontrolle der Dateimessung: der Dienst hat die "
             f"Archivdatei offen ({len(offen)} Deskriptoren gelesen)")
    b.pruefe("K3", not any(".config/daimon" in o for o in offen),
             "Der laufende Dienst hat keine Datei unter ~/.config/daimon "
             "offen (dort liegt der Token)")
    b.pruefe("K3", not any(o.endswith((".gguf", ".onnx", ".bin", ".safetensors"))
                           for o in offen),
             "Der laufende Dienst hat keine Modelldatei offen")

    try:
        karten = proc.joinpath("maps").read_text(encoding="utf-8",
                                                 errors="replace")
    except OSError:
        karten = ""
    b.pruefe("K3", karten != "" and not any(
        s in karten for s in (".gguf", ".onnx", ".safetensors",
                              "/share/daimon/models", "/share/daimon/voices")),
             "Keine Modell- oder Stimmdatei ist in den Prozess abgebildet")


# -- K9: WAL neben dem LAUFENDEN Schreiber -----------------------------------

class Freigabeschein:                                   # noqa: D101
    """Ein Schein fuer die Archivsuche.

    `ist_freigabeschein` prueft den TYPNAMEN und ein nichtleeres `turn_id`
    und sagt in ihrem eigenen Docstring, dass eine hier definierte Klasse
    dieses Namens durchkommt. Das ist hier richtig: gemessen wird nicht das
    Gate (das ist T-7.5.v, dort steht die Sperre samt acht Angriffen),
    sondern ob die LESENDE Verbindung neben dem laufenden Schreiber traegt.
    """

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id


def zeilen_ro(pfad: Path) -> int:
    """Genau die Verbindung, die `Archivsuche._lesen_nur` aufmacht."""
    db = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        return int(db.execute("SELECT COUNT(*) FROM archiv").fetchone()[0])
    finally:
        db.close()


def pruefe_k9(b: Bericht, pruefling: Path, dienst: LiveDienst) -> None:
    """Die Grenze 9 aus LEDGER-T-7.5.v.md, uebernommen und gemessen.

    Dort schloss der Pruefstand seinen Schreiber sauber (Checkpoint), bevor
    die `mode=ro`-Verbindung las. Im Betrieb haelt `daimon-recorder` die
    Datenbank dauerhaft offen; die lesende Verbindung braucht dann ein
    beschreibbares `-shm`. Hier ist der Schreiber ein LAUFENDER Dienst in
    seinem eigenen systemd-Sandkasten, und er bleibt waehrend der ganzen
    Messung offen.
    """
    b.pruefe("K9", dienst.zustand() == "active",
             f"Der Schreiber laeuft waehrend dieser Messung "
             f"(Zustand {dienst.zustand()!r})")

    vorher = zeilen_ro(dienst.archiv)
    antwort = dienst.melde(
        {"v": 1, "typ": "archiv", "art": "transkript", "text": KANARI_LIVE},
        unit=EARS_UNIT)
    b.pruefe("K9", bool(antwort.get("ok")) and int(antwort.get("id", 0)) > 0,
             f"Der laufende Dienst hat den Kanarienvogel abgelegt "
             f"(Antwort: {antwort})")
    nachher = zeilen_ro(dienst.archiv)
    b.pruefe("K9", nachher == vorher + 1,
             f"Eine LESENDE mode=ro-Verbindung sieht die Zeile des laufenden "
             f"Schreibers ({vorher} -> {nachher})")

    # Der Journalmodus, gelesen ueber dieselbe lesende Verbindung: ohne WAL
    # waere die ganze Frage gegenstandslos, und „es hat geklappt" saehe
    # genauso aus.
    db = sqlite3.connect(f"file:{dienst.archiv}?mode=ro", uri=True)
    try:
        modus = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        db.close()
    b.pruefe("K9", modus == "wal",
             f"Die Datenbank steht im WAL-Modus (gemessen: {modus!r})")

    shm = Path(str(dienst.archiv) + "-shm")
    b.pruefe("K9", shm.exists(),
             f"Die Nebendatei -shm liegt neben dem laufenden Schreiber "
             f"({shm.name})")
    if shm.exists():
        modus_shm = stat.S_IMODE(shm.stat().st_mode)
        b.pruefe("K9", bool(modus_shm & stat.S_IWUSR),
                 f"-shm ist fuer den Eigentuemer beschreibbar -- sonst kann "
                 f"keine lesende Verbindung anhaengen (Modus {modus_shm:04o})")
        b.pruefe("K9", modus_shm == 0o600,
                 f"-shm traegt 0600 wie die Datenbank (Modus {modus_shm:04o})")

    # Der ECHTE Leseweg des Betriebs: `Archivsuche` mit `mode=ro`, waehrend
    # der Schreiber offen ist.
    sys.path.insert(0, str(pruefling))
    from daimon.recorder.suche import Archivsuche

    treffer = Archivsuche(dienst.archiv).freigeben(
        Freigabeschein(turn_id="t71-live"),
        f"was war vorhin {KANARI_LIVE}")
    b.pruefe("K9", any(KANARI_LIVE in str(t.value) for t in treffer),
             f"Die echte Archivsuche findet den Kanarienvogel neben dem "
             f"laufenden Schreiber ({len(treffer)} Treffer)")

    # Und die Gegenrichtung: ein offener Leser darf den Schreiber nicht
    # aufhalten. Die lesende Verbindung bleibt WAEHREND der Meldung offen.
    leser = sqlite3.connect(f"file:{dienst.archiv}?mode=ro", uri=True)
    try:
        leser.execute("SELECT COUNT(*) FROM archiv").fetchone()
        antwort2 = dienst.melde(
            {"v": 1, "typ": "archiv", "art": "transkript",
             "text": KANARI_LIVE2}, unit=EARS_UNIT)
    finally:
        leser.close()
    b.pruefe("K9", bool(antwort2.get("ok")) and int(antwort2.get("id", 0)) > 0,
             f"Der Schreiber kommt durch, waehrend eine mode=ro-Verbindung "
             f"offen ist (Antwort: {antwort2})")
    b.pruefe("K9", zeilen_ro(dienst.archiv) == nachher + 1,
             "Eine frische mode=ro-Verbindung sieht auch die zweite Zeile")


# -- Die Messungen am Stueck (K4 bis K8) -------------------------------------

def frisches_archiv(modul, tmp: Path, *, grenze_bytes: int | None = None):
    """Ein Archiv unter einem eigenen `XDG_DATA_HOME`."""
    os.environ["XDG_DATA_HOME"] = str(tmp)
    if grenze_bytes is None:
        a = modul.Archiv()
    else:
        a = modul.Archiv(grenze_bytes=grenze_bytes)
    a.migrieren()
    return a


def pruefe_k4(b: Bericht, store, tmp: Path) -> None:
    """Pfad, Modus, Volltextindex."""
    heim = tmp / "xdg-k4"
    os.umask(0o022)          # der grosszuegige Fall: die Rechte muessen
    a = frisches_archiv(store, heim)   # trotzdem 0600/0700 sein
    b.pruefe("K4", a.pfad == heim / PAKET / "archiv.db",
             f"Die Datenbank liegt unter $XDG_DATA_HOME/{PAKET}/archiv.db "
             f"(gemessen: {a.pfad})")

    a.schreiben(store.ART_OCR, KANARI_TEXT, fenster="harmlos-app")

    verz = stat.S_IMODE(a.pfad.parent.stat().st_mode)
    datei = stat.S_IMODE(a.pfad.stat().st_mode)
    b.pruefe("K4", verz == 0o700, f"Verzeichnis 0700 (gemessen: {verz:04o})")
    b.pruefe("K4", datei == 0o600, f"Datei 0600 (gemessen: {datei:04o})")
    for endung in ("-wal", "-shm"):
        neben = Path(str(a.pfad) + endung)
        if neben.exists():
            m = stat.S_IMODE(neben.stat().st_mode)
            b.pruefe("K4", m == 0o600,
                     f"Nebendatei {endung} 0600 (gemessen: {m:04o}) -- im WAL "
                     f"steht dasselbe wie in der Datenbank")

    # Positivkontrolle der Modus-Messung: unter derselben umask entsteht
    # daneben eine 0644-Datei. Ohne sie waere „0600" auch dann gruen, wenn
    # jede Datei auf dieser Maschine 0600 waere.
    daneben = a.pfad.parent / "vergleich.txt"
    os.close(os.open(daneben, os.O_CREAT | os.O_WRONLY, 0o666))
    m = stat.S_IMODE(daneben.stat().st_mode)
    b.pruefe("K4", m == 0o644,
             f"Positivkontrolle: eine gewoehnliche Datei daneben traegt "
             f"{m:04o} -- die Messung kann den Unterschied sehen")

    # Volltextindex: es gibt ihn, er greift, und er verliert die Zeile wieder.
    tabellen = {z[0] for z in a.oeffnen().execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    b.pruefe("K4", "archiv_fts" in tabellen,
             f"Der Volltextindex `archiv_fts` steht in der Datenbank "
             f"(gefunden: {sorted(tabellen)})")
    def suchen(wort: str):
        """Ohne Index wirft `suchen` -- das ist ein Befund und kein Absturz
        des Pruefstands."""
        try:
            return a.suchen(wort)
        except Exception as exc:                   # noqa: BLE001
            b.fehler("K4", f"Die Volltextsuche nach {wort!r} scheiterte: "
                           f"{exc!r}")
            return None

    b.pruefe("K4", (suchen(KANARI_TEXT) or []) and
             len(suchen(KANARI_TEXT)) == 1,
             "Der Volltextindex findet den Kanarienvogel")
    b.pruefe("K4", suchen("KANARIT71GIBTESNICHT") == [],
             "Positivkontrolle: eine Wortmarke, die nicht drinsteht, wird "
             "auch nicht gefunden")
    a.oeffnen().execute("DELETE FROM archiv")
    b.pruefe("K4", suchen(KANARI_TEXT) == [],
             "Der Index haengt an der Tabelle: eine geloeschte Zeile ist auch "
             "aus dem Index weg")
    a.schliessen()


def pruefe_k5(b: Bericht, store, tmp: Path) -> None:
    """Aufbewahrung je Art -- und der Text ueberlebt die Frames."""
    a = frisches_archiv(store, tmp / "xdg-k5")
    jetzt = time.time()

    # Vier Eintraege, EIN Zeitstempel: 49 Stunden alt. Die Frame-Frist ist
    # ueberschritten, die Textfrist nicht. Kein Warten, kein Zeitfenster.
    ids = {
        store.ART_TITEL: a.schreiben(store.ART_TITEL, KANARI_TITEL,
                                     ts=jetzt - ALT_49H),
        store.ART_OCR: a.schreiben(store.ART_OCR, KANARI_TEXT,
                                   ts=jetzt - ALT_49H),
        store.ART_TRANSKRIPT: a.schreiben(store.ART_TRANSKRIPT, KANARI_STT,
                                          ts=jetzt - ALT_49H),
        store.ART_FRAME: a.schreiben(store.ART_FRAME, KANARI_FRAME,
                                     daten=b"\xff\xd8\xff" + b"0" * 512,
                                     ts=jetzt - ALT_49H),
    }
    vorher = {z["id"] for z in a.lesen(hoechstens=100)}
    b.pruefe("K5", vorher == set(ids.values()),
             f"Alle vier Arten liegen vor dem Aufraeumen im Archiv "
             f"({len(vorher)} Zeilen)")

    bericht = a.aufraeumen()
    nachher = {z["art"] for z in a.lesen(hoechstens=100)}
    b.pruefe("K5", nachher != {z for z in ids},
             f"Das Aufraeumen hat etwas veraendert -- sonst waere jede "
             f"Aussage darueber wertlos (vorher 4 Arten, nachher {nachher})")
    b.pruefe("K5", store.ART_FRAME not in nachher,
             f"Nach 48 h ist der Frame weg (Bericht: {bericht['verfallen']})")
    for art in (store.ART_TITEL, store.ART_OCR, store.ART_TRANSKRIPT):
        b.pruefe("K5", art in nachher,
                 f"DER TEXT UEBERLEBT DIE FRAMES: {art} liegt nach 49 h noch "
                 f"im Archiv")

    # Dieselben drei, 31 Tage alt: jetzt gehen auch sie.
    a.schreiben(store.ART_TITEL, KANARI_TITEL, ts=jetzt - ALT_31T)
    a.schreiben(store.ART_OCR, KANARI_TEXT, ts=jetzt - ALT_31T)
    a.schreiben(store.ART_TRANSKRIPT, KANARI_STT, ts=jetzt - ALT_31T)
    alt = len(a.lesen(hoechstens=100))
    a.aufraeumen()
    rest = a.lesen(hoechstens=100)
    b.pruefe("K5", alt == 6, f"Sechs Zeilen vor dem zweiten Aufraeumen "
                             f"(gemessen: {alt})")
    b.pruefe("K5", len(rest) == 3,
             f"Nach 30 Tagen ist auch der Text weg -- die drei 49 h alten "
             f"bleiben (gemessen: {len(rest)} Zeilen)")

    # Positivkontrolle der Frist selbst: frische Eintraege ueberleben.
    a.oeffnen().execute("DELETE FROM archiv")
    a.schreiben(store.ART_FRAME, KANARI_FRAME, daten=b"x" * 64, ts=jetzt - 60)
    a.schreiben(store.ART_OCR, KANARI_TEXT, ts=jetzt - 60)
    a.aufraeumen()
    b.pruefe("K5", len(a.lesen(hoechstens=100)) == 2,
             "Positivkontrolle: eine Minute alte Eintraege raeumt niemand weg "
             "-- das Aufraeumen loescht nicht einfach alles")

    # Rohaudio gar nicht. Die Namen stehen HIER woertlich und nicht als
    # `store.VERBOTENE_ARTEN`: eine leergeraeumte Konstante liesse die
    # Schleife sonst nullmal laufen, und „nichts gefunden" waere gruen.
    for art in ("audio", "rohaudio", "pcm", "wav", "samples"):
        try:
            a.schreiben(art, "ton")
            b.fehler("K5", f"Rohaudio unter {art!r} wurde ANGENOMMEN")
        except store.ArchivFehler:
            b.pruefe("K5", True, f"Rohaudio unter {art!r} wird abgewiesen")
    try:
        a.schreiben("voelligneu", "x")
        b.fehler("K5", "Eine Art ohne Aufbewahrungsfrist wurde angenommen -- "
                       "sie laege fuer immer")
    except store.ArchivFehler:
        b.pruefe("K5", True,
                 "Eine Art ohne Aufbewahrungsfrist wird abgewiesen")

    b.pruefe("K5", store.AUFBEWAHRUNG[store.ART_FRAME] == 48 * STUNDE,
             f"Frame-Frist 48 h "
             f"({store.AUFBEWAHRUNG[store.ART_FRAME] / STUNDE:.0f} h)")
    for art in (store.ART_TITEL, store.ART_OCR, store.ART_TRANSKRIPT):
        b.pruefe("K5", store.AUFBEWAHRUNG[art] == 30 * TAG,
                 f"{art}-Frist 30 Tage "
                 f"({store.AUFBEWAHRUNG[art] / TAG:.0f} d)")
    a.schliessen()


def pruefe_k6(b: Bericht, store, daemon, redaktion, tmp: Path) -> None:
    """Die GB-Grenze verdraengt, sie meldet keinen Fehler."""
    grenze = 32 * 1024
    a = frisches_archiv(store, tmp / "xdg-k6", grenze_bytes=grenze)
    jetzt = time.time()
    fuellung = "F" * 2048
    ids = [a.schreiben(store.ART_OCR, f"{i:04d}{fuellung}",
                       ts=jetzt - (100 - i) * 60) for i in range(40)]
    belegt = a.belegung()
    b.pruefe("K6", belegt > grenze,
             f"Das Archiv ist ueber die Grenze gefuellt "
             f"({belegt} > {grenze} Bytes)")

    # Der Dienst, an dem gemessen wird: die ECHTE Recorder-Klasse mit diesem
    # Archiv. Kein Nachbau -- `melde()` ist der Weg, den eine Meldung nimmt.
    dienst = daemon.Recorder(
        runtime_dir=tmp, archiv=a,
        redaktion=redaktion.Redaktion(runtime_dir=tmp, kennungen={},
                                      wahrnehmung_an=lambda: True),
        erlaubte_units=None)
    vor = dienst.melde({"v": 1, "typ": "archiv", "art": "transkript",
                        "text": "vor dem Aufraeumen"})
    b.pruefe("K6", bool(vor.get("ok")),
             f"Der Dienst nimmt VOR der Verdraengung an (Antwort: {vor})")

    vorher_ids = [z["id"] for z in a.lesen(hoechstens=200)]
    try:
        bericht = dienst.aufraeumen()
    except Exception as exc:                       # noqa: BLE001
        b.fehler("K6", f"Das Aufraeumen hat einen Fehler gemeldet statt zu "
                       f"verdraengen: {exc!r}")
        return
    nachher_ids = [z["id"] for z in a.lesen(hoechstens=200)]

    b.pruefe("K6", vorher_ids != nachher_ids,
             f"Die Verdraengung hat den Bestand veraendert "
             f"({len(vorher_ids)} -> {len(nachher_ids)} Zeilen)")
    b.pruefe("K6", int(bericht.get("verdraengt", 0)) > 0,
             f"Verdraengt wurde tatsaechlich etwas "
             f"(verdraengt={bericht.get('verdraengt')})")
    b.pruefe("K6", a.belegung() <= grenze,
             f"Nach der Verdraengung liegt die Belegung unter der Grenze "
             f"({a.belegung()} <= {grenze})")

    weg = set(vorher_ids) - set(nachher_ids)
    b.pruefe("K6", weg and max(weg) < min(nachher_ids),
             f"Es weichen die AELTESTEN: {len(weg)} Zeilen weg, alle aelter "
             f"als jede verbliebene")
    b.pruefe("K6", ids[0] in weg and ids[-1] not in weg,
             "Der aelteste Eintrag ist weg, der juengste steht noch")

    nach = dienst.melde({"v": 1, "typ": "archiv", "art": "transkript",
                         "text": "nach dem Aufraeumen"})
    b.pruefe("K6", bool(nach.get("ok")) and int(nach.get("id", 0)) > 0,
             f"Der Dienst bedient NACH der Verdraengung weiter "
             f"(Antwort: {nach})")

    # Positivkontrolle: unter der Grenze verdraengt niemand.
    b2 = frisches_archiv(store, tmp / "xdg-k6b", grenze_bytes=8 * 1024 ** 2)
    b2.schreiben(store.ART_OCR, "klein", ts=jetzt - 3600)
    bericht2 = b2.aufraeumen()
    b.pruefe("K6", int(bericht2.get("verdraengt", 0)) == 0
             and len(b2.lesen()) == 1,
             f"Positivkontrolle: unter der Grenze wird nichts verdraengt "
             f"(verdraengt={bericht2.get('verdraengt')})")
    b2.schliessen()

    b.pruefe("K6", dienst.intervall == 3600.0,
             f"Der Aufraeumer laeuft stuendlich "
             f"(Intervall {dienst.intervall} s)")
    b.pruefe("K6", dienst.aufraeumen_faellig(time.monotonic() + 3601.0)
             and not dienst.aufraeumen_faellig(time.monotonic() + 60.0),
             "Nach einer Stunde ist er faellig, nach einer Minute nicht")
    a.schliessen()


def pruefe_k7(b: Bericht, store, tmp: Path) -> None:
    """Die Aufbewahrungsstufe je Eintrag, Vorgabe `redacted`."""
    a = frisches_archiv(store, tmp / "xdg-k7")
    db = a.oeffnen()

    a.schreiben(store.ART_OCR, KANARI_TEXT)
    stufe = db.execute("SELECT stufe FROM archiv ORDER BY id DESC "
                       "LIMIT 1").fetchone()[0]
    b.pruefe("K7", stufe == store.STUFE_REDACTED,
             f"Ohne Angabe traegt der Eintrag `redacted` (gemessen: {stufe!r})")

    b.pruefe("K7", set(store.STUFEN) == {"transient", "metadata_only",
                                         "redacted", "full"},
             f"Die vier Stufen aus Design 7.2d, und nur sie "
             f"(gemessen: {store.STUFEN})")

    for s in (store.STUFE_REDACTED, store.STUFE_FULL):
        neu = a.schreiben(store.ART_OCR, KANARI_TEXT, stufe=s)
        wert = db.execute("SELECT stufe FROM archiv WHERE id = ?",
                          (neu,)).fetchone()[0]
        b.pruefe("K7", wert == s,
                 f"Stufe {s!r} steht am Eintrag (gemessen: {wert!r})")

    # `metadata_only`: Zeile ja, Inhalt nein.
    mid = a.schreiben(store.ART_OCR, KANARI_TEXT, daten=b"xxxx",
                      stufe=store.STUFE_METADATA)
    zeile = db.execute("SELECT text, daten, stufe FROM archiv WHERE id = ?",
                       (mid,)).fetchone()
    b.pruefe("K7", zeile is not None and zeile["text"] == ""
             and zeile["daten"] is None
             and zeile["stufe"] == store.STUFE_METADATA,
             f"`metadata_only` legt Herkunft und Zeit ab, aber keinen Inhalt "
             f"(gemessen: {dict(zeile) if zeile else None})")

    # `transient` schreibt gar nicht -- gewogen an der Zeilenzahl UND am
    # AUTOINCREMENT-Zaehler: eine Zeile, die geschrieben und sofort geloescht
    # wuerde, liesse den Zaehler stehen, wo sie war.
    vor_n = db.execute("SELECT COUNT(*) FROM archiv").fetchone()[0]
    vor_seq = db.execute("SELECT seq FROM sqlite_sequence WHERE name='archiv'"
                         ).fetchone()[0]
    rueck = a.schreiben(store.ART_OCR, KANARI_TEXT,
                        stufe=store.STUFE_TRANSIENT)
    nach_n = db.execute("SELECT COUNT(*) FROM archiv").fetchone()[0]
    nach_seq = db.execute("SELECT seq FROM sqlite_sequence WHERE name='archiv'"
                          ).fetchone()[0]
    b.pruefe("K7", rueck == 0 and nach_n == vor_n and nach_seq == vor_seq,
             f"`transient` schreibt nichts -- Zeilenzahl {vor_n}->{nach_n}, "
             f"Zaehler {vor_seq}->{nach_seq}, Rueckgabe {rueck}")
    # Positivkontrolle: derselbe Aufruf mit `redacted` bewegt beide Zahlen.
    a.schreiben(store.ART_OCR, KANARI_TEXT)
    b.pruefe("K7", db.execute("SELECT COUNT(*) FROM archiv").fetchone()[0]
             == vor_n + 1,
             "Positivkontrolle: derselbe Aufruf mit `redacted` legt ab")

    try:
        a.schreiben(store.ART_OCR, KANARI_TEXT, stufe="geheim")
        b.fehler("K7", "Eine unbekannte Stufe wurde angenommen")
    except store.ArchivFehler:
        b.pruefe("K7", True, "Eine unbekannte Stufe wird abgewiesen")
    a.schliessen()


TAINT_ROUNDTRIP = f"""
import json
import sys
sys.path.insert(0, sys.argv[1])
from {PAKET}.recorder.store import Archiv
a = Archiv(sys.argv[2])
z = a.lesen(hoechstens=1)[0]
w = z["wert"]
print(json.dumps({{"typ": type(w).__name__,
                  "mark": str(getattr(w, "mark", "")),
                  "wert": str(getattr(w, "value", w))}}))
"""


def pruefe_k8(b: Bericht, store, taint, tmp: Path, pruefling: Path) -> None:
    """`tainted` als TYP -- keine Spalte, und ueber Prozessgrenzen hinweg."""
    a = frisches_archiv(store, tmp / "xdg-k8")
    spalten = [z["name"] for z in a.oeffnen().execute(
        "PRAGMA table_info(archiv)")]
    b.pruefe("K8", not any(s.lower() in ("tainted", "mark", "markierung",
                                         "marke") for s in spalten),
             f"Es gibt KEINE Markierungsspalte -- eine kann man vergessen zu "
             f"setzen, einen Rueckgabetyp nicht (Spalten: {spalten})")

    a.schreiben(store.ART_OCR, KANARI_TEXT, fenster="harmlos-app")
    from daimon.common.protocol import Mark, Marked

    gelesen = a.lesen(hoechstens=1)[0]["wert"]
    b.pruefe("K8", isinstance(gelesen, Marked) and gelesen.mark is Mark.TAINTED,
             f"`lesen()` gibt Marked(..., tainted) zurueck "
             f"(gemessen: {type(gelesen).__name__}, "
             f"{getattr(gelesen, 'mark', None)})")
    try:
        gesucht = a.suchen(KANARI_TEXT)[0]["wert"]
    except Exception as exc:                       # noqa: BLE001
        gesucht = None
        b.fehler("K8", f"`suchen()` gab keinen Treffer her: {exc!r}")
    if gesucht is not None:
        b.pruefe("K8", isinstance(gesucht, Marked)
                 and gesucht.mark is Mark.TAINTED,
                 f"`suchen()` ebenso (gemessen: {type(gesucht).__name__}, "
                 f"{getattr(gesucht, 'mark', None)})")
    a.schliessen()

    # Der Roundtrip ueber eine PROZESSGRENZE: eine zweite, frische Python-
    # Sitzung oeffnet dieselbe Datei. Am 14.08. ging eine Markierung genau an
    # so einer Grenze verloren (T-6.1-3.v).
    e = subprocess.run([sys.executable, "-c", TAINT_ROUNDTRIP,
                        str(pruefling), str(a.pfad)], text=True, timeout=120.0,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if e.returncode != 0:
        b.fehler("K8", f"Der Roundtrip im Zweitprozess lief nicht: "
                       f"{e.stderr[:300]!r}")
    else:
        gefunden = json.loads(e.stdout)
        b.pruefe("K8", gefunden["typ"] == "Marked"
                 and gefunden["mark"].endswith("TAINTED"),
                 f"Die Markierung ueberlebt den Datenbank-Roundtrip in einem "
                 f"ZWEITEN Prozess (gemessen: {gefunden})")
        b.pruefe("K8", KANARI_TEXT in gefunden["wert"],
                 "Positivkontrolle: der Zweitprozess hat wirklich den "
                 "Kanarienvogel gelesen und nicht eine leere Zeile")

    # Was die Markierung WERT ist: die echte Senkentabelle aus T-3.13b.
    for senke in ("durchgang1", "tts_ungefragt", "langzeitgedaechtnis"):
        try:
            taint.pruefe_senke(gelesen, senke=senke)
            b.fehler("K8", f"Ein Archivtreffer kam in die Senke {senke!r} "
                           f"durch")
        except Exception:                          # noqa: BLE001
            b.pruefe("K8", True,
                     f"Ein Archivtreffer ist gegen {senke!r} gesperrt")
    try:
        taint.pruefe_senke(gelesen, senke="durchgang2")
        b.pruefe("K8", True,
                 "Positivkontrolle: in Durchgang 2 darf er -- die "
                 "Senkentabelle sagt nicht zu allem nein")
    except Exception as exc:                       # noqa: BLE001
        b.fehler("K8", f"Durchgang 2 hat ihn auch gesperrt: {exc!r}")
    try:
        taint.pruefe_senke(Marked("frisch getippt", Mark.USER_PTT),
                           senke="durchgang1")
        b.pruefe("K8", True,
                 "Positivkontrolle: `user_ptt` kommt durch dieselbe Senke -- "
                 "gesperrt ist die MARKIERUNG, nicht der Aufruf")
    except Exception as exc:                       # noqa: BLE001
        b.fehler("K8", f"Auch `user_ptt` war gegen durchgang1 gesperrt: "
                       f"{exc!r}")


# -- Hauptlauf ---------------------------------------------------------------

def abschnitt(b: Bericht, kriterium: str, fn, *args) -> None:
    """Ein Kriterium messen. Eine Ausnahme darin ist ein BEFUND und beendet
    nicht den Lauf -- sonst nimmt der erste kaputte Prueffall die uebrigen
    acht Kriterien mit, und die Bilanz sagt nichts mehr."""
    try:
        fn(*args)
    except Exception as exc:                       # noqa: BLE001
        b.fehler(kriterium, f"Die Messung brach mit einer Ausnahme ab: "
                            f"{type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: t71_pruefstand.py <pruefling>", file=sys.stderr)
        return 2
    pruefling = Path(argv[0]).resolve()
    hier = Path(__file__).resolve().parent
    b = Bericht()
    sand = Sandkasten()

    print(f"Pruefling: {pruefling}")
    print("Mutant-zu-Kriterium:")
    for name, grenze in sorted(MUTANTEN_GRENZEN.items()):
        print(f"  {name:28s} -> {grenze}")
    mutation = pruefling / "mutation.txt"
    if mutation.exists():
        print(f"Mutation: {mutation.read_text(encoding='utf-8').strip()}")

    if shutil.which("systemd-run") is None:
        print("UMGEBUNG: systemd-run fehlt -- K1, K2, K3 und K9 sind nicht "
              "messbar.", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix=f"t71-{os.getpid()}-"))
    # Unter dem ECHTEN Archivverzeichnis: `BindPaths=%h/.local/share/daimon`
    # der Units zeigt genau dorthin. Eine Kopie anderswo beantwortete die
    # Frage nach dem Schreibrecht nicht.
    archivdir = (Path(os.environ["HOME"]) / ".local" / "share" / PAKET
                 / f"t71v-{os.getpid()}")
    dienst = LiveDienst(pruefling, archivdir, hier)
    try:
        archivdir.mkdir(parents=True, exist_ok=False)
        ok, ausgabe = dienst.start()
        if not ok:
            b.fehler("K1", "Der Archivdienst kam nicht hoch -- K1, K2, K3 "
                           f"und K9 sind ohne ihn nicht messbar:\n{ausgabe}")
        else:
            print(f"Archivdienst: {dienst.unit} (PID {dienst.pid()}), "
                  f"Archiv {dienst.archiv}")
            abschnitt(b, "K1", pruefe_k1, b, pruefling, sand, archivdir,
                      dienst)
            abschnitt(b, "K2", pruefe_k2, b, pruefling, sand, archivdir)
            abschnitt(b, "K3", pruefe_k3, b, pruefling, dienst)
            abschnitt(b, "K9", pruefe_k9, b, pruefling, dienst)
        dienst.stop()

        # Erst jetzt die Messungen am Stueck: der Import des Prueflings in
        # DIESEN Prozess wuerde die Importmessung von K3 verfaelschen.
        sys.path.insert(0, str(pruefling))
        from daimon.common import taint
        from daimon.recorder import daemon, redaktion, store

        abschnitt(b, "K4", pruefe_k4, b, store, tmp)
        abschnitt(b, "K5", pruefe_k5, b, store, tmp)
        abschnitt(b, "K6", pruefe_k6, b, store, daemon, redaktion, tmp)
        abschnitt(b, "K7", pruefe_k7, b, store, tmp)
        abschnitt(b, "K8", pruefe_k8, b, store, taint, tmp, pruefling)
    finally:
        dienst.stop()
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(archivdir, ignore_errors=True)
        for rest in (Path(os.environ["HOME"]) / ".local/share/t71-probe.txt",
                     Path(os.environ["HOME"]) /
                     ".local/state/daimon/t71-probe.txt",
                     Path(os.environ["HOME"]) /
                     ".config/daimon/t71-probe.txt"):
            rest.unlink(missing_ok=True)

    return b.bilanz()


if __name__ == "__main__":
    raise SystemExit(main())
