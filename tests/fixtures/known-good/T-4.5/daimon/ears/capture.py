"""T-3.1 — Mikrofon-Aufnahme mit hartem Lebenszyklus.

Der ganze Task haengt an einem Methodennamen. `sd.InputStream.stop()` **pausiert**
nur: das Stream/Input/Audio-Objekt bleibt in PipeWire stehen, der Graph haelt das
Geraet weiter, und der Plasma-Mikrofon-Indikator sagt etwas anderes als die
Wirklichkeit -- mal bleibt er an, obwohl niemand mehr zuhoert, mal geht er aus,
obwohl das Objekt noch lebt. `close()` zerstoert das Objekt. Design SS7.4 fuehrt
den Kill-Switch deshalb als Korrektheitsanforderung, nicht als Hoeflichkeit: wer
dem Indikator glaubt, muss ihm glauben koennen. `stop()` unten heisst deshalb so,
weil der Aufrufer "aufhoeren" meint -- getan wird `close()`.

Von aussen nachpruefbar, und nur so zaehlt es:

    pw-dump | jq -r '.[]|select(.info.props["media.class"]=="Stream/Input/Audio")
                      |.info.props["application.name"]'

Gemessen am 02.08., beide Male bei WEITERLAUFENDEM Prozess -- sonst belegt man
nur, dass Prozessende Deskriptoren schliesst:

  * `close()`  -> der Knoten `alsa_capture.python3.12` ist beim naechsten
    Abtastpunkt (0,25 s) weg, waehrend der Prozess noch vier Sekunden lebt.
  * `stop()`   -> der Knoten steht diese vier Sekunden unveraendert weiter und
    verschwindet erst mit dem Prozess. Das ist die Positivkontrolle: ohne sie
    waere "der Knoten ist weg" kein Befund, sondern eine Vermutung.

Zwei Aufnahmen im selben Prozess sind geprueft, weil ein zweiter Strom der
naheliegende Weg waere, doch noch einen Knoten stehenzulassen: **nacheinander**
(auf, zu, auf, zu) und **gleichzeitig** (zwei Objekte, versetzt geoeffnet, einzeln
geschlossen). Beide Male entsteht je Aufnahme genau ein Knoten, und jedes
`close()` entfernt genau seinen -- bei weiterlaufendem Prozess gemessen.

Dazu ein Befund, der beinahe als "Messartefakt" abgelegt worden waere: in
mehreren Laeufen stand kurzzeitig ein ZWEITER Knoten, der das `close()`
ueberlebte. Er war nicht unserer. Auf derselben Maschine lief gleichzeitig ein
fremder Aufnahmeprozess (`kanarie.py`, im Prozessbaum sichtbar), und weil
PortAudio seine Knoten nach dem Programmnamen benennt, hiess er ebenfalls
`alsa_capture.python3.12` -- nicht unterscheidbar. Genau deshalb steht die
`PIPEWIRE_PROPS`-Zeile unten: ohne sie ist "nach dem Ausschalten steht kein
Aufnahmestrom mehr" eine Aussage ueber die ganze Maschine statt ueber uns, und
sie kippt in beide Richtungen. Was hier ausgeschlossen wurde: zwei Aufnahmen
nacheinander, zwei gleichzeitig, drei Prozesse dicht hintereinander, fuenf
Wiederholungen mit Vorher-Kontrolle -- keiner davon hinterlaesst etwas.

Das Geraet ist `Torch Streaming Microphone` mit 48 kHz stereo. Die Umrechnung auf
16 kHz mono int16 macht PipeWire; hier wird nicht selbst resampelt.
"""

from __future__ import annotations

import os
import signal
from typing import Any, Callable

# 16 kHz mono int16, 512 Sample je Block -- die Parameter, die T-3.2 (VAD) und
# T-3.8 (STT) erwarten. 512/16000 sind 32 ms.
RATE = 16000
KANAELE = 1
DTYPE = "int16"
BLOCKSIZE = 512

# ACHTUNG, REIHENFOLGE: diese Zeile steht VOR dem `sounddevice`-Import, und sie
# muss dort bleiben. `sounddevice` ruft beim Import `Pa_Initialize()`, und
# PortAudio tastet dabei die ALSA-Geraete ab -- jeder PipeWire-Client, der dabei
# entsteht, liest seine Umgebung genau einmal. Wer die Importe alphabetisch
# sortiert oder ein isort/ruff-Autofix darueberlaufen laesst, verschiebt das
# `os.environ`-Statement hinter den Import; der Code laeuft dann weiter und
# still mit einer anderen Latenz. Deshalb `noqa: E402` und dieser Absatz.
#
# Ehrlich gemessen, und das Ergebnis stuetzt die Regel NICHT: auf dieser
# Maschine wirkt die Variable auch, wenn sie erst nach dem Import gesetzt wird
# (`node.latency = 333/16000` in pw-dump, beide Reihenfolgen), und ohne sie
# ergibt PortAudios Periodengroesse ohnehin `512/16000`. Der PipeWire-Client
# entsteht hier also erst beim Oeffnen des PCM, nicht beim Import. Die
# Reihenfolge ist damit eine Vorsichtsregel -- der einzige Punkt, der garantiert
# vor jeder Geraeteberuehrung liegt -- und keine gemessene Notwendigkeit. Das
# gehoert hierhin, damit niemand die Regel spaeter mit einer Begruendung
# verteidigt, die nachweislich falsch ist.
os.environ.setdefault("PIPEWIRE_LATENCY", f"{BLOCKSIZE}/{RATE}")

# Derselbe Fall, dieselbe Reihenfolge: der Stream braucht einen Namen, an dem er
# von aussen als unserer erkennbar ist. Ohne ihn heisst er "PipeWire ALSA
# [python3.12]" / `alsa_capture.python3.12` -- und dann ist "nach close() kein
# Stream/Input/Audio mehr" keine Aussage ueber uns: die Pruefung wird rot,
# sobald irgendein fremdes Programm gerade aufnimmt, und gruen, wenn unser
# Stream stehenbleibt, waehrend ein fremder verschwindet. Gemessen, dass es
# nicht theoretisch ist: zwei gleichzeitige Aufnahmen im selben Prozess ergeben
# ZWEI Knoten mit identischem Namen, die sich ueber den Namen nicht
# auseinanderhalten lassen.
#
# `setdefault`, weil die Ears-Unit gewinnen soll, wenn sie etwas anderes
# vorgibt. Dort gehoert der Wert hin -- aber ein Modul, das nur unter seiner
# Unit korrekt messbar ist, ist im Test nicht messbar, und der Verifizierer
# laeuft ohne Unit. Die Eigenschaften gelten prozessweit fuer jeden
# PipeWire-Client; in einem Ohren-Prozess ist das genau richtig.
os.environ.setdefault(
    "PIPEWIRE_PROPS", "{ application.name = daimon-ears node.name = daimon-ears }"
)

import sounddevice as sd  # noqa: E402

# "pipewire" ist NICHT dasselbe wie "default". `default` ist der ALSA-Alias, den
# `~/.asoundrc`, `/etc/asound.conf` oder ein Paket-Snippet umbiegen koennen --
# auf einem System mit pulse-Bruecke landet er bei `pulse`, auf einem ohne
# direkt bei `hw:0`. Dann nimmt die Aufnahme am PipeWire-Graph vorbei auf: keine
# Umrechnung auf 16 kHz mono, kein Stream/Input/Audio-Objekt im Graph -- und
# damit auch kein Indikator, den man ausschalten koennte. `pipewire` benennt das
# ALSA-Plugin des Servers direkt und ist die einzige Variante, deren
# Lebenszyklus von aussen ueber `pw-dump` messbar ist.
DEVICE = "pipewire"


class Aufnahme:
    """Ein Aufnahmestrom. Nicht wiederverwendbar ueber `stop()` hinaus im Sinne
    von "derselbe Strom" -- `start()` nach `stop()` erzeugt einen neuen, weil der
    alte zerstoert wurde. Genau das ist der Punkt."""

    def __init__(
        self,
        *,
        senke: Callable[[Any], None] | None = None,
        device: str = DEVICE,
        rate: int = RATE,
        kanaele: int = KANAELE,
        dtype: str = DTYPE,
        blocksize: int = BLOCKSIZE,
    ) -> None:
        # ponytail: die Parameter sind Konstanten mit Ueberschreibmoeglichkeit,
        # keine Konfiguration. Obergrenze: sobald ein zweiter Aufrufer andere
        # Werte braucht (T-3.2 braucht sie nicht -- 512 bei 16 kHz ist genau die
        # Chunk-Groesse des VAD), gehoert ein `[ears]`-Abschnitt nach
        # config/daimon.toml und daimon/common/config.py, und der Konstruktor
        # nimmt ein `Config`. Vorher waere das eine Einstellung, die niemand
        # einstellt.
        self._senke = senke
        self.device = device
        self.rate = rate
        self.kanaele = kanaele
        self.dtype = dtype
        self.blocksize = blocksize
        self._stream: sd.InputStream | None = None
        self.blocks = 0

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        self.blocks += 1
        if self._senke is not None:
            # Kopie: PortAudio gibt denselben Puffer im naechsten Block wieder
            # heraus. Wer ihn aufhebt, haelt spaeter fremdes Audio in der Hand.
            self._senke(indata.copy())

    def start(self) -> None:
        if self._stream is not None:
            return
        self.blocks = 0
        stream = sd.InputStream(
            device=self.device,
            samplerate=self.rate,
            channels=self.kanaele,
            dtype=self.dtype,
            blocksize=self.blocksize,
            callback=self._callback,
        )
        stream.start()
        self._stream = stream

    def stop(self) -> None:
        """Beendet die Aufnahme -- durch `close()`, nicht durch `stop()`.

        `sd.InputStream.close()` ruft intern selbst ab, wenn der Strom noch
        laeuft; ein vorgeschaltetes `stream.stop()` waere nicht falsch, aber es
        stuende als Vorlage da, an der jemand spaeter das `close()` wegkuerzt.
        Idempotent, weil der Kill-Switch auch aus einem Signalhandler kommen
        darf und dann nicht wissen kann, ob schon jemand anders zugemacht hat.
        """
        # Reihenfolge: erst schliessen, DANN die Referenz aufgeben. Andersherum
        # -- und so stand es hier zuerst -- waere `zustand()["offen"]` nach einem
        # gescheiterten `close()` False, waehrend der Knoten in PipeWire noch
        # steht: eine Selbstauskunft, die das Gegenteil der Wahrheit meldet, und
        # zwar ausgerechnet am Kill-Switch (Regel 9). Ausserdem waere die
        # Referenz weg und ein zweiter Versuch unmoeglich.
        stream = self._stream
        if stream is not None:
            stream.close()
            self._stream = None

    def zustand(self) -> dict:
        return {
            "offen": self._stream is not None,
            "blocks": self.blocks,
            "rate": self.rate,
            "kanaele": self.kanaele,
            "dtype": self.dtype,
            "device": self.device,
        }

    def __enter__(self) -> "Aufnahme":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


# Harte Obergrenze fuer den Selbsttest. Ein haengender Aufnahmeprozess ist nicht
# nur laestig, er leuchtet dabei im Systemtray -- und der Nutzer hat keinen Weg
# zu erkennen, dass da niemand mehr zuhoert.
MAX_SEKUNDEN = 10.0


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import time

    ap = argparse.ArgumentParser(description="T-3.1 Selbsttest: auf, kurz, zu")
    ap.add_argument("--sekunden", type=float, default=2.0)
    args = ap.parse_args(argv)

    dauer = min(max(args.sekunden, 0.0), MAX_SEKUNDEN)
    # Zweites Netz: SIGALRM toetet den Prozess, falls PortAudio im Callback oder
    # im close() haengt. Der Kernel schliesst dann die Deskriptoren, PipeWire
    # raeumt das Stream-Objekt ab. Die Obergrenze ist damit nicht davon
    # abhaengig, dass unser eigener Code noch laeuft.
    signal.alarm(int(dauer) + 5)

    a = Aufnahme()
    try:
        a.start()
        print(json.dumps(a.zustand()), flush=True)
        time.sleep(dauer)
    finally:
        a.stop()
    print(json.dumps(a.zustand()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
