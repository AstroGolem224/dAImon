#!/usr/bin/env bash
# Erzeugt die acht T-0.9-Mutanten frisch aus dem Gut-Muster.
#
# Je Zusage von T-0.9 eine Mutante, und keine Beliebigkeit:
#   beide horchenden Unix-Sockets   -> hookbridge-fehlt-in-der-startliste
#   KEIN horchender TCP-Socket      -> tcp-diagnoseport-im-hub
#   state.sock 0600                 -> state-sock-weltschreibbar
#   hookbridge.sock 0600            -> hookbridge-sock-weltschreibbar
#   Ereignis erreicht den State     -> bus-hat-keinen-abnehmer
#   Blase ist gesetzt               -> blase-bleibt-aus
#   Befund vom wirklichen Prozess   -> hub-arbeitet-im-kindprozess
#   der Prueflauf darf senden       -> verify-scope-nicht-erlaubt
#
# Die letzte ist die neue: `daimon-verify.scope` steht seit bd0bb8e in
# `PRODUZENT_UNITS["hookbridge"]`. Ohne sie misst dieser Mutationstest die
# Zusage nicht, die der Prueflauf seit dem 27.08. selbst benutzt.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-0.9"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  hookbridge-fehlt-in-der-startliste
  tcp-diagnoseport-im-hub
  state-sock-weltschreibbar
  hookbridge-sock-weltschreibbar
  bus-hat-keinen-abnehmer
  blase-bleibt-aus
  hub-arbeitet-im-kindprozess
  verify-scope-nicht-erlaubt
)
for name in "${mutanten[@]}"; do
  mkdir -p "$TMP/$name"
  cp -a "$GUT/." "$TMP/$name/"
  find "$TMP/$name" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
done

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])
DAEMON = "daimon/hub/daemon.py"
BUS = "daimon/hub/bus.py"
IPC = "daimon/common/ipc.py"

def ersetze(name, pfad, alt, neu, beschreibung):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(f"{name}: Mutationsanker {alt!r} nicht genau einmal gefunden")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n", encoding="utf-8")

ersetze(
    "hookbridge-fehlt-in-der-startliste", DAEMON,
    '        for p in produzenten or ["hookbridge", "face", "auth", "ears", "plan"]:',
    '        for p in produzenten or ["face", "auth", "ears", "plan"]:  # MUTATION',
    "Der Hub startet den Produzenten `hookbridge` nicht mehr -- der Socket "
    "wird nie gebunden. Genau der Fehler, den dieses Repo sechsmal hatte: "
    "das Stueck ist da, aber niemand ruft es auf. Erwartet rot an 'haelt "
    "beide horchenden Unix-Sockets'; die Modus-Pruefung und die Zustellung "
    "fallen mit, weil es die Tuer nicht gibt.")

ersetze(
    "tcp-diagnoseport-im-hub", DAEMON,
    "        os.chmod(self.runtime_dir, 0o700)",
    "        os.chmod(self.runtime_dir, 0o700)\n"
    "        _diag = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # MUTATION\n"
    '        _diag.bind(("127.0.0.1", 0))\n'
    "        _diag.listen(1)\n"
    "        self._server.append(_diag)",
    "Der Hub oeffnet einen bequemen Diagnoseport auf 127.0.0.1. Damit ist "
    "RestrictAddressFamilies=AF_UNIX in T-0.14 nicht mehr erfuellbar -- eine "
    "ganze Schutzschicht faellt weg, weil jemand einen Endpunkt bequem "
    "fand. Erwartet rot an 'haelt KEINEN horchenden TCP-Socket'.")

ersetze(
    "state-sock-weltschreibbar", DAEMON,
    "        pfad = self.runtime_dir / dateiname\n"
    "        if pfad.exists():\n"
    "            pfad.unlink()\n"
    "        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
    "        srv.bind(str(pfad))\n"
    "        os.chmod(pfad, 0o600)",
    "        pfad = self.runtime_dir / dateiname\n"
    "        if pfad.exists():\n"
    "            pfad.unlink()\n"
    "        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
    "        srv.bind(str(pfad))\n"
    "        os.chmod(pfad, 0o666)  # MUTATION",
    "`_horche_einfach` laesst seine Sockets auf 0666 stehen -- state.sock, "
    "diag.sock, gpu.sock, tts.sock, ticket.sock und aktion.sock. Gemessen "
    "wird state.sock. Erwartet rot an 'state.sock hat Modus 0600'.")

ersetze(
    "hookbridge-sock-weltschreibbar", IPC,
    "    os.chmod(pfad, 0o600)",
    "    os.chmod(pfad, 0o666)  # MUTATION",
    "`ipc.listen` bindet Produzentensockets auf 0666 -- jeder Prozess jedes "
    "Nutzers darf Hook-Ereignisse einspeisen, und die Peer-Pruefung ist "
    "danach der einzige Wegweiser. Erwartet rot an 'hookbridge.sock hat "
    "Modus 0600'.")

ersetze(
    "bus-hat-keinen-abnehmer", DAEMON,
    "        self.bus.subscribe(self._on_event)",
    "        pass  # MUTATION: niemand haengt am Bus",
    "Der Hub abonniert den Bus nicht mehr. Socket, Protokoll und Bus "
    "arbeiten weiter, der State erfaehrt nichts -- die Naht reisst genau "
    "zwischen Zulauf und Wirkung. Erwartet rot an 'Ereignis erreicht den "
    "State ueber den Socket' und an 'Bubble ist gesetzt'.")

ersetze(
    "blase-bleibt-aus", BUS,
    '        if art == "permission_prompt":\n'
    '            return "needs_input", {\n'
    '                "title": "braucht dein OK",\n'
    '                "body": _kurz(payload.get("message") or "Claude wartet auf eine Freigabe."),\n'
    '                "urgent": True,\n'
    "            }",
    '        if art == "permission_prompt":\n'
    '            return "needs_input", None  # MUTATION',
    "Die Stimmung wechselt weiter nach `needs_input`, aber es entsteht keine "
    "Blase. Der Hub weiss also, dass jemand wartet, und sagt es nicht -- die "
    "eine Mutante, die nur ein Kriterium trifft. Erwartet rot an 'Bubble ist "
    "gesetzt', gruen bei der Stimmung.")

ersetze(
    "hub-arbeitet-im-kindprozess", DAEMON,
    "    _dumpbarkeit_abschalten()\n"
    "    hub = Hub(runtime_dir=args.runtime_dir)",
    '''    _dumpbarkeit_abschalten()
    if not os.environ.get("DAIMON_T09_KIND"):  # MUTATION
        import subprocess as _sp, sys as _sys
        os.environ["DAIMON_T09_KIND"] = "1"
        _sp.Popen([_sys.executable, "-m", "daimon.hub.daemon", *_sys.argv[1:]],
                  preexec_fn=lambda: ctypes.CDLL("libc.so.6").prctl(1, 15))
        os.execvp("sleep", ["sleep", "600"])
    hub = Hub(runtime_dir=args.runtime_dir)''',
    "Der gestartete Prozess reicht die Arbeit an ein Kind weiter und haengt "
    "sich als Wachhund davor. Die Sockets gehoeren dann NICHT dem Prozess, "
    "den der Prueflauf gestartet hat -- genau die Verwechslung, gegen die "
    "die Inode-/fd-Korrelation gebaut ist. Erwartet rot an 'Socket-Befund "
    "stammt vom wirklichen Hub-Prozess'.\n"
    "Zwei Einzelheiten sind Absicht und keine Umstaendlichkeit:\n"
    "* `os.execvp(\"sleep\")` statt `wait()`. Der Sondenfaden der "
    "Sitzung (tests/harness/t09_socket_probe) laeuft im Elternprozess "
    "weiter und schriebe denselben Befundpfad wie das Kind -- mit "
    "Eltern-PID und LEERER Socketliste. Wer zuletzt schreibt, entschiede "
    "dann, welches Kriterium rot wird, und beim ersten Lauf am 27.08. war "
    "es keines: die Mutante kam gruen durch. `exec` wirft den Faden mitsamt "
    "dem Interpreter weg; danach schreibt nur noch das Kind, und der Befund "
    "ist eindeutig. Kein `python -c sleep` an dieser Stelle -- ein frischer "
    "Interpreter zoege die Sonde ueber PYTHONPATH wieder hoch.\n"
    "* PR_SET_PDEATHSIG haengt das Kind an das Elternteil, damit der "
    "Prueflauf beim Aufraeumen keinen Hub zuruecklaesst.")

ersetze(
    "verify-scope-nicht-erlaubt", DAEMON,
    '    "hookbridge": ("daimon-hookbridge.service", VERIFY_SCOPE),',
    '    "hookbridge": ("daimon-hookbridge.service",),  # MUTATION',
    "`daimon-verify.scope` steht nicht mehr in PRODUZENT_UNITS -- der "
    "Prueflauf wird als `fremde_unit` abgewiesen, obwohl er in der Scope "
    "laeuft. Erwartet rot an 'Ereignis erreicht den State ueber den Socket' "
    "und an 'Bubble ist gesetzt', und ausdruecklich NICHT als 'nicht "
    "gemessen': die Scope kam zustande, der Sender lief, der Hub hat ihn "
    "weggeschickt.")
PY

for name in "${mutanten[@]}"; do
  rm -rf "$HIER/$name"
  mv "$TMP/$name" "$HIER/$name"
done
echo "erzeugt: ${#mutanten[@]} Mutanten unter $HIER"
