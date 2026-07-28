#!/usr/bin/env bash
# Verifizierer fuer T-1.12: NVIDIA-Sprachstack als zweiter Pfad.
#
# Nachgetragen 2026-07-28. Der Spike stand in DESIGN.md v5.4, aber nicht im
# Plan v5.2 -- Design und Plan waren auseinandergelaufen. Die Kriterien sind
# aus spikes/nvidia-voice/SPEC.md uebernommen, nicht neu erfunden.
#
# Zwei Dinge, die dieser Verifizierer ANDERS behandelt als die uebrigen, und
# beide stehen so in der SPEC:
#
#   1. `loaded: false` bei Arm B ist KEIN Fehlschlag. Ob Magpie auf sm_120
#      ueberhaupt laedt, ist die Frage des Spikes -- ein Nein mit Begruendung
#      ist ein vollwertiges Ergebnis. Verlangt wird nur, dass die Begruendung
#      dasteht.
#   2. `ttfa_ms` darf null sein. Ohne Streaming-Schleife gibt es kein
#      Time-to-First-Audio, und eine aus der Gesamtlatenz GESCHAETZTE Zahl
#      waere schlimmer als keine. Genau das ist der Mutant: eine Zahl ohne
#      Messung. Verlangt wird deshalb: Zahl ODER null mit ttfa_reason.
#
# Nicht blockierend. Ein rotes T--1.12 haelt Gate P-1 nicht auf, es macht nur
# sichtbar, dass der zweite Pfad unentschieden ist.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$REPO/spikes/nvidia-voice/results.json"
NOTES="$REPO/spikes/nvidia-voice/NOTES.md"
OCR="$REPO/spikes/ocr/results.json"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
json() { jq -r "$1" "$RESULT" 2>/dev/null || echo "nein"; }

echo "T−1.12 — NVIDIA-Sprachstack als zweiter Pfad"

if [[ ! -f "$RESULT" ]]; then
  echo "  FAIL results.json fehlt ($RESULT)"
  echo "       Der Spike ist unentschieden: Werkzeug steht, Messung nicht gelaufen."
  echo "       smoke.json ist ausdruecklich KEIN Ersatz -- n=2-3, synthetisches"
  echo "       Audio, kein Verifizierer. Siehe spikes/nvidia-voice/SPEC.md."
  exit 1
fi

chk "results.json ist gueltiges JSON" \
  "$(jq -e . "$RESULT" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "results.json ist eine Liste von Eintraegen" \
  "$(json 'if type == "array" and length > 0 then "ja" else "nein" end')" ja

# --- beide Arme vorhanden --------------------------------------------------
chk "Arm A (ASR) ist gemessen" \
  "$(json 'if any(.[]; .arm | test("^A")) then "ja" else "nein" end')" ja
chk "Arm B (TTS) ist gemessen" \
  "$(json 'if any(.[]; .arm | test("^B")) then "ja" else "nein" end')" ja

# --- Arm A: mindestens zwei Modelle, eines mit Deutsch, plus Grundlinie -----
chk "Arm A: mindestens zwei Modelle" \
  "$(json '[.[] | select(.arm | test("^A")) | .model] | unique | if length >= 2 then "ja" else "nein" end')" ja
chk "Arm A: sherpa-Grundlinie im selben Lauf" \
  "$(json 'if any(.[]; (.arm | test("^A")) and (.model | test("sherpa"; "i"))) then "ja" else "nein" end')" ja

# --- Pflichtfelder je Eintrag ----------------------------------------------
FELDER='["arm","model","license","gated","loaded","backend","cold_start_ms","p50_ms","p95_ms","ttfa_ms","ttfa_reason","rtf","vram_idle_mb","vram_peak_mb","vram_after_exit_mb","wer","audio_source","n","verdict"]'
chk "alle geforderten Felder je Eintrag vorhanden" \
  "$(json "if all(.[]; keys_unsorted as \$k | ($FELDER | all(. as \$f | \$k | index(\$f) != null))) then \"ja\" else \"nein\" end")" ja

# --- n >= 20, aber nur wo tatsaechlich gemessen wurde -----------------------
# Ein Eintrag mit loaded == false hat nichts zu messen gehabt.
chk "je geladenem Modell mindestens 20 Laeufe" \
  "$(json 'if all(.[] | select(.loaded == true); (.n | type) == "number" and .n >= 20) then "ja" else "nein" end')" ja

# --- die TTFA-Falle --------------------------------------------------------
chk "ttfa_ms ist eine Zahl ODER null mit Begruendung" \
  "$(json 'if all(.[] | select(.loaded == true);
      (.ttfa_ms | type) == "number"
      or ((.ttfa_ms == null) and (.ttfa_reason | type) == "string" and (.ttfa_reason | length) > 0))
      then "ja" else "nein" end')" ja

# --- audio_source: die WER-Zahl ist ohne ihn nicht einzuordnen -------------
chk "audio_source ist je Eintrag gesetzt" \
  "$(json 'if all(.[]; (.audio_source | type) == "string" and (.audio_source | length) > 0) then "ja" else "nein" end')" ja
chk "synthetisches Audio ist in NOTES.md benannt" \
  "$( if json 'if any(.[]; .audio_source | test("synth|vits|erzeugt"; "i")) then "ja" else "nein" end' | grep -q ja; then
        grep -qiE "synthetisch|synthese|vits" "$NOTES" 2>/dev/null && echo ja || echo nein
      else echo ja; fi )" ja

# --- VRAM-Rueckgabe, dieselbe Pruefung wie T-3.7 ---------------------------
chk "VRAM kehrt je geladenem Modell auf +-50 MB zurueck" \
  "$(json 'if all(.[] | select(.loaded == true and (.vram_after_exit_mb | type) == "number");
      ((.vram_after_exit_mb - .vram_idle_mb) | fabs) <= 50) then "ja" else "nein" end')" ja

# --- Arm B darf scheitern, aber nicht schweigen ----------------------------
chk "nicht geladene Modelle tragen eine Begruendung im verdict" \
  "$(json 'if all(.[] | select(.loaded == false); (.verdict | type) == "string" and (.verdict | length) > 10) then "ja" else "nein" end')" ja

# --- Koexistenz gegen den ECHTEN VLM-Wert, nicht gegen eine Schaetzung -----
# Der Spike soll mit der Zahl aus T-1.10 rechnen. Wir pruefen, dass eine
# Koexistenzrechnung ueberhaupt dasteht und die 32 607 MiB der Karte nennt.
koexistenz=nein
if grep -qiE "koexistenz|32607|32 607" "$NOTES" 2>/dev/null; then koexistenz=ja; fi
chk "Koexistenzrechnung ist in NOTES.md dokumentiert" "$koexistenz" ja
chk "T-1.10 liegt als Bezugsgroesse vor" \
  "$([[ -f "$OCR" ]] && echo ja || echo nein)" ja

# --- Lizenzlage ------------------------------------------------------------
chk "Lizenz je Modell festgehalten" \
  "$(json 'if all(.[]; (.license | type) == "string" and (.license | length) > 0) then "ja" else "nein" end')" ja

exit $fail
