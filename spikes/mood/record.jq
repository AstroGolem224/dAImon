# T-1.5 — Feldauswahl fuer den Hook-Rekorder.
#
# Haengt je Claude-Code-Hook-Ereignis eine Zeile an events.jsonl.
# Aufruf (siehe hooks.json):
#   jq -c -f record.jq >> events.jsonl 2>/dev/null || true
#
# Warum jq und nicht Python: dieser Filter haengt an JEDEM Werkzeugaufruf.
# Gemessen auf dieser Maschine, n=30: jq 1,4 ms gegen 12,0 ms fuer ein
# Python-Skript -- allein der nackte Interpreterstart kostet 5,7 ms.
#
# Was NICHT aufgezeichnet wird, ist so wichtig wie was aufgezeichnet wird:
# `tool_input` bleibt draussen. Dort stehen Befehle und Dateiinhalte.
# Texte werden auf 120 Zeichen gekuerzt -- genug, um ein Ereignis spaeter
# einer Situation zuzuordnen, zu wenig, um Inhalte zu rekonstruieren.
# events.jsonl ist trotzdem per .gitignore ausgeschlossen; ins Repo gehen
# nur die abgeleitete results.json und eine redigierte Stichprobe.

def clip: if . == null then null else (tostring | .[0:120]) end;

{
  wall:              now,
  hook_event_name:   .hook_event_name,
  session_id:        .session_id,
  cwd:               .cwd,
  tool_name:         .tool_name,
  notification_type: .notification_type,
  source:            .source,
  reason:            .reason,
  message:                .message               | clip,
  prompt:                 .prompt                | clip,
  last_assistant_message: .last_assistant_message | clip
}
| with_entries(select(.value != null))
