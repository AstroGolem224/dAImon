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
from daimon.hub.tickets import Ticketbuch

STATE_SOCKET = "state.sock"
DIAG_SOCKET = "diag.sock"
EVENTS_SOCKET = "events.sock"
GPU_SOCKET = "gpu.sock"
TTS_SOCKET = "tts.sock"
TICKET_SOCKET = "ticket.sock"
# T-4.16 im Betrieb: der Aktionsendpunkt. Wie gpu/tts/ticket AUSDRUECKLICH
# kein Produzent -- kein `ipc.PRODUZENTEN`-Eintrag, kein Bus-Ereignis. Der
# Mind schickt eine Zeile und bekommt eine zurueck; er sendet damit nichts
# in den Ereignisstrom und bekommt keine Rolle darin.
AKTION_SOCKET = "aktion.sock"
# T-5.9b: der Weg des Minds zum Deklassifizierungs-Gate. EIGENER Socket mit
# eigener Unit-Allowlist, dieselbe Bauart wie `recorder.sock`: ein Socket,
# ein Typ, ein erlaubter Absender. Nicht auf `aktion.sock` -- der traegt
# Tickets und Freigaben, und Bildschirmkontext hat dort nichts zu suchen.
# Nicht auf `state.sock` -- der ist der lesende Diagnoseweg, den mehrere
# Dienste kennen, und damit die schwaechste Tuer im Haus.
KONTEXT_SOCKET = "kontext.sock"
KONTEXT_UNITS = ("daimon-mind.service",)
# T-4.16 K3: der deterministische Hub-Parser. EIGENER Socket, dieselbe
# Bauart wie `kontext.sock` -- nicht auf `aktion.sock`, denn `quelle` dort
# ist per Definition IMMER "modell" (Kommentar bei `aktion_anfrage`); ein
# Parser-Ergebnis mit `quelle="parser"` kann von dort aus strukturell nicht
# kommen. `utterance` (der Bus-Event, `ipc.PRODUZENTEN["ears"]`) bleibt
# unberuehrt -- der setzt nur `voice_denkt_an()`, hier steht die Erkennung.
PARSER_SOCKET = "parser.sock"
PARSER_UNITS = ("daimon-ears.service",)
# Exakte Phrasen, kein NLU. Nur `direct: true`-Aktionen OHNE Pflichtparameter
# -- `audio.volume.set` braucht einen Wert, den eine feste Phrase nicht
# liefern kann, und steht deshalb bewusst nicht hier (Design §263: die
# Abkuerzung gilt nur "eindeutigen Kommandos"). Jede Phrase ist normalisiert
# (klein, ohne Satzzeichen) und zeigt auf GENAU eine `action_id` -- die
# Policy prueft trotzdem erneut, ob der Katalog `direct: true` fuer sie
# fuehrt (`policy.py`, Schritt 5); diese Tabelle ist die Erkennung, nicht
# die Erlaubnis.
DIREKT_PHRASEN: dict[str, str] = {
    "pause": "media.playpause",
    "musik pause": "media.playpause",
    "weiter": "media.playpause",
    "nächster titel": "media.next",
    "naechster titel": "media.next",
    "nächstes lied": "media.next",
    "vorheriger titel": "media.previous",
    "voriges lied": "media.previous",
    "stopp": "media.stop",
    "stop": "media.stop",
    "musik stopp": "media.stop",
    "vorspulen": "media.seek.forward",
    "zurückspulen": "media.seek.backward",
    "zurueckspulen": "media.seek.backward",
    "lauter": "audio.volume.up",
    "leiser": "audio.volume.down",
    "stumm": "audio.mute.toggle",
    "stumm schalten": "audio.mute.toggle",
    "nächster schreibtisch": "desktop.next",
    "naechster schreibtisch": "desktop.next",
    "vorheriger schreibtisch": "desktop.previous",
}

# Wer an den uebrigen Endpunkten sprechen darf. Bis zum 19.08. hatte NUR der
# Kontextsocket eine Liste; die Reviewer-Sitzung hat es gemessen (T-4.5,
# Abschnitt ZUSAETZLICH):
#
#     KONTEXT True -- AKTION False, TICKET False, GPU False, TTS False
#
# Jeder Eintrag unten ist ein gemessener Absender aus dem Quelltext, keiner
# ist vorsorglich. Wer eine Liste raet, sperrt im Betrieb den Falschen aus.
#
# Was sie leisten: einen falsch verdrahteten eigenen Dienst aufhalten und im
# Nachhinein sichtbar machen, wer gefragt hat. Einen Angreifer unter dieser
# uid halten sie nicht auf, und das sollen sie nicht (DESIGN.md 1.3).

# `aktion.sock` traegt DREI Dinge, und deshalb steht der Auth-Agent hier
# neben den Brokern: er fragt lesend, was offen ist (auth/agent.py:330); die
# vier Aktionsbroker loesen ihr Ticket unmittelbar vor der Ausfuehrung ein
# (brokers/dienst.py, brokers/dbus/daemon.py); `daimon-mind.service` schickt
# seit T-4.16 K1 `art: "ausfuehren"` (`Mind.frage_werkzeug`, Durchgang 1) --
# der Eintrag fehlte bis dahin ABSICHTLICH, siehe LEDGER-T-4.16.v.md K1: der
# Zulauf existierte nicht, ihn vorsorglich einzutragen haette eine Liste
# geführt, die etwas behauptet. Jetzt existiert er, jetzt steht er hier.
AKTION_UNITS = (
    "daimon-auth.service",
    "daimon-dbus.service",
    "daimon-fs.service",
    "daimon-exec.service",
    "daimon-mind.service",
    "daimon-input.service",
)
# T-3.11: die Kontingente. Vier Verbraucher, alle im Quelltext belegt.
TICKET_UNITS = (
    "daimon-lokal-broker.service",
    "daimon-egress.service",
    "daimon-cli-broker.service",
    "daimon-mind.service",
)
# Die Ladesperre. `daimon-gpu@.service` ist ein TEMPLATE -- der Instanzname
# ist das Modell und steht erst zur Laufzeit fest. Der Eintrag endet deshalb
# auf `@`; was das genau bedeutet, steht bei `ipc.unit_erlaubt`.
GPU_UNITS = (
    "daimon-gpu@",
    # Fremder GPU-Nutzer, und genau dafuer gibt es dieses Gate: mimic fragt
    # vor dem Laden um Erlaubnis, prueft die Antwort streng und respektiert
    # alle drei Absagegruende (mimic/worker.py:987 `request_gpu_permission`).
    # Wer sich an die Serialisierung haelt, gehoert an sie herangelassen --
    # sonst laden zwei Modelle gleichzeitig und beide scheitern am VRAM des
    # jeweils anderen (Design 5.4).
    #
    # NACHGETRAGEN am 19.08., nachdem die Allowlist ihn ausgesperrt hatte.
    # Das war keine bloss unbequeme Abweisung, sondern eine STILLE
    # Regression: `worker.py:992` faengt `OSError` und faellt dann OFFEN --
    # "nur ein nicht erreichbarer Hub faellt offen". Ein Connection reset
    # sieht fuer ihn aus wie ein toter Hub, also lud er ohne Sperre weiter.
    # Die Serialisierung war fuer ihn ausgefallen, ohne dass es irgendwo
    # rot wurde.
    "mimic-worker.service",
)
TTS_UNITS = ("daimon-tts.service",)

# Die PRODUZENTEN-Sockets. Sie fehlten am 19.08. bei `d5c012b` -- die Listen
# oben sitzen in `_horche_einfach`, und `_horche_produzent` ist eine ANDERE
# Funktion, die `ipc.accept` ohne `erlaubte_units` rief. Gefunden hat es die
# Reviewer-Sitzung am laufenden System, mit Positiv- und
# Unterscheidungskontrolle: kontext/aktion/ticket wiesen ihre Unit ab,
# auth/face/ears/hookbridge nahmen sie an.
#
# Das wog schwerer als die vier Endpunkte davor: `auth.sock` gibt auf
# `intent_mark` eine frische RUNDENMARKE aus -- genau die Marke, auf die sich
# `aktion.sock` verlaesst. Der Wegweiser stand an den unwichtigeren Tueren.
#
# Die Uebergabe hatte an dieser Stelle die falsche Vermutung notiert (4.2):
# ein Absender, der den Socketpfad zusammensetzt statt ihn zu nennen. Den
# gibt es nicht. Die Luecke war eine ganze Funktion, und meine Suche ging
# ueber die Aufrufer statt ueber die Annahmestellen.

# Die Scope, in die sich ein PRUEFLAUF haengt, um `hookbridge.sock` zu
# erreichen. Ohne sie laeuft ein Pruefstand unter der Unit seiner Sitzung
# (gemessen: `app-com.anthropic.Claude-2954.scope`), und `_horche_produzent`
# weist ihn ab -- `tests/verify/T-0.9.sh` war darum in zwei Kriterien rot.
# Vorbild und Vertrag: `daimon/plan/daemon.py:72` fuehrt `daimon-plan-cli.scope`
# genauso als exakten Eintrag; `ipc.unit_erlaubt` vergleicht exakt, die
# `.service`-Bedingung sitzt nur im `@`-Template-Zweig.
#
# DER HANDEL, ausdruecklich:
#   WAS ER OEFFNET: `hookbridge.sock` und damit genau `ipc.PRODUZENTEN
#   ["hookbridge"] == {"hook"}` -- SICHTBARKEIT (Blase, Stimmung), keine
#   FAEHIGKEIT. Ein `hook` erzeugt keine Rundenmarke, keine Freigabe, keinen
#   Auftrag an einen Broker.
#   WER IHN BENUTZEN DARF: wer eine Scope dieses Namens anlegen kann, also
#   jeder Prozess unter dieser uid. Das ist hinnehmbar, weil die
#   Peer-Pruefung ein Wegweiser ist und keine Authentifizierung
#   (DESIGN.md 1.3) -- sie schliesst den BEILAEUFIGEN Weg, nicht den
#   absichtlichen.
#   WARUM NUR HIER: `auth` traegt als einzige Typenmenge Faehigkeit
#   (`intent_mark`, `freigabe`, `ptt`) -- `auth.sock` gibt die Rundenmarke
#   aus, auf die sich `aktion.sock` verlaesst; heute haelt die Liste
#   `daimon-cli-broker.service` von dort fern. `plan` schreibt in den
#   Terminspeicher und auf das Overlay. `face` und `ears` melden Zustand des
#   Nutzers (Wegklicken, Transkript), den ein Prueflauf nicht faelschen
#   koennen soll. Keiner der vier bekommt diesen Eintrag; wer ihn braucht,
#   braucht eine eigene Begruendung an dieser Stelle.
VERIFY_SCOPE = "daimon-verify.scope"

PRODUZENT_UNITS: dict[str, tuple[str, ...]] = {
    # hookbridge/bridge.py:249 -- der einzige Schreiber im Betrieb.
    # VERIFY_SCOPE ist der zweite, ausdrueckliche Eintrag; Begruendung oben.
    "hookbridge": ("daimon-hookbridge.service",),  # MUTATION
    # face/src/hub.rs:139. Das Overlay meldet Blasen, Abschaltungen und seit
    # T-7.4 den Privatmodus.
    "face": ("daimon-face.service",),
    # auth/agent.py ueber `--hub-socket %t/daimon/auth.sock` in der Unit.
    "auth": ("daimon-auth.service",),
    # T-8.4: der Zeitplaner meldet faellige Termine und das Ende von
    # Fokusbloecken. Der Hub bildet beides auf `warnblase` ab.
    "plan": ("daimon-plan.service",),
    # KEIN gemessener Schreiber: `utterance` sendet im Quelltext niemand, der
    # Ohren-Dienst meldet sein Transkript ueber `recorder.sock`. Der Eintrag
    # steht hier trotzdem, und zwar aus einem anderen Grund als bei
    # `AKTION_UNITS`: dort haette `daimon-mind.service` eine Vermutung
    # ueber einen kuenftigen Absender bedeutet. Hier ist die Zuordnung
    # definiert -- der Socket heisst nach dem Dienst, und `ipc.PRODUZENTEN`
    # weist `utterance` genau ihm zu. Wer das aendert, aendert die Tabelle.
    "ears": ("daimon-ears.service",),
}
# Wie lange der Hub auf die Antwort des Menschen wartet. Laenger als ein
# Blick, kuerzer als ein Kaffee -- und danach `cancelled`, nicht `declined`.
RUECKFRAGE_FRIST_S = 120.0
# Wohin ein Auftrag geht. Die Namen sind Vertrag mit den Units unter
# config/systemd/ -- exec und input laufen erst, wenn eine Aktion sie
# braucht, aber der Weg gehoert dem Hub, nicht dem Auftrag.
BROKER_SOCKETS = {"dbus": "dbus-broker.sock", "fs": "fs-broker.sock",
                  "exec": "exec-broker.sock", "input": "input-broker.sock"}
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
# T-7.4: wie lange ein Privatmodus laeuft. Im Hub und NICHT in der Nachricht:
# ein Absender, der die Dauer waehlt, kann auch `0` waehlen -- und haette
# einen Privatmodus angefordert, der nichts tut, aber im Journal steht.
#
# Fuenfzehn Minuten, weil das die Groessenordnung eines Telefonats oder eines
# diktierten Passworts ist. Laenger waere ein abgeschalteter Mitschnitt mit
# einem beruhigenden Namen -- genau das, was `privat_setzen` mit seiner Frist
# verhindern soll.
PRIVAT_DAUER_S = 15 * 60.0
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

# T-3.11: das Ticketbuch aus T-0.8, hier zum ERSTEN MAL verdrahtet. Es war
# gebaut und getestet (12 Tests), aber kein Prozess hat es instanziiert -- also
# gab es die Zusage "hoechstens einmal einloesbar" auf Papier und nicht im
# laufenden System. Der Egress-Broker ist ihr erster Verbraucher.
#
# `frist_s`: ein Ticket ist eine Autorisierung, nicht ein Guthaben. Fuenf Minuten
# reichen fuer eine Anfrage samt Nachdenken, und laenger offen zu stehen macht ein
# gestohlenes Ticket wertvoller.
TICKET_DATEI = "tickets.json"
TICKET_FRIST_S = 300.0

# T-4.6, verdrahtet am 17.08.: wie oft der Kettenkopf des Audits ins Journal
# geht. Der Modulkopf von `audit.py` nennt drei Pruefstellen und sagt dazu:
# "Findet keine davon statt, ist die Kette wertlos und gehoert gestrichen
# statt behauptet." Es fand keine statt -- `verankern()` hatte genau einen
# Aufrufer (`rotieren`), und den rief niemand. `pruefe` meldete das die ganze
# Zeit selbst: "keine Journal-Anker gefunden; die Kette ist nur gegen sich
# selbst geprueft und damit gegen eine Neuberechnung blind."
#
# Eine Stunde deckelt das Fenster, in dem eine komplett neu gerechnete Datei
# unentdeckt bliebe: sie muesste auch die Anker der Vergangenheit treffen,
# und die stehen ausserhalb ihrer Reichweite im Journal. Kuerzer waere ein
# `systemd-cat` je Minute fuer nichts -- die Kette wandert nur, wenn etwas
# passiert.
AUDIT_ANKER_INTERVALL_S = 3600.0


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
        # T-7.3: das Sprite soll zeigen, wenn mitgeschnitten wird. Der Hub
        # liest dafuer den Herzschlag des Recorders -- eine winzige Datei im
        # Laufzeitverzeichnis. Kein Import des Recorders: nur der Pfad und
        # die Frist, beide in `pause` benannt.
        from daimon.recorder.pause import schneidet_mit
        self.state.set_mitschnitt_quelle(
            lambda: schneidet_mit(self.runtime_dir))
        self.diag = Diagnose()
        self.bus = Bus()
        # T-1.7: Marken- und Freigabebuch leben im Hub (Design 2.4: "Die
        # Marke bleibt im Hub"). Der Auth-Agent meldet nur; ausgegeben und
        # bestaetigt wird hier.
        self.marken = MarkenBuch(log=self.log)
        self.freigaben = FreigabeBuch(log=self.log)
        # T-5.9b: das Gate aus T-5.9, hier zum ERSTEN MAL verdrahtet. Es war
        # gebaut und geprueft (vier Pruefstaende), aber kein Prozess hat es
        # instanziiert -- also erreichte passiv Wahrgenommenes das Modell
        # nicht, weil niemand fragte, und nicht, weil das Gate verweigerte.
        # Dieselbe Sorte Luecke wie beim Ticketbuch in T-3.11.
        #
        # Erst beim ersten Aufruf gebaut: der Kontextspeicher liest beim
        # Anlegen nichts, aber die Archivsuche braucht `data_dir()`, und der
        # Hub soll ohne Archiv starten koennen.
        self._gate = None
        # Getrennt gehalten, weil er je Anfrage neu von der Platte liest --
        # siehe `_gate_teile`.
        self._speicher = None
        # Das gemeinsame Audit, siehe `audit_buch`.
        self._audit = None
        # T-3.7: die Ladesperre. Hoechstens ein Ladevorgang gleichzeitig, und
        # zwar HIER, weil ein Worker nur sich selbst kennt. `_gpu_sperre` ist
        # (Marke, Ablauf in monotoner Zeit) -- monoton, weil eine
        # NTP-Korrektur keine Sperre aufheben und keine erzeugen darf.
        # T-4.16: erst beim ersten Aufruf gebaut, siehe `_aktionsteile`.
        self._aktion = None
        self.consent = None
        # `aktion.sock` bedient nebenlaeufig (ein Thread je Verbindung).
        # Ohne dieses Schloss bauen zwei gleichzeitige ERSTE Aktions-
        # anfragen je eigene Koordinatoren/Consents/Schlangen/Auftrags-
        # buecher und mehrere Audit-Objekte auf demselben Verzeichnis
        # (Befund T-4.11 K8) -- `audit_buch()` sagt im eigenen Docstring,
        # warum das kaputt ist.
        self._aktion_lock = threading.RLock()
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
        # Welche Marke gerade spricht. Ohne dieses Feld loescht die verspaetete
        # Meldung einer abgebrochenen Aeusserung die Sperre einer neueren --
        # seit T-3.16 liegt zwischen Freigabe und erstem Ton eine Prozessgrenze
        # mehr, und damit ist das kein Randfall mehr.
        self._tts_aktive_marke: str | None = None
        self.tts_frist_s = float(self.cfg.get("tts.freigabefrist_s", TTS_FRIST_S))
        self.abkuehlung = Abkuehlung(
            Path(self.cfg.state_dir) / TTS_ABKUEHLUNG_DATEI,
            cfg=self.cfg, log=self.log)
        # T-3.11: Kontingente fuer den Egress. Persistent, weil ein Neustart des
        # Hubs sonst jedes ausgegebene Ticket wieder gueltig machen wuerde -- und
        # "hoechstens einmal" waere dann "hoechstens einmal je Hub-Laufzeit".
        self.ticket_frist_s = float(
            self.cfg.get("hub.ticket_frist_s", TICKET_FRIST_S))
        self.tickets = Ticketbuch(
            Path(self.cfg.state_dir) / TICKET_DATEI,
            frist_s=self.ticket_frist_s, log=self.log)
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
                    elif event.type == "privatmodus":
                        self._privatmodus()
                    else:
                        self.state.clear_bubble()
                    continue
                if produzent == "plan":
                    # T-8.4: der Zeitplaner meldet `termin_faellig` oder
                    # `fokus_ende` (mehr laesst `pruefe_typ` nicht durch).
                    # Beides wird eine Blase -- Sichtbarmachung, keine
                    # Handlung. Die Texte kommen aus der Plan-Datenbank und
                    # laufen durch dieselbe Saeuberung wie Hook-Text.
                    #
                    # `erinnerungsblase`, nicht `warnblase`: eine Erinnerung
                    # darf die Meldung "Audit-Kette gerissen" nicht vom
                    # Bildschirm schieben (Befund 26.08.).
                    p_plan = event.payload or {}
                    if not self.state.erinnerungsblase(
                            wert_saeubern(str(p_plan.get("titel", ""))),
                            wert_saeubern(str(p_plan.get("text", "")))):
                        # Zurueckgestellt, nicht verworfen: `HubState` haelt
                        # sie und zeigt sie, sobald die dringende Blase weg
                        # ist. Der Plan-Dienst hat keinen Rueckkanal.
                        self.log.warn(
                            "Erinnerung zurueckgestellt, dringende Blase steht",
                            DAIMON_ACTION="plan_blase_zurueckgestellt",
                            DAIMON_TYP=event.type)
                    continue
                if event.type == "utterance":
                    # T-3.14: hier faengt `processing` an. Kein neuer Typ und
                    # kein neuer Socket -- der Hub sieht die Aeusserung ohnehin,
                    # und was er ohnehin sieht, muss ihm niemand extra sagen.
                    self.state.voice_denkt_an()
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

    def _privatmodus(self) -> None:
        """T-7.4: der Mitschnitt pausiert -- Bild UND Ton, auf Zeit.

        DER ANLASS (Karte 62b90c95, abgespalten von T-7.2): der Tonpfad hat
        genau eine Sperre, und das ist dieser Modus. `urteil_ton` prueft ihn
        und sonst nichts, mit Begruendung: die Anwendungs-Denylist sperrt
        FENSTER, und ein gesprochener Satz hat keines. Nur konnte ihn bis
        heute niemand einschalten -- `privat_setzen` hatte im Produktivcode
        keinen einzigen Aufrufer, festgehalten vom Waechter in
        `tests/test_gate_zulauf.py`. Wer ein Passwort diktierte, hatte es im
        Archiv.

        **Warum ueber eine Nachricht und nicht per Datei aus dem Face.** Das
        Face DARF nach `%t/daimon` schreiben (`ReadWritePaths`), und der
        Bildschirm-Widerruf geht genau so -- er vermerkt eine leere Datei.
        Hier waere das falsch: diese Datei traegt einen WERT, den Ablauf in
        Sekunden. Das Format stuende dann einmal in Rust und einmal in
        Python, und zwei Fassungen einer Regel sind eine Regel und eine
        Attrappe. Wer den Ablauf in einer der beiden Sprachen anfasst, merkt
        es nicht. So schreibt genau eine Stelle: `redaktion.privat_setzen`,
        also das Modul, das ihn auch liest.

        **Warum ein neuer Nachrichtentyp vertretbar ist.** Dieselben drei
        Punkte, an denen `ipc.py` schon `wahrnehmung_aus` (T-2.7) und `ptt`
        (T-3.14) gemessen hat:

          1. EINSEITIG. Nur einschalten. Ein Gegenstueck gibt es nicht -- der
             Modus laeuft von selbst ab, und das ist der Grund, warum er eine
             Frist traegt und kein Schalter ist.
          2. FAIL-SAFE. Schlimmster Missbrauch: es wird weniger archiviert.
             Das ist die Richtung, in die ein Fehler laufen darf.
          3. KEINE FAEHIGKEIT. Keine Unit wird benannt, keine gestartet oder
             gestoppt, keine Freigabe erteilt, keine Rundenmarke erzeugt. Die
             Nutzlast ist leer -- selbst die Dauer kommt nicht aus der
             Nachricht, sondern steht hier.

        Die Dauer steht im Hub und nicht in der Nachricht, weil ein Absender,
        der sie waehlt, auch `0` waehlen kann -- und damit einen Privatmodus
        anfordern wuerde, der nichts tut, aber im Journal steht.
        """
        from daimon.recorder.redaktion import privat_setzen

        try:
            ablauf = privat_setzen(self.runtime_dir, PRIVAT_DAUER_S)
        except OSError as exc:
            # Laut scheitern: ein Privatmodus, der nicht greift, ist der
            # gefaehrlichste Zustand hier -- der Nutzer glaubt, es sei still.
            self.log.error("Privatmodus NICHT gesetzt", DAIMON_ACTION="privat",
                           DAIMON_GRUND=str(exc)[:200])
            self.diag.verworfen("privatmodus")
            return
        self.log.info("Privatmodus", DAIMON_ACTION="privat",
                      DAIMON_DAUER_S=PRIVAT_DAUER_S, DAIMON_ABLAUF=ablauf)

    def _zaehle_abweisung(self, typ: str) -> None:
        """Diagnose-Zaehler fuer abgewiesene Anfragen. Nur zaehlen -- keine
        turn_id, keine Nonce, kein Hash: die Diagnose ist kein Auskunftskanal
        ueber Geheimnisse."""
        if typ == "intent_mark":
            self.diag.zaehle("rundenmarke", "abgelehnt")
        elif typ == "freigabe":
            self.diag.zaehle("aktionsfreigabe", "abgelehnt")

    def _rueckfrage_schliessen(self, nonce: str, *,
                               zustand: str = "granted") -> None:
        """Die zum Nonce gehoerende Rueckfrage auf `zustand` setzen.

        Ohne offene Rueckfrage passiert nichts -- der eingefrorene Pruefstand
        T-1.7 schickt Freigaben ohne Rueckfrage, und die sollen weiter genau
        so durchlaufen wie bisher.
        """
        if self.consent is None:
            return
        rueckfrage = self.consent.antwort_zu_nonce(nonce)
        if rueckfrage is None:
            return
        try:
            self.consent.antwort(rueckfrage_id=rueckfrage.id,
                                 nonce=rueckfrage.nonce,
                                 absender=rueckfrage.absender,
                                 zustand=zustand, jetzt=time.monotonic())
        except Exception as fehler:
            self.log.warn("Rueckfrage nicht schliessbar",
                          DAIMON_GRUND=str(fehler)[:120])

    def _verarbeite_auth(self, event: Event) -> bool:
        """`intent_mark` und `freigabe` vom auth-Socket. True = angenommen,
        False = abgewiesen (die Verbindung wird geschlossen, nicht der Hub).

        `ipc.pruefe_typ` hat den Typ bereits gegen PRODUZENTEN gewehrt; hier
        koennen nur noch die beiden auth-Typen ankommen.
        """
        p = event.payload or {}
        if event.type == "ptt":
            # T-3.14: nur ein Wahrheitswert. Alles andere ist Unsinn und kein
            # Rollenbruch -- die Zeile faellt weg, die Verbindung bleibt. Die
            # Uhr fuer die Frist ist die des Hubs; ein Absender soll die Dauer
            # eines Zustands nicht steuern koennen.
            an = p.get("an")
            if not isinstance(an, bool):
                self.log.warn("ptt ohne Wahrheitswert verworfen",
                              DAIMON_WERT=str(an)[:40])
                return True
            self.state.set_voice(listening=an)
            return True
        try:
            if event.type == "intent_mark":
                # Die turn_id erzeugt der HUB, nicht der Absender (Design
                # 2.4: "Die Marke bleibt im Hub"). Eine mitgeschickte
                # turn_id im Payload wird nicht gelesen.
                self.marken.ausgeben(quelle="auth",
                                     turn_id=secrets.token_hex(16))
                self.diag.zaehle("rundenmarke", "ausgegeben")
            else:  # "freigabe"
                # `antwort` fehlt -> `granted` (T-1.7 ist eingefroren und
                # schickt das Feld nie; sie soll weiter genau wie bisher
                # laufen). Bei `declined`/`cancelled` bleibt das
                # FREIGABEBUCH UNBERUEHRT -- ein Nein erzeugt keine
                # Freigabe (Befund T-4.11 K5/K9); nur die offene Rueckfrage
                # wird mit genau diesem Zustand geschlossen.
                antwort = str(p.get("antwort") or "granted")
                if antwort == "granted":
                    # Nonce und Hash kommen aus der Nachricht, alles andere
                    # nicht.
                    self.freigaben.bestaetigen(
                        nonce=p.get("nonce", ""),
                        action_hash=p.get("action_hash", ""))
                    self.diag.zaehle("aktionsfreigabe", "ausgegeben")
                    # REIHENFOLGE: erst das Buch, dann hier. Das Buch ist die
                    # eingefrorene Zusage aus T-1.7; scheitert es, darf hier
                    # nichts passiert sein.
                    self._rueckfrage_schliessen(p.get("nonce", ""),
                                                zustand="granted")
                elif antwort in ("declined", "cancelled"):
                    self.diag.zaehle("aktionsfreigabe", "abgelehnt")
                    self._rueckfrage_schliessen(p.get("nonce", ""),
                                                zustand=antwort)
                else:
                    self.log.warn("Unbekannte Antwort verworfen",
                                  DAIMON_ANTWORT=antwort[:40])
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
                    # Seit dem 19.08. auch hier -- die Listen aus `d5c012b`
                    # sassen nur in `_horche_einfach`, und diese Funktion ist
                    # eine andere. `None` fuer einen unbekannten Produzenten
                    # heisst weiterhin "keine Pruefung": ein Prueflauf ohne
                    # systemd startet eigene Produzenten, und der soll nicht
                    # an einer Liste scheitern, die es fuer ihn nicht gibt.
                    erlaubte_units=PRODUZENT_UNITS.get(produzent),
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

    # -- Kontingente (T-3.11) ----------------------------------------------

    def ticket_anfrage(self, anfrage: object) -> dict:
        """Ausgeben und Einloesen. Dasselbe Muster wie `gpu.sock` und
        `tts.sock`: eine Zeile rein, eine raus, **kein Produzent** (kein
        `ipc.PRODUZENTEN`-Eintrag, kein Bus-Ereignis).

        Warum der Hub das haelt und nicht der Egress: ein Kontingent, das der
        Prozess ausgibt, der es auch verbraucht, ist eine Zaehlung und keine
        Autorisierung. Design 2.4 sagt es fuer Marken, und fuer Tickets gilt es
        genauso -- der Egress kann sich hier nichts selbst erteilen, weil er die
        Ausgabe nicht erreicht, ohne dass der Hub sie protokolliert.
        """
        if not isinstance(anfrage, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        art = anfrage.get("art")
        if art not in ("ausgeben", "einloesen"):
            return {"v": 1, "ok": False, "grund": "unbekannte_art",
                    "meldung": f"art={str(art)[:40]!r}"}

        auftrag_hash = anfrage.get("auftrag_hash")
        if not isinstance(auftrag_hash, str) or not auftrag_hash.strip():
            return {"v": 1, "ok": False, "grund": "kein_hash",
                    "meldung": "Feld `auftrag_hash` fehlt oder ist leer"}

        if art == "ausgeben":
            try:
                ticket = self.tickets.ausgeben(auftrag_hash=auftrag_hash)
            except MarkenFehler as exc:
                self.diag.verworfen("ticket_ausgabe")
                return {"v": 1, "ok": False, "grund": "abgelehnt",
                        "meldung": str(exc)[:200]}
            self.log.info("Kontingent ausgegeben",
                          DAIMON_ACTION="ticket_ausgabe",
                          DAIMON_ZWECK=str(anfrage.get("zweck", ""))[:40])
            return {"v": 1, "ok": True, "ticket": ticket,
                    "frist_s": self.ticket_frist_s}

        ticket = anfrage.get("ticket")
        if not isinstance(ticket, str) or not ticket.strip():
            return {"v": 1, "ok": False, "grund": "kein_ticket",
                    "meldung": "Feld `ticket` fehlt oder ist leer"}
        try:
            self.tickets.einloesen(ticket, auftrag_hash=auftrag_hash)
        except MarkenFehler as exc:
            # Ein Grund, viele Ursachen -- und das ist Absicht: unbekannt,
            # abgelaufen, schon verbraucht und "gehoert zu anderem Auftrag"
            # duerfen sich fuer den Aufrufer NICHT unterscheiden. Sonst ist die
            # Absage ein Orakel, mit dem sich gueltige Ticket-IDs von
            # abgelaufenen trennen lassen. Das Detail steht im Journal.
            self.diag.verworfen("ticket_abgelehnt")
            self.log.warn("Kontingent abgelehnt",
                          DAIMON_ACTION="ticket_abgelehnt",
                          DAIMON_GRUND=str(exc)[:160])
            return {"v": 1, "ok": False, "grund": "ticket_ungueltig"}
        self.log.info("Kontingent eingeloest", DAIMON_ACTION="ticket_einloesung")
        return {"v": 1, "ok": True}


    # -- Aktionen (T-4.16 im Betrieb) --------------------------------------

    def _aktionsteile(self):
        """Policy, Consent, Auftragsbuch, Schlange und Audit -- einmal gebaut.

        Erst beim ersten Aufruf: ein Hub, der den Katalog beim Start liest,
        stirbt an einer kaputten `core.yaml`, auch wenn nie eine Aktion
        kommt. Die Anzeige der Sitzungen ist das taegliche Versprechen; sie
        darf nicht an einer Datei haengen, die nur der Aktionspfad braucht.

        Unter `self._aktion_lock`: `aktion.sock` bedient nebenlaeufig, und
        zwei gleichzeitige ERSTE Aufrufe bauten sonst zwei Koordinatoren mit
        je eigenem Consent/Schlange/Auftragsbuch (Befund T-4.11 K8).
        """
        with self._aktion_lock:
            if self.consent is None:
                from daimon.hub.consent import Consent
                # EIN Buch: die Autoritaet bleibt `self.freigaben` (T-1.7,
                # eingefroren). Consent haelt daneben nur, was dort fehlt --
                # offene Rueckfragen, Absender, Persistenz und den
                # Unterschied zwischen `declined` und `cancelled`.
                self.consent = Consent.laden(
                    Path(self.cfg.state_dir) / "consent",
                    buch=self.freigaben)
            if self._aktion is None:
                from daimon.hub.action_queue import Aktionsschlange
                from daimon.hub.coordinator import Koordinator
                from daimon.hub.order import Auftragsbuch
                from daimon.hub.policy import Policy

                hub = self

                class HubKoordinator(Koordinator):
                    def consent_abwarten(self, rueckfrage):
                        # Der Weg zum Menschen: die Rueckfrage steht offen,
                        # der Auth-Agent zeigt sie und meldet die Antwort
                        # ueber seinen `freigabe`-Produzentenpfad zurueck.
                        # Hier wird gewartet -- in DIESEM Thread, nicht in
                        # der Hauptschleife; `aktion.sock` bedient je
                        # Verbindung.
                        #
                        # Keine Antwort ist `cancelled` und kein `declined`.
                        # Ein Zeitablauf ist kein Nein.
                        hub.log.info("Rueckfrage offen",
                                     DAIMON_ACTION="aktion_rueckfrage",
                                     DAIMON_RUECKFRAGE=rueckfrage.id)
                        return hub.consent.warten(
                            rueckfrage, timeout_s=RUECKFRAGE_FRIST_S)

                self._aktion = HubKoordinator(
                    policy=Policy.laden(),
                    consent=self.consent,
                    auftragsbuch=Auftragsbuch(),
                    schlange=Aktionsschlange(),
                    # DASSELBE Audit wie das Gate -- eine Kette, ein Objekt.
                    audit=self.audit_buch(),
                    broker=self._auftrag_zustellen,
                    vorschau=self._vorschau_bauen,
                    sprechen=self._sprechen,
                    undo=self._undo_vorbereiten)
            return self._aktion

    def _undo_vorbereiten(self, *, action_id: str, params: dict):
        """T-4.8: das Artefakt VOR der Mutation. Faellt es, faellt die Aktion.

        Wirft `UndoFehler`, und dann ruft der Koordinator den Broker
        gar nicht erst. Eine Aktion, die nichts zu verlieren hat,
        braucht kein Artefakt -- die steht hier nicht drin.
        """
        from daimon.brokers.fs import undo

        ziel = Path(str((params or {}).get("pfad") or ""))
        if action_id == "fs.file.delete":
            return undo.vorbereiten("trash", quelle=ziel)
        if action_id == "fs.file.write":
            return undo.vorbereiten(
                "kopie", quelle=ziel,
                ablage=Path(self.cfg.state_dir) / "undo")
        if action_id == "git.discard":
            return undo.vorbereiten("git-stash", repo=ziel)
        return None

    def _vorschau_bauen(self, *, action_id: str, params: dict) -> str:
        """Feste Vorlage aus Design 2.4, escapte Werte (T-1.7). Kein Modelltext.

        Bis zum 20.08. baute diese Methode ihren eigenen Text
        (`f"Aktion: {action_id}..."`) statt `preview.vorschau()` zu rufen --
        eine zweite Fassung derselben Regel, gemessen und benannt in
        LEDGER-T-4.12.v.md (K6). Der Katalogeintrag entscheidet die
        Umkehr-Beschriftung: eine Gegenaktion (`reversible_by`) heisst
        `gegenaktion`, eine folgenlose Aktion ohne Gegenaktion `unkritisch`,
        und `destructive: true` ohne Gegenaktion `keine`.
        """
        from daimon.auth.preview import VorschauFehler, pfad_saeubern, vorschau
        from daimon.hub.policy import Policy

        try:
            ziel = pfad_saeubern(str(next(iter((params or {}).values()), "")))
        except VorschauFehler:
            ziel = "(unlesbar)"
        eintrag = Policy.laden().katalog.get(action_id, {})
        if eintrag.get("reversible_by"):
            umkehr = "gegenaktion"
        elif eintrag.get("destructive"):
            umkehr = "keine"
        else:
            umkehr = "unkritisch"
        try:
            return vorschau(aktion=action_id, ziel=ziel, umkehr=umkehr)
        except VorschauFehler:
            # Ein unbekannter Aktionsschluessel ist nach Policy schon
            # abgewiesen (unknown_action, deny) -- kommt er trotzdem hierher,
            # zeigt diese Vorlage weder den rohen Schluessel noch bricht sie
            # den Aktionsweg ab.
            return "Ember will eine unbekannte Aktion ausführen. Bitte ablehnen."

    def _sprechen(self, text: str) -> None:
        """Ueber den TTS-Torwaechter, nicht am ihm vorbei.

        Der Weg ist derselbe wie fuer jede andere Aeusserung: Validator,
        Abkuehlung, Freigabe. Faellt er aus, bleibt es still -- eine Stimme,
        die sich am Torwaechter vorbei meldet, waere eine zweite Quelle.
        """
        try:
            antwort = self.tts_anfrage({"v": 1, "art": "freigabe",
                                        "kanal": "reaktion", "text": text,
                                        "marke": "trusted"})
            if not antwort.get("ok"):
                self.log.info("Sprachausgabe abgelehnt",
                              DAIMON_GRUND=str(antwort.get("grund"))[:60])
        except Exception as fehler:  # der Aktionspfad haengt nicht an der Stimme
            self.log.warn("Sprachausgabe fehlgeschlagen",
                          DAIMON_GRUND=str(fehler)[:120])

    def _auftrag_zustellen(self, auftrag) -> dict:
        """Den kanonischen Auftrag an den Broker seiner `audience`.

        Der Hub kennt den Socket, nicht der Auftrag: stuende der Pfad im
        Auftrag, koennte ein Absender sich seinen Broker aussuchen.
        """
        from daimon.common.order import kanonisch

        dateiname = BROKER_SOCKETS.get(auftrag.audience)
        if dateiname is None:
            return {"ok": False, "grund": "kein_broker",
                    "meldung": f"fuer {auftrag.audience!r} gibt es keinen Weg"}
        pfad = self.runtime_dir / dateiname
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(15.0)
                c.connect(str(pfad))
                c.sendall(kanonisch(auftrag) + b"\n")
                roh = c.recv(4096)
        except OSError as fehler:
            return {"ok": False, "grund": "broker_weg",
                    "meldung": f"{pfad}: {fehler}"}
        try:
            return json.loads(roh.decode("utf-8", "replace"))
        except ValueError:
            return {"ok": False, "grund": "broker_antwort_unlesbar"}

    def aktion_anfrage(self, anfrage: object) -> dict:
        """Eine Aktionsanfrage vom Mind. Eine Zeile rein, eine raus.

        Die Rundenmarke kommt aus dem MARKENBUCH, nicht aus der Anfrage --
        der Absender schickt nur die `turn_id`. Design 1357: ein Feld, das
        der Absender setzt, sagt nichts. Und `initiator` leitet die Policy
        daraus selbst ab.
        """
        if not isinstance(anfrage, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        if anfrage.get("art") == "ticket_einloesen":
            # Die Einloesung, um die der Broker unmittelbar vor der
            # Ausfuehrung bittet (T-4.5). Sie gehoert HIERHER, weil
            # "hoechstens einmal" eine Aussage ueber alle Broker zusammen ist
            # -- loeste jeder sein Ticket selbst ein, waere derselbe Auftrag
            # bei zwei Brokern zweimal ausfuehrbar.
            from daimon.common.order import AuftragsFehler

            self._aktionsteile()
            try:
                auftrag = self._aktion.auftragsbuch.einloesen(
                    str(anfrage.get("ticket") or ""))
            except AuftragsFehler as fehler:
                # Ein Grund, viele Ursachen -- wie beim Kontingent in T-3.11:
                # unbekannt, abgelaufen und verbraucht duerfen sich fuer den
                # Aufrufer nicht unterscheiden, sonst ist die Absage ein
                # Orakel ueber gueltige Ticket-IDs. Das Detail steht im
                # Journal.
                self.log.warn("Ticket abgelehnt",
                              DAIMON_ACTION="aktion_ticket",
                              DAIMON_GRUND=str(fehler)[:160])
                return {"v": 1, "ok": False, "grund": "ticket_ungueltig"}
            return {"v": 1, "ok": True, "action_id": auftrag.action_id}
        if anfrage.get("art") == "offene":
            # Der Weg, auf dem der Auth-Agent von einer Rueckfrage erfaehrt.
            # LESEND: er holt sich, was offen ist, und bekommt Nonce und
            # action_hash mit -- beides braucht er, um zu bestaetigen, und
            # beides hat der Hub selbst erzeugt. Der Vorschautext kommt
            # ebenfalls von hier: der Auth-Agent formuliert nichts.
            self._aktionsteile()
            offen = [{"id": r.id, "nonce": r.nonce,
                      "action_id": r.action_id,
                      "action_hash": r.action_hash,
                      "prompt_shown": r.prompt_shown,
                      "destructive": bool(self._aktion.policy.katalog.get(
                          r.action_id, {}).get("destructive")),
                      "frist": r.frist}
                     for r in self.consent.offen.values()]
            return {"v": 1, "ok": True, "offen": offen}
        if anfrage.get("art") != "ausfuehren":
            return {"v": 1, "ok": False, "grund": "unbekannte_art",
                    "meldung": f"art={str(anfrage.get('art'))[:40]!r}"}
        action_id = anfrage.get("action_id")
        if not isinstance(action_id, str) or not action_id.strip():
            return {"v": 1, "ok": False, "grund": "keine_aktion"}
        params = anfrage.get("params") or {}
        if not isinstance(params, dict):
            return {"v": 1, "ok": False, "grund": "params_unlesbar"}
        # Die `turn_id` kommt NICHT aus der Anfrage, wenn der Hub eine
        # offene Runde hat: sie entsteht im Markenbuch und wird nirgends
        # herausgegeben. Ein Absender koennte sie nur raten -- oder sie waere
        # ihm gesagt worden, und dann waere sie ein Feld, das er setzt
        # (Design 1357). Eine mitgeschickte gilt nur als Rueckfallwert fuer
        # Pruefstaende, die ohne Markenbuch messen.
        turn_id = self.marken.aktuelle() or str(anfrage.get("turn_id") or "")

        # Die Marke wird NACHGESCHLAGEN. `initiator()` ist eine Auskunft und
        # veraendert nichts -- eingeloest wird die Runde nicht hier, sonst
        # waere eine Runde mit zwei Aktionen nach der ersten tot.
        gueltig = self.marken.initiator(turn_id) == "user"
        marke = {"id": turn_id, "gueltig_bis": float("inf")} if gueltig else None

        teile = self._aktionsteile()
        # Die audience kommt aus dem KATALOG, nicht aus der Anfrage -- sonst
        # suchte sich der Absender seinen Broker aus, sobald es mehr als
        # einen Weg gibt (dasselbe Prinzip wie beim Socketpfad oben).
        audience = str(teile.policy.katalog.get(action_id, {})
                       .get("audience") or "dbus")
        try:
            lauf = teile.ausfuehren(
                action_id=action_id, params=params,
                # DIE QUELLE GEHOERT DEM HUB, nicht der Nachricht. Hier stand
                # `anfrage.get("quelle")` -- und `policy.entscheide` gibt die
                # Direktbefehl-Ausnahme genau dann, wenn sie `"parser"` sagt.
                # Wer das Feld mitschickte, bekam `allow` ohne Vorschau, wo
                # ehrlich `ask` gestanden haette. Gemessen von T-4.4 am
                # 18.08. ueber den echten Socket (K8, produktdefekt-rot).
                #
                # DER SOCKETWEG KANN NIE DER PARSER SEIN, und das ist die
                # strukturelle Antwort statt einer Pruefung: ein
                # deterministischer Hub-Parser laeuft IM Hub und ruft den
                # Koordinator direkt. Was durch `aktion.sock` kommt, ist per
                # Definition eine fremde Anfrage -- also `modell`.
                #
                # Dieselbe Regel wie bei der `audience` drei Zeilen darueber,
                # und derselbe Satz aus dem Modulkopf von `policy.py`: "Ein
                # Feld, das der Absender setzt, sagt nichts." Sie galt dort
                # fuer `initiator` und `params_hash` -- und nicht fuer die
                # `quelle`, die als einzige eine Schranke oeffnet.
                quelle="modell",
                marke=marke, session_id=str(anfrage.get("session_id") or ""),
                turn_id=turn_id,
                tool_use_id=str(anfrage.get("tool_use_id") or ""),
                audience=audience)
        except Exception as fehler:
            self.log.error("Aktionspfad gescheitert",
                           DAIMON_ACTION="aktion_fehler",
                           DAIMON_GRUND=str(fehler)[:200])
            # T-4.16.v K6: ein Hop, der wirft, blieb bisher stumm -- fuer den
            # Menschen am Overlay war eine still gescheiterte Aktion von einer
            # ausgefuehrten nicht zu unterscheiden. Dieselbe Vorlage wie im
            # Koordinator (Design 5.2: kuratiert, keine Modellformulierung).
            from daimon.hub.coordinator import SPRACHE
            self._sprechen(SPRACHE["broker"])
            return {"v": 1, "ok": False, "grund": "aktionspfad",
                    "meldung": str(fehler)[:200]}

        self.log.info("Aktion bearbeitet", DAIMON_ACTION="aktion",
                      DAIMON_AKTION_ID=action_id[:60],
                      DAIMON_VERDIKT=lauf.verdikt,
                      DAIMON_AUSGEFUEHRT=str(lauf.ausgefuehrt))
        return {"v": 1, "ok": True, "verdikt": lauf.verdikt,
                "ausgefuehrt": lauf.ausgefuehrt, "direkt": lauf.direkt,
                "grund": lauf.grund, "gesprochen": lauf.gesprochen,
                "dauer_ms": lauf.dauer_ms}

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
            # Fehlt `markierung`, gilt `tainted` -- nie `trusted`. Eine
            # Vorgabe, die die staerkste Marke verschenkt, macht die Regel
            # fuer `tts_ungefragt` durch WEGLASSEN umgehbar (Befund 26.08.).
            urteil = sprechtext.aus_vorlage(
                anfrage.get("anlass"), anfrage.get("werte"),
                markierung=str(anfrage.get("markierung", "tainted")))
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

        # Zwei Faelle duerfen die Abkuehlung umgehen, und beide sind
        # Entscheidungen, nicht Bequemlichkeiten:
        #
        # UNTERBRECHUNG. Solange wirklich gesprochen wird, ist die naechste
        # Aeusserung eine Korrektur und kein zweites Geschwaetz -- ein Pet, das
        # seinen eigenen laufenden Satz nicht abbrechen kann, ist genau das
        # Aergernis, das die Abkuehlung verhindern soll. Die Gegenprobe: nach
        # dem Ende der unterbrechenden Aeusserung greift die Frist wieder, denn
        # `gesprochen` setzt sie neu. Eine Kette von Unterbrechungen ist damit
        # kein Umweg, sondern ein einziger, immer wieder abgebrochener Satz.
        #
        # ERSATZSATZ. Er ist die Antwort auf eine abgelehnte Aeusserung
        # (Design §8.3: "sagt das Pet, dass die Antwort auf dem Bildschirm
        # steht"). Unterliegt er der Abkuehlung, dann sagt das Pet es beim
        # ersten Mal und schweigt danach -- und Schweigen ist von einem
        # abgestuerzten Dienst nicht zu unterscheiden. Am 03.08. gemessen: von
        # zehn Angriffstexten wurde genau einer beantwortet.
        #
        # ponytail: der Ersatzsatz hat KEINE eigene Frist. Obergrenze: er ist
        # anfragegetrieben, ein flutender Client flutet also sich selbst. Sobald
        # ein echter Produzent (Mind) das tut, gehoert hier eine eigene, kurze
        # Frist hin -- und die haette dann nichts mit `abkuehlung` zu tun.
        spricht_noch = bool(self.state.snapshot()["voice"].get("tts_active"))
        ist_ersatz = anfrage.get("anlass") == sprechtext.ERSATZ_VORLAGE
        if not darf and not (spricht_noch or ist_ersatz):
            self.diag.verworfen("tts_abkuehlung")
            return {"v": 1, "ok": False, "grund": "abkuehlung",
                    "rest_s": rest_s, "ersatz": ""}
        unterbrechung = (not darf) and spricht_noch and not ist_ersatz

        marke = secrets.token_hex(16)
        with self._tts_lock:
            jetzt = time.monotonic()
            # Abgelaufene Freigaben wegraeumen -- sonst waechst das Buch mit
            # jedem gestorbenen Sprecher.
            self._tts_freigaben = {m: v for m, v in self._tts_freigaben.items()
                                   if v[1] > jetzt}
            self._tts_freigaben[marke] = (kanal, jetzt + self.tts_frist_s,
                                          ist_ersatz)
        self.log.info("Sprechfreigabe erteilt", DAIMON_ACTION="tts_freigabe",
                      DAIMON_KANAL=kanal[:20],
                      DAIMON_ZEICHEN=len(urteil.text))
        return {"v": 1, "ok": True, "text": urteil.text, "marke": marke,
                "kanal": kanal, "frist_s": self.tts_frist_s,
                # Sichtbar machen, wenn eine Abkuehlung umgangen wurde. Eine
                # stille Umgehung waere eine Zusage, die im Protokoll fehlt.
                "unterbrechung": unterbrechung, "ersatz_freigabe": ist_ersatz,
                "rest_s": rest_s}

    def _tts_freigabe_holen(self, marke: object, *,
                            entfernen: bool) -> tuple[str, bool] | None:
        """`(Kanal, ist_ersatz)` zur Marke, oder None. Nur der Halter -- eine
        fremde oder abgelaufene Marke bewegt nichts."""
        with self._tts_lock:
            eintrag = self._tts_freigaben.get(marke) if isinstance(marke, str) else None
            if eintrag is None or eintrag[1] <= time.monotonic():
                return None
            if entfernen:
                del self._tts_freigaben[marke]
            return (eintrag[0], eintrag[2])

    def _tts_beginnt(self, marke: object) -> dict:
        eintrag = self._tts_freigabe_holen(marke, entfernen=False)
        if eintrag is None:
            return {"v": 1, "ok": False, "grund": "fremde_marke"}
        kanal, ist_ersatz = eintrag
        with self._tts_lock:
            self._tts_aktive_marke = marke if isinstance(marke, str) else None
        self.state.set_voice(tts_active=True)
        # Gedacht wurde offenbar zu Ende: es wird gesprochen. T-3.14 §2.
        self.state.voice_denkt_aus()
        if ist_ersatz:
            # Der Ersatzsatz vermerkt KEINE Abkuehlung: er ist die Antwort auf
            # eine Ablehnung und darf die naechste echte Aeusserung nicht
            # blockieren. Siehe die Begruendung in `tts_anfrage`.
            return {"v": 1, "ok": True, "kanal": kanal, "ersatz": True}
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
        eintrag = self._tts_freigabe_holen(marke, entfernen=True)
        # Nur die Marke, die zuletzt `beginnt` gemeldet hat, darf die Sperre
        # loeschen. Sonst raeumt die verspaetete Meldung einer abgebrochenen
        # Aeusserung das Mikrofon frei, waehrend die naechste noch spricht --
        # und die Rueckkopplungssperre hoert der eigenen Stimme zu.
        with self._tts_lock:
            eigene = self._tts_aktive_marke is None or marke == self._tts_aktive_marke
            if eigene:
                self._tts_aktive_marke = None
        if eigene:
            self.state.set_voice(tts_active=False)
        if eintrag is None:
            # `tts_active` wird trotzdem geloescht: ein Sprecher, dessen Marke
            # verfallen ist, spricht sicher nicht mehr, und ein haengendes
            # `true` waere fuer die Rueckkopplungssperre schlimmer als eine
            # verlorene Abkuehlung.
            return {"v": 1, "ok": False, "grund": "fremde_marke"}
        kanal, ist_ersatz = eintrag
        if ist_ersatz:
            return {"v": 1, "ok": True, "kanal": kanal, "ersatz": True}
        # Neu setzen: die Frist zaehlt ab dem LETZTEN TON, nicht ab dem Beginn
        # (dort wurde sie schon einmal gesetzt, siehe `_tts_beginnt`). Das ist
        # zugleich die Gegenprobe zur Unterbrechungs-Umgehung: eine Kette von
        # Unterbrechungen verlaengert die Frist, statt sie zu umgehen.
        ablauf = self.abkuehlung.vermerke(kanal)
        return {"v": 1, "ok": True, "kanal": kanal,
                "abkuehlung_bis": round(ablauf, 3)}

    # -- State-Socket ------------------------------------------------------

    def _eine_verbindung(self, conn, liefere, liest: bool) -> None:
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

    # -- T-5.9b: Deklassifizierung ------------------------------------------

    def audit_buch(self):
        """DAS Audit dieses Hubs. Eines, nicht zwei.

        Die Kette traegt `seq` und `prev_hash` im Objekt. Zwei Instanzen auf
        dasselbe Verzeichnis waeren zwei Ketten, die sich gegenseitig
        ueberschreiben -- jede fuer sich stimmig, zusammen kaputt, und
        auffallen wuerde es erst bei `pruefen`.

        Bis zum 17.08. baute nur `_aktionsteile()` eines, und zwar in sich
        selbst. `_gate_teile()` fragte `getattr(self, "audit", None)` -- ein
        Feld, das es nie gab. Das Gate schrieb also NICHTS, und die Frage
        "was hat es wann freigegeben" war unbeantwortbar. Jede Freigabe und
        jede Ablehnung soll protokolliert werden (declassify.py); im Betrieb
        wurde nichts geschrieben, weil `_schreiben` still auf `None` lief.

        Unter `self._aktion_lock` (RLock -- `_aktionsteile()` ruft diese
        Methode bereits innerhalb desselben Locks): ohne die Sperre bauten
        zwei gleichzeitige erste Aufrufer je ein eigenes Audit-Objekt auf
        demselben Verzeichnis -- zwei Ketten, die sich gegenseitig
        ueberschreiben (Befund T-4.11 K8).
        """
        with self._aktion_lock:
            if self._audit is None:
                from daimon.hub.audit import Audit
                self._audit = Audit.oeffnen(Path(self.cfg.state_dir) / "audit")
            return self._audit

    def audit_verankern(self) -> dict:
        """Die Kette pruefen und ihren Kopf ins Journal schreiben.

        DIE REIHENFOLGE IST DIE AUSSAGE: erst pruefen, dann verankern. Wer
        zuerst verankert, schreibt den Kopf einer womoeglich schon
        manipulierten Datei fest und macht den Anker zum Komplizen.

        Ein Befund wird LAUT, aber er haelt den Hub nicht an: eine gerissene
        Kette ist ein Verdacht auf Vergangenes, kein Grund, die Gegenwart
        einzustellen. Der Nutzer sieht sie ueber `--verify`, das Journal
        ueber diese Zeile.
        """
        from daimon.hub.audit import pruefe

        buch = self.audit_buch()
        befund = pruefe(buch.verzeichnis)
        # `ok` verlangt Anker. Beim ALLERERSTEN Lauf gibt es noch keine, und
        # das ist kein Fund -- gemeldet wird, was die Kette selbst sagt.
        kette_kaputt = [f for f in befund["fehler"] if "Anker" not in f]
        # „Datei fehlt" ist zweideutig, und die Blase macht den Unterschied
        # teuer: beim ALLERERSTEN Start hat noch niemand geschrieben, und
        # eine dringende Warnung bei jeder Neuinstallation ist genau die
        # Sorte, die man nach dem dritten Mal wegklickt.
        #
        # Die Unterscheidung liefert der zweite Strom, und dafuer gibt es
        # ihn: existieren ANKER im Journal, hat die Kette einmal existiert --
        # dann ist die fehlende Datei ein Fund. Ohne Anker ist es der erste
        # Lauf.
        #
        # Bis zum 19.08. stand das nicht da, weil der Befund nur ins Journal
        # ging und dort niemanden stoerte. Wer eine Meldung sichtbar macht,
        # erbt ihre Falschalarme.
        if befund["anker_gefunden"] == 0:
            kette_kaputt = [f for f in kette_kaputt if f != "Datei fehlt"]
        if kette_kaputt:
            self.log.warn("AUDIT-KETTE GERISSEN", DAIMON_ACTION="audit_kaputt",
                          DAIMON_SAETZE=befund["saetze"],
                          DAIMON_FEHLER="; ".join(kette_kaputt)[:300])
            # T-4.6 K5: und der Nutzer SIEHT es. Bis zum 19.08. stand hier
            # nur die Zeile darueber -- eine Warnung im Journal liest
            # niemand, der nicht ohnehin schon sucht, und das Overlay blieb
            # ruhig. Design 7.5 verlangt eine Blase mit hoher Dringlichkeit,
            # und es gab auf diesem Weg keinen Aufrufer.
            #
            # Der Text nennt KEINEN Pfad und keinen Satzinhalt: die Blase
            # steht auf dem Bildschirm, und was in der Kette steht, ist genau
            # das, was nicht dorthin gehoert. Wo nachzusehen ist, weiss
            # `--verify`.
            self.state.warnblase(
                "Audit-Kette gerissen",
                f"{len(kette_kaputt)} Abweichung(en) in der Vergangenheit. "
                "Pruefen mit: daimon-hub --verify")
        else:
            self.log.info("Audit geprueft", DAIMON_ACTION="audit_geprueft",
                          DAIMON_SAETZE=befund["saetze"],
                          DAIMON_ANKER=befund["anker_gefunden"],
                          DAIMON_ANKER_GETROFFEN=befund["anker_getroffen"])
        buch.verankern()
        return befund

    def _audit_schleife(self) -> None:
        """Stuendlich pruefen und verankern. Sofort beim Start, nicht erst
        nach einer Stunde: ein Hub, der taeglich neu startet, haette sonst
        nie einen Anker gesetzt."""
        while not self._stop.is_set():
            try:
                self.audit_verankern()
            except Exception as exc:      # noqa: BLE001
                # Ein klemmender Anker darf den Hub nicht mitnehmen. Er faellt
                # ohnehin auf: beim naechsten `pruefe` fehlt er.
                self.log.warn("Anker gescheitert",
                              DAIMON_GRUND=f"{type(exc).__name__}: {exc}"[:200])
            self._stop.wait(AUDIT_ANKER_INTERVALL_S)

    def _gate_teile(self):
        """Marken, Kontextspeicher, Archiv -- beim ersten Aufruf gebaut.

        `laden()` JE ANFRAGE, und das ist keine Vorsicht, sondern die
        Bedingung dafuer, dass hier ueberhaupt etwas herauskommt: die Ringe
        fuellt der AUGEN-Prozess, dieser hier sieht nur dessen Dateien. Ein
        Speicher, der nie liest, gibt fuer immer leere Listen heraus -- das
        Gate antwortet dann `ok` mit leerem Umfang, und von aussen ist das
        von "nichts gesehen" nicht zu unterscheiden. Einmal im Konstruktor zu
        lesen waere die zweite Gestalt desselben Fehlers: der Stand des
        Hub-Starts, fuer den Rest der Sitzung.

        Die Archivsuche braucht das nicht -- sie oeffnet die Datenbank je
        Freigabe neu.
        """
        if self._gate is None:
            from daimon.eyes.context import Kontextspeicher
            from daimon.hub.declassify import Deklassifizierung
            from daimon.recorder.suche import Archivsuche
            self._speicher = Kontextspeicher()
            self._gate = Deklassifizierung(
                marken=self.marken,
                speicher=self._speicher,
                archiv=Archivsuche(),
                audit=self.audit_buch())
        if self._speicher is not None:
            self._speicher.laden()
        return self._gate

    def kontext_anfrage(self, anfrage: object) -> dict:
        """Der eine Weg des Minds an den Bildschirmkontext.

        DIE `turn_id` KOMMT NICHT AUS DER ANFRAGE. Sie entsteht im Hub aus
        `secrets.token_hex` und verlaesst ihn nie; der Hub fragt sich selbst
        ueber `MarkenBuch.aktuelle()`, welche Runde offen ist. Ein Absender,
        der sie nennen muesste, koennte sie nur raten -- oder sie waere ihm
        gesagt worden, und dann waere sie ein Feld, das der Absender setzt.

        Der Mind fragt IMMER; das Gate entscheidet. Eine zweite Kopie der
        Bezugsliste im Prozess mit dem Modell waere eine zweite Wahrheit --
        genau der Riss, der bei der Denylist aufgefallen ist.
        """
        from daimon.hub.declassify import GateFehler

        if not isinstance(anfrage, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        if anfrage.get("art") != "deklassifizieren":
            return {"v": 1, "ok": False, "grund": "unbekannte_art"}
        text = anfrage.get("text")
        if not isinstance(text, str) or not text.strip():
            return {"v": 1, "ok": False, "grund": "kein_text"}

        try:
            gate = self._gate_teile()
            freigabe = gate.freigeben(aeusserung=text,
                                      turn_id=self.marken.aktuelle())
            # Die EINLOESUNG zaehlen, und zwar hier: `MarkenBuch.einloesen`
            # kennt die Diagnose nicht, und `declassify` soll sie nicht
            # kennen muessen. Der Zaehler stand seit T-0.9 in `diag.TYPEN`
            # und hatte KEINEN Schreiber -- am 17.08. bei der ersten Messung
            # der Naht aufgefallen: das Journal meldete `einloesung`, der
            # Zaehler blieb auf 0, und die Vorrichtung las daraus "Station
            # nicht getragen". Ein Falschbefund ueber eine heile Kette.
            self.diag.zaehle("rundenmarke", "eingeloest")
        except GateFehler as exc:
            self.diag.verworfen(f"kontext_{exc.grund}")
            return {"v": 1, "ok": False, "grund": exc.grund}
        except Exception as exc:  # noqa: BLE001
            # Ein klemmender Kontextspeicher darf den Sprachpfad nicht
            # mitreissen: der Mind bekommt eine Absage und antwortet ohne
            # Bildschirm, statt gar nicht zu antworten.
            self.log.warn("Deklassifizierung gescheitert",
                          DAIMON_GRUND=f"{type(exc).__name__}: {exc}"[:200])
            return {"v": 1, "ok": False, "grund": "gate_weg"}

        # Herausgegeben wird TEXT, nicht das Objekt: `Marked` ueberlebt keine
        # JSON-Grenze, und der Empfaenger markiert selbst neu. Deshalb steht
        # `senke` mit dabei -- der Mind darf das nur in Durchgang 2 verwenden.
        return {"v": 1, "ok": True, "turn_id": freigabe.turn_id,
                "umfang": freigabe.umfang, "senke": freigabe.senke,
                "eintraege": [str(getattr(e, "value", e))
                              for e in freigabe.eintraege],
                "archiv": [str(getattr(e, "value", e))
                           for e in freigabe.archiv]}

    def parser_anfrage(self, anfrage: object) -> dict:
        """T-4.16 K3: der deterministische Hub-Parser.

        `erkannt=False` ist der Normalfall -- die meisten Aeusserungen sind
        keine der wenigen exakten Phrasen. Der Aufrufer (Ohren-Dienst) faehrt
        dann unveraendert mit Mind fort; er wartet auf DIESE Antwort, bevor
        er das tut, sonst koennte dieselbe Aeusserung zweimal wirken --
        einmal hier, direkt, und einmal ueber Durchgang 1 (T-4.16 K1).
        """
        if not isinstance(anfrage, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        if anfrage.get("art") != "erkennen":
            return {"v": 1, "ok": False, "grund": "unbekannte_art"}
        text = anfrage.get("text")
        if not isinstance(text, str) or not text.strip():
            return {"v": 1, "ok": False, "grund": "kein_text"}
        # T-4.16.v Runde 2, K3: `marken.aktuelle()` bleibt 120 s offen und
        # wird vom Aktionspfad absichtlich nicht eingeloest ("sonst waere
        # eine Runde mit zwei Aktionen nach der ersten tot", `_verarbeite_
        # aktion`-Kommentar). Ohne diese Pruefung haette jede MITGEHOERTE
        # Aeusserung in diesem Fenster gewirkt, ohne Vorschau -- derselbe
        # Angriff, den T-4.19 fuer den Mind-Weg schon verbietet
        # (`router.py`: `was == "aktion" and marke != "user_ptt"`). Der
        # Hub-Parser ist der MAECHTIGERE Weg (keine Vorschau) und braucht
        # mindestens dieselbe Schranke.
        if str(anfrage.get("marke") or "") != "user_ptt":
            return {"v": 1, "ok": True, "erkannt": False}

        normalisiert = " ".join(text.strip().lower().split())
        action_id = DIREKT_PHRASEN.get(normalisiert)
        if action_id is None:
            return {"v": 1, "ok": True, "erkannt": False}

        turn_id = self.marken.aktuelle() or ""
        gueltig = bool(turn_id) and self.marken.initiator(turn_id) == "user"
        marke = {"id": turn_id, "gueltig_bis": float("inf")} if gueltig else None
        teile = self._aktionsteile()
        audience = str(teile.policy.katalog.get(action_id, {})
                       .get("audience") or "dbus")
        try:
            lauf = teile.ausfuehren(
                action_id=action_id, params={},
                # DER EINZIGE ORT, an dem "parser" je steht -- dieser Socket
                # traegt sonst nichts, und `aktion.sock` kann es strukturell
                # nicht (Kommentar bei `aktion_anfrage`).
                quelle="parser", marke=marke, session_id="",
                turn_id=turn_id, tool_use_id=secrets.token_hex(8),
                audience=audience)
        except Exception as fehler:
            self.log.error("Hub-Parser gescheitert",
                           DAIMON_ACTION="parser_fehler",
                           DAIMON_GRUND=str(fehler)[:200])
            from daimon.hub.coordinator import SPRACHE
            self._sprechen(SPRACHE["broker"])
            return {"v": 1, "ok": True, "erkannt": True,
                    "action_id": action_id, "ausgefuehrt": False,
                    "grund": "aktionspfad"}

        self.log.info("Hub-Parser erkannt", DAIMON_ACTION="parser",
                      DAIMON_AKTION_ID=action_id[:60],
                      DAIMON_VERDIKT=lauf.verdikt,
                      DAIMON_AUSGEFUEHRT=str(lauf.ausgefuehrt))
        return {"v": 1, "ok": True, "erkannt": True, "action_id": action_id,
                "verdikt": lauf.verdikt, "ausgefuehrt": lauf.ausgefuehrt,
                "grund": lauf.grund, "gesprochen": lauf.gesprochen}

    def _horche_einfach(self, dateiname: str, liefere, *,
                        liest: bool = False, nebenlaeufig: bool = False,
                        erlaubte_units: tuple[str, ...] | None = None) -> None:
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
            # Seit dem 19.08. setzen FUENF Endpunkte `erlaubte_units`, nicht
            # mehr nur der Kontextsocket (T-5.9b): dazu Aktion, Ticket, GPU
            # und TTS. Gemessen hatte die Reviewer-Sitzung genau eine gesetzte
            # Liste von fuenf -- und ueber `aktion.sock` laufen Aktionsbitte
            # und Ticketeinloesung.
            #
            # Die Peer-Pruefung bleibt, was sie ist: ein Wegweiser und keine
            # Authentifizierung (DESIGN.md 1.3, dort seit dem 19.08. samt der
            # Feststellung, dass ein Ersatz fuer die gestrichene Signatur
            # nicht vorgesehen ist). Sie haelt einen falsch verdrahteten
            # eigenen Dienst auf und macht im Nachhinein sichtbar, wer gefragt
            # hat.
            #
            # `ipc.unit_erlaubt` und NICHT `not in` von Hand: die Templates
            # (`daimon-gpu@`) brauchen den Instanzvergleich, und zwei
            # Fassungen dieser Regel waeren eine Regel und eine Attrappe.
            if erlaubte_units is not None:
                try:
                    peer = ipc.peer_of(conn, dateiname)
                    if not ipc.unit_erlaubt(peer.unit, erlaubte_units):
                        raise ipc.PeerError(f"Unit {peer.unit!r}")
                except ipc.PeerError as exc:
                    self.log.warn("Anfrage von fremder Unit",
                                  DAIMON_SOCKET=dateiname,
                                  DAIMON_GRUND=str(exc)[:120])
                    conn.close()
                    continue
            if nebenlaeufig:
                # Ein Thread je Verbindung -- und zwar NUR fuer den
                # Aktionsendpunkt. Grund, am 10.08. als Haenger gemessen: der
                # Broker loest sein Ticket ueber DENSELBEN Socket ein,
                # waehrend der Hub noch im Auftrag steckt. Sequentiell
                # nehmen heisst hier: der Hub wartet auf den Broker, der
                # Broker auf den Hub. Die anderen Endpunkte bleiben
                # sequentiell -- sie rufen niemanden, der zurueckruft.
                threading.Thread(target=self._eine_verbindung,
                                 args=(conn, liefere, liest),
                                 daemon=True).start()
                continue
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
        # `ears` steht seit T-3.14 dabei: der Ohren-DIENST entsteht erst in
        # T-3.15, aber der Socket muss vorher da sein, sonst ist `processing`
        # nirgends messbar -- und ein Zustand, den niemand messen kann, ist
        # eine Behauptung.
        # `plan` seit T-8.4: der Zeitplaner meldet faellige Termine hier.
        for p in produzenten or ["hookbridge", "face", "auth", "ears", "plan"]:
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
                             kwargs={"liest": True,
                                     "erlaubte_units": GPU_UNITS},
                             daemon=True)
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._horche_einfach,
                             args=(TTS_SOCKET, self.tts_anfrage),
                             kwargs={"liest": True,
                                     "erlaubte_units": TTS_UNITS},
                             daemon=True)
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._horche_einfach,
                             args=(TICKET_SOCKET, self.ticket_anfrage),
                             kwargs={"liest": True,
                                     "erlaubte_units": TICKET_UNITS},
                             daemon=True)
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._horche_einfach,
                             args=(AKTION_SOCKET, self.aktion_anfrage),
                             kwargs={"liest": True, "nebenlaeufig": True,
                                     "erlaubte_units": AKTION_UNITS},
                             daemon=True)
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._horche_einfach,
                             args=(KONTEXT_SOCKET, self.kontext_anfrage),
                             kwargs={"liest": True,
                                     "erlaubte_units": KONTEXT_UNITS},
                             daemon=True)
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._horche_einfach,
                             args=(PARSER_SOCKET, self.parser_anfrage),
                             kwargs={"liest": True,
                                     "erlaubte_units": PARSER_UNITS},
                             daemon=True)
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._horche_push, daemon=True)
        t.start()
        self._threads.append(t)
        # T-4.6: der zweite Strom. Ohne diesen Faden gibt es keine Anker, und
        # ohne Anker ist die Hash-Kette nur gegen sich selbst pruefbar -- eine
        # neu gerechnete Datei ist gegen sich selbst immer stimmig.
        t = threading.Thread(target=self._audit_schleife, daemon=True)
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
