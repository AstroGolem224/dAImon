#!/usr/bin/env bash
# Blinder Verifizierer fuer T-3.10: Persona-Lader.
#
# Aufruf:
#   tests/verify/T-3.10.sh
#   DAIMON_FIXTURE=tests/fixtures/known-good/T-3.10 tests/verify/T-3.10.sh
#
# Im Fixture-Modus bleibt K11 bewusst aus: Die beiden eingefrorenen, jeweils
# rund vier Minuten langen Integrationspruefungen messen den Arbeitsbaum und
# werden nicht fuer jede Mutante vorgetaeuscht oder vervielfacht.
set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$HIER/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
TARGET="$(cd "$TARGET" || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
RT="$(mktemp -d)"
trap 'rm -rf -- "$RT"' EXIT INT TERM

echo "T-3.10 — Persona-Pruefstand (blind aus Vertrag §2/§3)"
echo "  Baum: $TARGET"
echo "  Interpreter: $PY"
if [[ -n "${DAIMON_FIXTURE:-}" ]]; then
  echo "  Modus: FIXTURE (K11 wird nur im Arbeitsbaumlauf gemessen)"
else
  echo "  Modus: ARBEITSBAUM"
fi

PYTHONPATH="$TARGET" PYTHONDONTWRITEBYTECODE=1 \
  "$PY" -B -P - "$REPO" "$TARGET" "$RT" <<'PY'
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO, TARGET, RT = map(Path, sys.argv[1:4])


class Pruefstand:
    def __init__(self) -> None:
        self.n = defaultdict(int)
        self.rot = defaultdict(int)
        self.fail = False

    def check(self, kriterium: str, name: str, ist, soll) -> None:
        # Jede Zusage hat einen eigenen Aufruf; kein && tilgt Folgepruefungen.
        self.n[kriterium] += 1
        if ist == soll:
            print(f"  ok   [{kriterium}] {name}")
        else:
            print(f"  FAIL [{kriterium}] {name} (erwartet {soll!r}, war {ist!r})")
            self.rot[kriterium] += 1
            self.fail = True


P = Pruefstand()

print("\n--- Voraussetzungen und Herkunft des Prueflings ---")
modulpfad = TARGET / "daimon/mind/persona.py"
P.check("V", "Persona-Modul liegt im geprueften Baum", modulpfad.is_file(), True)
P.check("V", "Config-Modul liegt im geprueften Baum",
        (TARGET / "daimon/common/config.py").is_file(), True)
if P.fail:
    print("\nT-3.10: FEHLGESCHLAGEN — Voraussetzungen fehlen, nichts gemessen.")
    raise SystemExit(1)

from daimon.common.config import Config
from daimon.mind.persona import Persona, PersonaFehler, lade
import daimon.mind.persona as persona_modul

geladen = Path(persona_modul.__file__).resolve()
P.check("V", "geladenes Persona-Modul stammt exakt aus dem geprueften Baum",
        geladen, modulpfad.resolve())
P.check("V", "oeffentliche Klasse Persona ist eine Klasse", isinstance(Persona, type), True)
P.check("V", "PersonaFehler ist eine Ausnahme", issubclass(PersonaFehler, Exception), True)


def cfg(config_dir: Path, name: str, extra: dict | None = None) -> Config:
    daten = {"persona": {"name": name}}
    if extra:
        for abschnitt, werte in extra.items():
            daten.setdefault(abschnitt, {}).update(werte)
    return Config(data=daten, config_dir=config_dir,
                  state_dir=RT / "state", runtime_dir=RT / "run")


def xdg_dir(stamm: Path) -> Path:
    pfad = stamm / "daimon"
    (pfad / "persona").mkdir(parents=True, exist_ok=True)
    return pfad


def schreiben(config_dir: Path, name: str, text: str) -> Path:
    datei = config_dir / "persona" / f"{name.lower()}.toml"
    datei.write_text(text, encoding="utf-8")
    return datei


MINIMAL = '''name = "Positiv"
speech_threshold = "helpful"
traits = []
system_prompt = "Gueltiger Positivkanarienvogel mit Umlaut: grüß dich."
'''


def positiv(kriterium: str, stamm: Path, kennung: str) -> None:
    ort = xdg_dir(stamm / f"positiv-{kennung}")
    schreiben(ort, "positiv", MINIMAL)
    try:
        p = lade(cfg(ort, "PoSiTiV"))
        ok = p.name == "Positiv" and p.prompt().startswith("Gueltiger Positivkanarienvogel")
    except Exception:
        ok = False
    P.check(kriterium, f"{kennung}: gueltige Datei laedt (POSITIVKONTROLLE)", ok, True)


def ablehnung(kriterium: str, stamm: Path, kennung: str, text: str,
              tokens: tuple[str, ...], name: str = "kaputt") -> None:
    ort = xdg_dir(stamm / f"negativ-{kennung}")
    datei = schreiben(ort, name, text)
    try:
        lade(cfg(ort, name))
        meldung = ""
        typ_ok = False
    except Exception as exc:
        meldung = str(exc)
        typ_ok = isinstance(exc, PersonaFehler)
    P.check(kriterium, f"{kennung}: genau PersonaFehler", typ_ok, True)
    P.check(kriterium, f"{kennung}: Meldung nennt die Datei", str(datei) in meldung, True)
    for token in tokens:
        P.check(kriterium, f"{kennung}: Meldung nennt {token!r}", token.lower() in meldung.lower(), True)
    positiv(kriterium, stamm, kennung)


with tempfile.TemporaryDirectory(dir=RT) as tmp:
    stamm = Path(tmp)

    print("\n--- K1: vollstaendiges Format und API ---")
    ort = xdg_dir(stamm / "k1")
    voll = '''name = "Prüfpersona"
wake_words = ["HALLO PRÜFER", "LOS GEHTS"]
voice = "de_DE-pruefung-high"
speech_threshold = "chatty"
traits = ["trocken", "gründlich"]
palette = { idle = "#010203", active = "#aabbcc", alert = "#FEDCBA" }
system_prompt = """  Erste Zeile: Grüße.  
Zweite Zeile: vollständig.  """
'''
    quelle = schreiben(ort, "pruefpersona", voll)
    p = lade(cfg(ort, "PrUeFpErSoNa"))
    P.check("K1", "name gelesen", p.name, "Prüfpersona")
    P.check("K1", "wake_words als Tupel gelesen", p.wake_words,
            ("HALLO PRÜFER", "LOS GEHTS"))
    P.check("K1", "voice gelesen", p.voice, "de_DE-pruefung-high")
    P.check("K1", "speech_threshold gelesen", p.speech_threshold, "chatty")
    P.check("K1", "traits als Tupel gelesen", p.traits, ("trocken", "gründlich"))
    P.check("K1", "palette vollstaendig gelesen", p.palette,
            {"idle": "#010203", "active": "#aabbcc", "alert": "#FEDCBA"})
    P.check("K1", "system_prompt gelesen", p.system_prompt,
            "  Erste Zeile: Grüße.  \nZweite Zeile: vollständig.  ")
    P.check("K1", "quelle ist die tatsaechlich geladene Datei", p.quelle, quelle)
    P.check("K1", "lade liefert Persona", isinstance(p, Persona), True)

    print("\n--- K2: Enum mit genau vier Stufen ---")
    for schwelle in ("silent", "urgent", "helpful", "chatty"):
        o = xdg_dir(stamm / f"k2-{schwelle}")
        schreiben(o, schwelle, MINIMAL.replace('name = "Positiv"', f'name = "{schwelle}"')
                  .replace('"helpful"', f'"{schwelle}"'))
        P.check("K2", f"Stufe {schwelle} wird angenommen",
                lade(cfg(o, schwelle)).speech_threshold, schwelle)
    ablehnung("K2", stamm, "fuenfte-schwelle", MINIMAL.replace('"helpful"', '"launisch"'),
              ("speech_threshold", "launisch"))

    print("\n--- K3: Prompt zeichengenau, mit Umlauten und Zeilenumbruechen ---")
    erwartet_system = "  Erste Zeile: Grüße.  \nZweite Zeile: vollständig.  "
    erwartet_prompt = erwartet_system + "\n\nEigenschaften: trocken, gründlich"
    P.check("K3", "system_prompt bleibt zeichengenau", p.system_prompt, erwartet_system)
    P.check("K3", "prompt hat exakt Vertragstext plus Eigenschaftenzeile",
            p.prompt(), erwartet_prompt)
    leer = xdg_dir(stamm / "k3-ohne-traits")
    schreiben(leer, "ohneeigenschaften", MINIMAL.replace('name = "Positiv"',
              'name = "OhneEigenschaften"').replace("traits = []\n", ""))
    P.check("K3", "ohne traits erscheint keine leere Eigenschaftenzeile",
            lade(cfg(leer, "ohneeigenschaften")).prompt(),
            "Gueltiger Positivkanarienvogel mit Umlaut: grüß dich.")

    print("\n--- K4: Persona-Wechsel aendert Verhalten ---")
    kein_xdg = stamm / "k4-leer" / "daimon"
    ember = lade(cfg(kein_xdg, "EmBeR"))
    kiesel = lade(cfg(kein_xdg, "KIESEL"))
    P.check("K4", "Wechsel aendert Prompt", ember.prompt() != kiesel.prompt(), True)
    P.check("K4", "Kiesel hat andere Stimme", kiesel.voice != ember.voice, True)
    P.check("K4", "Kiesel-Stimme entspricht Vertrag", kiesel.voice, "de_DE-kerstin-high")
    P.check("K4", "Kiesel-Schwelle ist urgent", kiesel.speech_threshold, "urgent")

    print("\n--- K5: fehlende Persona ist sprechender Fehler ---")
    fehlt_cfg = stamm / "k5" / "daimon"
    gesucht = "gibt-es-garantiert-nicht"
    xdg_pfad = fehlt_cfg / "persona" / f"{gesucht}.toml"
    repo_pfad = TARGET / "config" / "persona" / f"{gesucht}.toml"
    try:
        lade(cfg(fehlt_cfg, gesucht.upper()))
        fehlmeldung = ""
        fehlt_typ = False
    except Exception as exc:
        fehlmeldung = str(exc)
        fehlt_typ = isinstance(exc, PersonaFehler)
    P.check("K5", "fehlende Persona wirft PersonaFehler", fehlt_typ, True)
    P.check("K5", "Meldung nennt kleingeschriebenen Suchnamen",
            gesucht in fehlmeldung.lower(), True)
    P.check("K5", "Meldung nennt XDG-Pfad", str(xdg_pfad) in fehlmeldung, True)
    P.check("K5", "Meldung nennt mitgelieferten Pfad", str(repo_pfad) in fehlmeldung, True)
    positiv("K5", stamm, "fehlende-persona")

    print("\n--- K6: XDG gewinnt ueber mitgelieferte Persona ---")
    xdg = xdg_dir(stamm / "k6")
    xdg_ember = schreiben(xdg, "ember", MINIMAL.replace('name = "Positiv"', 'name = "XDG Ember"')
                          .replace("Gueltiger Positivkanarienvogel", "XDG gewinnt sichtbar"))
    xdg_p = lade(cfg(xdg, "EMBER"))
    P.check("K6", "XDG-Inhalt wird geladen", xdg_p.name, "XDG Ember")
    P.check("K6", "quelle belegt XDG-Datei", xdg_p.quelle, xdg_ember)
    P.check("K6", "mitgelieferte Ember bleibt positive Gegenprobe", ember.name, "Ember")

    print("\n--- K7: sichtbare Rueckfallkette ---")
    k7 = xdg_dir(stamm / "k7")
    schreiben(k7, "fallback", MINIMAL.replace('name = "Positiv"', 'name = "Fallback"'))
    konfigwerte = {
        "persona": {"voice": "de_DE-aus-config"},
        "face": {"palette": {"idle": "#111111", "active": "#222222", "alert": "#333333"}},
        "ears": {"wake_words": ["CONFIG WORT"]},
    }
    aus_cfg = lade(cfg(k7, "fallback", konfigwerte))
    P.check("K7", "fehlende voice kommt tatsaechlich aus Config", aus_cfg.voice, "de_DE-aus-config")
    P.check("K7", "voice-Herkunft meldet config", aus_cfg.herkunft.get("voice"), "config")
    P.check("K7", "fehlende palette kommt tatsaechlich aus Config", aus_cfg.palette,
            konfigwerte["face"]["palette"])
    P.check("K7", "palette-Herkunft meldet config", aus_cfg.herkunft.get("palette"), "config")
    P.check("K7", "fehlende wake_words kommen tatsaechlich aus Config", aus_cfg.wake_words,
            ("CONFIG WORT",))
    P.check("K7", "wake_words-Herkunft meldet config", aus_cfg.herkunft.get("wake_words"), "config")
    aus_vorgabe = lade(cfg(k7, "fallback"))
    P.check("K7", "Code-Vorgabe voice ist eine nichtleere Zeichenkette",
            isinstance(aus_vorgabe.voice, str) and bool(aus_vorgabe.voice), True)
    P.check("K7", "voice-Herkunft meldet vorgabe", aus_vorgabe.herkunft.get("voice"), "vorgabe")
    P.check("K7", "Code-Vorgabe palette hat alle drei Schluessel",
            set(aus_vorgabe.palette), {"idle", "active", "alert"})
    P.check("K7", "palette-Herkunft meldet vorgabe", aus_vorgabe.herkunft.get("palette"), "vorgabe")
    P.check("K7", "Code-Vorgabe wake_words ist ein Tupel", isinstance(aus_vorgabe.wake_words, tuple), True)
    P.check("K7", "wake_words-Herkunft meldet vorgabe", aus_vorgabe.herkunft.get("wake_words"), "vorgabe")
    P.check("K7", "vorhandene Persona-Werte gewinnen", p.voice, "de_DE-pruefung-high")
    # Alle sieben Werte wurden oben bzw. in K1/K3 gegen den Dateiinhalt
    # gemessen. Erst danach wird die Herkunftsauskunft dagegen gehalten: Sie
    # darf kein lueckenhaftes Teilverzeichnis und keine blosse Selbstauskunft
    # sein.
    P.check("K7", "Persona-Herkunft ist fuer jedes Feld sichtbar", p.herkunft,
            {"name": "persona", "system_prompt": "persona",
             "speech_threshold": "persona", "voice": "persona",
             "traits": "persona", "wake_words": "persona",
             "palette": "persona"})

    print("\n--- K8: kaputte Dateien, jeweils mit Positivkontrolle ---")
    ablehnung("K8", stamm, "kaputtes-toml", 'name = "Kaputt"\nsystem_prompt = ???\n',
              ("toml", "line"))
    ablehnung("K8", stamm, "system-prompt-fehlt", 'name = "Kaputt"\n',
              ("system_prompt",))
    ablehnung("K8", stamm, "name-fehlt", 'system_prompt = "Text"\n', ("name",))
    ablehnung("K8", stamm, "palette-ohne-idle",
              MINIMAL + 'palette = { active = "#111111", alert = "#222222" }\n',
              ("palette", "idle"))
    ablehnung("K8", stamm, "traits-als-text", MINIMAL.replace("traits = []", 'traits = "trocken"'),
              ("traits",))
    ablehnung("K8", stamm, "wake-words-mit-zahl", MINIMAL + 'wake_words = ["HALLO", 7]\n',
              ("wake_words",))

    print("\n--- K9: Prompt ist deterministisch ---")
    stabil_1 = lade(cfg(kein_xdg, "ember")).prompt()
    os.environ["DAIMON_PERSONA_PRUEFRAUSCHEN"] = "erster-wert"
    stabil_2 = lade(cfg(kein_xdg, "ember")).prompt()
    os.environ["DAIMON_PERSONA_PRUEFRAUSCHEN"] = "zweiter-wert"
    stabil_3 = lade(cfg(kein_xdg, "ember")).prompt()
    P.check("K9", "zweimal laden ergibt denselben Prompt", stabil_1, stabil_2)
    P.check("K9", "Umgebungswechsel aendert den Prompt nicht", stabil_2, stabil_3)
    P.check("K9", "Pruefrauschen fliesst nicht in den Prompt",
            "erster-wert" in stabil_1 or "zweiter-wert" in stabil_1, False)
    P.check("K9", "heutiges Datum wird nicht an den bekannten Prompt angehaengt",
            date.today().isoformat() in p.prompt(), False)

    print("\n--- K10: zwei mitgelieferte Beispiel-Personas ---")
    ember_datei = TARGET / "config/persona/ember.toml"
    kiesel_datei = TARGET / "config/persona/kiesel.toml"
    P.check("K10", "ember.toml liegt im Repo", ember_datei.is_file(), True)
    P.check("K10", "kiesel.toml liegt im Repo", kiesel_datei.is_file(), True)
    P.check("K10", "Ember laedt aus mitgelieferter Datei", ember.quelle, ember_datei)
    P.check("K10", "Kiesel laedt aus mitgelieferter Datei", kiesel.quelle, kiesel_datei)
    P.check("K10", "beide Namen sind verschieden", ember.name != kiesel.name, True)

print("\n--- K11: eingefrorene Rueckfall-Regressionspruefungen ---")
if os.environ.get("DAIMON_FIXTURE"):
    print("  SKIP [K11] Fixture/Mutanten sind kein vollstaendiger Arbeitsbaum; keine Scheinpruefung.")
else:
    kind_env = os.environ.copy()
    kind_env.pop("DAIMON_FIXTURE", None)
    for task in ("T-3.9", "T-3.8"):
        skript = REPO / "tests/verify" / f"{task}.sh"
        rc = subprocess.run([str(skript)], cwd=REPO, env=kind_env).returncode
        P.check("K11", f"eingefrorener Pruefstand {task} bleibt vollstaendig gruen", rc, 0)

print("\n--- K12: persona.py nutzt nur stdlib und Projektcode ---")
baum = ast.parse(modulpfad.read_text(encoding="utf-8"), filename=str(modulpfad))
importe: set[str] = set()
for knoten in ast.walk(baum):
    if isinstance(knoten, ast.Import):
        importe.update(alias.name.split(".")[0] for alias in knoten.names)
    elif isinstance(knoten, ast.ImportFrom) and knoten.level == 0 and knoten.module:
        importe.add(knoten.module.split(".")[0])
fremd = sorted(i for i in importe if i not in sys.stdlib_module_names and i != "daimon")
P.check("K12", "alle absoluten Imports sind stdlib oder daimon", fremd, [])
P.check("K12", "tomllib wird fuer TOML verwendet", "tomllib" in importe, True)

print("\n--- Abrechnung je Kriterium ---")
gesamt = gesamt_rot = 0
for k in ["V"] + [f"K{i}" for i in range(1, 13)]:
    n, rot = P.n[k], P.rot[k]
    gesamt += n
    gesamt_rot += rot
    zusatz = " (im Fixture-Modus ausstehend)" if k == "K11" and n == 0 else ""
    print(f"  {k:>3}: {n:3d} Pruefungen, {rot} rot{zusatz}")
print(f"  gesamt: {gesamt} Pruefungen, {gesamt_rot} rot")

print("\n--- Auslegungen und Grenzen ---")
print("  (1) Konkrete Code-Vorgaben fuer optionale Felder sind im Vertrag nicht festgelegt;")
print("      geprueft werden gueltiger Typ/Wert, Verhalten und herkunft='vorgabe'.")
print("  (2) Fuehrende/abschliessende Leerzeilen des system_prompt duerfen entfallen;")
print("      alle inhaltlichen Zeichen und Zeilenumbrueche muessen erhalten bleiben.")
print("  (3) K11 laeuft nur gegen den Arbeitsbaum vollstaendig; meta.sh vervielfacht die")
print("      zwei langen eingefrorenen Integrationslaeufe nicht fuer Gut-Muster/Mutanten.")
print("  (4) Nicht geprueft werden Verbraucher von Schwelle, Wake-Words und Palette;")
print("      sie sind laut Vertrag ausdruecklich ausserhalb von T-3.10.")

if P.fail:
    print("\nT-3.10: FEHLGESCHLAGEN")
    raise SystemExit(1)
print("\nT-3.10: gruen — alle im aktuellen Modus messbaren Kriterien sind einzeln abgerechnet.")
PY
