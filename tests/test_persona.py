"""T-3.10 — Persona-Lader.

Geprueft wird: das Format, die vier Stufen, die Rueckfallkette samt ihrer
Herkunftsauskunft, der Vorrang der eigenen Datei, und dass der Prompt den
Charaktertext WOERTLICH traegt und nichts Wechselndes.

Nicht geprueft: dass ein API-Aufruf den Prompt wirklich mitschickt -- das ist
T-3.11. Und die Zusage, dass die eingefrorenen Pruefstaende von T-3.9 und T-3.8
nach diesem Task noch gruen sind (Kriterium 11): die laufen als Skript, nicht als
Unittest.
"""

import pathlib

import pytest

from daimon.common.config import Config
from daimon.mind.persona import (MITGELIEFERT, PALETTENSCHLUESSEL, Persona,
                                 PersonaFehler, SCHWELLEN, lade)

REPO = pathlib.Path(__file__).resolve().parents[1]
VOLLSTAENDIG = """
name = "Pruefling"
wake_words = ["HEY PRUEFLING"]
voice = "de_DE-thorsten-high"
speech_threshold = "chatty"
traits = ["knapp", "trocken"]
palette = { idle = "#111111", active = "#222222", alert = "#333333" }
system_prompt = \"\"\"
Erste Zeile mit Umlauten: Aenderung, Uebergabe, gruen.
Zweite Zeile.
\"\"\"
"""


def schreibe(dir_: pathlib.Path, name: str, inhalt: str) -> pathlib.Path:
    (dir_ / "persona").mkdir(parents=True, exist_ok=True)
    p = dir_ / "persona" / f"{name}.toml"
    p.write_text(inhalt, encoding="utf-8")
    return p


def cfg_mit(tmp_path, name="Pruefling", **daten):
    """Config, deren `config_dir` auf ein Temp-Verzeichnis zeigt."""
    grund = {"persona": {"name": name}}
    grund.update(daten)
    return Config(data=grund, config_dir=tmp_path)


# --------------------------------------------------------------------------
# Kriterium 1 und 12: das Format, reines stdlib
# --------------------------------------------------------------------------

def test_alle_felder_werden_gelesen(tmp_path):
    schreibe(tmp_path, "pruefling", VOLLSTAENDIG)
    p = lade(cfg_mit(tmp_path))
    assert isinstance(p, Persona)
    assert p.name == "Pruefling"
    assert p.wake_words == ("HEY PRUEFLING",)
    assert p.voice == "de_DE-thorsten-high"
    assert p.speech_threshold == "chatty"
    assert p.traits == ("knapp", "trocken")
    assert p.palette == {"idle": "#111111", "active": "#222222",
                         "alert": "#333333"}
    assert "Aenderung" in p.system_prompt


def test_persona_ist_unveraenderlich(tmp_path):
    # Ein Charakter, den ein Aufrufer zur Laufzeit umschreiben kann, ist keiner.
    schreibe(tmp_path, "pruefling", VOLLSTAENDIG)
    p = lade(cfg_mit(tmp_path))
    with pytest.raises(Exception):
        p.name = "anders"        # frozen dataclass


# --------------------------------------------------------------------------
# Kriterium 2: vier Stufen, kein stiller Rueckfall
# --------------------------------------------------------------------------

def test_es_gibt_genau_vier_stufen():
    assert SCHWELLEN == ("silent", "urgent", "helpful", "chatty")


@pytest.mark.parametrize("stufe", SCHWELLEN)
def test_jede_stufe_laedt(tmp_path, stufe):
    schreibe(tmp_path, "pruefling",
             VOLLSTAENDIG.replace('"chatty"', f'"{stufe}"'))
    assert lade(cfg_mit(tmp_path)).speech_threshold == stufe


def test_unbekannte_stufe_ist_ein_fehler_kein_rueckfall(tmp_path):
    schreibe(tmp_path, "pruefling", VOLLSTAENDIG.replace('"chatty"', '"laut"'))
    with pytest.raises(PersonaFehler) as exc:
        lade(cfg_mit(tmp_path))
    # Die Meldung muss den falschen Wert UND die erlaubten nennen -- sonst
    # sucht der Leser die Liste im Quelltext.
    assert "laut" in str(exc.value)
    for stufe in SCHWELLEN:
        assert stufe in str(exc.value)


# --------------------------------------------------------------------------
# Kriterium 3 und 9: der Prompt traegt den Text woertlich und nichts Wechselndes
# --------------------------------------------------------------------------

def test_system_prompt_steht_woertlich_im_prompt(tmp_path):
    schreibe(tmp_path, "pruefling", VOLLSTAENDIG)
    p = lade(cfg_mit(tmp_path))
    # Zeichengenau, inklusive Zeilenumbruch und Umlauten -- kein `in` auf eine
    # gekuerzte Fassung.
    assert p.system_prompt in p.prompt()
    assert "Erste Zeile mit Umlauten: Aenderung, Uebergabe, gruen." in p.prompt()
    assert "Zweite Zeile." in p.prompt()


def test_eigenschaften_stehen_als_eine_zeile_dahinter(tmp_path):
    schreibe(tmp_path, "pruefling", VOLLSTAENDIG)
    p = lade(cfg_mit(tmp_path))
    assert p.prompt().endswith("Eigenschaften: knapp, trocken")


def test_ohne_traits_keine_leere_eigenschaftszeile(tmp_path):
    ohne = "\n".join(z for z in VOLLSTAENDIG.splitlines()
                     if not z.startswith("traits"))
    schreibe(tmp_path, "pruefling", ohne)
    p = lade(cfg_mit(tmp_path))
    assert p.traits == ()
    assert "Eigenschaften" not in p.prompt()
    assert p.prompt() == p.system_prompt


def test_der_prompt_ist_zweimal_derselbe(tmp_path):
    # Kein Zeitstempel, kein Zufall, keine Umgebungsvariable: ein Prompt, der
    # sich aendert, ist nicht cachebar und macht jede Messung unvergleichbar.
    schreibe(tmp_path, "pruefling", VOLLSTAENDIG)
    a = lade(cfg_mit(tmp_path)).prompt()
    b = lade(cfg_mit(tmp_path)).prompt()
    assert a == b
    for verdaechtig in ("2026", "Uhr", ":", "T-3."):
        if verdaechtig == ":":
            continue
        assert verdaechtig not in a.replace("Umlauten:", "").replace(
            "Eigenschaften:", "")


# --------------------------------------------------------------------------
# Kriterium 4 und 10: zwei Personas, und der Wechsel wirkt
# --------------------------------------------------------------------------

def test_beide_mitgelieferten_personas_laden():
    leer = pathlib.Path("/gibt/es/nicht")
    for name in ("Ember", "Kiesel"):
        p = lade(Config(data={"persona": {"name": name}}, config_dir=leer))
        assert p.name == name
        assert p.quelle == MITGELIEFERT / f"{name.lower()}.toml"
        assert p.system_prompt and p.speech_threshold in SCHWELLEN


def test_der_wechsel_aendert_prompt_stimme_und_schwelle():
    leer = pathlib.Path("/gibt/es/nicht")
    e = lade(Config(data={"persona": {"name": "Ember"}}, config_dir=leer))
    k = lade(Config(data={"persona": {"name": "Kiesel"}}, config_dir=leer))
    assert e.prompt() != k.prompt()
    assert e.voice != k.voice
    assert e.speech_threshold != k.speech_threshold
    assert e.traits != k.traits
    assert e.palette != k.palette


def test_der_name_wird_kleingeschrieben_gesucht(tmp_path):
    # `name = "Ember"` findet `ember.toml`. Sonst suchte ein grossgeschriebener
    # Name eine Datei, die niemand so anlegt.
    schreibe(tmp_path, "grossklein", VOLLSTAENDIG)
    p = lade(cfg_mit(tmp_path, name="GrossKlein"))
    assert p.quelle.name == "grossklein.toml"


# --------------------------------------------------------------------------
# Kriterium 5: fehlende Persona ist ein Fehler mit sprechender Meldung
# --------------------------------------------------------------------------

def test_fehlende_persona_nennt_beide_pfade_und_den_namen(tmp_path):
    with pytest.raises(PersonaFehler) as exc:
        lade(cfg_mit(tmp_path, name="Niemand"))
    meldung = str(exc.value)
    assert "Niemand" in meldung
    assert str(tmp_path / "persona" / "niemand.toml") in meldung
    assert str(MITGELIEFERT / "niemand.toml") in meldung


def test_ohne_persona_name_in_der_konfiguration_wird_geworfen(tmp_path):
    with pytest.raises(PersonaFehler) as exc:
        lade(Config(data={}, config_dir=tmp_path))
    assert "persona.name" in str(exc.value)


# --------------------------------------------------------------------------
# Kriterium 6: die eigene Datei gewinnt
# --------------------------------------------------------------------------

def test_xdg_gewinnt_ueber_mitgeliefert(tmp_path):
    # Gleicher Name wie eine mitgelieferte Persona, anderer Inhalt.
    eigen = VOLLSTAENDIG.replace('name = "Pruefling"', 'name = "Ember"')
    eigen = eigen.replace("Erste Zeile mit Umlauten", "EIGENE FASSUNG")
    schreibe(tmp_path, "ember", eigen)
    p = lade(cfg_mit(tmp_path, name="Ember"))
    assert p.quelle == tmp_path / "persona" / "ember.toml"
    assert "EIGENE FASSUNG" in p.system_prompt
    assert "Glut-Geist" not in p.system_prompt


# --------------------------------------------------------------------------
# Kriterium 7: die Rueckfallkette, gegen das Verhalten geprueft
# --------------------------------------------------------------------------

def ohne_feld(feld: str) -> str:
    return "\n".join(z for z in VOLLSTAENDIG.splitlines()
                     if not z.startswith(feld))


def test_voice_faellt_auf_die_konfiguration_zurueck(tmp_path):
    schreibe(tmp_path, "pruefling", ohne_feld("voice"))
    p = lade(cfg_mit(tmp_path, persona={"name": "Pruefling",
                                        "voice": "de_DE-kerstin-high"}))
    assert p.voice == "de_DE-kerstin-high"
    # Die Herkunftsauskunft ist eine Selbstauskunft -- geprueft wird sie GEGEN
    # das Verhalten: der Wert kommt aus der Konfiguration, also muss sie das
    # sagen.
    assert p.herkunft["voice"] == "config"


def test_voice_faellt_zuletzt_auf_die_vorgabe_zurueck(tmp_path):
    schreibe(tmp_path, "pruefling", ohne_feld("voice"))
    p = lade(cfg_mit(tmp_path))
    assert p.voice == "de_DE-thorsten-high"
    assert p.herkunft["voice"] == "vorgabe"


def test_palette_faellt_auf_face_palette_zurueck(tmp_path):
    schreibe(tmp_path, "pruefling", ohne_feld("palette"))
    farben = {"idle": "#aaaaaa", "active": "#bbbbbb", "alert": "#cccccc"}
    p = lade(cfg_mit(tmp_path, face={"palette": farben}))
    assert p.palette == farben
    assert p.herkunft["palette"] == "config"


def test_wake_words_bleiben_leer_statt_erfunden(tmp_path):
    # T−1.1 hat das Wake-Word auf Plan C gestellt: es gibt in Phase 3 keins.
    # Eine erfundene Vorgabe waere eine behauptete Faehigkeit.
    schreibe(tmp_path, "pruefling", ohne_feld("wake_words"))
    p = lade(cfg_mit(tmp_path))
    assert p.wake_words == ()
    assert p.herkunft["wake_words"] == "vorgabe"


def test_herkunft_sagt_persona_wenn_die_datei_den_wert_traegt(tmp_path):
    schreibe(tmp_path, "pruefling", VOLLSTAENDIG)
    p = lade(cfg_mit(tmp_path, persona={"name": "Pruefling",
                                        "voice": "andere-stimme"},
                     face={"palette": {"idle": "#0", "active": "#0",
                                       "alert": "#0"}}))
    # Die Datei traegt beide Werte -- die Konfiguration darf sie NICHT
    # ueberschreiben, sonst waere die Reihenfolge umgekehrt.
    assert p.voice == "de_DE-thorsten-high"
    assert p.palette["idle"] == "#111111"
    assert p.herkunft["voice"] == "persona"
    assert p.herkunft["palette"] == "persona"


# --------------------------------------------------------------------------
# Kriterium 8: kaputte Dateien sind Fehler mit Ort
# --------------------------------------------------------------------------

def test_ungueltiges_toml_nennt_die_datei(tmp_path):
    pfad = schreibe(tmp_path, "pruefling", 'name = "x"\nkaputt ==\n')
    with pytest.raises(PersonaFehler) as exc:
        lade(cfg_mit(tmp_path))
    assert str(pfad) in str(exc.value)


@pytest.mark.parametrize("feld", ["name", "system_prompt", "speech_threshold"])
def test_fehlendes_pflichtfeld_wird_benannt(tmp_path, feld):
    inhalt = VOLLSTAENDIG
    if feld == "system_prompt":
        inhalt = inhalt.split("system_prompt")[0]
    else:
        inhalt = "\n".join(z for z in inhalt.splitlines()
                           if not z.startswith(feld))
    schreibe(tmp_path, "pruefling", inhalt)
    with pytest.raises(PersonaFehler) as exc:
        lade(cfg_mit(tmp_path))
    assert feld in str(exc.value)


def test_palette_ohne_idle_wird_abgelehnt(tmp_path):
    schreibe(tmp_path, "pruefling", VOLLSTAENDIG.replace(
        'palette = { idle = "#111111", active = "#222222", alert = "#333333" }',
        'palette = { active = "#222222", alert = "#333333" }'))
    with pytest.raises(PersonaFehler) as exc:
        lade(cfg_mit(tmp_path))
    assert "idle" in str(exc.value)


def test_traits_als_zeichenkette_wird_abgelehnt(tmp_path):
    # `traits = "knapp"` waere in Python iterierbar und ergaebe fuenf
    # Eigenschaften mit je einem Buchstaben. Genau diese stille Umdeutung soll
    # auffallen.
    schreibe(tmp_path, "pruefling",
             VOLLSTAENDIG.replace('traits = ["knapp", "trocken"]',
                                  'traits = "knapp"'))
    with pytest.raises(PersonaFehler) as exc:
        lade(cfg_mit(tmp_path))
    assert "traits" in str(exc.value)


def test_traits_mit_zahl_darin_wird_abgelehnt(tmp_path):
    schreibe(tmp_path, "pruefling",
             VOLLSTAENDIG.replace('traits = ["knapp", "trocken"]',
                                  'traits = ["knapp", 42]'))
    with pytest.raises(PersonaFehler):
        lade(cfg_mit(tmp_path))


def test_palettenschluessel_sind_die_drei_erwarteten():
    assert PALETTENSCHLUESSEL == ("idle", "active", "alert")


# --------------------------------------------------------------------------
# Die mitgelieferten Dateien selbst
# --------------------------------------------------------------------------

def test_beide_dateien_liegen_im_repo():
    for name in ("ember", "kiesel"):
        assert (REPO / "config/persona" / f"{name}.toml").is_file()


def test_die_zweite_persona_unterscheidet_sich_in_jedem_feld():
    # Waere ein Feld gleich, koennte ein Vergleich zufaellig bestehen.
    import tomllib
    with (REPO / "config/persona/ember.toml").open("rb") as fh:
        e = tomllib.load(fh)
    with (REPO / "config/persona/kiesel.toml").open("rb") as fh:
        k = tomllib.load(fh)
    for feld in ("name", "voice", "speech_threshold", "traits", "palette",
                 "system_prompt", "wake_words"):
        assert e[feld] != k[feld], feld
