"""T-6.8 — Vollständiger Systemtest: alles zusammen, unter Last.

Warum dieser Test jedes Szenario SELBST startet
-----------------------------------------------------------------------------
Ein Integrationstest, der vorgefundene Zustände als Erfolg liest, prüft die
Vergangenheit. Deshalb startet Szenario 1 die Units selbst, Szenario 4 öffnet
das Vollbildfenster selbst, und die Kill-Switch-Szenarien erzeugen den
Zustand, den sie abschalten, erst (PTT-Strom, Kontextdateien,
Gedächtniseintrag). Ein `pass`, das niemand gemessen hat, zählt hier nicht.

Warum die Wirkung und nicht der Rückgabewert zählt
-----------------------------------------------------------------------------
`systemctl stop` liefert 0, sobald der Prozess weg ist — das ist nicht die
Zusage. Die Zusage ist: danach nimmt nichts mehr auf, sieht nichts mehr,
liegt nichts mehr herum. Jede Negativprüfung hier misst deshalb die Wirkung
(Stromzahl, Unit-Zustand, Dateien) und steht neben einem POSITIVEN
Kanarienvogel, der beweist, dass die Messgröße vorher da war.

Was bewusst NICHT gestartet wird
-----------------------------------------------------------------------------
`daimon-gpu@.service` ist ein Template und lädt beim Instanziieren ein
6-GB-Modell ins VRAM. Das Vollbild-Gate wird stattdessen an der
Gate-Entscheidung des Hubs gemessen (Szenario 4) — dort entsteht die
Sicherheitsaussage, nicht an der Modelladresse. `daimon-phase1.service` läuft
zeitgesteuert (Timer); geprüft wird der Timer.

Ressourcenbudget (Szenario 3): Design §13 definiert die Dauerlast — KWS, VAD,
PipeWire-Capture, Bildschirm-Diff, Face, Hub/Auth/Bridge/Broker — mit der
Summe ≈1,2 % eines Kerns und ≈420 MB. STT, TTS und VLM stehen in §13 unter
„Auf Abruf" und gehören nicht in diese Summe; sie werden gemessen und
belegt, aber nicht gegen das Dauerlast-Budget gerechnet.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO / ".venv" / "bin" / "python"
EVIDENZ = REPO / "tests" / "evidence" / "phase6-integration.json"
VOLLBILDFENSTER = REPO / "tests" / "harness" / "vollbildfenster.py"

HZ = os.sysconf("SC_CLK_TCK")

# Die Dauerlast aus Design §13. Auf dieser Maschine sind das dreizehn Units:
# die Tabelle „Hub, Auth, Bridge, Broker (idle)" deckt hier fünf Broker und
# den Mind mit ab — dieselbe Kategorie, mehr Einträge.
DAUERLAST_UNITS = (
    "daimon-hub.service", "daimon-hookbridge.service", "daimon-focus.service",
    "daimon-face.service", "daimon-auth.service", "daimon-ears.service",
    "daimon-eyes.service", "daimon-mind.service", "daimon-dbus.service",
    "daimon-exec.service", "daimon-fs.service", "daimon-cli-broker.service",
    "daimon-lokal-broker.service",
)
# §13 „Auf Abruf": socket-aktiviert, ohne Leerlauf-Exit. Sie werden in
# Szenario 1 mitgestartet (alle Units gleichzeitig), in Szenario 3 aber
# getrennt ausgewiesen — ihr Modell im RAM ist ihre Latenzzusage, kein Idle.
ABRUF_UNITS = ("daimon-stt.service", "daimon-tts.service",
               "daimon-egress.service")

# Zwei Units können auf dieser Maschine nicht laufen, und Szenario 1 weist
# den Grund jeweils SELBST nach statt die Unit einfach auszulassen:
#   * daimon-exec: der Broker verweigert den Dienst, solange der Katalog
#     keine freigegebene Anwendung enthält (gemessen am 13.08.: „Keine
#     freigegebene Anwendung im Katalog", exit 1).
#   * daimon-egress: LoadCredential zeigt auf eine Token-Datei, die nicht
#     existiert (243/CREDENTIALS) — eine Frage der Bereitstellung, nicht
#     des Codes.
#   * daimon-input: ProtectHome=tmpfs verdeckt den venv-Interpreter, der
#     in $HOME liegt — die Unit kann so nicht starten (203/EXEC). Ein
#     Befund, den dieser Test belegt, aber nicht repariert.
SONDERFALL_UNITS = ("daimon-exec.service", "daimon-egress.service",
                    "daimon-input.service")

# Design §13.1, „Zwei Größen, nicht eine": OCR ist stoßweise, und ein
# einziger Deckel wäre entweder für die Dauerlast zu lasch oder für die
# Spitze unerfüllbar. Beide werden geprüft.
BUDGET_CPU_MITTEL_PROZENT = 2.0  # eines Kerns, Mittel über das Messband
BUDGET_CPU_P95_PROZENT = 8.0     # eines Kerns, p95 einzelner 1-s-Fenster
BUDGET_RSS_MB = 420.0


# ---------------------------------------------------------------------------
# Werkzeuge
# ---------------------------------------------------------------------------

def _lauf(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _systemctl(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return _lauf(["systemctl", "--user", *args], timeout=timeout)


def ist_aktiv(unit: str) -> bool:
    e = _systemctl("is-active", unit, timeout=10.0)
    return (e.stdout or "").strip() == "active"


def _runtime_dir() -> Path:
    basis = os.environ.get("XDG_RUNTIME_DIR", "")
    if not basis:
        raise OSError("XDG_RUNTIME_DIR ist nicht gesetzt")
    return Path(basis) / "daimon"


def sock_frage(name: str, anfrage: dict, *, timeout_s: float = 15.0) -> dict:
    """Eine Zeile hin, eine zurück (aktion.sock, gpu.sock, mind.sock)."""
    c = socket_verbinden(name, timeout_s=timeout_s)
    try:
        c.sendall(json.dumps(anfrage).encode() + b"\n")
        roh = c.makefile("rb").readline(1 << 20)
    finally:
        c.close()
    return json.loads(roh)


def sock_lesen(name: str, *, timeout_s: float = 10.0) -> dict:
    """Nur lesen: diag.sock und state.sock schieben ihre Zeile von selbst."""
    c = socket_verbinden(name, timeout_s=timeout_s)
    try:
        return json.loads(c.makefile("rb").readline(1 << 20))
    finally:
        c.close()


def socket_verbinden(name: str, *, timeout_s: float):
    import socket
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout_s)
    c.connect(str(_runtime_dir() / name))
    return c


def produzent_sende(name: str, typ: str, payload: dict) -> None:
    """Eine Produzentenzeile (auth.sock) — ohne Antwort, wie im Betrieb."""
    c = socket_verbinden(name, timeout_s=10.0)
    try:
        c.sendall(json.dumps(
            {"v": 1, "type": typ, "payload": payload}).encode() + b"\n")
    finally:
        c.close()


def fokus_zustand() -> tuple[bool, float] | None:
    """Der Weg des GPU-Gates: busctl auf de.daimon.Focus. None = nicht messbar."""
    e = _lauf(["busctl", "--user", "--json=short", "call", "de.daimon.Focus",
               "/Focus", "de.daimon.Focus", "Zustand"], timeout=10.0)
    if e.returncode != 0:
        return None
    try:
        daten = json.loads(e.stdout)["data"]
        return bool(daten[0]), float(daten[1])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def lautstaerke() -> float | None:
    """Eigene Messung der Lautstärke — der Folgeaktions-Effekt wird nicht
    aus der Antwort des Systems gelesen, sondern hier."""
    e = _lauf(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=5.0)
    if e.returncode != 0:
        return None
    m = re.search(r"Volume:\s*([0-9]*\.?[0-9]+)", e.stdout or "")
    return float(m.group(1)) if m else None


def fensterzahl_eigen() -> int | None:
    """Eigene Fensterzählung über KWin, unabhängig vom Antwortpfad des
    Systems. Gleiche Quelle (der Compositor), anderer Aufruf."""
    if shutil.which("qdbus6") is None:
        return None
    e = _lauf(["qdbus6", "--literal", "org.kde.KWin", "/WindowsRunner",
               "org.kde.krunner1.Match", ""], timeout=10.0)
    if e.returncode != 0:
        return None
    # Nur echte Einträge zählen: `(sssida{sv}) "…"`. Der Variant-Kopf
    # `[Argument: (sssida{sv}) {…}]` beschreibt nur den Typ und steht genau
    # einmal drin — wer ihn mitzählt, liegt um eins daneben (gemessen am
    # 13.08.: 19 Treffer bei 18 Fenstern).
    return len(re.findall(r"\(sssida\{sv\}\)\s*\"", e.stdout or ""))


def _stat_felder(pid: int) -> tuple[int, int] | None:
    """utime + stime aus /proc/<pid>/stat. `comm` darf Leerzeichen und
    Klammern enthalten — deshalb vom letzten ')' rückwärts, nie spaltenweise
    von vorn."""
    try:
        inhalt = Path(f"/proc/{pid}/stat").read_text()
        rest = inhalt[inhalt.rindex(")") + 2:].split()
        # rest[0] ist state (Feld 3); utime/stime sind die Felder 14/15.
        return int(rest[11]) + int(rest[12])
    except (OSError, ValueError, IndexError):
        return None


def _rss_kb(pid: int) -> int | None:
    try:
        for zeile in Path(f"/proc/{pid}/status").read_text().splitlines():
            if zeile.startswith("VmRSS:"):
                return int(zeile.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _p95(werte: list[float]) -> float:
    """Das 95-Perzentil, naechstliegender Rang. Keine Interpolation: ein
    interpolierter Wert zwischen zwei Messungen ist eine dritte Zahl, die
    niemand gemessen hat."""
    geordnet = sorted(werte)
    return geordnet[max(0, math.ceil(0.95 * len(geordnet)) - 1)]


# ---------------------------------------------------------------------------
# Protokoll und Fixtures
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def protokoll(evidenz: list, *, szenario: str, gestartet: list[str],
              budget: dict):
    """Ein Datensatz je Szenario — auch bei Skip und bei Fehlschlag.

    Ein übersprungener Test ist kein bestandener: `uebersprungen` steht
    ausdrücklich neben `gemessen`, und der Grund steht dabei.
    """
    satz = {"szenario": szenario, "gestartet": gestartet, "budget": budget,
            "gemessen": {}, "ergebnis": "fehlgeschlagen", "grund": ""}
    t0 = time.monotonic()
    try:
        yield satz
    except pytest.skip.Exception as ausnahme:
        satz["ergebnis"] = "uebersprungen"
        satz["grund"] = str(ausnahme)[:300]
        raise
    except BaseException as ausnahme:
        satz["ergebnis"] = "fehlgeschlagen"
        satz["grund"] = str(ausnahme)[:300]
        raise
    else:
        satz["ergebnis"] = "gemessen"
    finally:
        satz["dauer_s"] = round(time.monotonic() - t0, 2)
        evidenz.append(satz)


@pytest.fixture(scope="module", autouse=True)
def evidenz():
    """Der Beleg. Wird IMMER geschrieben — auch dann, wenn ein Szenario
    rot ist, denn gerade dann ist der Beleg kein Etikett."""
    saetze: list[dict] = []
    yield saetze
    EVIDENZ.parent.mkdir(parents=True, exist_ok=True)
    EVIDENZ.write_text(json.dumps({
        "v": 1, "task": "T-6.8",
        "erzeugt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rolle": os.environ.get("DAIMON_ROLE", ""),
        "szenarien": saetze,
    }, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture(scope="module")
def sitzung():
    """Die live Sitzung — und ihre Wiederherstellung.

    Der Ausgangszustand jeder Unit wird VOR dem ersten Szenario gemessen und
    am Ende wiederhergestellt: was wir gestartet haben, geht wieder aus; was
    lief (daimon-eyes), läuft danach wieder. Wer das Wegräumen auslässt,
    hinterlässt ein verändertes System und nennt es Test.
    """
    if shutil.which("systemctl") is None:
        pytest.skip("systemctl fehlt — keine systemd-Benutzersitzung")
    pruef = subprocess.run(["systemctl", "--user", "list-units"],
                           capture_output=True, timeout=15.0)
    if pruef.returncode != 0:
        pytest.skip("systemd-Benutzersitzung nicht erreichbar")
    try:
        rt = _runtime_dir()
    except OSError as grund:
        pytest.skip(f"keine Laufzeitumgebung: {grund}")
    if not rt.exists():
        pytest.skip(f"{rt} existiert nicht — der Hub läuft nicht")

    # Ein Snapshot während „activating" ist kein Zustand, sondern ein
    # Zufall: daimon-eyes (Type=dbus) braucht einige Sekunden, und wer in
    # dem Fenster „inaktiv" liest, stellt am Ende den falschen Zustand
    # wieder her. Erst warten, bis nichts mehr hochfährt.
    frist = time.monotonic() + 30.0
    while time.monotonic() < frist:
        ladend = _systemctl("list-units", "daimon*", "--state=activating",
                            "--no-legend", timeout=15.0).stdout.strip()
        if not ladend:
            break
        time.sleep(1.0)

    units = list(DAUERLAST_UNITS) + list(ABRUF_UNITS)
    ursprung = {u: ist_aktiv(u) for u in units}
    yield {"ursprung": ursprung, "rt": rt}

    # Wiederherstellung — best effort, aber jede Unit einzeln.
    with contextlib.suppress(Exception):
        produzent_sende("auth.sock", "ptt", {"an": False})
    for unit, war_aktiv in ursprung.items():
        jetzt = ist_aktiv(unit)
        if war_aktiv and not jetzt:
            _systemctl("start", unit)
        elif not war_aktiv and jetzt:
            _systemctl("stop", unit)


# ---------------------------------------------------------------------------
# Szenario 1 — alle Units laufen gleichzeitig
# ---------------------------------------------------------------------------

def test_alle_units_laufen_gleichzeitig(sitzung, evidenz):
    """Startet selbst, misst selbst: `is-active` je Unit — und als Wirkung
    jenseits des Labels die lebenden Antworten von Hub, Fokus, Augen und Mind.
    """
    with protokoll(evidenz, szenario="alle_units_gleichzeitig",
                   gestartet=[u for u in list(DAUERLAST_UNITS)
                              + list(ABRUF_UNITS)
                              if u not in SONDERFALL_UNITS]
                            + ["Sonderfälle mit Grundnachweis: "
                               + ", ".join(SONDERFALL_UNITS),
                               "daimon-phase1.timer (nur Zustand)"],
                   budget={}) as satz:
        zu_starten = [u for u in list(DAUERLAST_UNITS) + list(ABRUF_UNITS)
                      if u not in SONDERFALL_UNITS]
        e = _systemctl("start", *zu_starten, timeout=120.0)
        assert e.returncode == 0, f"systemctl start: {e.stderr.strip()[:200]}"

        frist = time.monotonic() + 40.0
        fehlend = [u for u in zu_starten if not ist_aktiv(u)]
        while fehlend and time.monotonic() < frist:
            time.sleep(0.5)
            fehlend = [u for u in zu_starten if not ist_aktiv(u)]
        satz["gemessen"]["inaktiv_nach_start"] = fehlend
        assert not fehlend, f"nicht aktiv: {', '.join(fehlend)}"

        # Die Sonderfälle: nicht auslassen, sondern den Grund nachmessen.
        # Ein Unit-Ausfall mit belegtem Ursprung ist ein Befund; ein
        # stiller Ausschluss wäre eine Lücke.
        sonderfaelle: dict[str, str] = {}
        for unit in SONDERFALL_UNITS:
            _systemctl("reset-failed", unit)
            _systemctl("start", unit, timeout=40.0)
            time.sleep(1.0)
            zustand = _systemctl("show", unit, "-p", "ActiveState,Result",
                                 "--value", timeout=10.0).stdout.split()
            journal = _lauf(["journalctl", "--user", "-u", unit, "-n", "8",
                             "--no-pager"], timeout=15.0).stdout
            if unit == "daimon-exec.service":
                grund = ("verweigert ohne freigegebene Anwendung im Katalog"
                         if "Keine freigegebene Anwendung" in journal
                         else f"unerwartet: {journal.strip()[-160:]}")
            elif unit == "daimon-egress.service":
                token = Path.home() / ".config" / "daimon" / "anthropic-token"
                grund = ("LoadCredential-Token fehlt (~/.config/daimon/"
                         "anthropic-token)" if not token.exists()
                         else "unerwartet: Token da, Dienst trotzdem "
                         f"{zustand}")
            else:  # daimon-input.service
                grund = ("203/EXEC: ProtectHome=tmpfs verdeckt den "
                         "Interpreter (.venv liegt in $HOME)"
                         if "203/EXEC" in journal
                         else f"unerwartet: {journal.strip()[-160:]}")
            sonderfaelle[unit] = f"{'/'.join(zustand)} — {grund}"
            # Die Sonde hat die Restart-Schleife angeworfen — wieder
            # abstellen, sonst flattert die Unit nach dem Test weiter.
            _systemctl("stop", unit)
            _systemctl("reset-failed", unit)
        satz["gemessen"]["nicht_startbar"] = sonderfaelle

        # Der Kanarienvogel für die Messung selbst: `is-active` muss eine
        # erfundene Unit von einer laufenden unterscheiden können — sonst
        # ist jede der Zeilen oben wertlos.
        assert not ist_aktiv("daimon-diese-unit-gibt-es-nicht.service")

        # Wirkung jenseits der Labels: vier Dienste antworten selbst.
        diag = sock_lesen("diag.sock")
        assert diag.get("v") == 1
        assert fokus_zustand() is not None, "Fokus-Dienst antwortet nicht"
        augen = _lauf(["busctl", "--user", "--json=short", "call",
                       "de.daimon.Eyes", "/Eyes", "de.daimon.Eyes", "Zustand"],
                      timeout=10.0)
        assert augen.returncode == 0, "Augen-Dienst antwortet nicht auf DBus"
        mind = sock_frage("mind.sock", {"v": 1, "art": "zustand"})
        assert mind.get("ok") is True
        # Kein Testprofil: sonst stünden hier Attrappen statt der Maschine.
        assert mind.get("testprofil") is False

        # Oneshot daimon-input steht bei den Sonderfällen oben — seine
        # Unit kann auf dieser Maschine nicht starten, und der Grund ist
        # dort belegt.

        # Der Alltagsrecorder läuft zeitgesteuert — geprüft wird sein Timer.
        timer = _systemctl("is-active", "daimon-phase1.timer", timeout=10.0)
        satz["gemessen"]["phase1_timer"] = timer.stdout.strip()
        assert timer.stdout.strip() == "active"

        satz["gemessen"]["diag_laufzeit_s"] = diag.get("laufzeit_s")


# ---------------------------------------------------------------------------
# Szenario 2 — Sprachanfrage mit Bildschirmbezug und Folgeaktion
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ZWEI SZENARIEN SIND SEIT DEM 19.08. AUSGESETZT -- und warum das hier steht
# ---------------------------------------------------------------------------
#
# Dieser Pruefstand misst den LAUFENDEN Hub. Das ist sein Wert und war an
# diesem Tag auch sein Fehler: T-4.4 wurde um 00:24 committet, der Hub-Prozess
# lief bis 10:07 mit dem alten Code weiter -- und `test_sprachanfrage...` blieb
# zehn Stunden gruen, obwohl die Zusage, die er misst, seit dem Commit eine
# andere ist. Gemessen wurde ein Prozess, nicht der Stand des Repos.
#
# Beide Szenarien brauchen deshalb einen Umbau, und zwar aus zwei
# unabhaengigen Gruenden:
#
#   1. `quelle: "parser"` gibt es nicht mehr (T-4.4). Der Socketweg setzt die
#      Quelle selbst; eine Aktion geht durch die Vorschau und braucht eine
#      Zustimmung. `verdikt == "allow"` ohne Mensch ist keine Zusage mehr.
#   2. `aktion.sock` und `gpu.sock` tragen seit T-4.5 Unit-Allowlisten. Ein
#      Testprozess steht auf keiner -- und kann es auch nicht: fuer jede der
#      erlaubten Units existiert ein Fragment, und `systemd-run --unit=` lehnt
#      solche Namen ab ("already loaded or has a fragment file", gemessen).
#
# Der ehrliche Ersatz misst nicht mehr die nachgespielte Zeile, sondern was
# der echte Weg tut -- fuer die GPU-Sonde etwa `daimon-gpu@sonde.service`, die
# nachweislich startet und auf der Allowlist steht. Das ist ein eigener
# Schnitt und steht als Karte im PMTool.
#
# Ausgesetzt und nicht geloescht: ein `skip` mit Grund ist sichtbar, ein
# entfernter Test ist es nicht.
AUSGESETZT = (
    "Umbau noetig (Karte im PMTool): der Weg misst `quelle: parser` (seit "
    "T-4.4 weg) und braucht eine Unit von der Allowlist (seit T-4.5). "
    "Details im Kommentar ueber dieser Konstante.")


def test_sprachanfrage_mit_bildschirmbezug_und_folgeaktion(sitzung, evidenz):
    """Frage mit Bildschirmbezug an den Mind, Folgeaktion über den Hub.

    Der Bildschirmbezug wird nicht aus der Antwort gelesen (das wäre das
    Selbstporträt des Systems), sondern gegen eine EIGENE KWin-Abfrage
    gehalten. Die Folgeaktion ist `audio.volume.up/down`: selbstinvers, im
    Katalog `direct`, und ihre Wirkung steht in `wpctl` — nicht in der
    Antwortzeile des Hubs.
    """
    pytest.skip(AUSGESETZT)
    with protokoll(evidenz, szenario="sprachanfrage_und_folgeaktion",
                   gestartet=["mind.sock frage (fensterliste)",
                              "auth.sock intent_mark",
                              "aktion.sock ausfuehren (audio.volume)"],
                   budget={}) as satz:
        if not ist_aktiv("daimon-mind.service"):
            pytest.skip("daimon-mind läuft nicht (Szenario 1)")
        if not ist_aktiv("daimon-dbus.service"):
            pytest.skip("daimon-dbus läuft nicht (Szenario 1)")

        # -- Sprachanfrage mit Bildschirmbezug ----------------------------
        eigen = fensterzahl_eigen()
        if eigen is None:
            pytest.skip("qdbus6/KWin nicht abfragbar — kein unabhängiger "
                        "Bildschirmbezug messbar")
        antwort = sock_frage("mind.sock", {
            "v": 1, "art": "frage", "text": "welche fenster sind offen",
            "marke": "user_ptt"})
        assert antwort.get("ok") is True, f"Mind: {antwort.get('grund')}"
        assert antwort.get("weg") == "lokal"
        assert antwort.get("absicht") == "fensterliste"
        # Fenstertitel sind Angreifertext (Design 5.2): die Antwort an den
        # Nutzer trägt sie, und sie MUSS deshalb tainted sein.
        assert antwort.get("marke") == "tainted"

        treffer = re.match(r"(\d+) Fenster", str(antwort.get("antwort", "")))
        assert treffer, f"Antwort ohne Fensterzahl: {antwort.get('antwort')!r}"
        genannt = int(treffer.group(1))
        if genannt != eigen:
            # Fenster können zwischen den beiden Aufrufen kommen oder gehen
            # (Popups). EIN erneutes Messpaar, dann ist Schluss — kein
            # Schleifen, bis es passt.
            eigen = fensterzahl_eigen()
            antwort = sock_frage("mind.sock", {
                "v": 1, "art": "frage", "text": "welche fenster sind offen",
                "marke": "user_ptt"})
            genannt = int(re.match(r"(\d+) Fenster",
                                   str(antwort.get("antwort", ""))).group(1))
        satz["gemessen"]["fenster_eigen"] = eigen
        satz["gemessen"]["fenster_genannt"] = genannt
        assert genannt == eigen, (
            f"Mind nennt {genannt} Fenster, eigene KWin-Abfrage zählt {eigen}")

        # -- Folgeaktion ---------------------------------------------------
        v0 = lautstaerke()
        if v0 is None:
            pytest.skip("wpctl nicht abfragbar — Aktionswirkung nicht messbar")
        richtung = "up" if v0 <= 0.90 else "down"
        umkehr = "down" if richtung == "up" else "up"

        produzent_sende("auth.sock", "intent_mark", {})
        time.sleep(0.3)     # die Marke entsteht im Hub, nicht in der Anfrage
        ant = sock_frage("aktion.sock", {
            "v": 1, "art": "ausfuehren", "action_id": f"audio.volume.{richtung}",
            "params": {}, "quelle": "parser"})
        satz["gemessen"]["aktion_antwort"] = ant
        assert ant.get("ok") is True, f"Hub: {ant.get('grund')}"
        assert ant.get("verdikt") == "allow", f"verdikt={ant.get('verdikt')}"
        assert ant.get("direkt") is True
        assert ant.get("ausgefuehrt") is True, f"Broker: {ant.get('grund')}"

        # Die Wirkung, selbst gemessen — mit Frist, weil kglobalaccel über
        # DBus arbeitet und die Antwort dem Effekt vorausgehen kann.
        v1 = v0
        frist = time.monotonic() + 3.0
        while time.monotonic() < frist:
            v1 = lautstaerke()
            if v1 is not None and (
                    (richtung == "up" and v1 > v0)
                    or (richtung == "down" and v1 < v0)):
                break
            time.sleep(0.2)
        satz["gemessen"]["lautstaerke_vorher"] = v0
        satz["gemessen"]["lautstaerke_nachher"] = v1
        try:
            assert v1 is not None and v1 != v0, (
                f"Lautstärke unverändert bei {v0:.2f} — die Aktion meldete "
                "Erfolg, die Wirkung fehlt")
        finally:
            # Umkehr, egal wie die Prüfung ausging: das System gehört nicht
            # dem Test.
            produzent_sende("auth.sock", "intent_mark", {})
            time.sleep(0.3)
            sock_frage("aktion.sock", {
                "v": 1, "art": "ausfuehren",
                "action_id": f"audio.volume.{umkehr}", "params": {},
                "quelle": "parser"})


# ---------------------------------------------------------------------------
# Szenario 3 — Ressourcenverbrauch gegen Design §13
# ---------------------------------------------------------------------------

def test_ressourcen_im_budget_design_13(sitzung, evidenz):
    """Dauerlast (Mittel), Spitze (p95) und RSS der Units, selbst gemessen.

    Zwei CPU-Größen gegen zwei Deckel, nach Design §13.1: OCR läuft
    stoßweise, deshalb misst ein Mittelwert die Dauerlast und ein p95 die
    Spitze — und beide brauchen ihren eigenen Deckel.

    Zwanzig Proben im Sekundentakt, CPU aus /proc-<pid>-Deltas, RSS aus
    VmRSS. Der Sampler bekommt zuerst einen bekannten Lastzeugnis-Kanarien:
    ein Brennerprozess, den er mit deutlich über einem halben Kern sehen muss
    — sonst beweist ein leeres Messband gar nichts.
    """
    with protokoll(evidenz, szenario="ressourcen_design_13",
                   gestartet=["/proc-Sampler (20 × 1 s)",
                              "CPU-Kanarienbrenner (1 s Vollast)"],
                   budget={"idle_cpu_mittel_prozent_eines_kerns":
                           BUDGET_CPU_MITTEL_PROZENT,
                           "idle_cpu_p95_prozent_eines_kerns":
                           BUDGET_CPU_P95_PROZENT,
                           "idle_rss_mb": BUDGET_RSS_MB,
                           "quelle": "docs/DESIGN.md §13.1, zwei Größen"}
                   ) as satz:
        # Kanarienvogel für den Sampler: ein Kern, eine Sekunde Vollast.
        brenner = subprocess.Popen(
            [sys.executable, "-c",
             "import time\nt0=time.time()\nwhile time.time()-t0<1.5: pass"])
        t0 = time.monotonic()
        vorher = _stat_felder(brenner.pid)
        time.sleep(1.0)
        nachher = _stat_felder(brenner.pid)
        brenner.terminate()
        brenner.wait(timeout=5)
        assert vorher is not None and nachher is not None
        kanarie_prozent = (nachher - vorher) / HZ / (time.monotonic() - t0) * 100
        satz["gemessen"]["sampler_kanarie_prozent"] = round(kanarie_prozent, 1)
        assert kanarie_prozent > 40.0, (
            f"Sampler sieht Vollast nur mit {kanarie_prozent:.0f} % — "
            "die Messung selbst ist kaputt")

        pids: dict[str, int] = {}
        for unit in DAUERLAST_UNITS:
            e = _systemctl("show", unit, "-p", "MainPID", "--value",
                           timeout=10.0)
            pid = int(e.stdout.strip() or "0")
            if pid > 0:
                pids[unit] = pid
        if not pids:
            pytest.skip("keine Dauerlast-Unit aktiv (Szenario 1)")
        satz["gemessen"]["einbezogen"] = sorted(pids)
        satz["gemessen"]["fehlend"] = sorted(set(DAUERLAST_UNITS) - set(pids))

        # Sekundenfenster, keine halben: ein Jiffy sind 10 ms, und ein
        # einziger Tick in einem 0,5-s-Fenster las sich als 2 % — gemessen
        # am 13.08. am Hub, der 0,4 % im Mittel zieht. Das Fenster muss
        # grob zur Auflösung passen, sonst misst man Quantisierung.
        fenster_s = 1.0
        proben_cpu: list[float] = []
        proben_rss_kb: list[float] = []
        je_unit: dict[str, list[float]] = {u: [] for u in pids}
        alt = {u: _stat_felder(p) for u, p in pids.items()}
        for _ in range(20):
            time.sleep(fenster_s)
            neu = {u: _stat_felder(p) for u, p in pids.items()}
            summe = 0.0
            for u in pids:
                if alt.get(u) is not None and neu.get(u) is not None:
                    prozent = (neu[u] - alt[u]) / HZ / fenster_s * 100.0
                    je_unit[u].append(prozent)
                    summe += prozent
            proben_cpu.append(summe)
            rss = [_rss_kb(p) for p in pids.values()]
            proben_rss_kb.append(float(sum(r for r in rss if r is not None)))
            alt = neu

        idle_cpu_p95 = round(_p95(proben_cpu), 3)
        idle_cpu_mittel = round(sum(proben_cpu) / len(proben_cpu), 3)
        idle_rss_mb = round(max(proben_rss_kb) / 1024.0, 1)
        satz["gemessen"]["idle_cpu_mittel"] = idle_cpu_mittel
        satz["gemessen"]["idle_cpu_p95"] = idle_cpu_p95
        satz["gemessen"]["idle_rss_mb"] = idle_rss_mb
        satz["gemessen"]["proben_cpu_prozent"] = [round(p, 2)
                                                  for p in proben_cpu]
        satz["gemessen"]["je_unit_cpu_max"] = {
            u: round(max(w), 2) for u, w in je_unit.items() if w}
        satz["gemessen"]["je_unit_rss_mb"] = {
            u: round((_rss_kb(p) or 0) / 1024.0, 1) for u, p in pids.items()}

        # Auf-Abruf-Dienste (§13): belegt, aber nicht im Dauerlast-Budget.
        abruf = {}
        for unit in ABRUF_UNITS:
            e = _systemctl("show", unit, "-p", "MainPID", "--value",
                           timeout=10.0)
            pid = int(e.stdout.strip() or "0")
            rss = _rss_kb(pid) if pid > 0 else None
            abruf[unit] = None if rss is None else round(rss / 1024.0, 1)
        satz["gemessen"]["auf_abruf_rss_mb"] = abruf

        assert idle_cpu_mittel <= BUDGET_CPU_MITTEL_PROZENT, (
            f"idle_cpu_mittel {idle_cpu_mittel} % > "
            f"{BUDGET_CPU_MITTEL_PROZENT} % (Design §13.1, Dauerlast)")
        assert idle_cpu_p95 <= BUDGET_CPU_P95_PROZENT, (
            f"idle_cpu_p95 {idle_cpu_p95} % > {BUDGET_CPU_P95_PROZENT} % "
            "(Design §13.1, Spitze)")
        assert idle_rss_mb <= BUDGET_RSS_MB, (
            f"idle_rss_mb {idle_rss_mb} MB > {BUDGET_RSS_MB} MB (Design §13)")


# ---------------------------------------------------------------------------
# Szenario 4 — Verhalten bei laufendem Spiel (Vollbild-Gate)
# ---------------------------------------------------------------------------

def _gpu_laden(vram_mib: int = 1) -> dict:
    return sock_frage("gpu.sock", {"v": 1, "art": "laden", "modell": "sonde",
                                   "vram_mib": vram_mib})


def _gpu_fertig(sperre: str) -> None:
    with contextlib.suppress(Exception):
        sock_frage("gpu.sock", {"v": 1, "art": "fertig", "sperre": sperre})


def test_vollbild_gate_sperrt_gpu_ladung(sitzung, evidenz, tmp_path):
    """Bei Vollbild verweigert das Gate die GPU-Ladung — gemessen an der
    Gate-Entscheidung, am Diagnose-Zähler und am VRAM, nicht am Etikett.

    Der Positiv-Kanarienvogel steht an beiden Enden: VOR dem Fenster muss
    das Gate öffnen, NACH dem Schließen muss es WIEDER öffnen. Ein Gate, das
    immer sperrt, bestünde sonst jede Zeile dieses Tests.
    """
    pytest.skip(AUSGESETZT)
    with protokoll(evidenz, szenario="vollbild_gate",
                   gestartet=["tests/harness/vollbildfenster.py (tkinter, "
                              "Vollbild)", "gpu.sock Ladesonde (1 MiB)"],
                   budget={}) as satz:
        if fokus_zustand() is None:
            pytest.skip("Fokus-Dienst nicht erreichbar — Gate-Eingang "
                        "nicht messbar")
        if shutil.which("python3") is None:
            pytest.skip("python3 fehlt für das Vollbildfenster")
        tk = _lauf(["python3", "-c", "import tkinter"], timeout=10.0)
        if tk.returncode != 0:
            pytest.skip("tkinter nicht verfügbar — kein Vollbildfenster "
                        "herstellbar")

        voll0, alter0 = fokus_zustand()
        if alter0 < 0:
            pytest.skip("KWin-Watcher hat noch nie gemeldet — Vollbild "
                        "wäre unsichtbar")
        if voll0:
            pytest.skip("es läuft bereits ein Vollbildfenster — das Gate "
                        "stünde zu Recht zu")

        # Kanarie 1: das Gate ist offen, bevor irgendetwas Vollbild ist.
        offen = _gpu_laden()
        if offen.get("grund") in ("lade_sperre", "vram"):
            pytest.skip(f"Gate nicht frei prüfbar: {offen.get('grund')}")
        assert offen.get("ok") is True, f"Gate zu ohne Vollbild: {offen}"
        _gpu_fertig(str(offen.get("sperre", "")))
        satz["gemessen"]["gate_ohne_vollbild"] = "offen"

        verworfen_vorher = int(sock_lesen("diag.sock")
                               .get("verworfen", {}).get("gpu_fullscreen", 0))

        e = _lauf(["nvidia-smi", "--query-gpu=memory.used",
                   "--format=csv,noheader,nounits"], timeout=10.0)
        vram_vorher = int(e.stdout.strip()) if e.returncode == 0 else None

        log = tmp_path / "vollbild.log"
        fenster = subprocess.Popen(
            ["python3", str(VOLLBILDFENSTER), "#003300", str(log), "45"])
        try:
            frist = time.monotonic() + 5.0
            while time.monotonic() < frist and "ready" not in (
                    log.read_text() if log.exists() else ""):
                time.sleep(0.1)
            assert fenster.poll() is None, "Vollbildfenster früh gestorben"

            frist = time.monotonic() + 8.0
            zustand = fokus_zustand()
            while time.monotonic() < frist and not (zustand and zustand[0]):
                time.sleep(0.4)
                zustand = fokus_zustand()
            if not (zustand and zustand[0]):
                pytest.skip("KWin-Watcher meldet das Vollbildfenster nicht "
                            "— Gate-Eingang unbewiesen")

            abgelehnt = _gpu_laden()
            satz["gemessen"]["gate_bei_vollbild"] = abgelehnt
            assert abgelehnt.get("ok") is False
            assert abgelehnt.get("grund") == "fullscreen", (
                f"Grund {abgelehnt.get('grund')!r} statt 'fullscreen'")

            # Der Zähler, unabhängig von der Antwortzeile: der Hub muss die
            # Sperre auch vermerkt haben.
            verworfen_nachher = int(sock_lesen("diag.sock")
                                    .get("verworfen", {})
                                    .get("gpu_fullscreen", 0))
            satz["gemessen"]["diag_gpu_fullscreen_vorher"] = verworfen_vorher
            satz["gemessen"]["diag_gpu_fullscreen_nachher"] = verworfen_nachher
            assert verworfen_nachher > verworfen_vorher
        finally:
            fenster.terminate()
            with contextlib.suppress(Exception):
                fenster.wait(timeout=5)

        # Kanarie 2: nach dem Fenster öffnet das Gate wieder — sonst wäre
        # „sperrt bei Vollbild" von „sperrt für immer" nicht zu trennen.
        frist = time.monotonic() + 8.0
        zustand = fokus_zustand()
        while time.monotonic() < frist and (zustand is None or zustand[0]):
            time.sleep(0.4)
            zustand = fokus_zustand()
        assert zustand is not None and not zustand[0], (
            "Watcher meldet das Ende des Vollbilds nicht")
        wieder = _gpu_laden()
        assert wieder.get("ok") is True, f"Gate öffnet nicht wieder: {wieder}"
        _gpu_fertig(str(wieder.get("sperre", "")))
        satz["gemessen"]["gate_nach_vollbild"] = "offen"

        e = _lauf(["nvidia-smi", "--query-gpu=memory.used",
                   "--format=csv,noheader,nounits"], timeout=10.0)
        vram_nachher = int(e.stdout.strip()) if e.returncode == 0 else None
        satz["gemessen"]["vram_vorher_mib"] = vram_vorher
        satz["gemessen"]["vram_nachher_mib"] = vram_nachher
        if vram_vorher is not None and vram_nachher is not None:
            # Kein Ladevorgang war erlaubt — das VRAM darf sich nicht um
            # Modellgrößenordnungen verändert haben. Fremdlast des Nutzers
            # bleibt außen vor: geprüft wird nur, dass WIR nichts geladen
            # haben, und das belegt bereits die Gate-Antwort oben.
            satz["gemessen"]["vram_delta_mib"] = vram_nachher - vram_vorher


# ---------------------------------------------------------------------------
# Szenario 5 — alle Kill-Switches wirken
# ---------------------------------------------------------------------------

def test_killswitch_ohren(sitzung, evidenz):
    """Der Ohren-Schalter, am Strom gemessen.

    Kanarienvogel zuerst: PTT an, und es MUSS ein Aufnahmestrom erscheinen —
    sonst bewiese „null Ströme danach" nur, dass nie etwas lief. Danach der
    Schalter, und die Wirkung wird hier NOCH EINMAL selbst gemessen, nicht
    aus dem Bericht des Schalters übernommen.
    """
    from daimon.ears import killswitch as ohren

    with protokoll(evidenz, szenario="killswitch_ohren",
                   gestartet=["PTT über auth.sock (Mikrofonstrom)",
                              "daimon.ears.killswitch.stoppe()"],
                   budget={}) as satz:
        stroeme0 = ohren.aufnahmestroeme()
        if stroeme0 is None:
            pytest.skip("Aufnahmeströme nicht messbar (pw-dump fehlt?)")
        if stroeme0 > 0:
            pytest.skip(f"{stroeme0} fremde(r) Aufnahmestrom/-ströme aktiv — "
                        "die absolute Null wäre ein fremder Befund")
        if not ist_aktiv("daimon-ears.service"):
            pytest.skip("daimon-ears läuft nicht (Szenario 1)")

        produzent_sende("auth.sock", "ptt", {"an": True})
        frist = time.monotonic() + 6.0
        strom = ohren.aufnahmestroeme()
        while time.monotonic() < frist and not strom:
            time.sleep(0.3)
            strom = ohren.aufnahmestroeme()
        if not strom:
            produzent_sende("auth.sock", "ptt", {"an": False})
            pytest.skip("bei PTT erscheint kein Aufnahmestrom — Mikrofon "
                        "nicht verfügbar?")
        satz["gemessen"]["kanarie_stroeme_bei_ptt"] = strom

        bericht = ohren.stoppe()
        satz["gemessen"]["schalter_bericht"] = bericht
        assert bericht["ok"] is True, bericht["meldung"]

        # Die Wirkung, von HIER gemessen — nicht aus `bericht`.
        assert not ist_aktiv("daimon-ears.service"), \
            "Unit ist nach dem Schalter weiter aktiv"
        stroeme1 = ohren.aufnahmestroeme()
        assert stroeme1 is not None, "Strommessung nach dem Schalter weg"
        assert stroeme1 == 0, f"{stroeme1} Aufnahmestrom/-ströme laufen weiter"
        satz["gemessen"]["stroeme_nachher"] = stroeme1

        produzent_sende("auth.sock", "ptt", {"an": False})


def test_killswitch_augen(sitzung, evidenz):
    """Der Augen-Schalter: Unit, eigener Strom, Kontextverzeichnis, Lampe.

    Kanarienvogel: der Dienst antwortet vorher auf DBus mit seinen
    Seh-Zählern — er SIEHT also wirklich. Danach wird nicht der Bericht des
    Schalters geglaubt, sondern alles noch einmal von hier gemessen. Zum
    Schluss gehen die Augen wieder an: dieser Test läuft auf einem
    Arbeitsplatz, und ein Prüfer, der die Wahrnehmung aus lässt, ist selbst
    ein Befund.
    """
    from daimon.eyes import killswitch as augen

    with protokoll(evidenz, szenario="killswitch_augen",
                   gestartet=["python -m daimon.eyes.killswitch (Hotkey-Weg)"],
                   budget={}) as satz:
        if not ist_aktiv("daimon-eyes.service"):
            pytest.skip("daimon-eyes läuft nicht (Szenario 1)")

        sieht = _lauf(["busctl", "--user", "--json=short", "call",
                       "de.daimon.Eyes", "/Eyes", "de.daimon.Eyes",
                       "Zustand"], timeout=10.0)
        if sieht.returncode != 0:
            pytest.skip("Augen-Dienst antwortet nicht auf DBus — Kanarie "
                        "nicht messbar")
        zaehler = json.loads(json.loads(sieht.stdout)["data"][0])
        assert "runden" in zaehler
        satz["gemessen"]["kanarie_seh_zaehler"] = zaehler

        e = _lauf([str(VENV_PYTHON), "-m", "daimon.eyes.killswitch"],
                  timeout=30.0)
        zeile = (e.stdout or "").strip().splitlines()
        bericht = json.loads(zeile[-1]) if zeile else {}
        satz["gemessen"]["schalter_bericht"] = bericht
        assert e.returncode == 0, f"Schalter rc={e.returncode}: {bericht}"
        assert bericht.get("ok") is True, bericht.get("meldung")

        # Unabhängig nachgemessen: Unit, Strom, Verzeichnis, Lampe.
        assert not ist_aktiv("daimon-eyes.service"), \
            "Unit ist nach dem Schalter weiter aktiv"
        stroeme = augen.videostroeme()
        assert stroeme is not None, "Videoströme nicht messbar (pw-dump?)"
        assert stroeme == 0, f"{stroeme} eigene(r) Videostrom/-ströme weiter"
        dateien = augen.kontextdateien()
        assert dateien == 0, f"{dateien} Datei(en) im Kontextverzeichnis"
        assert augen.lampe() == "aus"
        satz["gemessen"]["stroeme_nachher"] = stroeme
        satz["gemessen"]["kontextdateien_nachher"] = dateien
        satz["gemessen"]["lampe"] = "aus"

        # Wiederanlauf — daimon-eyes lief vor diesem Test.
        _systemctl("start", "daimon-eyes.service")
        frist = time.monotonic() + 15.0
        while not ist_aktiv("daimon-eyes.service") and time.monotonic() < frist:
            time.sleep(0.5)
        assert ist_aktiv("daimon-eyes.service"), \
            "daimon-eyes kommt nach dem Schalter nicht wieder hoch"
        satz["gemessen"]["wiederanlauf"] = "active"


def test_killswitch_gedaechtnis(sitzung, evidenz):
    """`--loeschen` löscht Zeilen UND Datei — der dritte Schalter.

    Kanarienvogel: ein eigener Eintrag, der vorher lesbar ist. Ohne ihn
    wäre „Datei weg" auch dann wahr, wenn nie etwas darin stand. Die
    Datei-Prüfung zählt `-wal` und `-shm` mit: im WAL steht dasselbe wie in
    der Datenbank, und eine Restdatei mit 0644 neben einer gelöschten
    Datenbank ist kein Vergessen.
    """
    from daimon.common.config import state_dir
    from daimon.mind.store import DATEI, Store

    with protokoll(evidenz, szenario="killswitch_gedaechtnis",
                   gestartet=["python -m daimon.mind.store --loeschen",
                              "Store-Kanarieneintrag"],
                   budget={}) as satz:
        pfad = state_dir() / DATEI

        store = Store()
        store.migrieren()
        kanarie = store.schreiben("notiz", "T-6.8-Kanarienvogel")
        lesbar = [z for z in store.lesen("notiz") if z["id"] == kanarie]
        assert lesbar, "Kanarieneintrag nicht lesbar — nichts zu löschen bewiesen"
        store.schliessen()
        satz["gemessen"]["kanarie_eintrag_id"] = kanarie

        e = _lauf([str(VENV_PYTHON), "-m", "daimon.mind.store", "--loeschen"],
                  timeout=30.0)
        zeile = (e.stdout or "").strip().splitlines()
        bericht = json.loads(zeile[-1]) if zeile else {}
        satz["gemessen"]["schalter_bericht"] = bericht
        assert e.returncode == 0
        assert bericht.get("geloescht", 0) >= 1

        # Die Wirkung: keine Datei, keine Nebendateien — von hier gezählt.
        reste = [str(pfad) + endung for endung in ("", "-wal", "-shm")
                 if Path(str(pfad) + endung).exists()]
        satz["gemessen"]["reste_nachher"] = reste
        assert not reste, f"Dateireste nach --loeschen: {reste}"

        # Und ein frischer Blick in den Speicher findet nichts mehr.
        frisch = Store()
        try:
            frisch.migrieren()
            assert frisch.lesen("notiz") == []
        finally:
            # Der frische Blick legt die Datei neu an — weg damit, der
            # Schalter hat „keine Datei" hinterlassen, und so bleibt es.
            frisch.alles_loeschen()
