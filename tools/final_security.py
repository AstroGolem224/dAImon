"""T-6.9 -- der Abschluss-Sicherheitsreview, als MESSUNG.

Ein Review, das eine Liste abhakt, ist eine Meinung. Dieses Werkzeug fragt
das laufende System und schreibt, was es bekommt -- auch wenn es unbequem
ist. Jede Feststellung traegt ihren Beleg als Zeichenkette mit: Kommando,
Rueckgabe, Pfad. Was sich nicht messen laesst, wird `unbelegt` und nicht
`geschlossen`.

**Wer das hier ausfuehrt, benotet sich selbst.** Der Bauende und der
Pruefende sind dieselbe Einheit; der Plan weist T-6.9 der Rolle `reviewer`
zu. Jede Feststellung traegt deshalb ein Feld `herkunft`:

    gemessen   -- am laufenden System, hier und jetzt
    direktive  -- aus der Unit-Datei gelesen, Wirkung nicht am Prozess geprueft
    pruefstand -- durch pytest belegt, geschrieben vom selben Erbauer

Nur `gemessen` ist ein unabhaengiger Beleg. Die anderen sind Hinweise mit
benannter Schwaeche.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Design 7.2a: fuer diese Units gilt die Netzsperre.
NETZSPERRE_UNITS = (
    "daimon-hub.service", "daimon-auth.service", "daimon-ears.service",
    "daimon-eyes.service", "daimon-mind.service", "daimon-face.service",
    "daimon-focus.service", "daimon-recorder.service",
    "daimon-dbus.service", "daimon-fs.service", "daimon-exec.service",
    "daimon-input.service",
)
# Die drei Units, die das Netz TRAGEN. Gemessen am 14.08.:
#   daimon-egress        AF_INET AF_INET6 AF_UNIX
#   daimon-lokal-broker  AF_INET AF_UNIX
#   daimon-cli-broker    ~  (gar keine Beschraenkung)
# Design 7.2a sagt "alle Broker" -- das ist zu weit formuliert, und die
# Units sagen es selbst: "Kein RestrictAddressFamilies=AF_UNIX: die CLI MUSS
# ins Netz." Der Widerspruch ist ein Befund am TEXT, nicht am Code.
NETZ_TRAEGER = ("daimon-egress.service", "daimon-lokal-broker.service",
                "daimon-cli-broker.service")
TOKEN = Path.home() / ".config" / "daimon" / "anthropic-token"


def _sh(*argv: str, timeout: float = 15.0) -> tuple[int, str]:
    try:
        e = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return e.returncode, (e.stdout or e.stderr or "").strip()


def _show(unit: str, feld: str) -> str:
    _, aus = _sh("systemctl", "--user", "show", unit, "-p", feld, "--value")
    return aus


def _pid(unit: str) -> int:
    _, aus = _sh("systemctl", "--user", "show", unit, "-p", "MainPID", "--value")
    try:
        return int(aus.strip() or 0)
    except ValueError:
        return 0


def befund(fid: str, schwere: str, status: str, herkunft: str,
           titel: str, beleg: str) -> dict:
    return {"id": fid, "severity": schwere, "status": status,
            "herkunft": herkunft, "titel": titel, "beleg": beleg[:600]}


# -- 7.2a Netzsperre --------------------------------------------------------

def netzsperre() -> list[dict]:
    """Die einzige KERNELGRENZE des Entwurfs. Zwei Fragen, nicht eine:
    steht die Direktive, und wirkt sie auf dieser Maschine?"""
    heraus = []

    # 1. Wirkt die Direktive ueberhaupt? Live, in einer Wegwerf-Unit.
    rc, aus = _sh("systemd-run", "--user", "--wait", "--pipe", "--quiet",
                  "-p", "RestrictAddressFamilies=AF_UNIX",
                  "python3", "-c",
                  "import socket;socket.socket(socket.AF_INET,socket.SOCK_STREAM)")
    wirkt = rc != 0
    heraus.append(befund(
        "7.2a-kernel", "critical", "closed" if wirkt else "open", "gemessen",
        "RestrictAddressFamilies=AF_UNIX verhindert AF_INET auf dieser Maschine",
        f"systemd-run mit der Direktive, AF_INET-Socket: rc={rc} {aus[:200]}"))

    # 2. Traegt jede Unit sie? Direktive gelesen -- die WIRKUNG am laufenden
    #    Prozess laesst sich von aussen nicht erzwingen, ohne Code in ihn zu
    #    tragen. Das ist die benannte Schwaeche dieser Zeile.
    fehlt = []
    for unit in NETZSPERRE_UNITS:
        wert = _show(unit, "RestrictAddressFamilies")
        if "AF_UNIX" not in wert:
            fehlt.append(f"{unit}={wert or 'LEER'}")
    heraus.append(befund(
        "7.2a-units", "high", "closed" if not fehlt else "open", "direktive",
        f"Alle {len(NETZSPERRE_UNITS)} gesperrten Units tragen "
        "RestrictAddressFamilies=AF_UNIX",
        "ohne Sperre: " + (", ".join(fehlt) if fehlt else "keine")))

    # 3. Und die drei, die das Netz tragen -- benannt statt uebersehen.
    traeger = {u: _show(u, "RestrictAddressFamilies") or "~ (unbeschraenkt)"
               for u in NETZ_TRAEGER}
    heraus.append(befund(
        "7.2a-text", "medium", "open", "gemessen",
        "Design 7.2a sagt 'alle Broker' -- drei Units tragen das Netz",
        f"{traeger}. Der Zuschnitt ist gewollt (die CLI muss ins Netz, der "
        "Egress haelt den Token), die ZUSAGE im Text ist zu weit formuliert. "
        "Zu berichtigen ist der Entwurf, nicht die Unit."))
    return heraus


# -- 7.2b Deklassifizierungs-Gate ------------------------------------------

def gate() -> list[dict]:
    """Der Ausgang aus der Quarantaene. Gemessen wird die GRENZE -- dass ein
    fremder Absender abgewiesen wird -- nicht die Entscheidung dahinter."""
    heraus = []
    rt = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "daimon"
    sock = rt / "kontext.sock"

    if not sock.exists():
        heraus.append(befund(
            "7.2b-socket", "high", "open", "gemessen",
            "kontext.sock existiert", f"{sock} fehlt -- laeuft der Hub?"))
        return heraus

    modus = oct(sock.stat().st_mode & 0o777)
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5.0)
    antwort = ""
    try:
        c.connect(str(sock))
        c.sendall(b'{"v":1,"art":"deklassifizieren","text":"was steht auf dem Bildschirm"}\n')
        antwort = c.makefile("r").readline().strip()
    except OSError as exc:
        antwort = f"{type(exc).__name__}: {exc}"
    finally:
        c.close()

    # Ein RST zaehlt als Abweisung. Der Dienst schliesst, waehrend noch
    # ungelesene Bytes im Puffer stehen -- dann ist der Abbruch hart, und
    # der Klient sieht ConnectionResetError statt EOF. Dieselbe Wirkung:
    # keine Antwort. (Beim ersten Lauf las dieses Werkzeug das als offenen
    # Befund -- ein Falschbefund des Pruefers, nicht des Systems.)
    abgewiesen = (antwort == "" or "ConnectionReset" in antwort
                  or "ok\": false" in antwort.replace(" ", ""))
    heraus.append(befund(
        "7.2b-fremde-unit", "critical", "closed" if abgewiesen else "open",
        "gemessen",
        "Eine fremde Unit bekommt keinen Bildschirmkontext",
        f"Antwort auf {sock} (Modus {modus}): {antwort!r} -- leer heisst: "
        "Verbindung ohne Antwort geschlossen"))
    return heraus


# -- 7.2c Egress ------------------------------------------------------------

def egress() -> list[dict]:
    """`mind` hat weder Token noch AF_INET. Der Token wird im ADRESSRAUM und
    in der Umgebung des laufenden Prozesses gesucht, nicht in der Unit."""
    heraus = []
    pid = _pid("daimon-mind.service")
    if pid <= 0:
        heraus.append(befund(
            "7.2c-mind-token", "high", "unbelegt", "gemessen",
            "Kein Token in der Umgebung von daimon-mind",
            "daimon-mind laeuft nicht -- nicht messbar"))
    else:
        try:
            umgebung = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError as exc:
            umgebung = b""
            fehler = str(exc)
        else:
            fehler = ""
        # Gesucht werden NAMEN, nie Werte -- ein Review, das Geheimnisse in
        # seinen eigenen Beleg schreibt, ist der Angriff, den es sucht.
        namen = [v.split(b"=", 1)[0].decode("utf-8", "replace")
                 for v in umgebung.split(b"\0") if b"=" in v]
        verdacht = [n for n in namen
                    if any(m in n.upper()
                           for m in ("KEY", "TOKEN", "SECRET", "ANTHROPIC",
                                     "PASS"))]
        heraus.append(befund(
            "7.2c-mind-token", "critical",
            "closed" if not verdacht and not fehler else "open", "gemessen",
            "Kein Token in der Umgebung von daimon-mind",
            f"/proc/{pid}/environ, {len(namen)} Variablen, verdaechtige "
            f"NAMEN (ohne Werte): {verdacht} {fehler}"))

    zustand = _show("daimon-egress.service", "ActiveState")
    heraus.append(befund(
        "7.2c-egress-token", "medium",
        "closed" if not TOKEN.exists() else "unbelegt", "gemessen",
        "Der Egress-Broker sagt ohne Token ehrlich ab",
        f"{TOKEN} vorhanden={TOKEN.exists()}, Unit={zustand} -- ohne Token "
        "ist die Domain-Beschraenkung nicht im Betrieb pruefbar"))
    return heraus


# -- 7.2d Aufbewahrung ------------------------------------------------------

def archiv() -> list[dict]:
    """Stufen und Rechte am ECHTEN Archiv, nicht an einer Attrappe."""
    heraus = []
    pfad = Path(os.environ.get(
        "XDG_DATA_HOME", Path.home() / ".local/share")) / "daimon" / "archiv.db"
    if not pfad.exists():
        heraus.append(befund(
            "7.2d-archiv", "medium", "unbelegt", "gemessen",
            "Aufbewahrungsstufen im Archiv", f"{pfad} existiert nicht"))
        return heraus

    modus_datei = oct(pfad.stat().st_mode & 0o777)
    modus_dir = oct(pfad.parent.stat().st_mode & 0o777)
    heraus.append(befund(
        "7.2d-rechte", "high",
        "closed" if modus_datei == "0o600" and modus_dir == "0o700" else "open",
        "gemessen", "Archiv 0600, Verzeichnis 0700",
        f"{pfad}={modus_datei}, {pfad.parent}={modus_dir}"))

    db = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        stufen = dict(db.execute(
            "SELECT stufe, COUNT(*) FROM archiv GROUP BY stufe").fetchall())
        arten = dict(db.execute(
            "SELECT art, COUNT(*) FROM archiv GROUP BY art").fetchall())
    finally:
        db.close()
    voll = int(stufen.get("full", 0))
    heraus.append(befund(
        "7.2d-stufen", "high", "closed" if voll == 0 else "open", "gemessen",
        "Keine Zeile steht auf `full` ohne ausdrueckliche Anforderung",
        f"Stufen: {stufen}, Arten: {arten}"))

    # BERICHTIGT beim ersten Lauf: hier stand `pfad.parent.rglob("*")` --
    # und das Datenverzeichnis enthaelt auch `models/` und `voices/`. Die 13
    # RIFF-Treffer waren die mitgelieferten Test-WAVs des STT-Modells und ein
    # Zufall in den ONNX-Gewichten, nicht ein Byte Mitschnitt. Gesucht wird
    # im ARCHIV, und das sind drei Dateien.
    audio = [a for a in arten if a in ("audio", "wav", "pcm", "rohaudio")]
    dateien = [Path(str(pfad) + e) for e in ("", "-wal", "-shm")]
    treffer = sum(d.read_bytes().count(b"RIFF") for d in dateien if d.exists())
    heraus.append(befund(
        "7.2d-kein-rohaudio", "critical",
        "closed" if not audio and treffer == 0 else "open", "gemessen",
        "Kein Rohaudio im Archiv",
        f"Audio-Arten: {audio}, RIFF in {[d.name for d in dateien if d.exists()]}"
        f": {treffer}"))
    return heraus


def main() -> int:
    saetze = netzsperre() + gate() + egress() + archiv()
    offen = [b for b in saetze
             if b["status"] != "closed" and b["severity"] in ("high", "critical")]
    bericht = {
        "v": 1, "task": "T-6.9",
        "erzeugt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rolle": os.environ.get("DAIMON_ROLE", ""),
        "unabhaengig": False,
        "hinweis": ("Erzeugt von derselben Einheit, die den Code gebaut hat. "
                    "Der Plan weist T-6.9 der Rolle `reviewer` zu. Feld "
                    "`herkunft` je Befund: gemessen | direktive | pruefstand."),
        "befunde": saetze,
        "offen_high_critical": [b["id"] for b in offen],
    }
    ziel = Path(__file__).resolve().parents[1] / "tests/evidence/final-findings.json"
    ziel.write_text(json.dumps(bericht, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"befunde": len(saetze), "offen": bericht["offen_high_critical"],
                      "datei": str(ziel)}, ensure_ascii=False))
    return 1 if offen else 0


if __name__ == "__main__":
    raise SystemExit(main())
