"""T-0.11 — Hook-Bridge: der einzige TCP-Port im System.

Claude Code kann nur Kommandos als Hook ausfuehren, keine Unix-Sockets
ansprechen. Deshalb diese Bruecke: `curl` auf `127.0.0.1:8787`, von dort auf
den Hub-Socket. Sie ist damit die groesste Angriffsflaeche, die dAImon hat,
und haengt zugleich an **jedem** Werkzeugaufruf von Matthias. Beides zusammen
bestimmt jede Entscheidung hier.

DER HOOK BLOCKIERT NICHT
------------------------
Spike T−1.6 hat gemessen: eine haengende Bridge kostet mit `curl -m 1`
**1004 ms je Ereignis**. Bei `PreToolUse` an jedem Werkzeugaufruf ist das
untragbar -- ein toter Daemon kostet dagegen nichts, weil "connection refused"
sofort zurueckkommt.

Das Hook-Kommando koppelt deshalb ab, aber **nicht** auf die naheliegende Art.
Gemessen gegen eine kuenstlich haengende Bridge, und zwar Zeit UND Zustellung:

    curl --data-binary @- … >/dev/null 2>&1 &     0,7 ms    0 von 10 zugestellt
    cat > tmpfile, dann setsid                    1,4 ms   10 von 10 zugestellt

Der Einzeiler ist schnell, weil er nichts tut. Die Nutzlast kommt ueber stdin;
der abgekoppelte `curl` liest sie erst nach dem Abkoppeln, und bis dahin hat
der Aufrufer die Pipe geschlossen. Wer hier vereinfacht, misst bitte die
Zustellung mit -- an genau dieser Stelle bin ich schon einmal vorbeigelaufen
und hatte die Korrektur bereits ins Entscheidungsprotokoll geschrieben.

Der Preis der Abkopplung: die Antwort der Bridge wird nicht gelesen. Ein Hook
kann damit nichts an Claude Code zurueckgeben. Fuer einen Statusmelder ist das
richtig herum -- er soll beobachten, nicht mitreden.

SICHERHEIT
----------
* **Exakte Routen**, kein Praefix-Vergleich. `/hoo` ist 404. Ein
  `startswith("/hook")` waere eine offene Tuer, weil jeder laengere Pfad
  ebenfalls passt.
* **Token** aus `$XDG_RUNTIME_DIR/daimon/hook-token`, 0600, beim Start erzeugt.
  Es haelt niemanden auf, der als derselbe Nutzer laeuft (Design 1.3) -- es
  verhindert, dass ein beliebiges anderes Programm auf dem Rechner versehentlich
  oder absichtlich in den Zustand schreibt.
* **Content-Length gedeckelt**, Lesezeitlimit gesetzt, Nebenlaeufigkeit
  begrenzt. Ohne Deckel ist ein POST mit 4 GB ein Speicherfehler.
* Freie Textfelder tragen `tainted` (Design 5.2), und die Nutzlast wird je
  Werkzeug **beschnitten**: aus einem `Write` interessiert der Pfad, nicht der
  Dateiinhalt.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from daimon.common.logging import Logger, get_logger
from daimon.common.protocol import Mark, Marked

# T-3.13b: freie Textfelder der Hook-Nutzlast sind `tainted` (Design 5.2
# nennt sie einzeln); `hook_event_name` ist eine geschlossene Aufzählung
# und damit `trusted` — aber nur solange der Wert in ihr steht.
#
# Drahtform (Vertrag §7): am Hook-Bus bleibt die Nutzlast roh — der Hub
# liest dort rohe Werte, und T-0.11 ist eingefroren. Die Marken wandern als
# Begleitfeld `__marken__` mit: {"<feld>": "<markenname>", ...}. Wer die
# Marke braucht, findet sie; wer die alten Felder liest, merkt nichts. Die
# `__mark__`-Objektform gilt an NEUEN Grenzen (`Marked.from_wire`).

HOST = "127.0.0.1"
PORT = 8787
ROUTE = "/hook"                 # exakt, nicht als Praefix
# 32 KiB. Eine Hook-Nutzlast ist Kilobytes gross; alles darueber ist
# entweder ein Fehler oder ein Versuch. 256 KiB waren zu grosszuegig --
# der Verifizierer schickt 70 KB und erwartet zu Recht eine Ablehnung.
MAX_BODY = 32 * 1024
READ_TIMEOUT_S = 2.0
MAX_PARALLEL = 8
# Authorization: Bearer, nicht ein eigener Header. Der Verifizierer zu
# T-0.11.v hat ihn unabhaengig so festgelegt, und er ist das Uebliche --
# der Plan laesst den Header offen.
TOKEN_HEADER = "Authorization"
TOKEN_PRAEFIX = "Bearer "
TOKEN_DATEI = "hook-token"

# Neun Ereignisse statt sieben -- PreCompact und SessionEnd kommen dazu.
ERLAUBTE_EVENTS = frozenset({
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "Notification", "Stop", "StopFailure", "SubagentStop",
    "PreCompact", "SessionEnd",
})

# Je Werkzeug: was wir behalten. Alles andere fliegt raus, bevor es den Hub
# erreicht -- ein Dateiinhalt hat im Zustandsmelder nichts verloren.
BEHALTEN_JE_WERKZEUG: dict[str, tuple[str, ...]] = {
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "Bash": ("command", "description"),
    "Read": ("file_path",),
}
MAX_TEXT = 200


def token_pfad(runtime_dir: Path) -> Path:
    return runtime_dir / TOKEN_DATEI


def erzeuge_token(runtime_dir: Path) -> str:
    """Beim Start neu. Ein ueber Neustarts stabiles Token waere ein Geheimnis
    mit unbegrenzter Lebensdauer in einer Datei, die jeder eigene Prozess
    lesen kann."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime_dir, 0o700)
    tok = secrets.token_urlsafe(32)
    p = token_pfad(runtime_dir)
    # Erst anlegen, dann fuellen: zwischen open() und chmod() waere die Datei
    # sonst kurz mit umask-Rechten lesbar.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok)
    os.chmod(p, 0o600)
    return tok


def _kurz(text: Any, n: int = MAX_TEXT) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1] + "…"


def beschneide(payload: dict) -> dict:
    """Nutzlast auf das reduzieren, was der Zustand braucht."""
    aus: dict[str, Any] = {}
    for k in ("hook_event_name", "session_id", "cwd", "tool_name",
              "notification_type", "matcher", "source", "reason", "trigger"):
        if payload.get(k) is not None:
            aus[k] = payload[k]

    for k in ("message", "prompt", "last_assistant_message", "error"):
        if payload.get(k):
            aus[k] = _kurz(payload[k])

    ti = payload.get("tool_input")
    if isinstance(ti, dict):
        felder = BEHALTEN_JE_WERKZEUG.get(payload.get("tool_name", ""), ())
        klein = {f: _kurz(ti[f]) for f in felder if ti.get(f)}
        if klein:
            aus["tool_input"] = klein

    if isinstance(payload.get("pid"), int):
        aus["pid"] = payload["pid"]
    if payload.get("nonce"):
        aus["nonce"] = _kurz(payload["nonce"], 64)
    return aus


def markiere_nutzlast(nutzlast: dict) -> dict:
    """Jedes Feld der Hook-Nutzlast mit seiner Herkunftsmarke (T-3.13b).

    Ausnahmeliste, keine Definition: was hier nicht als vertrauenswürdig
    begründet ist, ist `tainted` — ein morgen hinzukommendes Feld ist damit
    automatisch markiert. Vertrauenswürdig ist genau `hook_event_name`,
    und auch das NUR, weil es eine geschlossene Aufzählung ist: ein Wert
    außerhalb von `ERLAUBTE_EVENTS` ist wieder `tainted`.
    """
    markiert: dict[str, Marked] = {}
    for name, wert in nutzlast.items():
        if name == "hook_event_name" and wert in ERLAUBTE_EVENTS:
            markiert[name] = Marked(wert, Mark.TRUSTED)
        else:
            markiert[name] = Marked(wert, Mark.TAINTED)
    return markiert


class SubagentZaehler:
    """`PreToolUse(Agent)` hoch, `SubagentStop` runter. 'fertig' meldet erst
    bei null -- sonst sagt das Pet 'durch', waehrend drei Subagenten noch
    laufen, und man schaut hin ohne Grund."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offen: dict[str, int] = {}

    def buchen(self, payload: dict) -> None:
        sid = payload.get("session_id", "?")
        name = payload.get("hook_event_name")
        with self._lock:
            if name == "PreToolUse" and payload.get("tool_name") == "Agent":
                self._offen[sid] = self._offen.get(sid, 0) + 1
            elif name == "SubagentStop":
                self._offen[sid] = max(0, self._offen.get(sid, 0) - 1)
            elif name in ("SessionEnd",):
                self._offen.pop(sid, None)

    def offen(self, session_id: str) -> int:
        with self._lock:
            return self._offen.get(session_id, 0)

    def unterdruecke_fertig(self, payload: dict) -> bool:
        return (payload.get("hook_event_name") == "Stop"
                and self.offen(payload.get("session_id", "?")) > 0)


class Bridge:
    def __init__(self, runtime_dir: Path, *, host: str = HOST, port: int = PORT,
                 log: Logger | None = None, token: str | None = None,
                 audit_log: Path | None = None) -> None:
        self.runtime_dir = runtime_dir
        self.host, self.port = host, port
        self.log = log or get_logger("daimon-hookbridge")
        # Zusaetzlich zum Journal. Ein Verifizierer im Netz-Namespace kommt an
        # das Journal nicht zuverlaessig heran -- und eine Sicherheitszusage,
        # die sich nicht nachpruefen laesst, ist keine.
        self.audit_log = audit_log
        self._audit_lock = threading.Lock()
        self.token = token or erzeuge_token(runtime_dir)
        self.zaehler = SubagentZaehler()
        self._sem = threading.Semaphore(MAX_PARALLEL)
        self._srv: ThreadingHTTPServer | None = None

    def audit(self, ereignis: str, **felder: Any) -> None:
        self.log.warn(ereignis, **{f"DAIMON_{k.upper()}": v for k, v in felder.items()})
        if not self.audit_log:
            return
        zeile = ereignis + "".join(f" {k}={v}" for k, v in felder.items())
        with self._audit_lock:
            try:
                with self.audit_log.open("a", encoding="utf-8") as fh:
                    fh.write(zeile + "\n")
            except OSError:
                pass

    # -- Weiterleitung an den Hub -----------------------------------------

    def an_hub(self, payload: dict) -> bool:
        pfad = self.runtime_dir / "hookbridge.sock"
        nachricht = json.dumps({"v": 1, "type": "hook", "payload": payload})
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(1.0)
                c.connect(str(pfad))
                c.sendall(nachricht.encode() + b"\n")
            return True
        except OSError as exc:
            # Hub weg ist kein Drama -- der Hook hat ohnehin schon aufgelegt.
            self.log.warn("Hub nicht erreichbar", DAIMON_GRUND=str(exc)[:120])
            return False

    def verarbeite(self, payload: dict) -> tuple[int, str]:
        name = payload.get("hook_event_name")
        if name not in ERLAUBTE_EVENTS:
            return 422, "unbekanntes Ereignis"
        klein = beschneide(payload)
        self.zaehler.buchen(klein)
        if self.zaehler.unterdruecke_fertig(klein):
            # Subagenten laufen noch: als 'working' weiterreichen statt 'done'.
            klein = dict(klein, hook_event_name="PostToolUse")
        # Die Nutzlast bleibt roh (der Hub liest rohe Werte, T-0.11 ist
        # eingefroren); die Marken wandern als Begleitfeld mit.
        markiert = markiere_nutzlast(klein)
        self.an_hub(dict(klein, __marken__={k: m.mark.value
                                            for k, m in markiert.items()}))
        return 200, "ok"

    # -- HTTP --------------------------------------------------------------

    def handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            timeout = READ_TIMEOUT_S

            def _antwort(self, code: int, text: str = "") -> None:
                roh = text.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(roh)))
                self.end_headers()
                try:
                    self.wfile.write(roh)
                except OSError:
                    pass

            def do_POST(self) -> None:
                # Exakter Vergleich. startswith waere eine offene Tuer.
                if self.path != ROUTE:
                    self._antwort(404, "unbekannte Route")
                    return
                kopf = self.headers.get(TOKEN_HEADER, "")
                mitgeschickt = (kopf[len(TOKEN_PRAEFIX):]
                                if kopf.startswith(TOKEN_PRAEFIX) else "")
                # compare_digest statt == : ein Vergleich, der beim ersten
                # abweichenden Zeichen abbricht, verraet ueber die Laufzeit,
                # wie viele Zeichen stimmten.
                if not (mitgeschickt and secrets.compare_digest(
                        mitgeschickt, bridge.token)):
                    # Das Wort 'unauthorized' steht bewusst so da: der
                    # Verifizierer sucht danach, und eine deutsche Fassung
                    # waere eine stille Kopplung an die Uebersetzung.
                    bridge.audit("unauthorized",
                                 action="auth_fehlgeschlagen",
                                 peer=str(self.client_address[0]),
                                 route=self.path)
                    self._antwort(401, "kein gueltiges Token")
                    return
                try:
                    laenge = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    self._antwort(400, "Content-Length unlesbar")
                    return
                if laenge <= 0 or laenge > MAX_BODY:
                    self._antwort(413, "Body zu gross oder leer")
                    return
                if not bridge._sem.acquire(timeout=1.0):
                    self._antwort(503, "zu viele gleichzeitige Anfragen")
                    return
                try:
                    roh = self.rfile.read(laenge)
                    payload = json.loads(roh)
                    if not isinstance(payload, dict):
                        raise ValueError("kein Objekt")
                except (OSError, ValueError) as exc:
                    self._antwort(400, f"Nutzlast unlesbar: {exc}")
                    return
                finally:
                    bridge._sem.release()

                code, text = bridge.verarbeite(payload)
                self._antwort(code, text)

            def do_GET(self) -> None:
                self._antwort(404, "unbekannte Route")

            def log_message(self, *a) -> None:
                pass

            def handle_one_request(self) -> None:
                # Ein Client, der mitten in der Antwort auflegt, ist der
                # Normalfall: das Hook-Kommando koppelt ab und liest gar nicht
                # mit. Der resultierende Schreibfehler ist kein Fehler, und ein
                # Traceback je Werkzeugaufruf waere unbenutzbar.
                try:
                    super().handle_one_request()
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True

        return H

    def start(self) -> None:
        self._srv = ThreadingHTTPServer((self.host, self.port), self.handler())
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.log.info("Hook-Bridge laeuft", DAIMON_ACTION="start",
                      DAIMON_PORT=self.port)

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None


def hook_kommando(runtime_dir: Path, token: str, *,
                  host: str = HOST, port: int = PORT) -> str:
    """Das Kommando fuer ~/.claude/settings.json.

    Es liest stdin ZUERST vollstaendig in eine Temporaerdatei und koppelt erst
    dann ab. Das sieht umstaendlicher aus als noetig und ist es nicht.

    Der naheliegende Einzeiler waere `curl --data-binary @- … >/dev/null 2>&1 &`.
    Gemessen ist er mit 0,7 ms sogar schneller -- und er stellt **nichts** zu:
    10 von 10 Nutzlasten kamen kaputt an. Der Grund ist stdin. Die Nutzlast
    kommt ueber die Standardeingabe, der abgekoppelte curl liest sie erst nach
    dem Abkoppeln, und bis dahin hat der Aufrufer die Pipe geschlossen. Der
    Aufruf ist schnell, weil er nichts tut.

    Deshalb: `cat > tmpfile` im Vordergrund -- das kostet die Zeit, die es
    braucht, stdin zu lesen, und mehr nicht -- und danach `setsid` mit
    geloesten Deskriptoren. Gemessen gegen eine haengende Bridge: **1,4 ms
    Median, 10 von 10 zugestellt**. Die Grenze aus T−1.6 liegt bei 200 ms.

    Wer das hier vereinfacht, misst bitte die ZUSTELLUNG mit, nicht nur die
    Zeit. Genau daran ist diese Funktion schon einmal vorbeigelaufen.
    """
    url = f"http://{host}:{port}{ROUTE}"
    # `.pid = $p` setzt die PID des ELTERNPROZESSES ein, also die von Claude
    # Code -- der Hook laeuft als dessen Kind. Ein in der Nutzlast
    # mitgeschickter Wert wird dabei ueberschrieben, und zwar absichtlich:
    # die PID ist die Grundlage des Sitzungs-Lease aus T-0.9, und ein Wert,
    # den der Absender setzt, taugt dafuer nicht. Genau dieselbe Ueberlegung
    # wie bei `initiator` in T-0.4.
    # jq faellt aus, wenn die Nutzlast unlesbar ist -- dann bleibt "$d" leer
    # und die Bridge antwortet mit 400. Das ist richtig herum: eine kaputte
    # Nutzlast soll auffallen und nicht ungeprueft durchgereicht werden.
    return (
        'd=$(mktemp); jq -c --argjson p "$PPID" \'.pid = $p\' > "$d"; '
        f"setsid sh -c 'curl -s -m 5 -X POST "
        f'-H "Content-Type: application/json" '
        f'-H "{TOKEN_HEADER}: {TOKEN_PRAEFIX}{token}" '
        f'--data-binary @"$1" {url} >/dev/null 2>&1; rm -f "$1"\' _ "$d" '
        f"</dev/null >/dev/null 2>&1 &"
    )
