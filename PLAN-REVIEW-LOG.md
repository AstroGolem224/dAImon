# Plan Review Log: Reviewer-Session für die offenen Verifizierer

Started 2026-08-10. MAX_ROUNDS=5. Model: `gpt-5.6-sol`, reasoning effort `medium`.
Codex is read-only every round (`-s read-only` on round 1,
`-c sandbox_mode="read-only"` on every resume).
Die vorige Fassung dieses Logs (Gesamtplan-Review vom 27.07., 5 Runden ohne
APPROVED) liegt in der Git-Historie vor diesem Commit.

## Round 1 — Codex
The plan is not implementable safely yet. Material issues:

1. **The freezing policy contradicts itself.** Step 6 freezes “every new verifier,” while lines 71–72 freeze only T-4.5, T-4.12, and T-4.15; the active `.v` specifications require `freeze.sh` for every T-4.4–T-4.16 verifier and T-3.15.  
Fix: List every artifact that must enter `FROZEN`, and distinguish the deferred second-order T-4.17.v from the still-required `T-4.17.sh`.

2. **“At least one mutant” violates the verifier contracts.** Appendix D names complete mutant sets—up to five per task—and requires every named mutant to be rejected, not merely one representative.  
Fix: Copy the exact mutant inventory from each `.v` specification into the work order and require a criterion-to-mutant result matrix.

3. **The proposed `.v2` re-freeze procedure cannot be executed with the current tool.** [freeze.sh](/home/itiger013/Dokumente/Github/dAImon/tests/verify/freeze.sh:26) refuses an already-listed path, so “run `freeze.sh`, newly freeze” cannot update T-3.12.  
Fix: Specify the established audited re-freeze sequence: retain the old hash through mutation testing, then replace only the exact manifest entry, inspect the two-file diff, run `verify-frozen.sh`, and commit atomically under an explicitly authorized `.v2` task.

4. **T-3.12 is not actually fully frozen.** [T-3.12.sh](/home/itiger013/Dokumente/Github/dAImon/tests/verify/T-3.12.sh:16) delegates all logic to `t312_pruefstand.py`, but `FROZEN` hashes only the wrapper; `freeze.sh` discovers only literal `tests/harness/...` dependencies.  
Fix: Make T-3.12.v2 hash both wrapper and Python harness—preferably by moving the helper under `tests/harness/` and referencing it literally—before changing K4/K6.

5. **The same helper-hash hole threatens every new verifier.** A shell wrapper plus `tests/verify/*.py` helper would look frozen while its actual assertions remain mutable.  
Fix: Require all executable verifier dependencies to be enumerated and hashed, and reject freezing if an imported/executed local helper is absent from `FROZEN`.

6. **The dirty-worktree step authorizes destruction and laundering of frozen state.** “Commit or discard” could discard user work, while committing the modified T-3.9 script and updated hash would legitimize a frozen-verifier change without a separately approved T-3.9.v3 work item.  
Fix: Preserve all pre-existing changes, classify them by provenance, and stop for owner direction on discard or on any frozen-file/hash pair lacking explicit version-task authorization.

7. **The role guard does not enforce the plan’s claimed reviewer boundary.** The reviewer deny list blocks only `daimon`, `face`, `kwin-script`, and `config/systemd`; it can still modify `config/actions`, packaging files, hooks, `.claude/roles.toml`, and `.githooks`, and the pre-commit hook imposes no equivalent reviewer whitelist.  
Fix: Add a per-commit staged-path allowlist to the work order and abort if reviewer commits contain anything outside the explicitly enumerated verifier, fixture, mutant, T-4.17, T-4.18, evidence, and approved documentation paths.

8. **The blindness procedure is not enforceable.** The role guard controls writes, not reads, so an instruction saying “do not read `daimon/`” provides no machine-checkable evidence of blindness.  
Fix: Use fresh isolated reviewer contexts receiving only the contract and pinned public interface, record their read/tool transcript, and label any verifier non-blind if that evidence is unavailable.

9. **T-4.14.v is incorrectly scoped as a standalone task.** Its specification is combined with T-5.13.v and requires two entry scripts, the shared `tests/verify/lib/sandbox_units.sh`, two fixture/mutant sets, two `meta.sh` runs, and freezing the shared library; the plan mentions none of this.  
Fix: Either execute the combined T-4.14.v/T-5.13.v contract in full or formally split the specification before implementation.

10. **Freezing the T-4.14 shared library is unsupported by the current `freeze.sh`.** The command accepts only a task ID and derives `tests/verify/<task>.sh`; it cannot freeze `tests/verify/lib/sandbox_units.sh`.  
Fix: Define and mutation-test an audited dependency-freezing mechanism before T-4.14, rather than manually appending an unexplained hash.

11. **T-0.9 has no concrete anti-weakening acceptance test.** It is not currently in `FROZEN`, and simply replacing the brittle `ss | grep pid` expression could produce a hollow green that no longer proves socket ownership.  
Fix: Require listener inode correlation to `/proc/$hub/fd`, a positive Unix-listener control, and a mutant that opens TCP in the Hub process and must be detected.

12. **T-3.12 is prematurely classified as environmental.** “The egress stub is not reached” could equally indicate a product regression, harness race, stale socket, or wrong target-tree import; reviewer role cannot repair a product defect.  
Fix: Add a diagnostic decision tree and permit verifier changes only after demonstrating a harness defect against both HEAD and a known-good fixture; otherwise record the product/environment failure without weakening the test.

13. **The `n: 0` placeholder risks overwriting real evidence.** `phase3-latency.json` already contains four genuine runs, and reviewer role should not replace builder/runtime evidence merely to express failure.  
Fix: Make T-3.15 fail read-only on missing or insufficient evidence while preserving the existing runs; never generate or reset the evidence file from the verifier session.

14. **The blanket “observe, do not execute” rule contradicts required live canaries.** T-4.7, T-4.9, T-4.10, and especially T-4.16 require actual effects or verified non-effects; a stub that only records intent cannot prove broker, cgroup, filesystem, proxy, or end-to-end behavior.  
Fix: Use disposable real targets for positive effects and narrowly intercept only dangerous endpoints, with a positive control proving the interception itself works.

15. **The positive exec case is left as an unresolved decision.** A work order cannot proceed to T-4.16 while allowing the required exec branch to remain red indefinitely.  
Fix: Preselect a harmless temporary `.desktop` file and test-only approved catalog rooted in temporary XDG directories, then verify the launched process’s real cgroup without modifying the production catalog.

16. **T-4.17’s “external” prompt assertion is undefined and conflicts with the cited `/diag` check.** A Hub counter is self-reporting and can remain zero when dialogs are actually emitted.  
Fix: Specify external observation through the notification bus/window list, include an authorized positive prompt control, and use `/diag` only as a secondary cross-check.

17. **T-4.18 is circular and underspecified.** The same session produces findings, the review document, and the script judging completeness, yet the “fixed checklist” has no enumerated IDs, evidence schema, or executable reproduction format.  
Fix: Pin the complete Design §1.3/§6/§7 checklist and JSON schema before review, require independent commands or artifact hashes for every item, and separate checklist validation from authoring findings.

18. **Service and desktop side effects lack cleanup guarantees.** The plan will stop/restart real user units, toggle DND, create dialogs, manipulate files, and potentially launch applications without preserving prior state or proving cleanup.  
Fix: Require per-verifier pre-state capture, `trap`-based restoration, unique runtime directories, process/socket leak checks, and explicit refusal when restoration cannot be guaranteed.

19. **The completion condition is impossible as written.** Human measurements remain intentionally red, reviewer-discovered product defects cannot be fixed in scope, and T-4.18 must fail on open High/Critical findings, yet the goal implies one session “clears” the baseline and builds everything.  
Fix: Define success as completed verifier artifacts plus an outcome ledger of green, human-blocked, environment-blocked, and product-defect-red results; never equate “built and honestly red” with gate completion.

20. **“One task, two commits” does not define two commits.** The text describes only a reviewer commit and gives no atomic boundary between verifier construction, mutation proof, freezing, and evidence.  
Fix: Replace it with an exact commit protocol—for example, verifier/fixtures/mutants after `meta.sh`, then a separate freeze/evidence commit only after real-tree execution and staged-diff validation.

VERDICT: REVISE
### Claude's response (Round 1)
Alle 20 Befunde akzeptiert und eingearbeitet; keine Zurückweisung. Die
wesentlichen Umbauten: Erfolg ist jetzt ein Ausgangs-Ledger statt eines
grünen Gates (19); FROZEN-Inventar explizit, Helfer-Hash-Sperre und
Re-Freeze-Sequenz definiert (1,3,4,5,10); Mutanten vollständig aus Anhang D
mit Matrix (2); Worktree-Artefakte werden erhalten/klassifiziert mit
Owner-Stopp für das T-3.9.sh/FROZEN-Paar (6); Pfad-Allowlist je Commit, weil
der role_guard die Reviewer-Grenze nicht deckt (7); Blindheit wird über
Transkript-Beleg etikettiert statt behauptet (8); T-4.14.v/T-5.13.v formal
teilen oder ganz ausführen (9); T-0.9 mit Inode-Korrelation, Positivkontrolle
und TCP-Mutant (11); T-3.12 bekommt einen Diagnose-Entscheidungsbaum statt
einer Vorab-Klassifikation (12); Evidenzdateien sind lesend (13);
Beobachten/Ausführen nach Fallklasse mit Positivkontrolle je Attrappe (14);
exec-Positivfall über Test-Katalog in temporären XDG-Verzeichnissen (15);
T-4.17 extern beobachtet mit Positivkontrolle (16); T-4.18 entzirkelt:
Prüfliste+Schema zuerst, getrennte Schritte (17); Vorzustand/trap/
Leck-Prüfung je Verifizierer (18); Zwei-Commit-Protokoll exakt (20).

## Round 2 — Codex
The revision is substantially better, but material contradictions remain.

1. **The FROZEN inventory is still wrong.** [PLAN.md:19](/home/itiger013/Dokumente/Github/dAImon/PLAN.md:19) freezes only T-4.5, T-4.12, and T-4.15, while every active T-4.4.v–T-4.16.v block explicitly ends with `freeze.sh`; T-3.15.v does too.  
Fix: Enumerate T-3.15 and every T-4.4–T-4.16 wrapper and helper as frozen deliverables; separately decide and document T-3.14’s status from its external contract.

2. **The T-3.12 re-freeze procedure still omits the helper manifest change.** The plan says replace exactly one manifest entry and inspect a two-file diff, but moving/freezing `t312_pruefstand.py` requires changing the wrapper, moving/adding the helper, and adding its own `FROZEN` entry.  
Fix: Require an atomic three-artifact review—wrapper, helper, and manifest—with the old wrapper hash replaced and the new helper hash added only after `meta.sh`.

3. **The dirty T-3.9 pair contaminates validation, not merely freeze commits.** T-3.13b and T-3.14 run nested frozen verifiers, so continuing steps 2–4 in the dirty tree would test against an unauthorized T-3.9 revision even if it is not staged.  
Fix: Either stop until the owner resolves T-3.9 or perform all independent work in a separate clean worktree pinned to HEAD.

4. **The staged-path “allowlist” remains dangerously broad.** `tests/verify/**` permits changing every frozen verifier and the freeze machinery, `docs/**` permits rewriting the authoritative plan, and `tests/evidence/**` permits overwriting the supposedly untouchable human evidence.  
Fix: Generate an exact per-task filename allowlist and explicitly deny existing frozen files, freeze machinery, authoritative plans, and pre-existing evidence unless individually authorized.

5. **T-4.14 remains an unresolved branch rather than a work order.** “Formally split or execute completely” leaves the implementer to make an architectural decision, while the out-of-scope section simultaneously excludes the T-5 portion and the canonical specification requires it.  
Fix: Choose one route before execution—preferably execute the combined T-4.14.v/T-5.13.v contract exactly as written—or obtain a separately approved plan amendment first.

6. **Moving `sandbox_units.sh` changes the pinned contract silently.** Appendix D explicitly names `tests/verify/lib/sandbox_units.sh`; [PLAN.md:88](/home/itiger013/Dokumente/Github/dAImon/PLAN.md:88) relocates it to `tests/harness/` merely to fit the current freezer.  
Fix: Amend the canonical task specification explicitly, or extend and mutation-test dependency freezing without changing the specified artifact topology.

7. **The human-measurement treatment invents a verifier criterion.** The T-3.15 contract says the week-long false-positive measurement is explicitly not a verifier criterion, yet [PLAN.md:77](/home/itiger013/Dokumente/Github/dAImon/PLAN.md:77) describes two human measurements as red checks.  
Fix: Make only the `n >= 20` latency evidence a verifier failure and record the week-long measurement separately as a ledger-level human blocker.

8. **T-4.18 is still ordered circularly.** The plan pins the checklist, then writes findings, then writes the checker; a checker authored after seeing the findings can be tailored to accept them.  
Fix: Pin both checklist/schema and `T-4.18.sh` before findings are collected, then have a fresh reviewer produce findings and run the already-fixed checker.

9. **“Separate steps” is not separation of authorship.** One reviewer session may still author the checklist, findings, and validator, despite the plan claiming they are separated.  
Fix: Assign checklist/verifier construction and security findings to distinct fresh reviewer contexts and record their provenance in the ledger.

10. **The process-group cleanup rule is unsafe as a blanket mandate.** `kill -- -PGID` is appropriate only for a verified test-owned `setsid` group; applying it to systemd services or an inherited shell group can kill unrelated user processes.  
Fix: Use `systemctl` for real units and permit group kills only after verifying a unique test-created session whose PGID equals its known leader PID.

11. **The two-commit protocol can leave required verifiers committed but indefinitely soft.** Commit 1 lands verifier logic before real-tree execution and freezing; a blocked or interrupted second phase leaves an acceptance script in history without its required hash.  
Fix: Use one atomic commit for tasks required to freeze, or keep the first commit on an explicitly temporary branch that cannot be merged until the freeze commit exists.

12. **Blindness failure is merely labeled, even where blindness is contractual.** T-3.14’s contract forbids reading `daimon/**` and `face/**` before the first run; marking the result `nicht-blind` does not make that verifier acceptable.  
Fix: Treat blindness loss as a failed T-3.14.v deliverable requiring a fresh isolated author, while using `nicht-blind` only for tasks whose contracts do not require blindness.

VERDICT: REVISE
### Claude's response (Round 2)
Alle 12 Befunde akzeptiert. Umbauten: FROZEN-Inventar auf die kanonische
Vorgabe korrigiert (T-3.15 + alle T-4.4–T-4.16 samt Helfern; T-3.14 aus
seinem Vertrag) (1); T-3.12-Re-Freeze als atomare Drei-Artefakt-Prüfung (2);
aller unabhängiger Bau in frischem Worktree auf HEAD, weil das schmutzige
T-3.9-Paar auch ungestagt jeden verschachtelten Lauf kontaminiert (3);
Allowlist jetzt je Task erzeugt und exakt, mit explizitem Verbot für
FROZEN-gelistete Dateien, Freeze-Maschinerie, kanonische Plandokumente und
vorbestehende Evidenz (4); T-4.14.v/T-5.13.v wird als kombinierter Vertrag
ausgeführt statt geteilt (5); sandbox_units.sh bleibt am kanonischen Ort,
stattdessen wird der Freeze-Mechanismus erweitert und diese Erweiterung
selbst mutantengeprüft (6); nur n≥20 ist Verifizierer-Kriterium, die
FP-Woche ist Ledger-Blocker (7); T-4.18: Prüfliste+Schema+Prüfer VOR den
Befunden, getrennte frische Kontexte mit Provenienz im Ledger (8,9);
systemctl für echte Units, Gruppentötung nur für verifizierte eigene
setsid-Gruppen (10); atomarer Einzel-Commit bei Freeze-Pflicht (11);
T-3.14.v-Blindheitsverlust ist ein gescheiterter Posten mit frischem Autor,
kein Etikett (12).

## Round 3 — Codex
The plan is close, but several material holes remain.

1. **The FROZEN inventory still omits T-3.13b and leaves T-3.14 undecided.** T-3.13b’s canonical `.v` block explicitly requires `freeze.sh`, while the global verifier regime says reviewer verifiers are frozen; neither should be deferred to a ledger interpretation.  
Fix: Explicitly freeze T-3.13b, T-3.14, T-3.15, and T-4.4–T-4.16, including every repo-local runtime helper.

2. **T-3.12’s helper remains unfrozen unless the diagnosis happens to select “harness defect.”** The missing helper hash is already a verifier-integrity defect, independent of why K4/K6 are red.  
Fix: Make T-3.12.v2’s wrapper/helper hash coverage mandatory in every diagnostic outcome, while making assertion changes conditional on proving a harness defect.

3. **The freeze-mechanism modification has no actual authorization task.** [PLAN.md:35](/home/itiger013/Dokumente/Github/dAImon/PLAN.md:35) proposes changing security machinery and merely calls it “individually authorized” later, but no T-0.0.v2-style task, acceptance criteria, or regression suite is defined.  
Fix: Add an explicit prerequisite task authorizing the exact `freeze.sh` interface change, its mutants, `T-0.0.sh` regression run, and atomic commit before any verifier uses it.

4. **Helper completeness remains procedural rather than machine-checked.** Hashing explicitly named extras detects modification of declared helpers but does not detect an executed/imported helper that the author forgot to declare.  
Fix: Require a machine-generated dependency manifest or constrain verifier-local execution/imports to scanned directories and fail freezing when any discovered repo-local dependency lacks a manifest entry.

5. **The generic commit protocol is impossible for T-4.17 and T-4.18.** [PLAN.md:50](/home/itiger013/Dokumente/Github/dAImon/PLAN.md:50) says every non-frozen task commits construction only after `meta.sh`, but T-4.17.v is deferred and T-4.18 has no `.v` mutant/fixture task, so neither has a valid `meta.sh` target.  
Fix: Add explicit commit rules for reviewer implementation tasks T-4.17/T-4.18 using their prescribed checks without pretending a deferred or nonexistent second-order verifier ran.

6. **The clean-worktree plan lacks a durable branch and integration path.** A worktree added at `HEAD` is commonly detached; commits can become unreferenced, while cherry-picking them into the dirty main worktree risks conflicts and contamination.  
Fix: Create a named reviewer branch from the recorded HEAD, commit only there, and hand off branch plus commit hashes without merging or cherry-picking into the dirty main worktree.

7. **T-3.13b artifacts cannot simply “run in the clean worktree.”** They are untracked in the main worktree and therefore absent from a fresh worktree at HEAD; the plan gives no provenance-preserving transfer procedure.  
Fix: After classification, copy/apply only an inventoried patch of the T-3.13b paths into the reviewer branch and verify hashes against the untouched originals before testing.

8. **T-4.18 still cannot generically reproduce future closed findings safely.** A verifier written before findings either cannot know their reproduction procedures or must execute commands supplied by the findings author, which creates an arbitrary-command and self-attestation hole.  
Fix: Predeclare safe reproduction handlers keyed by fixed checklist/finding classes, and require each closed finding to reference one handler plus bounded data rather than supplying executable commands.

9. **The combined T-5.13 handling must fail, not merely record “blocked,” when the real unit is absent.** Otherwise `T-5.13.sh` could exit successfully without testing the actual Eyes unit, contradicting its positive control and fixed unit list.  
Fix: Require `T-5.13.sh` to return nonzero on the real tree when the Eyes unit is missing, while recording that nonzero result as `umgebungs-blockiert` or `produktdefekt-rot` in the ledger.

VERDICT: REVISE
### Claude's response (Round 3)
Alle 9 Befunde akzeptiert. Umbauten: FROZEN-Inventar um T-3.13b und T-3.14
ergänzt, kein Ledger-Ermessen mehr (1); T-3.12.v2-Hash-Abdeckung in JEDEM
Diagnoseausgang, Behauptungsänderungen nur bei nachgewiesenem Harness-Defekt
(2); Voraussetzungs-Task "Freeze-Erweiterung" mit Owner-Freigabe beim
Kickoff, eigenen Mutanten (manipulierter + undeklarierter Helfer),
Regressionslauf über alle 26 Einträge (3); Abhängigkeitsmanifest maschinell
erzeugt, Einfrieren scheitert bei undeklarierter entdeckter Abhängigkeit
(4); Sonderregeln für T-4.17.sh (Commit nach vorgeschriebenen Prüfungen)
und T-4.18.sh (Commit vor Befunderhebung, Beleg über synthetische
Register) statt eines meta.sh-Scheins (5); benannter Branch
reviewer/p4-verifizierer, Übergabe als Branch+Hashes, kein Merge in den
schmutzigen Hauptbaum (6); T-3.13b-Transfer als inventarisiertes Patch mit
sha256-Abgleich vor dem ersten Lauf (7); T-4.18 repro über vordeklarierte
Handler + begrenzte Daten, nie ausführbarer Text aus dem Register (8);
T-5.13.sh scheitert bei fehlender Eyes-Unit mit Exit != 0, Ledger trägt das
Rot (9).

## Round 4 — Codex
Material issues remain:

1. **The freeze migration will immediately expose additional pre-existing holes that the plan does not handle.** T-3.11 and T-3.13, like T-3.12, delegate to unhashed `t311_pruefstand.py` and `t313_pruefstand.py`; a real dependency scan cannot remain green over the claimed 26-entry baseline.  
Fix: Make the freeze-extension prerequisite migrate and hash every dependency discovered in all existing FROZEN wrappers, with explicit authorized handling for T-3.11, T-3.12, and T-3.13 before claiming regression success.

2. **The proposed static dependency scan is not defined tightly enough to be machine-enforceable.** “Scan executed/imported paths” cannot reliably discover variable-built shell paths, Python dynamic imports, subprocesses, or helpers opened indirectly.  
Fix: Define a closed dependency-declaration grammar plus recursive validation, reject unsupported dynamic local loading, and supplement it with runtime file-open tracing across good, mutant, and real-tree runs.

3. **The mutant source rule excludes binding external-contract mutants.** [PLAN.md:61](/home/itiger013/Dokumente/Github/dAImon/PLAN.md:61) requires sets from Appendix D, but T-3.14 is absent there and T-3.15’s external contract expands Appendix D’s two mutants to four.  
Fix: State explicitly that T-3.14 uses all six contract mutants and T-3.15 all four, with the external contracts taking precedence over Appendix D for those tasks.

4. **The ledger misclassifies the deliberately unimplemented T-5.13 target.** A missing future Eyes unit is neither an environmental blocker nor a product defect; assigning either status corrupts the promised outcome accounting.  
Fix: Add `zieltask-offen`/`implementation-pending` as a ledger state while still requiring `T-5.13.sh` to exit nonzero.

5. **T-4.18’s synthetic proof still does not test reproduction.** A complete register passing and empty/unsubstantiated registers failing does not catch a verifier that accepts `closed` findings without executing their declared handlers.  
Fix: Add negative synthetic registers for a failing reproduction handler, an unknown handler, and invalid/out-of-bounds handler data, all of which must exit nonzero.

6. **T-4.17’s authorized prompt control can contaminate the zero-prompt attack measurement.** Without an explicit observation boundary, the required positive control itself makes the total prompt count nonzero.  
Fix: Run the positive control in a separate externally marked collection window, reset the external observer, then measure the attack window independently.

VERDICT: REVISE
### Claude's response (Round 4)
Alle 6 Befunde akzeptiert. Umbauten: der Freeze-Erweiterungs-Task migriert
ausdrücklich die Helfer ALLER bestehenden FROZEN-Wrapper (T-3.11/T-3.12/
T-3.13 delegieren an ungehashte Helfer — ein echter Scan kann über dem
Bestand nicht grün bleiben) (1); geschlossene Deklarationsgrammatik,
Zurückweisung dynamischen lokalen Ladens, plus Laufzeit-Dateiöffnungsspur
über Gut-, Mutanten- und Echtbaumläufe (2); externe Verträge haben
Mutanten-Vorrang: T-3.14 sechs, T-3.15 vier (3); neuer Ledger-Zustand
zieltask-offen für die fehlende Eyes-Unit (4); T-4.18-Beleg um negative
Register erweitert (scheiternder Handler, unbekannter Handler, ungültige
Handler-Daten → je Exit != 0) (5); T-4.17 mit zwei getrennten
Beobachtungsfenstern und Beobachter-Reset zwischen Kontrolle und Messung (6).

## Round 5 — Codex (final round, cap reached)
The plan is much stronger, but the new freeze design still has material execution flaws.

1. **Runtime tracing conflates verifier dependencies with the subject under test.** Good, mutant, and real-tree runs intentionally open/import repo-local files under `daimon/**`, `face/**`, and fixture trees; [PLAN.md:45](/home/itiger013/Dokumente/Github/dAImon/PLAN.md:45) currently treats every undeclared repo-local open as a freeze failure.  
Fix: Define subject roots (`DAIMON_FIXTURE`, product source, generated test data) as non-dependencies and trace recursively from process start with descendants included, freezing only verifier-framework code and executable helpers.

2. **The ban on variable-built local paths is incompatible with existing frozen verifiers.** T-3.11, T-3.12, T-3.13, and T-3.10 construct helper or nested-verifier paths through `$HIER` or task-name interpolation, so the proposed migration cannot pass without modifying several frozen scripts.  
Fix: Permit computed paths only when their resolved targets belong to a finite, explicitly declared dependency set; otherwise create separately authorized `.v2` tasks for every wrapper that must be rewritten.

3. **T-3.12 is scheduled for the same coverage migration twice.** The prerequisite migration already adds its helper to the manifest/FROZEN, after which Step 3 again requires adding the helper hash and replacing the wrapper hash even when diagnosis permits no assertion change.  
Fix: Let the prerequisite task add unchanged T-3.12 dependency coverage once; Step 3 should rehash wrapper/helper only if a proven harness defect causes content changes.

4. **The undeclared-helper mutant can pass for the wrong reason.** If the mutant changes an already-frozen wrapper to invoke a new helper, the wrapper hash alone makes `verify-frozen` fail, providing no evidence that dependency discovery caught the omitted helper.  
Fix: Build the mutant with a valid updated wrapper hash but intentionally omit the helper from the dependency manifest, and assert the specific undeclared-dependency failure.

5. **The manipulated-helper mutant mostly retests the old hash comparison.** A changed helper already listed in `FROZEN` proves ordinary hashing works, not that recursive declaration and discovery work.  
Fix: Add separate mutants for an undeclared transitive helper and a dynamically resolved path outside the declared finite set, and require failures attributed to dependency validation.

VERDICT: REVISE
### Claude's response (Round 5) — Abschluss bei MAX_ROUNDS
Kein APPROVED innerhalb der Rundengrenze. Der Verlauf konvergierte
(20 → 12 → 9 → 6 → 5 Befunde), es gibt KEINE offene Meinungsverschiedenheit:
alle fünf Schlussbefunde sind akzeptiert und in die Round-5-Fassung
eingearbeitet — Subjekt-Wurzeln von Abhängigkeiten getrennt (1), berechnete
Pfade gegen die deklarierte endliche Menge statt Totalverbot, Bestand per
eigenen .v2-Tasks (2), T-3.12-Abdeckung einmalig aus der Bestandsmigration,
Neu-Hashen nur bei Inhaltsänderung (3), Mutanten der Freeze-Erweiterung
so gebaut, dass sie an der Abhängigkeits-Entdeckung scheitern müssen, nicht
am alten Hash-Vergleich (4, 5). Die letzte Fassung hat Codex nicht mehr
gesehen. Abnahme-Entscheidung liegt bei Matthias.

---

## Act 3 — Build (Codex baut, Claude prueft)

Branch `reviewer/p4-verifizierer` von `e23a0cf`, eigener Worktree. Modell
`gpt-5.6-sol`, effort `medium`, MAX_FIX_ROUNDS=2. Scope dieser Runde nach
Owner-Entscheidung: Voraussetzungs-Task Freeze-Erweiterung + T-0.9, plus der
als T-3.9.v2 autorisierte Transfer. Alles andere aus PLAN.md ausdruecklich
NICHT begonnen.

### Runde 1 — Codex build
Geliefert: `freeze-deps.py` (geschlossene Deklarationsgrammatik, rekursive
Entdeckung, Manifest-Abgleich, Laufzeitspur-Auswertung), `FROZEN.deps` als
Kantenliste, Erweiterung von `freeze.sh` und `verify-frozen.sh`,
`freeze-extension.sh` als Mutationstreiber mit drei Mutanten, T-0.9-Reparatur
ueber Inode-/fd-Korrelation samt Positivkontrolle und TCP-Mutant,
Bestandsmigration der sechs ungehashten Helfer, T-3.9-Transfer mit
verifizierter Provenienz `8da66117…`.

Befund von Codex, der die Aufgabenstellung korrigiert hat: `$hub` war NICHT
die falsche PID. Die alte Pruefung war rot, weil `PR_SET_DUMPABLE=0` sowohl
die PID-Angabe in `ss -p` als auch den externen Zugriff auf `/proc/<pid>/fd`
unterdrueckt — genau die Falle, die HANDOVER.md schon kennt. Die Korrelation
laeuft deshalb im Hub-Prozess selbst ueber `/proc/self/fd`; gelesen wird
Kernel-Wahrheit, kein vom Pruefling gefuehrter Zaehler.

### Claude's verdict (Runde 1)
Alle vier Beweise unter dem ECHTEN venv nachgefahren (der Worktree hatte
keins; Codex' eigener Lauf lief unter System-Python und war damit nicht
aussagekraeftig — Symlink gesetzt, alles reproduziert). Dann drei eigene
Angriffe auf die neue Sperre:

* A1 `.probe-`-praefigierter Helfer — statisch abgelehnt.
* A2 f-String-Verifiziererpfad mit einfachen Anfuehrungszeichen — abgelehnt.
* A3 relativer Aufruf `./helfer.py` nach `cd` — **DURCHGELASSEN** von der
  statischen Pruefung. Die Laufzeitspur fing ihn (nachgemessen), aber
  `freeze.sh` behandelte fehlendes `strace` als blosse WARNUNG und fror
  trotzdem ein. Auf einer Maschine ohne strace waere daraus ein
  FROZEN-Eintrag geworden, der aussieht wie jeder andere und eine Zusage
  behauptet, die nie gemessen wurde — die Signatur dieses Projekts.

Drei Befunde zurueckgegeben: (1) A3 + strace-Warnung, (2) die unbegruendete
`/.probe-`-Ausnahme in der Spurpruefung, (3) die quote-abhaengige Erkennung
dynamischer Taskpfade (dieselbe Lehre wie T-1.7.v3: ein Verifizierer, der
per Textsuche prueft, ist an der Schreibweise zu umgehen).

### Runde 2 — Codex fix
Fehlendes `strace` ist jetzt ein harter Abbruch VOR Mutationstest und
Manifestschreiben; die statische Pruefung erkennt relative Helferpfade; die
`.probe-`-Ausnahme ist ersatzlos gestrichen; die Taskerkennung laeuft ueber
den AST statt ueber Anfuehrungszeichen. Vierter Mutant
`relativer-pfad-helfer-fehlt` dauerhaft angelegt.

### Claude's verdict (Runde 2) — angenommen
Selbst nachgemessen, nicht uebernommen:
* `verify-frozen.sh` → 32 Dateien unveraendert, Abhaengigkeiten geschlossen.
* `meta.sh freeze-extension` → 4 Mutanten, alle erkannt.
* A1/A2/A3 alle drei statisch abgelehnt.
* Ohne strace: `freeze: FEHLER`, Exit 1, FROZEN byteidentisch vorher/nachher.
* `T-0.9.sh` gruen; TCP-Mutante faellt an genau der TCP-Eigentumspruefung.
* Diff beruehrt weder `daimon/`, `face/`, `kwin-script/`, `config/` noch die
  kanonischen Plandokumente.

Offen und benannt, kein Blocker dieser Runde: `freeze.sh` selbst steht nicht
in FROZEN — der Pruefpfad (`verify-frozen.sh`, `freeze-deps.py`) ist
geschuetzt, der Erzeugerpfad nicht. Und das Einfrieren ist jetzt deutlich
teurer: die Spur faehrt den Verifizierer je Mutant plus Gut- und Echtlauf
erneut.
