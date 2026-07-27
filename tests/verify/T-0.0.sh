#!/usr/bin/env bash
# Verifizierer fuer T-0.0: greift die Rollen-Durchsetzung selbst an.
# Ein bestandener Lauf heisst: der Mechanismus lehnt tatsaechlich ab.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUARD="$REPO/.claude/hooks/role_guard.py"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

# Liefert "deny" oder "pass"
probe() {
  local role="$1" tool="$2" path="$3"
  local out
  out="$(printf '{"tool_name":"%s","tool_input":{"file_path":"%s"}}' "$tool" "$path" \
        | DAIMON_ROLE="$role" python3 "$GUARD" 2>/dev/null)"
  if grep -q '"permissionDecision": *"deny"' <<<"$out"; then echo deny; else echo pass; fi
}
probe_bash() {
  local role="$1" cmd="$2" out
  out="$(python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$cmd" \
        | DAIMON_ROLE="$role" python3 "$GUARD" 2>/dev/null)"
  if grep -q '"permissionDecision": *"deny"' <<<"$out"; then echo deny; else echo pass; fi
}

echo "1. Builder darf Verifizierer nicht schreiben"
chk "Write tests/verify/T-9.9.sh"      "$(probe builder Write "$REPO/tests/verify/T-9.9.sh")"        deny
chk "Edit  tests/verify/FROZEN"        "$(probe builder Edit  "$REPO/tests/verify/FROZEN")"          deny
chk "Write tests/mutants/x/y.py"       "$(probe builder Write "$REPO/tests/mutants/x/y.py")"         deny
chk "Write .claude/roles.toml"         "$(probe builder Write "$REPO/.claude/roles.toml")"           deny
chk "Bash  tee > FROZEN"               "$(probe_bash builder 'echo x > tests/verify/FROZEN')"        deny
chk "Bash  sed -i verifier"            "$(probe_bash builder 'sed -i s/a/b/ tests/verify/T-0.8.sh')" deny

echo "2. Positiv-Kanarienvogel: Builder darf Produktivcode schreiben"
chk "Write daimon/hub/state.py"        "$(probe builder Write "$REPO/daimon/hub/state.py")"          pass
chk "Write face/src/main.rs"           "$(probe builder Write "$REPO/face/src/main.rs")"             pass

echo "3. Reviewer darf Verifizierer, aber keinen Produktivcode"
chk "Write tests/verify/T-9.9.sh"      "$(probe reviewer Write "$REPO/tests/verify/T-9.9.sh")"       pass
chk "Write daimon/hub/state.py"        "$(probe reviewer Write "$REPO/daimon/hub/state.py")"         deny
chk "Write face/src/main.rs"           "$(probe reviewer Write "$REPO/face/src/main.rs")"            deny

echo "4. Fail closed"
chk "unknown Rolle -> deny"            "$(probe unknown Write "$REPO/daimon/x.py")"                  deny
chk "leere Rolle   -> deny"            "$(probe ''      Write "$REPO/daimon/x.py")"                  deny
chk "Symlink-Umgehung"                 "$(probe builder Write "$REPO/docs/../tests/verify/T-9.9.sh")" deny

echo "5. Investigator nur in spikes/ und docs/"
chk "Write spikes/x/results.json"      "$(probe investigator Write "$REPO/spikes/x/results.json")"   pass
chk "Write daimon/hub/state.py"        "$(probe investigator Write "$REPO/daimon/hub/state.py")"     deny

echo "6. verify-frozen erkennt Manipulation"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp "$REPO/tests/verify/verify-frozen.sh" "$tmp/vf.sh"
printf 'x\n' > "$tmp/probe.sh"
( cd "$REPO" && printf '%s tests/verify/.probe-tmp\n' "$(sha256sum "$tmp/probe.sh" | cut -d' ' -f1)" > /tmp/frozen-probe )
cp "$tmp/probe.sh" "$REPO/tests/verify/.probe-tmp"
cp "$REPO/tests/verify/FROZEN" "$tmp/FROZEN.bak"
cat /tmp/frozen-probe >> "$REPO/tests/verify/FROZEN"
if "$REPO/tests/verify/verify-frozen.sh" >/dev/null 2>&1; then r=pass; else r=deny; fi
chk "unveraendert -> besteht" "$r" pass
printf 'y\n' >> "$REPO/tests/verify/.probe-tmp"
if "$REPO/tests/verify/verify-frozen.sh" >/dev/null 2>&1; then r=pass; else r=deny; fi
chk "veraendert   -> bricht ab" "$r" deny
cp "$tmp/FROZEN.bak" "$REPO/tests/verify/FROZEN"; rm -f "$REPO/tests/verify/.probe-tmp" /tmp/frozen-probe

echo
if [[ $fail -eq 0 ]]; then echo "T-0.0: alle Pruefungen bestanden."; else echo "T-0.0: FEHLGESCHLAGEN."; fi
exit $fail
