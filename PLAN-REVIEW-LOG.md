# Plan Review Log: dAImon — Desktop-Pet als Familiar und Assistent

Started 2026-07-27. MAX_ROUNDS=5. Model: `gpt-5.6-sol`, reasoning effort `medium`.
Codex is read-only every round (`-s read-only` on round 1, `-c sandbox_mode="read-only"` on every resume —
the local `config.toml` defaults to `workspace-write`).

## Round 1 — Codex (thread 019fa241-627f-74e2-9820-e9cd4a802383)

The plan is not safe enough to implement as written. The process separation limits blast radius, but several claimed guarantees do not follow from the architecture.

### Security and trust boundaries

- `initiator` is supplied by the untrusted Mind in the action request, so injected context can simply label itself `foreground` and obtain weaker policy treatment ([Design §9](</home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Design.md:353>)). — Fix: Have the Hub derive initiator from a non-forgeable, single-use user-turn capability; never accept it from Mind.
- Prompt delimiters and “context is data” do not stop an LLM from following screen instructions ([T-5.9](</home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Implementierungsplan.md:1036>)). — Fix: Parse actionable intent solely from the user utterance in a tool-free pass and expose screen context only through typed, read-only lookup results.
- The three hops still allow injected text to trigger every action classified `allow`; policy separation constrains consequences but does not establish user intent. — Fix: Require explicit user-turn authorization for all actions and make passive context incapable of originating an action request.
- Audio is treated as `foreground`, but webpages, speakers, games, and TTS can speak the wake word and commands; logging that speech is spoofable does not mitigate it. — Fix: Treat wake-word audio as unauthenticated and require push-to-talk or explicit confirmation for every actuation.
- Face TTS can be recaptured by Ears, turning injected text into a fresh “foreground” voice command. — Fix: Suspend KWS while TTS is active plus a post-roll interval, and add echo-reference rejection.
- The DBus proxy filters method names, not arguments; allowing `invokeShortcut` permits every shortcut in that component, including dangerous ones omitted by policy ([Design §6.3](</home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Design.md:413>)). — Fix: Put an argument-validating broker behind the proxy and expose one fixed operation per approved shortcut.
- Hands also invokes `systemd-run`, `wpctl`, `playerctl`, and filesystem backends, all of which bypass the declared “real security boundary” of the DBus proxy. — Fix: Give each backend a separate minimal broker or stop claiming the DBus proxy bounds the whole executor.
- Auto-generating a whitelist from all installed shortcuts confuses “enumerable” with “safe”; only PowerDevil receives special treatment ([T-4.1](</home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Implementierungsplan.md:714>)). — Fix: Generate candidates, then require a reviewed allowlist with explicit parameter and consequence classifications.
- The Hub retains AF_INET for localhost HTTP and therefore can also make outbound Internet connections, contradicting “Hub: no network” and “only Mind has network.” — Fix: Make Hub AF_UNIX-only and place a tiny authenticated loopback hook bridge in a separate sandbox.
- Networkless Eyes can send passive OCR to Hub, which can send it through Mind; therefore the plan enforces only “Eyes cannot exfiltrate directly,” not “passive perception cannot reach the cloud.” — Fix: Add a trusted declassification gate that releases context only under a fresh user-turn capability and test indirect exfiltration.
- `/hook`, `/event`, bubble dismissal, and internal sockets have no peer authentication or role authorization; any same-user process can forge hooks, screen events, or action traffic. — Fix: Use separate 0600 Unix sockets, verify `SO_PEERCRED`, assign producer-specific message types, and authenticate the unavoidable HTTP bridge.
- A compromised Input broker can emit arbitrary input after one portal/ydotool authorization; “always ask” in another process does not constrain the broker itself. — Fix: Make Input a one-shot process receiving a bounded immutable event sequence, then terminate it and its portal session.
- Detecting whether a password field has focus is not generally possible from KWin window metadata on Wayland, so the stated circuit breaker cannot be guaranteed ([T-4.11](</home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Implementierungsplan.md:859>)). — Fix: Deny synthetic typing except in explicitly allowlisted applications/modes, and never claim password-field detection.
- Path canonicalization before execution leaves a symlink/rename TOCTOU race between policy, consent, undo snapshot, and mutation. — Fix: Resolve and operate through directory FDs using `openat2` constraints, then hash the Hub-generated immutable execution envelope.
- `params_hash` is included in Mind’s request and is not proof that the Hub approved the executed parameters. — Fix: Have Hub canonicalize parameters, compute the hash itself, sign/bind it to a nonce, and have Hands atomically consume it once.
- Consent lacks request nonces, sender validation, replay protection, concurrency rules, and restart behavior; late DBus signals can approve the wrong request. — Fix: Bind notification ID, unique DBus sender, canonical parameters, deadline, and one-shot nonce in persisted pending-consent state.
- The implementation provides only notification consent even though the design says notifications are unsuitable for destructive actions. — Fix: Add the required trusted modal destructive-action dialog before enabling such actions.
- A plain hash chain cannot detect complete-log replacement, removal of the tail, or recomputation by the same user, so “manipulation is detectable” is overstated ([T-4.6](</home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Implementierungsplan.md:785>)). — Fix: Periodically anchor chain heads in an independently retained journal/TPM-backed record and make verification cross-check both streams.
- Trash, reflinks, and `git stash` do not guarantee reversibility: disks fill, cross-filesystem moves fail, stashes omit data or conflict, and snapshots can fail before mutation. — Fix: Downgrade an action only after a durable, verified restore artifact exists and abort the mutation on any undo-preparation failure.
- `ProtectHome=read-only` prevents the planned file overwrite/trash/git operations, while relaxing it destroys much of the stated Hands sandbox. — Fix: Move filesystem mutations into narrowly scoped per-operation helpers with explicit path access rather than granting Hands broad home writes.
- The unbounded `ThreadingHTTPServer`, unbounded `Content-Length`, blocking reads, prefix route matching, and unconditional 200 responses preserve trivial local DoS and malformed-event ambiguity from `pet_daemon.py`. — Fix: Add exact routes, body limits, read deadlines, bounded concurrency, schema validation, and distinct malformed-request responses while keeping valid hooks fast.
- No Mind service/unit, credential isolation, outbound destination restriction, or API-token handling task exists. — Fix: Add a sandboxed `daimon-mind.service`, secret loading, API-domain egress policy, redaction tests, and credential-rotation procedure.

### Architecture and ordering

- “Eight processes” is inaccurate once GPU instances, DBus proxy, socket units, Ollama, KWin script, and optional Python TTS are counted; the operational surface is considerably larger. — Fix: Document the real process/unit graph and justify every boundary against a concrete capability it removes.
- The split is over-engineered where it does not create a boundary: GPU model types can use one supervised worker, while dangerous filesystem, DBus, and input backends currently share Hands despite having different capabilities. — Fix: Consolidate same-capability perception/model workers and split actuation by actual privilege.
- P1 spends eight tasks and a week building then deleting a Godot application although event logs can validate mood mapping and the native overlay needs an early feasibility spike. — Fix: Implement a minimal two-state native Face first and validate mood semantics concurrently from logs and daily use.
- Wake-word feasibility is the highest-risk blocker but is postponed until after the full native overlay; the design even inconsistently says it blocks Phase 2. — Fix: Move the wake-word measurement, Portal restart, ORT import, and layer-shell smoke tests into a pre-P0 feasibility phase.
- T-3.7 requires fullscreen detection before T-5.1/T-5.2 implement the KWin watcher supplying it. — Fix: Move the read-only focus watcher ahead of GPU workers or make the gate dependency explicit.
- T-4.11 requires lock/password focus knowledge before Eyes exists, and Eyes still cannot provide password-widget focus. — Fix: Remove that dependency claim and replace it with an enforceable allowlisted-input policy.
- Building all Hands before Eyes is unnecessary: Eyes can be built locally without exposing its output to Mind, while the real injection test cannot happen until Eyes is connected. — Fix: Build capture/OCR earlier in quarantined read-only mode, then enable context declassification only after policy review.
- T-3.11 routes “volume” locally before any action catalog, policy, consent, or executor exists. — Fix: In P3 classify such requests as unsupported intents and add execution only after the P4 action pipeline exists.
- No task connects Mind action requests through Hub policy/consent to Hands, and T-4.9 does not depend on Policy or Consent. — Fix: Add an end-to-end action coordinator task depending on T-4.4, T-4.7, T-4.9, IPC authentication, audit, and idempotency.
- A Python 3.12 isolated venv cannot simply import Arch’s system Python 3.14 ONNX Runtime binding; native sm_120 support in the system package does not prove the planned Python environment works. — Fix: Make a cp312-compatible native ORT build/import spike blocking P3 or use a versioned C-API worker outside the venv.
- Ollama contradicts self-terminating GPU workers because the Ollama daemon may retain the model and is another network-addressable process ([T-5.8](</home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Implementierungsplan.md:1022>)). — Fix: Run VLM directly in the disposable worker or enforce `keep_alive=0`, local socket-only access, and verify VRAM release.
- Screen processing triggered primarily by window changes misses terminal updates, page mutations, videos, and dialogs within the same focused window. — Fix: Add a low-rate bounded sampling trigger in addition to focus events.
- Parallel OCR can finish out of order and overwrite newer context; retaining only one current frame also conflicts with asynchronous tile processing. — Fix: Tag frames with generations, copy bounded changed regions, coalesce work, and discard stale completions.
- There is no backpressure design for hook storms, OCR queues, simultaneous GPU loads, TTS interruption, or action retries. — Fix: Specify bounded queues, per-source rate limits, deadlines, cancellation, GPU serialization, and exactly-once action semantics.
- Local observability is dismissed as telemetry even though there are no queue-depth, dropped-event, latency, health, or cross-hop correlation measurements. — Fix: Add a local diagnostics endpoint and structured turn/action IDs with counters and latency histograms, without external telemetry.

### Verification failures

- T-0.1 verifies only the Python version, not the repo structure, editable installation, pytest behavior, or commit. — Fix: Use a script that asserts every artifact and command exit condition.
- T-0.2 checks only that some JSON contains `"mood"`; it does not prove byte-identical moves or unchanged behavior. — Fix: Record pre-move hashes and run the complete original smoke-test assertions.
- T-1.1 has no baseline timing, repeats, or variance control, so it cannot prove “measurably no slowdown.” — Fix: Benchmark matched hub-on/off samples and assert a defined percentile/regression bound.
- T-1.3 and T-1.4 use `grep -qv`, which succeeds if any output line lacks the error text and therefore passes alongside actual errors. — Fix: Capture output and assert that no error pattern occurs while Godot exits successfully.
- T-1.5 contains a literal `$(...)` placeholder and cannot run. — Fix: Implement deterministic window discovery and numerically assert coordinates against the active output geometry.
- T-1.6, T-1.7, T-2.2, T-2.7, T-2.8, and T-3.12 rely on visual or auditory observation and do not measure their latency/count criteria. — Fix: Add instrumented state/event timestamps and automated compositor/audio canaries.
- T-1.2, T-1.8, T-2.12, T-3.2, T-3.14, and T-4.13 merely grep for headings or words in author-written documents; these are tautologies. — Fix: Generate machine-readable evidence from raw trials and make gates calculate sample counts, rates, thresholds, and unresolved severity.
- Gate P1 proves only Hub state, not that the Pet exists, is visible, polls it, or renders `alert`. — Fix: Add an observable Face test hook or compositor screenshot assertion tied to the posted revision.
- Gate P2’s `nvidia-smi --query-compute-apps=pid... | grep -qv daimon-face` cannot match a process name and passes on headers or unrelated processes. — Fix: Resolve the Face PID and assert its absence from both compute and graphics client lists.
- Gate P3 repeats the same `grep -qv daimon` error against PID-only output and can fail when the list is correctly empty. — Fix: Assert that no listed PID belongs to any dAImon GPU worker, treating empty output as success.
- T-4.5 tests only a negative call; denial could result from a dead/misconfigured proxy rather than the filter. — Fix: Pair a positive allowed canary with a negative side-effect-checked `loadScript` call.
- T-4.10 tests a newly created transient unit rather than `daimon-hands`, and its compound command passes if either SSH or networking fails. — Fix: execute separate read and network probes inside the actual Hands namespace and require both failures.
- T-4.12 uses ten fixed synthetic strings before Eyes exists and accepts consent spam as a successful mitigation. — Fix: Run adversarial end-to-end tests after P5 covering provenance forgery, allowed actions, replay, audio re-entry, concurrent prompts, and a zero-consent-spam requirement.
- T-5.1’s `grep -qv 'script error'` has the same false-positive structure and does not prove any focus event arrived. — Fix: switch windows under automation and assert a uniquely correlated watcher event.
- T-5.10’s glob-based `test ! -s .../*` is unreliable when the glob has no match and does not prove the portal session closed. — Fix: assert an empty context directory with `find`, absence of the PipeWire stream, and a recorded successful `Session.Close`.
- Phase gates can pass with unresolved critical security-review findings because they check only document existence or generic test success. — Fix: encode findings in machine-readable form and fail the gate on any open high/critical issue.

VERDICT: REVISE
### Claude's response — Round 1

**Accepted: all 27 findings. Rejected: none.** The critique was substantively correct on every
point that mattered, including two internal contradictions in my own documents.

Structural changes:

1. **Capability tokens replace the `initiator` field.** Hub-issued, single-use, nonce-bound,
   expiring, granted only on an observed physical user action. `initiator` is derived from the
   redeemed token, never read from the request. Wake-word grants `scope=question` only.
   → Design §2.4, T-0.8
2. **Voice no longer authorizes.** Audio is unauthenticatable. Actions requested by voice are
   proposed and need physical confirmation. TTS/KWS interlock with 500 ms post-roll and echo
   reference closes the self-reactivation loop I had missed entirely.
   → Design §4.3, §4.6, T-3.4
3. **Two-pass routing.** Pass 1 has tools and no screen context; pass 2 has screen context and no
   tool schema. Injection is countered by removing the capability, not by delimiters — the
   critique was right that delimiters stop nothing. Plus a declassification gate for the indirect
   Eyes→Hub→Mind path.
   → Design §5.1, §7.2, T-3.12/T-3.13/T-5.9
4. **One executor → four brokers** split by actual privilege, with a Hub-computed `params_hash`
   and a signed, nonce-bound, single-use execution envelope.
   → Design §3, §6.2, T-4.5, T-4.7…T-4.13
5. **Phase order rebuilt.** New Phase −1 with six spikes, two of them hard gates. Godot throwaway
   deleted. Focus watcher moved to P0 (the GPU gate depends on it — a genuine ordering bug).
   → Anhang C

Claims withdrawn as undeliverable: password-field detection on Wayland; "the DBus proxy is the
security boundary"; "always-on processes cannot reach the API" as a complete privacy statement;
"tampering is detectable" for a bare hash chain.

Self-contradictions the critique exposed and that are now fixed: `ProtectHome=read-only` alongside
planned trash/reflink/git operations; Ollama alongside self-terminating GPU workers; a Hub holding
a TCP port while claiming `AF_UNIX`-only.

All 16 flagged verification commands were rewritten under six explicit rules (no `grep -qv`, no
grepping author-written prose, no visual acceptance, negative tests need positive canaries, no
`&&`-chaining of independent assertions, measured latency with n and percentile). Investigation
tasks now emit `results.json` and the gate does the arithmetic.

Task count 76 → 95.

### Claude's response — Round 2

**Accepted: all findings. Rejected: none — but one whole class was resolved by deleting a
mechanism rather than strengthening it.**

The decisive finding was that my own verification command for T-1.7 triggered push-to-talk via
`kglobalaccel.invokeShortcut`, thereby demonstrating that any same-uid process can manufacture
the supposedly physical event. Combined with the observation that all processes share one uid
(and can therefore ptrace each other, control `systemd --user`, and read 0600 runtime files),
this invalidated the "unforgeable provenance" framing wholesale.

**Resolution: write the threat model down instead of building crypto that cannot help.**
New Design §1.2, placed before the architecture. In scope: injected instructions in observed
content, spoofed audio, model error, accidental disclosure. Explicitly out of scope: an attacker
already executing code as this uid. Process boundaries are stated as blast-radius limits and
enumerability aids, not integrity enforcement between peers.

That reframing let me **delete** the HMAC envelope — which Codex correctly noted was internally
impossible anyway (a broker cannot verify with a key that exists only in the Hub) and whose key
was ptrace-readable regardless. Provenance now rests on the authenticated socket, plus `audience`,
`schema`, and a monotonic deadline. Less code, honest claim.

Substantive changes beyond that:

- **No ambient authority.** `scope=full` replaced by a round marker that authorizes an API call
  and a *proposal*; execution binds to the canonicalized action, confirmed against a preview.
- **Wake-word grants an API quota, not authorization**, and no longer passes declassification —
  spoofed audio could otherwise have exfiltrated the screen with no user action at all.
- **Taint tracking with an enforced sink table.** The two-pass split held within a turn but the
  short-term memory copied pass-2 output into the next tool-capable pass. Also: no action anaphora
  ("do that" must name action and target), and long-term memory stores only verbatim user spans.
- **Auth agent split out of Face.** Face renders model text; the component that grants approvals
  now renders none.
- **Egress broker.** Mind loses `AF_INET` and the token; a separate process enforces domain and
  per-request quota.
- **At-most-once**, Hub-held persistent ticket ledger, `outcome=unknown` on crash, no auto-retry.
- **FS preflight with a pinned FD** bound to the consent nonce — the "same FD throughout" claim
  was not representable in a JSON envelope.
- **`.desktop` approval bound to file hash**, revalidated before launch.
- **Undo preparation is a broker transaction** with its own audited states.
- `SO_PEERPIDFD` instead of `SO_PEERCRED` + PID lookup; PTT is toggle, not hold.

Verification regime hardened: verifiers are now separate reviewer-owned `.v` tasks written before
implementation and validated by mutation test; self-reported booleans no longer count (pixel
probes for visibility, `cuobjdump` for native cubins, OS queries for resources); five ordering
errors fixed (T-1.3 and T-2.7 depended on units from later phases, T-4.5 required execution before
brokers existed, T-4.16 exercised only one of four brokers, T-5.7 was tautological by construction).

New tasks: T−1.8 (test input fixture), T-1.7b (auth agent), T-3.13b (taint tracking),
T-6.7b (cross-turn injection test, which T-5.11 structurally could not catch).

Task count 95 → 99.

### Claude's response — Round 3

**Accepted: all findings. Rejected: none.**

The decisive criticism was procedural: v2.1 stated a new threat model up front but left eight
dependent sections describing the superseded mechanics, and PLAN.md still described v2.0
entirely. I had patched rather than propagated. Both documents are therefore **regenerated as
v3.0**, not patched again.

Consistency:
- Canonical service inventory (Design §2.1): twelve services + KWin script + worker-spawned
  helper. Previous versions said "eight", "eleven" and "thirteen" in different places.
- Vocabulary fixed in §1.2 and used throughout: intent mark, trusted component path, peer check,
  ticket. No remaining "unforgeable", "physical" or "proves".
- Hub named as the trusted computing base with explicit input validation and failure boundaries.
- Credential inventory and sandbox matrix updated: token and AF_INET live in `egress`, not `mind`.
- Egress restated as transporting content opaquely — it necessarily carries prompt bodies.
- T-1.7 builds the auth agent directly; the superseded Face-owned path and its migration task
  are gone.

Taint model, three real holes closed:
- `user_ptt` and `user_audio` split. Spoofed audio could previously write durable memory that
  influenced later authorized turns.
- Free-text hook fields are `tainted`. v2.1 called them trusted while the threat model called
  hooks injectable — a direct self-contradiction.
- Taint derives from data provenance, not the fetching component.
- Labels are typed values; protected sinks reject raw strings as a type error; mutation tests at
  every transformation and persistence boundary.

The Auth preview is now a declared taint sink with a fixed template and escaped, bounded, quoted
values. Codex was right that "never renders model text" plus "meaningful confirmation" was
unresolvable as stated.

The direct-command exception became Hub-owned (catalog flag AND deterministic Hub parser);
model-derived actions always go through preview.

Verification regime became an actual task graph: 33 reviewer-owned `.v` tasks preceding their
implementations, mutants per criterion, and a `FROZEN` hash list the gate checks so a builder
cannot weaken a verifier after acceptance. Self-reported values removed from gates (hard-coded
blocking set, portal-signal-derived `restart_prompted`, image-hash mood distinctness, randomized
marker with before/after capture). T-5.10's egress capture records only hashes and structural
markers, test-profile only.

New: cross-turn test covers one laundering variant per provenance boundary. New risks R5 (taint
loss), R13 (verifier integrity), R16 (Hub as TCB).

Tasks: 99 implementation + 33 verifier = 132.

### Claude's response — Round 4

**Accepted: all findings. Rejected: none.** Same procedural failure one level down: round 3
regenerated the *design* but only patched the *plan*, so the executable plan still implemented
the superseded architecture.

Rewrote the contradicting tasks against v3.0: T-0.7 (`SO_PEERPIDFD`, with a race mutant),
T-0.8 (four separate state machines — round mark, action approval, API quota, broker ticket —
replacing "capability"), T-3.5/T-3.6 (Ears emits labelled transcripts only; the PTT toggle lives
solely in Auth), T-3.11 (Egress transports opaquely; capture is test-profile, hash-only),
T-4.5 (unsigned, audience-bound, deadline-limited ticket contract; a mutant that reintroduces
HMAC is rejected), T-4.12 (modal dialog in Auth, externally verified), T-4.15 (at-most-once with
a crash-window test), T-4.16 (mark → preview → ticket), T-4.19 (wake-word action requests are
refused tool-free and never open a dialog), T-5.9 (declassification binds to a PTT round mark),
T-6.1/6.2/6.3 (depend on the taint task; DB round-trip mutation test moved here since the
database only exists now). Purged the remaining capability/envelope vocabulary.

Verifier graph instantiated: 45 enumerated `.v` tasks with named mutants per criterion, plus a
new reviewer-owned T-0.0 that sets up role path allowlists and mutation-tests them (a builder
write to `tests/verify/` must fail). `verify-frozen.sh` is now the first command of every phase
gate. `FROZEN` lives in a reviewer-only path so a builder cannot change script and hash together.
The meta-verifier runs two-stage against a reviewer-owned known-good fixture before the
implementation exists, then against the real code — v3.0 required a "reference implementation"
that could not exist at that point.

Design fixes: thirteen services stated consistently; §4.6 no longer says "physical" or claims to
prove who acted; §2.3 distinguishes a Face recommendation from an action proposal and states that
passive content can never open Auth (dialog flooding is itself an attack); all free model output
is `tainted` regardless of pass, so there is no unlabeled model path; the preview normalizes
Unicode and visibly escapes bidi, zero-width and format characters (a path that looks like
`~/Bilder/urlaub.png` but is `~/.ssh/id_ed25519` would otherwise void the confirmation);
`user_ptt` classifies by text provenance, not the focused widget, so paste and IME insertion keep
their source label.

Counts regenerated from the graph: 100 implementation + 45 verifier = 145 tasks.

## Round 5 — Codex (final round)

[full critique: 15 findings — see the verdict text above this entry in the session; summarised below]

Codex's framing: "The architectural design is now broadly sound, including the narrowed threat
model and four-label taint model. The implementation plan is still not safe to start."

### Claude's response — Round 5 (applied AFTER the last review; NOT itself reviewed)

Twelve of the fifteen findings were mechanical and are fixed in v3.1: stale counts everywhere
(thirteen services, 45 verifiers, 145 tasks), Phase 4's "physically authorized" wording,
T-3.13b (all boundary adapters, `.v` dependency, DB round-trip moved to T-6.1, pass-1 output
classification plus a mutant against promoting structured model strings to `trusted`), T-1.7
(Unicode: bidi, isolates, zero-width, NFC, confusable path), T-4.6 (`mark_id`; redaction by
provenance rather than catalog flag), T-4.10 (.desktop hash binding with a swap test), T-4.11
(presentation moves to Auth), T-4.15 (exactly one effect from the first order, zero from replays
— "at most one" would pass with a dead broker), T-5.10 (reviewer-owned ephemeral upstream instead
of Egress self-report), T-5.11 (external dialog observation plus an authorized positive control),
T-4.13 (ydotool fallback off by default; if enabled, `ydotoold` lives and dies inside the broker's
process tree).

Two findings are acknowledged and left open, recorded in Anhang C4:
- The 45 verifiers exist as a table with named mutants, not as individual task nodes with files,
  dependencies and agent ownership; only a few implementation tasks carry an explicit `.v` dep.
- T-0.0 names role path allowlists but no concrete enforcement mechanism. Without a runner or
  patch-application boundary that rejects per-role paths, "the builder cannot write tests/verify"
  is a statement of intent.

## Outcome

**MAX_ROUNDS (5) reached without VERDICT: APPROVED. This did not converge.**

Design: Codex called it broadly sound in the final round. Substantively settled.
Implementation plan: five rounds materially improved it, but round 5 still judged it not safe to
start, and the two structural items above remain open. The v3.1 edits were made after the final
review and have not been checked by anyone.

Recommendation: resolve the two open items (instantiate the verifier task nodes; specify a real
enforcement mechanism for role path allowlists) before P0.0, or run a fresh review loop against
v3.1 with a higher round cap.
