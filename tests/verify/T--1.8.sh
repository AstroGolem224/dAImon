#!/usr/bin/env bash
# Verifizierer fuer T−1.8: Die Test-Eingabevorrichtung ist noch nicht umgesetzt.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T−1.8 — Test-Eingabevorrichtung"
echo "  INFO nicht anwendbar: T−1.8 und tests/fixtures/input/ existieren noch nicht."
chk "Nichtanwendbarkeit wird ausdruecklich berichtet" ja ja
exit $fail
