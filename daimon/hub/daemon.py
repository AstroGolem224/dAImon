"""T-0.9 — der Hub. Nimmt Ereignisse, liefert State. Ausschliesslich ueber
Unix-Sockets.

KEIN TCP. Nicht als Vorsichtsmassnahme, sondern damit
`RestrictAddressFamilies=AF_UNIX` in der systemd-Unit erfuellbar ist (T-0.14).
Ein einziger `socket.AF_INET` irgendwo macht diese Direktive unmoeglich, und
dann faellt eine ganze Schutzschicht weg, weil jemand einen Debug-Endpunkt
bequem fand. Der Verifizierer prueft es deshalb mit `ss -ltn` am laufenden
Prozess und nicht am Quelltext.

Zwei Arten von Socket:

  * je Produzent einer (`hookbridge.sock`, `eyes.sock`, ...), Zeilen-JSON rein
  * `state.sock` und `diag.sock`, beide nur lesend

Die Trennung ist nicht Kosmetik: die Produzentensockets pruefen die
Gegenstelle ueber T-0.7, die beiden lesenden nicht. Und die Diagnose bleibt
aus demselben Grund auf einem Unix-Socket wie alles andere -- sie verraet
Warteschlangenlaengen und Ereigniszaehler, also mehr ueber den Nutzer als in
einen Netzwerkendpunkt gehoert.

Der `auth`-Socket (T-1.7) ist der einzige Produzent, dessen Ereignisse nicht
auf den Bus gehen: `intent_mark` und `freigabe` werden hier im Hub gegen
MarkenBuch und FreigabeBuch verarbeitet -- die Marke bleibt im Hub (Design
2.4), und die turn_id erzeugt der Hub selbst, nie der Absender.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import socket
import subprocess
import time
import threading
from pathlib import Path

from daimon.auth.preview import wert_saeubern
from daimon.common import ipc
from daimon.gpu import worker as gpu
from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger
from daimon.common.protocol import Event, ProtocolError
from daimon.hub.bus import Bus, mood_of, projekt_aus_cwd
from daimon.hub.diag import Diagnose
from daimon.hub import sprechtext
from daimon.hub.abkuehlung import Abkuehlung
from daimon.hub.marks import FreigabeBuch, MarkenBuch, MarkenFehler
from daimon.hub.state import HubState

STATE_SOCKET = "state.sock"
DIAG_SOCKET = "diag.sock"
EVENTS_SOCKET = "events.sock"
GPU_SOCKET = "gpu.sock"
TTS_SOCKET = "tts.sock"
MAX_ZEILE = 1 << 20  # 1 MiB. Eine Hook-Nutzlast ist Kilobytes gross.

# Wie oft der Push-Endpunkt nachsieht, ob sich `rev` bewegt hat. 50 ms deckelt
# die Zustellverzoegerung; T-1.6 verlangt p95 < 300 ms, das ist reichlich Luft.
PUSH_INTERVALL_S = 0.05
PR_SET_DUMPABLE = 4

# T-2.7: die einzigen Schluessel, die `wahrnehmung_aus` tragen darf. Der
# Unit-NAME steht dahinter in der Konfiguration (`hub.wahrnehmung_units`),
# nie in der Nachricht -- sonst koennte das Face den Hub selbst, den
# Auth-Agenten oder jede beliebige Unit des Nutzers stoppen. Diese Menge ist
# die zweite Haelfte derselben Grenze: sie deckelt, welche
# Konfigurationseintraege ueberhaupt erreichbar sind.
WAHRNEHMUNG_ZIELE = frozenset({"ears", "eyes"})
# Ein haengendes `systemctl` darf den Produzenten-Thread nicht festhalten.
SYSTEMCTL_TIMEOUT_S = 10.0

# T-3.7: die drei Absagegruende des GPU-Gates. Maschinenlesbar getrennt, nie
# ein gemeinsames `error: true` -- T-3.14 macht daraus Overlay-Zustaende, und
# "zu wenig VRAM" braucht dort eine andere Anzeige als "der Nutzer spielt".
GPU_GRUENDE = frozenset({"vram", "fullscreen", "lade_sperre"})
# Frist, nach der eine Ladesperre VON SELBST verfaellt. Fail-safe ist hier
# oeffnen, nicht sperren: ein beim Laden gestorbener Worker darf nicht jeden
# weiteren Ladevorgang dauerhaft blockieren. Der Schaden einer verlorenen
# Sperre ist eine gleichzeitige Ladung; der Schaden einer ewigen Sperre ist ein
# totes Sprachsystem. 120 s ist grosszuegig gegen den gemessenen Kaltstart aus
# T−1.2 (293--419 ms fuer whisper-base) -- die Frist soll ein HAENGEN abfangen,
# nicht ein langsames Laden abschneiden.
GPU_FRIST_S = 120.0
# Soviel VRAM bleibt nach dem Laden frei. Ohne Reserve gaebe die Pruefung genau
# dann gruen, wenn danach nichts mehr uebrig ist -- und der naechste, der
# nachfordert, ist der Compositor.
GPU_RESERVE_MIB = 1024
# Der GPU-Endpunkt liest, anders als state.sock und diag.sock. Ein Client, der
# verbindet und schweigt, darf den Horcher nicht festhalten.
GPU_LESE_TIMEOUT_S = 5.0

# T-3.9: Frist einer Sprechfreigabe. Sie deckt Synthese plus Wiedergabe eines
# Satzes ab (gemessen: 100--300 ms Synthese, unter 4 s Audio) und ist bewusst
# grosszuegig -- sie soll einen gestorbenen Sprecher abfangen, nicht eine
# langsame Wiedergabe abschneiden. Nach Ablauf wird die Freigabe verworfen; ein
# `gesprochen` danach vermerkt KEINE Abkuehlung, weil niemand mehr weiss, ob
# wirklich gesprochen wurde.
TTS_FRIST_S = 30.0
TTS_ABKUEHLUNG_DATEI = "tts-abkuehlung.json"


def _dumpbarkeit_abschalten() -> None:
    """Design 7.5: keine ptrace-/Core-Dump-Freigabe fuer den Hub.

    Das ist nur eine Haertungsgeste gegen versehentliche Diagnosezugriffe,
    keine Grenze gegen einen bereits kompromittierten Benutzerprozess.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        fehler = ctypes.get_errno()
        raise OSError(fehler, os.strerror(fehler))


class Hub:
    def __init__(self, cfg: Config | None = None, *, log: Logger | None = None,
                 runtime_dir: Path | None = None) -> None:
        self.cfg = cfg or load_config()
        self.runtime_dir = runtime_dir or self.cfg.runtime_dir
        self.log = log or get_logger("daimon-hub")
        self.state = HubState(ttl_s=float(self.cfg.get("hub.state_ttl_s", 3600)))
        self.diag = Diagnose()
        self.bus = Bus()
        # T-1.7: Marken- und Freigabebuch leben im Hub (Design 2.4: "Die
        # Marke bleibt im Hub"). Der Auth-Agent meldet nur; ausgegeben und
        # bestaetigt wird hier.
        self.marken = MarkenBuch(log=self.log)
        self.freigaben = FreigabeBuch(log=self.log)
        # T-3.7: die Ladesperre. Hoechstens ein Ladevorgang gleichzeitig, und
        # zwar HIER, weil ein Worker nur sich selbst kennt. `_gpu_sperre` ist
        # (Marke, Ablauf in monotoner Zeit) -- monoton, weil eine
        # NTP-Korrektur keine Sperre aufheben und keine erzeugen darf.
        self._gpu_lock = threading.Lock()
        self._gpu_sperre: tuple[str, float] | None = None
        self.gpu_frist_s = float(self.cfg.get("gpu.sperrfrist_s", GPU_FRIST_S))
        self.gpu_reserve_mib = int(self.cfg.get("gpu.reserve_mib", GPU_RESERVE_MIB))
        # T-3.9: der Sprechtext-Torwaechter. Validator und Abkuehlung liegen
        # HIER, weil eine Pruefung im sprechenden Dienst umgehbar ist, sobald
        # ein anderer Produzent Text an die Ausgabe schicken kann (Design §8.3).
        # Die Freigabe ist eine Marke mit Frist -- dasselbe Muster wie die
        # GPU-Ladesperre, aus demselben Grund: der Halter gibt zurueck, was er
        # bekommen hat, und ein gestorbener Sprecher blockiert nichts dauerhaft.
        self._tts_lock = threading.Lock()
        self._tts_freigaben: dict[str, tuple[str, float]] = {}   # Marke: (Kanal, Ablauf)
        self.tts_frist_s = float(self.cfg.get("tts.freigabefrist_s", TTS_FRIST_S))
        self.abkuehlung = Abkuehlung(
            Path(self.cfg.state_dir) / TTS_ABKUEHLUNG_DATEI,
            cfg=self.cfg, log=self.log)
        self._server: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.bus.subscribe(self._on_event)

    # -- Ereignisse --------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        if event.type != "hook":
            self.diag.verworfen("fremder_typ")
            return
        t0 = time.perf_counter()
        p = event.payload or {}
        mood, bubble = mood_of(p)
        if bubble is not None:
            # Hook-Text wird genau an der Hub-Grenze fuer alle Anzeigen
            # gesaeubert. Das Face rendert nur diesen Zustand und baut die
            # Unicode-/Laengenregeln aus preview.py absichtlich nicht nach.
            bubble = {
                **bubble,
                "title": wert_saeubern(str(bubble.get("title", ""))),
                "body": wert_saeubern(str(bubble.get("body", ""))),
            }
        cwd = p.get("cwd", "") or ""
        pid = p.get("pid")
        self.state.apply(
            mood,
            session_id=p.get("session_id", "?"),
            bubble=bubble,
            cwd=cwd,
            project=projekt_aus_cwd(cwd),
            pid=int(pid) if isinstance(pid, int) else None,
            nonce=p.get("nonce", "") or "",
        )
        if mood is None:
            # Unbekanntes Ereignis. Es aendert nichts, aber es ist ein Befund:
            # T-1.5 hat SubagentStop und Notification:auth_success genau so
            # gefunden. Ohne Zaehler bliebe die Luecke unsichtbar.
            self.diag.verworfen(f"unbekanntes_ereignis:{p.get('hook_event_name','?')}")
        self.diag.hop("hook_to_state", (time.perf_counter() - t0) * 1000)

    # -- Produzentensockets ------------------------------------------------

    def _bediene_produzent(self, conn: socket.socket, produzent: str) -> None:
        with conn, conn.makefile("rb") as fh:
            for roh in fh:
                if len(roh) > MAX_ZEILE:
                    self.log.warn("Zeile zu lang, Verbindung ab",
                                  DAIMON_PRODUZENT=produzent)
                    return
                roh = roh.strip()
                if not roh:
                    continue
                try:
                    daten = json.loads(roh)
                    event = Event.from_dict(daten)
                    ipc.pruefe_typ(produzent, event.type)
                except (json.JSONDecodeError, ProtocolError) as exc:
                    self.log.warn("Nutzlast verworfen", DAIMON_PRODUZENT=produzent,
                                  DAIMON_GRUND=str(exc)[:200])
                    continue
                except ipc.MessageTypeError as exc:
                    # Das ist der Fall, um den es in T-0.7 geht: ein Produzent
                    # in fremder Rolle. Verbindung ab, nicht bloss verwerfen.
                    self.log.error("Typ nicht erlaubt, Verbindung ab",
                                   DAIMON_PRODUZENT=produzent,
                                   DAIMON_GRUND=str(exc)[:200])
                    self._zaehle_abweisung(event.type)
                    return
                if produzent == "auth":
                    # Auth-Ereignisse gehen nicht auf den Bus: der Hub gibt
                    # Marken aus und bestaetigt Freigaben selbst (T-1.7).
                    if not self._verarbeite_auth(event):
                        return  # Abweisung: Verbindung ab, Hub laeuft weiter
                    continue
                if produzent == "face":
                    # `ipc.pruefe_typ` laesst hier ausschliesslich
                    # bubble_dismiss und wahrnehmung_aus durch. Das Face
                    # erhaelt insbesondere keine Auth-Faehigkeit aus T-1.7
                    # zurueck -- und es gibt kein Einschalten.
                    if event.type == "wahrnehmung_aus":
                        self._wahrnehmung_aus((event.payload or {}).get("ziel"))
                    else:
                        self.state.clear_bubble()
                    continue
                self.bus.publish(event)

    def _wahrnehmung_aus(self, ziel: object) -> None:
        """T-2.7: eine Wahrnehmungs-Unit ABschalten. Es gibt kein Gegenstueck.

        Zwei Schluessel-Pruefungen, und beide sind Absicht:

          1. `ziel` muss in WAHRNEHMUNG_ZIELE stehen -- eine im Code feste,
             kurze Menge.
          2. Der Unit-Name kommt aus `hub.wahrnehmung_units`, also aus der
             Konfiguration des Nutzers, nie aus der Nachricht.

        Damit ist der schlimmste Fall eines kompromittierten Overlays: Ohren
        oder Augen gehen aus. Naehme der Hub den Namen aus der Nachricht,
        waere der schlimmste Fall `systemctl --user stop daimon-auth` -- und
        damit das Ende jeder Bestaetigungsschranke.
        """
        units = self.cfg.get("hub.wahrnehmung_units", {}) or {}
        # `isinstance` vor dem Mengentest: eine Liste oder ein dict aus der
        # Nutzlast waere nicht hashbar und wuerde `in` mit TypeError sprengen.
        erlaubt = isinstance(ziel, str) and ziel in WAHRNEHMUNG_ZIELE
        unit = units.get(ziel) if erlaubt else None
        if not isinstance(unit, str) or not unit:
            # Kein Weiterreichen des Werts ins Log ausser gekuerzt: er kommt
            # von aussen und soll das Journal nicht fuellen.
            self.log.warn("wahrnehmung_aus mit unbekanntem Ziel verworfen",
                          DAIMON_ZIEL=str(ziel)[:40])
            self.diag.verworfen("wahrnehmung_ziel")
            return
        try:
            lauf = subprocess.run(
                ["systemctl", "--user", "stop", unit],
                capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            self.log.error("wahrnehmung_aus fehlgeschlagen", DAIMON_ZIEL=str(ziel),
                           DAIMON_UNIT=unit, DAIMON_GRUND=str(exc)[:200])
            return
        self.log.info("wahrnehmung_aus", DAIMON_ACTION="stop", DAIMON_ZIEL=str(ziel),
                      DAIMON_UNIT=unit, DAIMON_RC=lauf.returncode,
                      DAIMON_GRUND=(lauf.stderr or "").strip()[:200])

    def _zaehle_abweisung(self, typ: str) -> None:
        """Diagnose-Zaehler fuer abgewiesene Anfragen. Nur zaehlen -- keine
        turn_id, keine Nonce, kein Hash: die Diagnose ist kein Auskunftskanal
        ueber Geheimnisse."""
        if typ == "intent_mark":
            self.diag.zaehle("rundenmarke", "abgelehnt")
        elif typ == "freigabe":
            self.diag.zaehle("aktionsfreigabe", "abgelehnt")

    def _verarbeite_auth(self, event: Event) -> bool:
        """`intent_mark` und `freigabe` vom auth-Socket. True = angenommen,
        False = abgewiesen (die Verbindung wird geschlossen, nicht der Hub).

        `ipc.pruefe_typ` hat den Typ bereits gegen PRODUZENTEN gewehrt; hier
        koennen nur noch die beiden auth-Typen ankommen.
        """
        p = event.payload or {}
        try:
            if event.type == "intent_mark":
                # Die turn_id erzeugt der HUB, nicht der Absender (Design
                # 2.4: "Die Marke bleibt im Hub"). Eine mitgeschickte
                # turn_id im Payload wird nicht gelesen.
                self.marken.ausgeben(quelle="auth",
                                     turn_id=secrets.token_hex(16))
                self.diag.zaehle("rundenmarke", "ausgegeben")
            else:  # "freigabe"
                # Nonce und Hash kommen aus der Nachricht, alles andere
                # nicht.
                self.freigaben.bestaetigen(
                    nonce=p.get("nonce", ""),
                    action_hash=p.get("action_hash", ""))
                self.diag.zaehle("aktionsfreigabe", "ausgegeben")
            return True
        except MarkenFehler as exc:
            self.log.warn("Auth-Anfrage abgewiesen, Verbindung ab",
                          DAIMON_TYP=event.type, DAIMON_GRUND=str(exc)[:200])
            self._zaehle_abweisung(event.type)
            return False

    def _horche_produzent(self, produzent: str) -> None:
        srv = ipc.listen(self.runtime_dir, produzent)
        self._server.append(srv)
        srv.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, peer = ipc.accept(
                    srv, produzent,
                    audit=lambda was, p: self.log.info(
                        "ipc", DAIMON_ACTION=was, DAIMON_PRODUZENT=produzent,
                        DAIMON_PEER_PID=p.pid, DAIMON_PEER_UNIT=p.unit),
                )
            except socket.timeout:
                continue
            except (ipc.PeerError, OSError):
                continue
            t = threading.Thread(target=self._bediene_produzent,
                                 args=(conn, produzent), daemon=True)
            t.start()
            self._threads.append(t)

    # -- GPU-Ladesperre (T-3.7) --------------------------------------------

    def gpu_anfrage(self, anfrage: object) -> dict:
        """Der ganze Torwaechter: Sperre, Fullscreen, VRAM -- in dieser Folge.

        Warum alle drei hier und nicht im Worker: die VRAM-Pruefung ist nur
        etwas wert, wenn sie UNTER der Sperre laeuft. Prueft ein Worker sein
        VRAM, bevor er die Sperre hat, ist die Zahl veraltet, sobald sie gilt
        -- der andere Ladevorgang belegt sein VRAM ja gerade waehrend man
        wartet. Genau dieser Fall ist der Grund, warum die Sperre im Hub liegt.

        Die Reihenfolge ist nach Kosten und nach Aussagekraft sortiert: die
        Sperre ist lokal und kostet nichts, `busctl` kostet einen Rundlauf,
        `nvidia-smi` einen Prozessstart. Und ein Ladevorgang, der ohnehin an
        der Sperre scheitert, soll nicht zwei Unterprozesse kosten.
        """
        if not isinstance(anfrage, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        art = anfrage.get("art")
        if art == "fertig":
            return self._gpu_freigeben(anfrage.get("sperre"))
        if art != "laden":
            return {"v": 1, "ok": False, "grund": "unbekannte_art"}

        noetig = anfrage.get("vram_mib", 0)
        noetig = int(noetig) if isinstance(noetig, int) else 0
        with self._gpu_lock:
            jetzt = time.monotonic()
            if self._gpu_sperre is not None:
                _, ablauf = self._gpu_sperre
                if jetzt < ablauf:
                    self.diag.verworfen("gpu_lade_sperre")
                    return {"v": 1, "ok": False, "grund": "lade_sperre",
                            "rest_s": round(ablauf - jetzt, 3)}
                # Frist um: oeffnen. Nicht schweigend -- eine verfallene
                # Sperre heisst, dass ein Worker beim Laden gestorben ist.
                self.log.warn("GPU-Ladesperre verfallen -- geoeffnet",
                              DAIMON_FRIST_S=self.gpu_frist_s)
                self.diag.verworfen("gpu_sperre_verfallen")
                self._gpu_sperre = None

            voll = gpu.fullscreen_aktiv()
            if voll is True:
                self.diag.verworfen("gpu_fullscreen")
                return {"v": 1, "ok": False, "grund": "fullscreen"}

            frei = gpu.vram_frei_mib()
            if frei is None or frei - noetig < self.gpu_reserve_mib:
                self.diag.verworfen("gpu_vram")
                return {"v": 1, "ok": False, "grund": "vram",
                        "frei_mib": frei, "noetig_mib": noetig,
                        "reserve_mib": self.gpu_reserve_mib}

            marke = secrets.token_hex(16)
            self._gpu_sperre = (marke, jetzt + self.gpu_frist_s)
            self.log.info("GPU-Ladesperre erteilt", DAIMON_ACTION="gpu_laden",
                          DAIMON_MODELL=str(anfrage.get("modell", "?"))[:40],
                          DAIMON_FREI_MIB=frei, DAIMON_NOETIG_MIB=noetig)
            return {"v": 1, "ok": True, "sperre": marke,
                    "frist_s": self.gpu_frist_s, "frei_mib": frei,
                    "noetig_mib": noetig, "reserve_mib": self.gpu_reserve_mib,
                    # Sichtbar machen, dass bei totem Fokus-Dienst nachgesehen
                    # wurde und die Antwort trotzdem gruen ist.
                    "fullscreen_bekannt": voll is not None}

    def _gpu_freigeben(self, marke: object) -> dict:
        """Nur der Halter gibt frei. Sonst raeumt ein verspaeteter `fertig`-
        Ruf eines toten Workers die Sperre des naechsten weg."""
        with self._gpu_lock:
            if self._gpu_sperre is not None and self._gpu_sperre[0] == marke:
                self._gpu_sperre = None
                return {"v": 1, "ok": True}
            return {"v": 1, "ok": False, "grund": "fremde_sperre"}

    # -- Sprechfreigabe (T-3.9) --------------------------------------------

    def tts_anfrage(self, anfrage: object) -> dict:
        """Der Torwaechter der Stimme: Validator, Abkuehlung, Freigabe.

        Drei Arten:

        * `freigabe` -- Text oder Vorlage rein, sprechbarer Text plus Marke
          raus. Ohne Marke spricht der Dienst nicht; das ist der Grund, warum
          ein Direktzugriff auf den TTS-Socket nichts erreicht.
        * `beginnt` -- der Sprecher hat angefangen. Setzt `voice.tts_active`.
        * `gesprochen` -- fertig. Loescht `tts_active` UND vermerkt die
          Abkuehlung. Vermerkt wird am **Ende**, nicht am Anfang: die Frist
          zaehlt ab dem letzten Ton, sonst laufen 20 s Abkuehlung waehrend
          eines 4 s langen Satzes schon zur Haelfte ab.

        Die Reihenfolge in `freigabe` ist Absicht: **erst Validator, dann
        Abkuehlung**. Ein Text, der die Regeln verletzt, soll `code` oder
        `geheimnis` heissen und nicht `abkuehlung` -- sonst verschwindet ein
        Injektionsversuch hinter einer Frist, und im Journal steht nur, dass es
        zu schnell war.
        """
        if not isinstance(anfrage, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        art = anfrage.get("art")
        if art == "beginnt":
            return self._tts_beginnt(anfrage.get("marke"))
        if art == "gesprochen":
            return self._tts_gesprochen(anfrage.get("marke"))
        if art != "freigabe":
            return {"v": 1, "ok": False, "grund": "unbekannte_art"}

        kanal = str(anfrage.get("kanal", ""))
        if "anlass" in anfrage:
            urteil = sprechtext.aus_vorlage(
                anfrage.get("anlass"), anfrage.get("werte"),
                markierung=str(anfrage.get("markierung", "trusted")))
        else:
            urteil = sprechtext.pruefe(anfrage.get("text"), kanal=kanal)
        if not urteil.ok:
            self.diag.verworfen(f"tts_{urteil.grund}")
            self.log.warn("Sprechtext abgelehnt", DAIMON_ACTION="tts_abgelehnt",
                          DAIMON_KANAL=kanal[:20], DAIMON_GRUND=urteil.grund)
            # Der abgelehnte Text kommt NICHT ins Journal. Er ist genau das
            # Material, das nicht weitergegeben werden soll -- ein Logeintrag
            # waere eine Ausgabe an einer Stelle, die niemand als Ausgabe liest.
            return urteil.als_dict()

        darf, rest_s = self.abkuehlung.darf(kanal)
        if not darf:
            self.diag.verworfen("tts_abkuehlung")
            return {"v": 1, "ok": False, "grund": "abkuehlung",
                    "rest_s": rest_s, "ersatz": ""}

        marke = secrets.token_hex(16)
        with self._tts_lock:
            jetzt = time.monotonic()
            # Abgelaufene Freigaben wegraeumen -- sonst waechst das Buch mit
            # jedem gestorbenen Sprecher.
            self._tts_freigaben = {m: v for m, v in self._tts_freigaben.items()
                                   if v[1] > jetzt}
            self._tts_freigaben[marke] = (kanal, jetzt + self.tts_frist_s)
        self.log.info("Sprechfreigabe erteilt", DAIMON_ACTION="tts_freigabe",
                      DAIMON_KANAL=kanal[:20],
                      DAIMON_ZEICHEN=len(urteil.text))
        return {"v": 1, "ok": True, "text": urteil.text, "marke": marke,
                "kanal": kanal, "frist_s": self.tts_frist_s}

    def _tts_freigabe_holen(self, marke: object, *, entfernen: bool) -> str | None:
        """Kanal zur Marke, oder None. Nur der Halter -- eine fremde oder
        abgelaufene Marke bewegt nichts."""
        with self._tts_lock:
            eintrag = self._tts_freigaben.get(marke) if isinstance(marke, str) else None
            if eintrag is None or eintrag[1] <= time.monotonic():
                return None
            if entfernen:
                del self._tts_freigaben[marke]
            return eintrag[0]

    def _tts_beginnt(self, marke: object) -> dict:
        kanal = self._tts_freigabe_holen(marke, entfernen=False)
        if kanal is None:
            return {"v": 1, "ok": False, "grund": "fremde_marke"}
        self.state.set_voice(tts_active=True)
        # Die Abkuehlung faengt HIER an, nicht erst beim `gesprochen` und nicht
        # schon bei der Freigabe.
        #
        # Beim `gesprochen` allein war sie fuer schnelle Aufrufer wirkungslos:
        # zwei Anfragen hintereinander liefen BEIDE durch, weil die Wiedergabe
        # der ersten noch lief und deshalb nichts vermerkt war -- die zweite
        # unterbrach die erste. Am 03.08. gemessen, zwei `rueckfrage`-Saetze.
        #
        # Bei der FREIGABE war sie zu frueh: eine Freigabe, die nie gesprochen
        # wird -- ein Probelauf, ein toter Dienst, eine fehlende Stimme --
        # haette das Pet fuer die ganze Frist stummgeschaltet. Beim Gegenlesen
        # ist genau das passiert und hat die Entdeckungsphase des Verifizierers
        # abgewuergt.
        #
        # `beginnt` heisst: es sind Samples beim Wiedergabeprozess. Das
        # Restrisiko ist das Fenster zwischen Freigabe und erstem Sample, also
        # der TTFA (gemessen 40--150 ms). Wer in diesem Fenster zweimal
        # anfragt, bekommt zweimal frei -- und die zweite Aeusserung
        # unterbricht die erste, was bei 100 ms Abstand ohnehin die richtige
        # Antwort ist. Beim `gesprochen` wird die Frist neu gesetzt, damit sie
        # ab dem LETZTEN TON zaehlt.
        self.abkuehlung.vermerke(kanal)
        return {"v": 1, "ok": True, "kanal": kanal}

    def _tts_gesprochen(self, marke: object) -> dict:
        kanal = self._tts_freigabe_holen(marke, entfernen=True)
        self.state.set_voice(tts_active=False)
        if kanal is None:
            # `tts_active` wird trotzdem geloescht: ein Sprecher, dessen Marke
            # verfallen ist, spricht sicher nicht mehr, und ein haengendes
            # `true` waere fuer die Rueckkopplungssperre schlimmer als eine
            # verlorene Abkuehlung.
            return {"v": 1, "ok": False, "grund": "fremde_marke"}
        # Neu setzen: die Frist zaehlt ab dem letzten Ton, nicht ab der
        # Freigabe (dort wurde sie nur reserviert, siehe `tts_anfrage`).
        ablauf = self.abkuehlung.vermerke(kanal)
        return {"v": 1, "ok": True, "kanal": kanal,
                "abkuehlung_bis": round(ablauf, 3)}

    # -- State-Socket ------------------------------------------------------

    def _horche_einfach(self, dateiname: str, liefere, *,
                        liest: bool = False) -> None:
        """Ein Socket, eine Zeile JSON, fertig. Fuer State und Diagnose --
        beide sind lesend und brauchen kein Protokoll.

        `liest=True` fuer den GPU-Endpunkt: eine Zeile rein, eine raus. Er ist
        AUSDRUECKLICH kein Produzent (kein `ipc.PRODUZENTEN`-Eintrag, kein
        Bus-Ereignis, keine Zustandsaenderung ausser der Ladesperre). Ein
        Produzentensocket haette ihm eine Rolle im Ereignisprotokoll gegeben,
        die er nicht braucht.
        """
        pfad = self.runtime_dir / dateiname
        if pfad.exists():
            pfad.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(pfad))
        os.chmod(pfad, 0o600)
        srv.listen(8)
        srv.settimeout(0.5)
        self._server.append(srv)
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    if liest:
                        conn.settimeout(GPU_LESE_TIMEOUT_S)
                        roh = conn.makefile("rb").readline(MAX_ZEILE)
                        try:
                            antwort = liefere(json.loads(roh))
                        except (json.JSONDecodeError, ValueError):
                            antwort = {"v": 1, "ok": False, "grund": "unlesbar"}
                    else:
                        antwort = liefere()
                    conn.sendall(json.dumps(antwort).encode() + b"\n")
                except OSError:
                    pass

    # -- Push-Socket -------------------------------------------------------

    def _horche_push(self) -> None:
        """`events.sock`: beim Verbinden sofort ein Snapshot, danach je einer
        pro `rev`-Aenderung. Der Endpunkt liest nichts vom Client.

        Warum Push und nicht der vorhandene lesende `state.sock`: das Face
        haengt damit an einem Deskriptor und braucht **keinen Timer**. Die
        Null-Idle-CPU aus T-1.5 (gemessen: 0,000 % ueber 60 s) war die Zusage,
        auf der die ganze Overlay-Architektur steht -- ein Poll-Timer im Face
        haette sie wieder aufgemacht. Nachgesehen wird stattdessen hier, in
        einem Daemon, der ohnehin Threads haelt.
        """
        pfad = self.runtime_dir / EVENTS_SOCKET
        if pfad.exists():
            pfad.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(pfad))
        os.chmod(pfad, 0o600)
        srv.listen(8)
        srv.settimeout(0.5)
        self._server.append(srv)
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._push_schleife, args=(conn,),
                                 daemon=True)
            t.start()
            self._threads.append(t)

    def _push_schleife(self, conn: socket.socket) -> None:
        letzte_rev = None
        with conn:
            while not self._stop.is_set():
                schnapp = self.state.snapshot()
                if schnapp["rev"] != letzte_rev:
                    try:
                        conn.sendall(json.dumps(schnapp).encode() + b"\n")
                    except OSError:
                        # Client weg. Kein Log: ein Face, das neu startet,
                        # darf keine Zeile im Journal kosten.
                        return
                    letzte_rev = schnapp["rev"]
                self._stop.wait(PUSH_INTERVALL_S)

    # -- Leben --------------------------------------------------------------

    def start(self, produzenten: list[str] | None = None) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.runtime_dir, 0o700)
        for p in produzenten or ["hookbridge", "face", "auth"]:
            t = threading.Thread(target=self._horche_produzent, args=(p,), daemon=True)
            t.start()
            self._threads.append(t)
        for datei, liefere in ((STATE_SOCKET, self.state.snapshot),
                               (DIAG_SOCKET, self.diag.snapshot)):
            t = threading.Thread(target=self._horche_einfach,
                                 args=(datei, liefere), daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._horche_einfach,
                             args=(GPU_SOCKET, self.gpu_anfrage),
                             kwargs={"liest": True}, daemon=True)
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._horche_einfach,
                             args=(TTS_SOCKET, self.tts_anfrage),
                             kwargs={"liest": True}, daemon=True)
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._horche_push, daemon=True)
        t.start()
        self._threads.append(t)
        self.log.info("Hub laeuft", DAIMON_ACTION="start",
                      DAIMON_RUNTIME=str(self.runtime_dir))

    def stop(self) -> None:
        self._stop.set()
        for s in self._server:
            try:
                s.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=2.0)


def main() -> int:
    ap = argparse.ArgumentParser(description="dAImon-Hub")
    ap.add_argument("--runtime-dir", type=Path, default=None)
    args = ap.parse_args()

    _dumpbarkeit_abschalten()
    hub = Hub(runtime_dir=args.runtime_dir)
    hub.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
