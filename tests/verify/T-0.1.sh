#!/usr/bin/env bash
# Verifizierer fuer T-0.1: Repo-Struktur und Werkzeugkette.
#
# Jedes Akzeptanzkriterium einzeln, keine &&-Verkettung -- man muss sehen,
# welches von zehn gerissen ist und nicht nur, dass irgendeines gerissen ist.
#
# Die Python-Version ist nicht frei gewaehlt: T-1.2 hat native sm_120a-Cubins
# ausschliesslich fuer das cp312-Wheel von onnxruntime-gpu 1.27.0 belegt
# (cuobjdump-Nachweis in spikes/ort/results.json). Eine andere Nebenversion
# zieht ein anderes Wheel und damit einen ungeprueften Zustand nach sich.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$REPO/.venv"
WANT_PY="3.12"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.1 — Repo-Struktur und Werkzeugkette"

# --- venv ------------------------------------------------------------------
if [[ -x "$VENV/bin/python" ]]; then have_venv=ja; else have_venv=nein; fi
chk "venv unter .venv/ existiert" "$have_venv" ja

py_ver=""
if [[ "$have_venv" == ja ]]; then
  py_ver="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
fi
chk "venv laeuft auf Python $WANT_PY (aus T-1.2)" "${py_ver:-keine}" "$WANT_PY"

# --- Paket ist installiert und importierbar --------------------------------
importable=nein
if [[ "$have_venv" == ja ]]; then
  "$VENV/bin/python" -c 'import daimon' >/dev/null 2>&1 && importable=ja
fi
chk "Paket 'daimon' ist importierbar" "$importable" ja

# Editierbar installiert heisst: der Import zeigt auf den Arbeitsbaum, nicht
# auf eine Kopie in site-packages. Sonst editiert man ins Leere.
editable=nein
if [[ "$importable" == ja ]]; then
  loc="$("$VENV/bin/python" -c 'import daimon,os; print(os.path.dirname(daimon.__file__))' 2>/dev/null)"
  [[ "$loc" == "$REPO/daimon" ]] && editable=ja
fi
chk "Installation ist editierbar (zeigt in den Arbeitsbaum)" "$editable" ja

# --- pytest ----------------------------------------------------------------
# Exit 5 heisst "keine Tests gefunden" und ist hier zulaessig: T-0.1 legt die
# Werkzeugkette an, die Tests kommen erst danach. Alles andere ausser 0 nicht.
collect=nein
if [[ "$have_venv" == ja ]]; then
  ( cd "$REPO" && "$VENV/bin/python" -m pytest --collect-only -q ) >/dev/null 2>&1
  rc=$?
  [[ $rc -eq 0 || $rc -eq 5 ]] && collect=ja
fi
chk "pytest --collect-only endet mit 0 oder 5" "$collect" ja

# --- pyproject -------------------------------------------------------------
chk "pyproject.toml existiert" "$([[ -f "$REPO/pyproject.toml" ]] && echo ja || echo nein)" ja
chk "dev-Extra ist deklariert" \
  "$(grep -qE '^\s*dev\s*=' "$REPO/pyproject.toml" 2>/dev/null && echo ja || echo nein)" ja
# T-3.8 verlangt onnxruntime-gpu nackt gepinnt und KEINE nvidia-*-pip-Pakete;
# die Pin gehoert schon hier hin, damit sie nicht spaeter vergessen wird.
chk "onnxruntime-gpu ist exakt gepinnt" \
  "$(grep -qE 'onnxruntime-gpu\s*==\s*1\.27\.0' "$REPO/pyproject.toml" 2>/dev/null && echo ja || echo nein)" ja

# --- Verzeichnisbaum -------------------------------------------------------
# Abgeleitet aus den Dateipfaden, die der Plan in den Tasks nennt.
DIRS=(
  daimon daimon/audit daimon/auth daimon/brokers daimon/brokers/dbus
  daimon/brokers/egress daimon/brokers/exec daimon/brokers/fs
  daimon/brokers/input daimon/common daimon/context daimon/ears daimon/eyes
  daimon/face daimon/gpu daimon/hookbridge daimon/hub daimon/mind
  face face/assets kwin-script config config/systemd
  tests tests/verify tests/mutants tests/fixtures tests/evidence
)
missing=""
for d in "${DIRS[@]}"; do
  [[ -d "$REPO/$d" ]] || missing="$missing $d"
done
chk "alle Konventions-Verzeichnisse existieren${missing:+ (fehlt:$missing)}" \
  "$([[ -z "$missing" ]] && echo ja || echo nein)" ja

# Ein Verzeichnis ohne __init__.py ist zwar importierbar, aber als
# Namespace-Paket -- das verschleiert Tippfehler in Importpfaden.
no_init=""
for d in "${DIRS[@]}"; do
  [[ "$d" == daimon* ]] || continue
  [[ -f "$REPO/$d/__init__.py" ]] || no_init="$no_init $d"
done
chk "jedes daimon-Unterpaket hat __init__.py${no_init:+ (fehlt:$no_init)}" \
  "$([[ -z "$no_init" ]] && echo ja || echo nein)" ja

# --- Git -------------------------------------------------------------------
chk "Git-Repo mit mindestens einem Commit" \
  "$(git -C "$REPO" rev-parse HEAD >/dev/null 2>&1 && echo ja || echo nein)" ja
# .venv und Modelle duerfen nicht in die Historie geraten.
chk ".venv ist ignoriert" \
  "$(git -C "$REPO" check-ignore -q .venv 2>/dev/null && echo ja || echo nein)" ja

exit $fail
