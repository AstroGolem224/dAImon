#!/usr/bin/env bash
# Verifizierer fuer T-0.2: die vier Bestandsdateien liegen am Zielort,
# nachweislich unveraendert, und der Legacy-Daemon funktioniert weiterhin.
#
# Zwei Teile, und der zweite ist der eigentliche Punkt. Prueffsummen zeigen nur,
# dass niemand beim Verschieben editiert hat. Dass der Daemon danach noch
# LAEUFT, zeigt erst der Smoke-Test aus docs/PHASE3-original.md 2.3 -- und der
# wird hier vollstaendig gefahren, nicht nur angetippt. Die Akzeptanz nennt
# ausdruecklich beides: mood == "needs_input" UND ein nichtleeres bubble. Ein
# Daemon, der den Mood setzt aber die Sprechblase verschluckt, waere aus Sicht
# des Pets kaputt, und eine Pruefung nur auf mood haette das durchgelassen.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUMS="$REPO/tests/fixtures/known-good/T-0.2/checksums.txt"
DAEMON="$REPO/daimon/hub/legacy_daemon.py"
PORT=8787

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.2 — Bestandsdateien einsortiert"

# --- 1. Zielorte -----------------------------------------------------------
chk "legacy_daemon.py liegt unter daimon/hub/" \
  "$([[ -f "$DAEMON" ]] && echo ja || echo nein)" ja
chk "legacy_daemon.py liegt NICHT mehr im Paketwurzelverzeichnis" \
  "$([[ -f "$REPO/daimon/legacy_daemon.py" ]] && echo nein || echo ja)" ja
chk "claude-hooks.json liegt unter config/" \
  "$([[ -f "$REPO/config/claude-hooks.json" ]] && echo ja || echo nein)" ja
chk "pet_client.gd liegt im Archiv docs/attic/" \
  "$([[ -f "$REPO/docs/attic/pet_client.gd" ]] && echo ja || echo nein)" ja
chk "PHASE3-original.md liegt unter docs/" \
  "$([[ -f "$REPO/docs/PHASE3-original.md" ]] && echo ja || echo nein)" ja

# --- 2. Byte-Identitaet ----------------------------------------------------
chk "Pruefsummenliste existiert" "$([[ -f "$SUMS" ]] && echo ja || echo nein)" ja
if [[ -f "$SUMS" ]]; then
  while read -r want path; do
    [[ -z "${want:-}" || "$want" == \#* ]] && continue
    # daimon/legacy_daemon.py wurde nach daimon/hub/ verschoben; die Liste haelt
    # den Pfad von damals fest, geprueft wird der Zielort.
    cur="$path"
    [[ "$path" == "daimon/legacy_daemon.py" ]] && cur="daimon/hub/legacy_daemon.py"

    # Geprueft wird der Blob aus b4fadb5, nicht der Arbeitsbaum. T-0.2 sagt
    # aus, dass das VERSCHIEBEN kein Byte geaendert hat -- das bleibt wahr,
    # unabhaengig davon, was spaeter erlaubt daran geaendert wird.
    blob="$(git -C "$REPO" show "b4fadb5:$path" 2>/dev/null | sha256sum | cut -d' ' -f1)"
    chk "Ursprungsblob unveraendert: $path" "${blob:-fehlt}" "$want"

    # Zusaetzlich der Arbeitsbaum -- aber nur fuer Dateien, die kein spaeterer
    # Task aendern darf. config/claude-hooks.json ist in T-0.11 ausdruecklich
    # als [aendern] gefuehrt: dort wird das Hook-Kommando eingesetzt. Es hier
    # einzufrieren hiesse, zwei Tasks gegeneinander zu stellen.
    if [[ "$cur" != "config/claude-hooks.json" ]]; then
      have="$(sha256sum "$REPO/$cur" 2>/dev/null | cut -d' ' -f1)"
      chk "Arbeitsbaum unveraendert: $cur" "${have:-fehlt}" "$want"
    else
      echo "  INFO $cur wird von T-0.11 planmaessig geaendert - nur Ursprungsblob geprueft"
      chk "$cur ist weiterhin gueltiges JSON mit Hooks" \
        "$(jq -e '.hooks | length > 0' "$REPO/$cur" >/dev/null 2>&1 && echo ja || echo nein)" ja
    fi
  done < "$SUMS"
fi

# --- 3. Der Smoke-Test aus PHASE3-original.md 2.3 --------------------------
# Der Daemon ist stdlib-only und bindet auf 127.0.0.1:8787. Laeuft dort schon
# etwas, wird NICHT gemessen -- ein fremder Prozess koennte alles antworten.
if command -v curl >/dev/null 2>&1; then have_curl=ja; else have_curl=nein; fi
chk "curl vorhanden" "$have_curl" ja

busy=nein
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":$PORT "; then busy=ja; fi
chk "Port $PORT ist frei (sonst antwortet ein fremder Prozess)" "$busy" nein

state=""
mood=""
bubble=""
started=nein
if [[ "$have_curl" == ja && "$busy" == nein && -f "$DAEMON" ]]; then
  python3 "$DAEMON" >/tmp/t02-daemon.log 2>&1 &
  pid=$!
  # Auf echtes Lauschen warten, nicht blind schlafen.
  for _ in $(seq 1 40); do
    curl -s -m 1 "http://127.0.0.1:$PORT/state" >/dev/null 2>&1 && { started=ja; break; }
    sleep 0.1
  done

  if [[ "$started" == ja ]]; then
    curl -s -m 2 -X POST -H 'Content-Type: application/json' \
      -d '{"hook_event_name":"Notification","session_id":"s1","notification_type":"permission_prompt","message":"Bash ausführen?"}' \
      "http://127.0.0.1:$PORT/hook" >/dev/null 2>&1
    state="$(curl -s -m 2 "http://127.0.0.1:$PORT/state" 2>/dev/null)"
    mood="$(jq -r '.mood // empty' <<<"$state" 2>/dev/null)"
    bubble="$(jq -r '.bubble // empty' <<<"$state" 2>/dev/null)"
  fi

  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
fi

chk "Daemon startet und antwortet auf /state" "$started" ja
chk "Smoke-Test setzt mood auf needs_input" "${mood:-keine}" needs_input
chk "Smoke-Test liefert ein nichtleeres bubble" \
  "$([[ -n "$bubble" && "$bubble" != "null" ]] && echo ja || echo nein)" ja

exit $fail
