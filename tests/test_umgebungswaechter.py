"""Der Waechter fuer fremde Schluessel in der Umgebung der Dienste.

Er ersetzt `test_egress.py::test_mind_traegt_keinen_fremden_schluessel_in_der_
umgebung` (T-6.9, 14.08.). Der alte Waechter kannte eine von 23 Units und hatte
drei stille Ausgaenge; genau deshalb ist derselbe Befund dreimal aufgetreten:
14.08. (ELEVENLABS/MISTRAL im Mind), 29.08. (NVIDIA, in FROZEN-HALTE.md
ausdruecklich als "Befund am Betrieb, kein Pruefstandsfehler" vermerkt),
01.09. (NVIDIA/OPENROUTER in Mind und Plan). Ueberliefert statt behoben.

Geprueft werden NAMEN, nie Werte. Ein Pruefstand, der ein Geheimnis in seine
eigene Fehlermeldung schreibt, ist der Angriff, den er sucht -- die Fehlerpfade
hier tragen darum ausschliesslich Variablennamen und Unit-Namen.

Drei Entscheidungen, die diesen Waechter vom alten unterscheiden:

1. **Alle Units, nicht der Mind.** Die Quelle ist der systemd-Benutzermanager;
   jede Unit erbt aus derselben Quelle. `daimon-cli-broker.service` ist dabei
   die interessanteste: sie hat kein `RestrictAddressFamilies=` und startet
   `claude` als Kind, das die Umgebung ein zweites Mal erbt.

2. **Ein nicht laufender Dienst ist kein Skip.** Ein Skip ist in der Bilanz
   gruen, und "nichts gefunden" waere dann nicht von "nicht gemessen" zu
   unterscheiden -- der teuerste wiederkehrende Fehler dieses Repos. Er ist
   aber auch nicht rot: dass `daimon-face` gerade nicht laeuft, ist kein
   Befund ueber seine Umgebung. Er ist ein **ausgewiesenes "nicht gemessen"**:
   eine `UserWarning` mit der vollstaendigen Liste, die in der pytest-Bilanz
   als Warnung stehenbleibt und im Warnungsbericht namentlich auftaucht.
   Rot wird der Lauf, wenn **gar nichts** messbar war -- ein gruener Lauf ohne
   eine einzige Messung ist der Fehlerzustand, nicht der Normalfall.

3. **Positivkontrolle im selben Lauf.** `test_der_waechter_saehe_einen_
   gesetzten_namen` startet zwei Wegwerf-Units mit einem ERFUNDENEN Namen
   (`TESTFALL_API_KEY`, Wert `nicht-echt`) und belegt beides: der Waechter
   sieht ihn, und `UnsetEnvironment=` nimmt ihn wieder weg. Ohne die erste
   Haelfte ist "keine Funde" wertlos; ohne die zweite ist die Sperrliste aus
   Teil 2 eine Behauptung.
"""

import pathlib
import re
import shutil
import subprocess
import warnings

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
UNIT_DIR = REPO / "config/systemd"
UNITS = sorted(p.name for p in UNIT_DIR.glob("*.service"))

# Dasselbe Suchmuster wie seit T-6.9, absichtlich breiter als jede Sperrliste.
MUSTER = re.compile(r"KEY|TOKEN|SECRET|ANTHROPIC")

# Die Sperrliste, die in JEDER Unit unter config/systemd/ stehen muss. Sie ist
# ein Pflaster: eine Denylist kann das Muster oben per Konstruktion nie
# einholen. Sie steht hier EINMAL, damit 23 Kopien in den Units nicht 23
# Fassungen derselben Regel werden.
SPERRLISTE = frozenset("""
ANTHROPIC_API_KEY ELEVENLABS_API_KEY MISTRAL_API_KEY
OPENAI_API_KEY GEMINI_API_KEY GOOGLE_API_KEY HF_TOKEN
GITHUB_TOKEN GH_TOKEN AWS_SECRET_ACCESS_KEY
NVIDIA_API_KEY OPENROUTER_API_KEY
""".split())


def _unset_namen(unit: str) -> set[str]:
    namen: set[str] = set()
    for zeile in (UNIT_DIR / unit).read_text().splitlines():
        if zeile.startswith("UnsetEnvironment="):
            namen.update(zeile.split("=", 1)[1].split())
    return namen


def _instanzen(datei: str) -> list[str]:
    """Namen, unter denen systemd die Unit kennt.

    Eine Template-Unit (`daimon-gpu@.service`) laeuft nur als Instanz; ohne
    laufende Instanz gibt es nichts zu messen.
    """
    if "@." not in datei:
        return [datei]
    roh = subprocess.run(
        ["systemctl", "--user", "list-units", "--all", "--plain",
         "--no-legend", datei.replace("@.", "@*.")],
        capture_output=True, text=True, timeout=10)
    return [z.split()[0] for z in roh.stdout.splitlines() if z.split()]


def _hauptpid(unit: str) -> int:
    roh = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"],
        capture_output=True, text=True, timeout=10)
    return int((roh.stdout or "0").strip() or 0)


def _verdaechtige_namen(pid: int) -> list[str]:
    """Verdaechtige NAMEN aus /proc/<pid>/environ. `OSError` faellt durch."""
    roh = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
    namen = (v.split(b"=", 1)[0].decode("utf-8", "replace")
             for v in roh.split(b"\0") if b"=" in v)
    return sorted(n for n in namen if MUSTER.search(n.upper()))


def test_es_gibt_ueberhaupt_units_zu_pruefen():
    """Positivkontrolle fuer die Datei-Seite: ein leeres Glob-Ergebnis liesse
    jede Parametrisierung unten spurlos verschwinden."""
    assert len(UNITS) >= 20, UNITS


@pytest.mark.parametrize("unit", UNITS)
def test_jede_unit_sperrt_die_gemessenen_namen(unit):
    assert SPERRLISTE <= _unset_namen(unit), sorted(
        SPERRLISTE - _unset_namen(unit))


@pytest.mark.parametrize("unit", UNITS)
def test_keine_unit_fuehrt_eine_eigene_fassung_der_sperrliste(unit):
    """Zwei Fassungen einer Regel sind eine Regel und eine Attrappe.

    `mind` und `plan` waren am 01.09. zeichengleich -- und trugen darum auch
    dieselbe Luecke. Systemd kennt keine gemeinsame Fassung ohne Drop-in
    ausserhalb des Repos; die 23 Kopien bleiben, und diese Pruefung ist der
    Grund, warum das tragbar ist: eine Kopie, die driftet, wird rot.

    Proxy-Namen (`HTTP_PROXY` und Verwandte in `egress`/`lokal-broker`) faengt
    das Muster nicht -- sie sind eine andere Zusage und duerfen abweichen.
    """
    geheim = {n for n in _unset_namen(unit) if MUSTER.search(n.upper())}
    assert geheim == set(SPERRLISTE), {
        "zuviel": sorted(geheim - SPERRLISTE),
        "zuwenig": sorted(SPERRLISTE - geheim)}


def test_kein_laufender_dienst_traegt_einen_fremden_schluessel():
    if shutil.which("systemctl") is None:
        pytest.fail("kein systemd -- NICHT GEMESSEN. Ein Skip waere hier "
                    "gruen und damit die falsche Antwort.")
    funde: dict[str, list[str]] = {}
    gemessen: list[str] = []
    ungemessen: list[str] = []
    for datei in UNITS:
        instanzen = _instanzen(datei)
        if not instanzen:
            ungemessen.append(f"{datei}: keine Instanz laeuft")
            continue
        for unit in instanzen:
            pid = _hauptpid(unit)
            if pid <= 0:
                ungemessen.append(f"{unit}: laeuft nicht")
                continue
            try:
                verdacht = _verdaechtige_namen(pid)
            except OSError as exc:
                # z.B. der Hub: er setzt selbst PR_SET_DUMPABLE=0, danach
                # gehoert /proc/<pid>/environ root. Haertung, kein Fehler --
                # aber eben auch keine Messung.
                ungemessen.append(f"{unit}: environ nicht lesbar "
                                  f"({exc.strerror})")
                continue
            gemessen.append(unit)
            if verdacht:
                funde[unit] = verdacht
    if ungemessen:
        warnings.warn("NICHT GEMESSEN (" + str(len(ungemessen)) + " von "
                      + str(len(ungemessen) + len(gemessen)) + "): "
                      + "; ".join(ungemessen), stacklevel=1)
    assert gemessen, ("NICHTS GEMESSEN -- kein einziger Dienst lief oder war "
                      "lesbar. Ein gruener Lauf waere hier eine Luege: "
                      + "; ".join(ungemessen))
    assert funde == {}, funde


def _wegwerf_dienst(name: str, *eigenschaften: str) -> int:
    """Startet eine transiente Unit mit `TESTFALL_API_KEY` und gibt ihre PID.

    Erfundener Name, Wert `nicht-echt`: die Positivkontrolle fasst kein
    echtes Geheimnis an. Der Name traegt bewusst kein `daimon-`, damit die
    Prozesszaehlungen der eingefrorenen Pruefstaende ihn nicht mitzaehlen.
    """
    subprocess.run(["systemctl", "--user", "reset-failed", name],
                   capture_output=True, timeout=10)
    ruf = subprocess.run(
        ["systemd-run", "--user", "--quiet", "--unit", name,
         "--property=Type=exec", "--setenv=TESTFALL_API_KEY=nicht-echt",
         *eigenschaften, shutil.which("sleep") or "/usr/bin/sleep", "20"],
        capture_output=True, text=True, timeout=30)
    assert ruf.returncode == 0, ruf.stderr
    pid = _hauptpid(name)
    assert pid > 0, f"{name} hat keine MainPID"
    return pid


def test_der_waechter_saehe_einen_gesetzten_namen():
    """Positivkontrolle, beide Haelften.

    Ohne sie ist "keine Funde" oben nicht von "nicht gemessen" zu
    unterscheiden -- und die Sperrliste aus den Units waere unbelegt.
    """
    if shutil.which("systemd-run") is None:
        pytest.fail("kein systemd-run -- NICHT GEMESSEN.")
    offen = ("waechter-positivkontrolle-sichtbar.service",
             "waechter-positivkontrolle-gesperrt.service")
    try:
        sichtbar = _verdaechtige_namen(_wegwerf_dienst(offen[0]))
        assert "TESTFALL_API_KEY" in sichtbar, sichtbar

        gesperrt = _verdaechtige_namen(_wegwerf_dienst(
            offen[1], "--property=UnsetEnvironment=TESTFALL_API_KEY"))
        assert "TESTFALL_API_KEY" not in gesperrt, gesperrt
    finally:
        for name in offen:
            subprocess.run(["systemctl", "--user", "stop", name],
                           capture_output=True, timeout=30)
            subprocess.run(["systemctl", "--user", "reset-failed", name],
                           capture_output=True, timeout=10)
