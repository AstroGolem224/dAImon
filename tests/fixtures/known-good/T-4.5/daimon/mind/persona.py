"""T-3.10 — Charakter aus der Datei, nicht aus dem Code.

Warum eine fehlende Persona ein FEHLER ist
----------------------------------------------------------------------------
Ein Pet ohne Persona koennte man mit einem Vorgabe-Charakter starten lassen, und
es wuerde funktionieren -- es waere nur nicht das Pet, das jemand konfiguriert
hat. Genau diese Sorte Stille ist in diesem Projekt teuer geworden: ein toter
Watcher hat wochenlang "Gate P0: 11 von 11" gemeldet (Fall 15). Fehlt die Datei,
sagt `lade()` deshalb, WELCHE zwei Pfade geprueft wurden und welcher Name gesucht
war -- und wirft.

Die Rueckfallkette, und warum sie sichtbar ist
----------------------------------------------------------------------------
`voice`, `palette` und `wake_words` darf die Persona weglassen; dann gilt der
Wert aus `daimon.toml`, und zuletzt die Vorgabe im Code. Das ist keine
Bequemlichkeit, sondern die Bedingung dafuer, dass die eingefrorenen Pruefstaende
von T-3.9 (Stimme) und T-3.8 (Erkenner) weiter gelten: beide setzen
`persona.voice` in ihrer eigenen Testkonfiguration und legen keine Persona-Datei
an. Waere die Persona Pflicht, waeren zwei eingefrorene Zusagen rot -- und dann
haette dieser Task sie aufgeweicht, statt etwas hinzuzufuegen.

Damit die Kette nicht zur Ausrede wird, sagt `herkunft` je Feld, welcher Ort
gegolten hat: `persona`, `config` oder `vorgabe`. Eine Kette, die man nicht
ablesen kann, ist eine Kette, in der Werte verschwinden.

Welcher NAME gilt: erst die Wahl, dann die Konfiguration
----------------------------------------------------------------------------
`$XDG_STATE_HOME/daimon/persona.json` schlaegt `persona.name` aus
`daimon.toml`. Diese Datei schreibt das Kontextmenue des Face -- in den
ZUSTAND, weil `daimon-face.service` `ProtectHome=read-only` traegt und nur
`%t/daimon` und `%h/.local/state/daimon` freigibt. Ein Schreibrecht auf
`~/.config/daimon` haette dem Overlay das Verzeichnis geoeffnet, in dem laut
`docs/TOKEN-ROTATION.md` der `anthropic-token` liegt; das waere ein zu hoher
Preis fuer einen Menueeintrag.

`herkunft` bekommt dafuer KEINEN neuen Schluessel: T-3.10.sh prueft die
Auskunft als exakte Menge aus sieben Feldern. Wer wissen will, woher der Name
kam, sieht in die Auswahldatei -- sie existiert oder eben nicht.

`system_prompt` wird NICHT angefasst
----------------------------------------------------------------------------
Nicht gekuerzt, nicht umgebrochen, keine Platzhalterersetzung. Es fallen
ausschliesslich die **aeusseren LF-Zeichen** (`.strip("\n")`), die das
TOML-Mehrzeilenformat ohnehin erzeugt -- und zwar nur sie: eine Randzeile, die
Leerzeichen enthaelt, BLEIBT stehen, ebenso Leerzeichen am Ende jeder Zeile.
Die frueher hier stehende Formulierung "fuehrende und abschliessende Leerzeilen
fallen" war staerker als das Verhalten; codex hat das beim Gegenlesen bemerkt,
und die Zusage ist jetzt praeziser statt das Verhalten unsichtbar groesser.
Was in der Datei steht, geht so an die API.
Ein Lader, der den Charaktertext "aufbereitet", ist ein zweiter Autor, den
niemand gelesen hat.

Und `prompt()` traegt nichts Wechselndes: kein Datum, keine Uhrzeit, keinen
Projektnamen. Ein Systemprompt, der sich bei jedem Aufruf aendert, ist nicht
cachebar (Prompt-Caching zahlt pro Praefix) und macht jede Messung
unvergleichbar. Wer Kontext braucht, haengt ihn als eigene Nachricht an -- das
ist T-3.11.

`speech_threshold` hat heute keinen Verbraucher
----------------------------------------------------------------------------
Vier Stufen, validiert, bereitgestellt -- und ungenutzt. Bis T-3.11 (Mind) und
T-3.14 (Overlay-Zustaende) liest sie niemand. Das steht hier, statt zu
suggerieren, sie wirke schon: ein Feld, das nur behauptet wird, gehoert
markiert.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Die vier Stufen aus Design §10.1. Ein fuenfter Wert ist ein Fehler und faellt
# NICHT auf `helpful` zurueck: ein stiller Rueckfall macht aus einem Tippfehler
# eine Einstellung, die niemand gewaehlt hat.
SCHWELLEN = ("silent", "urgent", "helpful", "chatty")

# Die Palette braucht genau diese drei Schluessel -- das Face erwartet sie, und
# eine Palette mit zwei Farben ergibt einen Mood ohne Anzeige.
PALETTENSCHLUESSEL = ("idle", "active", "alert")

# Mitgelieferte Personas. `Path(__file__).parents[2]` ist die Repowurzel:
# daimon/mind/persona.py -> daimon/mind -> daimon -> <repo>.
#
# DAS GILT FUER DEN CHECKOUT, und das ist hier der einzige Betriebsweg: alle
# systemd-Units tragen absolute Pfade in dieses Repo (`WorkingDirectory=` und
# `ExecStart=.../.venv/bin/python`). Nach einem `pip install .` zeigte dieser
# Pfad nach `site-packages/config/persona`, wo nichts liegt -- `pyproject.toml`
# paketiert nur `daimon`. Wer Installation zu einem Betriebsweg macht, legt die
# Personas als Paketdaten unter `daimon/` ab und sucht sie mit
# `importlib.resources`; solange die Units auf das Repo zeigen, waere das
# Vorratsarbeit. Befund von codex beim Gegenlesen, hier benannt statt behoben.
MITGELIEFERT = Path(__file__).resolve().parents[2] / "config" / "persona"

VORGABE_VOICE = "de_DE-thorsten-high"
VORGABE_PALETTE = {"idle": "#3a2418", "active": "#ff6b1a", "alert": "#ffd24a"}


class PersonaFehler(ValueError):
    """Persona fehlt, ist kaputt, oder ein Feld passt nicht. Nennt Datei und Feld."""


@dataclass(frozen=True)
class Persona:
    name: str
    system_prompt: str
    speech_threshold: str
    voice: str
    traits: tuple[str, ...]
    wake_words: tuple[str, ...]
    palette: dict[str, str]
    quelle: Path
    # Je Feld: "persona", "config" oder "vorgabe". Siehe Modulkopf.
    herkunft: dict[str, str] = field(default_factory=dict)

    def prompt(self) -> str:
        """Der Text fuer den API-Aufruf.

        Aufbau, festgelegt im Plandokument §2 und hier eingehalten:

            <system_prompt woertlich>
            <Leerzeile>
            Eigenschaften: a, b, c

        Die Eigenschaftszeile faellt weg, wenn `traits` leer ist -- ein
        "Eigenschaften:" ohne Inhalt waere eine Behauptung ueber einen Charakter,
        der keine hat. Sonst kommt nichts dazu; die Begruendung steht im
        Modulkopf.
        """
        if not self.traits:
            return self.system_prompt
        return f"{self.system_prompt}\n\nEigenschaften: {', '.join(self.traits)}"


# -- Lesen und pruefen -----------------------------------------------------

def _auswahl(cfg: Any) -> str | None:
    """Der zuletzt im Kontextmenue gewaehlte Name, oder None.

    Warum im ZUSTAND und nicht in `daimon.toml`: das Face schreibt diese
    Datei, und `daimon-face.service` traegt `ProtectHome=read-only`. Ein
    Schreibrecht auf `~/.config/daimon` haette dem Overlay das Verzeichnis
    geoeffnet, in dem der `anthropic-token` liegt. Der Zustand ist ohnehin der
    ehrlichere Ort: die Konfiguration sagt, was eingestellt IST, die Auswahl,
    was zuletzt GEWAEHLT wurde -- und die Wahl gewinnt.

    Kaputte Datei heisst FEHLER, kein stiller Rueckfall auf die Konfiguration:
    sonst redet nach einem halben Schreibvorgang eine andere Persona als die
    im Menue angehakte, und niemand erfaehrt davon.
    """
    pfad = Path(cfg.state_dir) / "persona.json"
    try:
        with pfad.open("rb") as fh:
            daten = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise PersonaFehler(
            f"{pfad}: Persona-Auswahl nicht lesbar ({exc}). Die Datei schreibt "
            f"das Kontextmenue des Face; loeschen stellt die Konfiguration "
            f"wieder her.") from exc
    if not isinstance(daten, dict):
        raise PersonaFehler(f"{pfad}: erwartet wird ein Objekt mit `name`")
    name = daten.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PersonaFehler(
            f"{pfad}: Feld `name` fehlt oder ist keine nichtleere Zeichenkette")
    return name.strip()


def _pfade(cfg: Any) -> tuple[str, list[Path]]:
    """`(name, [XDG-Pfad, mitgelieferter Pfad])` -- in dieser Reihenfolge.

    Kleingeschrieben gesucht: `name = "Ember"` findet `ember.toml`. Sonst suchte
    ein grossgeschriebener Name eine Datei, die niemand so anlegt.
    """
    name = _auswahl(cfg) or str(cfg.get("persona.name", "") or "").strip()
    if not name:
        raise PersonaFehler(
            "In der Konfiguration steht kein `persona.name`. Ohne Namen ist "
            "nicht entscheidbar, welche Persona gemeint ist -- und eine "
            "geratene Persona ist schlimmer als keine.")
    datei = f"{name.lower()}.toml"
    return name, [Path(cfg.config_dir) / "persona" / datei, MITGELIEFERT / datei]


def _lies(pfad: Path) -> dict:
    try:
        with pfad.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        # tomllib nennt Zeile und Spalte -- durchreichen statt verschlucken.
        raise PersonaFehler(f"{pfad}: ungueltiges TOML ({exc})") from exc
    except OSError as exc:
        raise PersonaFehler(f"{pfad}: nicht lesbar ({exc})") from exc


def _text(daten: dict, feld: str, pfad: Path, *, pflicht: bool = True) -> str:
    wert = daten.get(feld)
    if wert is None:
        if pflicht:
            raise PersonaFehler(f"{pfad}: Feld `{feld}` fehlt")
        return ""
    if not isinstance(wert, str) or not wert.strip():
        raise PersonaFehler(
            f"{pfad}: Feld `{feld}` muss eine nichtleere Zeichenkette sein "
            f"(ist {type(wert).__name__})")
    return wert


def _liste(daten: dict, feld: str, pfad: Path) -> tuple[str, ...] | None:
    """Liste von Zeichenketten, oder None wenn das Feld fehlt.

    Eine einzelne Zeichenkette wird NICHT als Liste mit einem Element
    durchgelassen: `traits = "knapp"` waere in Python iterierbar und ergaebe
    sieben Eigenschaften mit je einem Buchstaben. Genau diese Sorte stiller
    Umdeutung ist ein Mutant wert.
    """
    if feld not in daten:
        return None
    wert = daten[feld]
    if isinstance(wert, str) or not isinstance(wert, (list, tuple)):
        raise PersonaFehler(
            f"{pfad}: Feld `{feld}` muss eine Liste von Zeichenketten sein "
            f"(ist {type(wert).__name__})")
    for eintrag in wert:
        if not isinstance(eintrag, str):
            raise PersonaFehler(
                f"{pfad}: Feld `{feld}` enthaelt einen Eintrag vom Typ "
                f"{type(eintrag).__name__}, erwartet werden Zeichenketten")
        if not eintrag.strip():
            # `traits = [""]` ergaebe eine leere Eigenschaftszeile im Prompt,
            # `wake_words = ["   "]` ein Wake-Word, das niemand sagen kann.
            # Beides ist ein Eintrag, der aussieht wie eine Angabe und keine
            # ist. Befund von codex beim Gegenlesen.
            raise PersonaFehler(
                f"{pfad}: Feld `{feld}` enthaelt einen leeren Eintrag")
    return tuple(wert)


def _palette(daten: dict, pfad: Path) -> dict[str, str] | None:
    if "palette" not in daten:
        return None
    wert = daten["palette"]
    if not isinstance(wert, dict):
        raise PersonaFehler(
            f"{pfad}: Feld `palette` muss eine Tabelle sein "
            f"(ist {type(wert).__name__})")
    fehlt = [k for k in PALETTENSCHLUESSEL if k not in wert]
    if fehlt:
        raise PersonaFehler(
            f"{pfad}: `palette` fehlt {', '.join(fehlt)} -- gebraucht werden "
            f"{', '.join(PALETTENSCHLUESSEL)}")
    for k in PALETTENSCHLUESSEL:
        if not isinstance(wert[k], str):
            raise PersonaFehler(
                f"{pfad}: `palette.{k}` muss eine Zeichenkette sein "
                f"(ist {type(wert[k]).__name__})")
    return {k: wert[k] for k in PALETTENSCHLUESSEL}


def lade(cfg: Any) -> Persona:
    """Die aktive Persona. Wirft `PersonaFehler`, wenn etwas fehlt oder klemmt.

    Gesucht wird zuerst unter `$XDG_CONFIG_HOME/daimon/persona/`, dann im
    mitgelieferten `config/persona/`. Die erste gefundene Datei gewinnt --
    dieselbe Reihenfolge wie bei `daimon.toml`, damit eine eigene Persona die
    mitgelieferte ueberschreibt und nicht ergaenzt.
    """
    name, kandidaten = _pfade(cfg)
    quelle = next((p for p in kandidaten if p.is_file()), None)
    if quelle is None:
        raise PersonaFehler(
            f"Keine Persona-Datei fuer {name!r} gefunden. Geprueft: "
            + "; ".join(str(p) for p in kandidaten)
            + ". Es gibt ABSICHTLICH keinen Vorgabe-Charakter: ein Pet, das "
              "ohne Persona irgendwie redet, ist nicht das Pet, das jemand "
              "konfiguriert hat.")

    daten = _lies(quelle)
    herkunft: dict[str, str] = {}

    # -- Pflichtfelder --
    persona_name = _text(daten, "name", quelle)
    system_prompt = _text(daten, "system_prompt", quelle).strip("\n")
    herkunft["name"] = herkunft["system_prompt"] = "persona"

    schwelle = _text(daten, "speech_threshold", quelle)
    if schwelle not in SCHWELLEN:
        raise PersonaFehler(
            f"{quelle}: `speech_threshold` ist {schwelle!r}, erlaubt sind "
            f"{', '.join(SCHWELLEN)}. Kein Rueckfall: ein stiller Rueckfall "
            f"macht aus einem Tippfehler eine Einstellung, die niemand "
            f"gewaehlt hat.")
    herkunft["speech_threshold"] = "persona"

    # -- Felder mit Rueckfallkette --
    voice = daten.get("voice")
    if isinstance(voice, str) and voice.strip():
        herkunft["voice"] = "persona"
    else:
        if "voice" in daten:
            raise PersonaFehler(
                f"{quelle}: Feld `voice` muss eine nichtleere Zeichenkette "
                f"sein (ist {type(daten['voice']).__name__})")
        # Die Konfiguration wird GENAUSO streng geprueft wie die Persona-Datei.
        # Vorher stand hier `aus_config or VORGABE_VOICE` -- eine Zahl oder eine
        # leere Zeichenkette waere durchgelaufen, waehrend dieselbe Angabe in der
        # Persona-Datei ein Fehler ist. Eine Strenge, die nur an einem Ende gilt,
        # ist keine. Befund von codex beim Gegenlesen.
        aus_config = cfg.get("persona.voice")
        if aus_config is None:
            voice, herkunft["voice"] = VORGABE_VOICE, "vorgabe"
        elif isinstance(aus_config, str) and aus_config.strip():
            voice, herkunft["voice"] = aus_config, "config"
        else:
            raise PersonaFehler(
                f"`persona.voice` in der Konfiguration muss eine nichtleere "
                f"Zeichenkette sein (ist {type(aus_config).__name__})")

    traits = _liste(daten, "traits", quelle)
    herkunft["traits"] = "persona" if traits is not None else "vorgabe"
    traits = traits or ()

    wake_words = _liste(daten, "wake_words", quelle)
    if wake_words is not None:
        herkunft["wake_words"] = "persona"
    else:
        aus_config = cfg.get("ears.wake_words")
        if isinstance(aus_config, (list, tuple)) and aus_config:
            # KEIN `str(w)`: eine Zahl in der Liste war vorher ein Wake-Word
            # "42", waehrend dieselbe Zahl in der Persona-Datei ein Fehler ist.
            for w in aus_config:
                if not isinstance(w, str) or not w.strip():
                    raise PersonaFehler(
                        f"`ears.wake_words` in der Konfiguration enthaelt einen "
                        f"Eintrag vom Typ {type(w).__name__}, erwartet werden "
                        f"nichtleere Zeichenketten")
            wake_words = tuple(aus_config)
            herkunft["wake_words"] = "config"
        elif aus_config is not None and not isinstance(aus_config, (list, tuple)):
            raise PersonaFehler(
                f"`ears.wake_words` in der Konfiguration muss eine Liste sein "
                f"(ist {type(aus_config).__name__})")
        else:
            # Leer und nicht erfunden: T−1.1 hat das Wake-Word auf Plan C
            # gestellt, es gibt in Phase 3 keins. Eine erfundene Vorgabe waere
            # eine Faehigkeit, die behauptet wird.
            wake_words = ()
            herkunft["wake_words"] = "vorgabe"

    palette = _palette(daten, quelle)
    if palette is not None:
        herkunft["palette"] = "persona"
    else:
        aus_config = cfg.get("face.palette")
        if isinstance(aus_config, dict) and all(
                k in aus_config for k in PALETTENSCHLUESSEL):
            # KEIN `str(...)`: eine Zahl als Farbwert war vorher "16711680",
            # waehrend dieselbe Angabe in der Persona-Datei ein Fehler ist.
            for k in PALETTENSCHLUESSEL:
                if not isinstance(aus_config[k], str) or not aus_config[k].strip():
                    raise PersonaFehler(
                        f"`face.palette.{k}` in der Konfiguration muss eine "
                        f"nichtleere Zeichenkette sein "
                        f"(ist {type(aus_config[k]).__name__})")
            palette = {k: aus_config[k] for k in PALETTENSCHLUESSEL}
            herkunft["palette"] = "config"
        else:
            palette = dict(VORGABE_PALETTE)
            herkunft["palette"] = "vorgabe"

    return Persona(name=persona_name, system_prompt=system_prompt,
                   speech_threshold=schwelle, voice=voice, traits=traits,
                   wake_words=wake_words, palette=palette, quelle=quelle,
                   herkunft=herkunft)


def main(argv: list[str] | None = None) -> int:
    """Handlauf: `python -m daimon.mind.persona` zeigt die aktive Persona."""
    import argparse
    import json

    from daimon.common.config import load as load_config

    ap = argparse.ArgumentParser(description="dAImon Persona (T-3.10)")
    ap.add_argument("--prompt", action="store_true",
                    help="nur den erzeugten Systemprompt ausgeben")
    args = ap.parse_args(argv)

    p = lade(load_config(make_dirs=False))
    if args.prompt:
        print(p.prompt())
        return 0
    print(json.dumps({
        "name": p.name, "voice": p.voice,
        "speech_threshold": p.speech_threshold, "traits": list(p.traits),
        "wake_words": list(p.wake_words), "palette": p.palette,
        "quelle": str(p.quelle), "herkunft": p.herkunft,
        "prompt_zeichen": len(p.prompt()),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
