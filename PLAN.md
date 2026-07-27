# Plan: dAImon — Desktop-Pet als Familiar und Assistent

_Round 3 revision — regenerated from the v3.0 architecture, not patched_

> **Documents under review** (these are the plan; this file is the contestable summary):
> - `/home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Design.md` **v3.0** — read §1.2 first
> - `/home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Implementierungsplan.md` **v3.0**
>
> Existing code: `pet_daemon.py`, `pet_client.gd`, `claude-hooks.json`, `PHASE3.md`.
> German documents; review in English is fine.

## Goal

Extend an existing agent-status pet into a desktop familiar: answers when addressed, reads the screen for context, controls the PC under explicit confirmation via a reviewed whitelist, and has a configurable character. Single machine, single user, KDE Plasma 6.7.3 on Wayland, RTX 5090. Facts marked `[V]` were verified live on this machine.

## Threat model (Design §1.2) — read this before judging any claim

**In scope:** injected instructions in observed content (screen text, window titles, **hook payloads**, files); spoofed audio (speakers, video, our own TTS); model error; accidental disclosure.

**Explicitly out of scope:** an attacker already executing code as this uid. Such a process can ptrace peers, control `systemd --user`, read any 0600 runtime file, and call `kglobalaccel.invokeShortcut` to manufacture the "push-to-talk" event. Unit names, file modes and signatures cannot exclude them.

Consequently the vocabulary is fixed: **intent mark** (Auth reported a user action — not proof a human acted), **trusted component path** (model output does not flow here — not "an attacker cannot"), **peer check** (`SO_PEERPIDFD`, a signpost between our own components — not authentication), **ticket** (single-use and deadline — not cryptographic provenance).

This is what made round 2's HMAC deletion correct rather than evasive: the key was ptrace-readable by the excluded attacker, and a broker cannot verify with a key that exists only in the Hub. **v3.0 exists because v2.1 stated this model but left eight dependent sections describing the superseded mechanics.** This revision regenerates both documents rather than patching them.

## What changed since round 2

1. **Both documents regenerated** against §1.2. Service inventory is now canonical (Design §2.1: twelve services + one KWin script + one worker-spawned helper); every count and diagram derives from it. Previous versions said "eight", "eleven" and "thirteen" in different places.
2. **The Hub is named as the trusted computing base**, with explicit input validation and failure boundaries, instead of being implicitly treated as attack-surface-free.
3. **Taint model reworked.** Four labels: `user_ptt`, `user_audio`, `trusted`, `tainted`. Three fixes: (a) wake-word speech is `user_audio` and reaches neither the tool-capable pass nor memory nor proactive triggers — otherwise spoofed audio writes durable instructions; (b) free-text hook fields are `tainted`, contradicting v2.1 which called them trusted while the threat model called hooks injectable; (c) taint derives from **data provenance, not the fetching component** — a broker result carrying file contents or a window title is tainted in that part. Labels are typed values that survive IPC, serialization and DB round-trips; protected sinks reject raw strings as a type error.
4. **The Auth preview is a declared taint sink.** v2.1 promised Auth "never renders model text" while requiring meaningful confirmation — unresolvable. Resolution: fixed template, fixed labels, parameter values escaped, length-bounded, visibly quoted; no free text, no markup, no control characters.
5. **The direct-command exception is Hub-owned:** it fires only when the catalog marks the action `direct: true` **and** a deterministic Hub parser recognized it in the utterance. Anything model-derived goes through the preview regardless.
6. **The verifier regime became a task graph.** v2.1 stated rules without creating tasks. Now 33 reviewer-owned `.v` tasks precede their implementation tasks, each with mutants per acceptance criterion, and verifier hashes are frozen in `tests/verify/FROZEN` — the phase gate fails if a builder modified one. T-1.7 no longer builds the superseded Face-owned PTT path first.
7. **Self-reported values eliminated** from gates: the P−1 blocking set is hard-coded rather than read from investigator JSON; `restart_prompted` is derived from portal signals by the verifier; mood distinctness is compared as image hashes of the pet region, not sprite identifiers; pixel probes use a randomized marker in a controlled region with before/after capture.
8. **T-5.10's egress capture cannot itself leak:** structural markers and hashes only, test-profile only.

## Approach

Twelve services, each boundary listed in Design §2.1 with the capability it removes:

| Group | Services | Network | System access |
|---|---|---|---|
| Core | `hub` (TCB), `auth`, `hookbridge` | bridge: loopback listen only | no |
| Perception | `ears`, `eyes` | **no** (`RestrictAddressFamilies=AF_UNIX`) | read-only |
| Cognition | `mind`, `gpu@`, `egress` | **only `egress`**; `mind` has no token and no `AF_INET` | no |
| Actuation | `dbus`, `fs`, `exec`, `input` | no | one capability each |
| Presentation | `face` | no | no |

**Core rule:** passive context cannot originate an action. Screen text, hook events and background loops may *propose*; execution needs an intent mark plus, for anything model-derived, a confirmed canonical preview.

Phases: **P−1** feasibility (8) · **P0** core (14) · **P1** minimal overlay + auth agent (10) · **P2** full overlay (7) · **P3** voice, egress, taint (17) · **P4** actuation (19) · **P5** eyes (13) · **P6** memory, character, cross-turn test (11). **99 implementation + 33 verifier = 132 tasks.**

## Key decisions & tradeoffs

Attack these.

1. **Writing down the threat model instead of hardening against same-uid.** Is anything left in the documents that still exceeds it? Is the "trusted component path" framing meaningful, or just a nicer name for the same gap?
2. **Voice asks, PTT authorizes.** A real usability cost on a machine with no adversary present. Over-correction?
3. **Taint as typed values with protected sinks.** Every transformation boundary is a place labels can be lost. Is a type system plus mutation tests enough, or does this need something stronger to be credible?
4. **The Auth preview.** It must display a model-selected path to be a meaningful confirmation, so it *is* a taint sink. Is escaping + bounding + quoting sufficient, or does showing attacker-influenced strings in the approval dialog defeat the approval?
5. **Hub as TCB.** The entire model rests on one process that owns policy, marks, tickets, declassification, references and audit. Is concentrating this correct, or should declassification and ticketing be separated from routing?
6. **33 verifier tasks and a FROZEN hash list.** Meaningful independence, or ceremony that a single agent loop will route around anyway?
7. **P−1 gates on two measurements**: German wake-word FRR/FAR, and whether sm_120 ONNX Runtime is importable from a cp312 venv — Arch's package ships no Python bindings **[V]**. If both fail, is the rest still worth building?
8. **Overlay: smithay-client-toolkit + `wl_shm`, no GPU context**, excluding the NVIDIA Blackwell risk class structurally.

## Risks / open questions

- **R1/R2 (blocking, P−1):** no German KWS model; ONNX Runtime bindings absent from the system package.
- **R3/R4:** injection and audio spoofing — mitigations structural, unproven until T-5.11 (25 attacks rendered on a real screen) and T-6.7b (cross-turn laundering through hook fields, action results, serialization, summaries, and `user_audio` memory).
- **R5:** taint loss at a serialization boundary.
- **R13:** verifier written or later weakened by the implementer.
- **R16:** Hub compromise defeats everything above the kernel-level network split.
- **R17:** thirteen units as maintenance burden on a single-user system.
- Unmeasured: end-to-end screenshot→VLM latency; GPU cost of a `PLAYING` ScreenCast pipeline during a game.

## Out of scope

Continuous recording; cloud processing of passive perception without a user turn; free mouse/keyboard computer-use; multi-user; platform portability beyond Linux/Wayland/KDE; cursor-following (impossible on Wayland by design); external telemetry; containerization; password-field detection; defence against same-uid code execution.
