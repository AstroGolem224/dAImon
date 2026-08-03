#!/usr/bin/env bash
# Die STT-Gewichte holen. 465 MB gepackt, 665 MB entpackt -- deshalb nicht in
# der Historie (siehe .gitignore).
#
# `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8`: parakeet-tdt-0.6b-v3 ist die
# Modellentscheidung aus T−1.2 (whisper-base halluziniert auf Deutsch "im
# basat"), und k2-fsa veroeffentlicht es fuer sherpa-onnx vorkonvertiert. Damit
# deckt EINE Apache-2.0-Abhaengigkeit Wake-Word, VAD, TTS und STT ab -- der Plan
# verlangt genau das ("wo moeglich ueber sherpa-onnx").
#
# int8 und nicht fp16: gemessen am 03.08. auf der CPU mit 8 Threads ergibt int8
# eine WER von 5,2 % (deutsch, eigene Aufnahmen) bei RTF 0,02. Das reicht, und
# fp16 waere hier ohne GPU nur groesser.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${NAME}.tar.bz2"
ZIEL="$HIER/models"

if [[ -d "$ZIEL/$NAME" ]]; then
  echo "Schon da: $ZIEL/$NAME"
  exit 0
fi

mkdir -p "$ZIEL"
echo "Hole $NAME (465 MB) ..."
curl -fL --progress-bar -o "$ZIEL/$NAME.tar.bz2" "$URL"
tar xjf "$ZIEL/$NAME.tar.bz2" -C "$ZIEL"
rm "$ZIEL/$NAME.tar.bz2"

# Nachsehen, statt dem Entpacken zu glauben: ein abgebrochener Download ergibt
# ein Verzeichnis, dem eine Datei fehlt, und das faellt sonst erst beim Laden auf.
for datei in encoder.int8.onnx decoder.int8.onnx joiner.int8.onnx tokens.txt; do
  [[ -s "$ZIEL/$NAME/$datei" ]] || { echo "FEHLT: $datei" >&2; exit 1; }
done
echo "Fertig: $ZIEL/$NAME"
