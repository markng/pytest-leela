# Timeout repair — partial implementation checkpoint, NOT accepted complete

## Authority / frozen source
Sole writer R, HEAD b1bb565883c75356be1a493c64084607d5769f3f, branch fix/responsive-watch-generation-20261022. Owner explicitly authorised timeout seam, per-process Django import semantics and later removed Windows support/validation promise. Only pyproject classifier OS Independent→POSIX changed; dependencies/config/uv.lock unchanged. No commit/stage/publication. Final input: frozen-final-source.sha256; owned task delta timeout-task-delta.patch SHA8cb071f5cb9c211bc91c70f123fa776db6d18dc43f861eb74c191836eaf9f518. Historical71 manifest d24b5c1667543d182b96e238ab0bca408b738c93a055bf055f39a73e0e06c3ec remains immutable.

## Implementation
Budgeted InnerSession uses private stdlib exec worker and exec guardian, not multithread fork/main-thread signals/asynchronous Python exceptions. Parent retains Engine/SQLite/publication/callbacks and result classification. Existing budget max(2*sum(selected durations)+1,5) preserved; no timing/IDs means in-process disabled semantics. Worker startup debits deadline. Clean controls inherit budget; timeout remains native protocol kill but NEVER genuine CALL evidence.

Private guardian owns worker group, watches caller lifetime pipe EOF, kills/reaps before returning result. Leela nested guardians independently receive caller EOF on outer termination. Scope: ordinary contained descendants and Leela-owned nested workers, not deliberate user setsid/double-fork/external side effects/security sandbox. Process is never signalled after reap; delayed IPC error clears ownership. Explicit non-daemon IPC/lifetime threads, raw selector caller IPC, stop/join lifetime watcher avoid buffered-daemon shutdown race. Guardian exit after response checked and bounded stderr diagnostic returned as error. Native JSON collector/import evidence/fingerprints preserve causal controls. Prefix/sys.path identity restored before sysconfig/imports in -I -S worker.

Owned product runner + _timeout_process/_timeout_worker/_timeout_guardian; no Engine/index changes. Necessary Django e2e now requires exactly4 processes/20 imports, each pinned module once per process with original real registry/model/reference checks. README documents repeated startup side effects, POSIX boundary, no global shutdown guarantee. Nine Timer/plugin mocks replaced with real budget/classification/preemption/lifecycle controls; two dead plugin tests removed. Final suite1711 versus historical1662 (+49); full test/source inventory in frozen manifest and task delta.

## T1 evidence and limitations
- Pre-edit red background-thread busy/blocking/collection entered, exceeded unchanged5s and independently killed/reaped9s; normal/failure controls passed. red-results.json/red-run.log, originalpatch29b0.
- Initial same-bootstrap green ~5.25–5.32s; subsequent real CALL/collection/teardown, failure, crash, malformedIPC, cancellation, owned descendant normal/crash/timeout, nested callerSIGKILL+sentinel controls. Latest rawguardian nested death0.052s, no helper leftovers. Fault scheduling delays real threads/events, genuine is_alive, bounded joins, finally release/drain.
- Original exact engine415:49 Compare:Is native witness current producer/enrichment/native selection135, clean135PASS531.626s; actual MutatingLoader hashdd934f…; nativeTIMEOUT1208.547s versus measured1207.409s,0CALLcredit. These are intermediate-source receipts, not final-source revalidation. Early Engine no-diff CALL preflight added, native default/all witness assertion kills before unsafe recursion, genuine phase observed; final complete native selection refresh remains incomplete.
- Native Python3.12 crash incident: parent observed SIGABRT child87617 with parent86826; exact command/test unknown. Buffered daemon stdin finalization was leading theory. Parent INT thenTERM original312 run actual−15. Safe correction/no abort repro;15real returns each312/13/14 and later full suites pass. Catching exit does not suppress OS crash UI; no CrashReporter/settings changes.

## T2 — NOT SATISFIED
Final focused coverage259testsPASS65.06s (final-coverage.log): private guardian41statements/8branches, process128/28, worker33/6 all100%; earlytest20/0, timeouttest59/6, testprocess674/20 all100%. Runner467statements/180branches98%, missing7 lines and4 arcs; global99%. Zero earlytest arc domain is explicitly zero, containing module line-qualified, not vacuous mutation perfection. Expanded AST audit is line-position diagnostic, not full expanded statement proof. Embedded fixture/bootstrap93fragments remain independent executable-code qualification debt.

Native component progress (each receipt must be read with source history): processDEFAULT43+ALL53 named/live-bound CALL; guardian4/5 and worker5/11. Earlier guardian/worker entryguard bootstrap IndexError receipts were rejected; safe valid-argv/request/lifetime lazy-import contracts produce successful module execution then genuine assertion; predicate corrected to require successful execution. Latest explicit non-daemon flags/ownership edits invalidate some component receipts; no final union/fullscore claim.

Testcode earlyALL5 and timeoutALL24 genuineCALL; testprocessALL189178genuine then191189genuine, last2 faithful selector/conjunction repairs ordinaryPASS but final native rerun intentionally deferred after directed stop. DEFAULT/full fixture domains not complete. Runner changed-containing-function DEFAULT63 denominator stopped at900s with53records/8survivors/10incomplete; ALL not run. Last activation unknown, no score. Supervisor directed no resume/broad expansion and partial handoff. Old substrate107/112/56/8 mutation proofs not blindly reused; affected runner/import/selection/control inputs require requalification. Whole configured native13/14 deferred parentreview, not green/N/A.

## T3 fresh final gates
- final-ordinary312.log:1711PASS302.90s exact two CI warning exclusions, no skips, no -x.
- final-ordinary313.log:1711PASS341.93s.
- final-ordinary314.log:1711PASS344.34s.
- final-mypy313/314.log:22source files clean each.
- Exact fatal flake8 root BOTH fail144 thirdparty errors (40F821,99F822,5F824), retained. Same exact command clean-export-final BOTH0, no excludes/config edits.
- Reused verified offline hatchling1.32.4 backend/deps copied owned build-tool; build --no-isolation clean source sd+wheel PASS. Both archives all22package files byte-match frozen source; no.pi/no removed Windows backend. final-package-bindings.tsv, final-distribution.sha256. No installs/env package changes/network build.
- uv.lock unchanged; prior lock proof qualified historical input, no new locksync claim. New metadata-only classifier does not alter dependency inputs, but no fresh lockcheck asserted.

## T4 preservation / incident
Original71/patch29b0, index90e, new14test82b, uv.lock, canonical5a preserved. Revised-scope manifest checks clean; initial scope preserved separately (earlier in-place scope updates disclosed). Obsolete host frozen pyproject assertion fails exactly owner-authorised classifier delta; never hidden or waived. git diff --check/stagedempty pass, no commits. final-processes.txt empty.

Sensitive-output incident: broad environment grep accidentally printed inherited credential variables into internal transcript. Values not repeated in report/artifacts; parent notified/owner informed. Persistence/model-request exposure not disproven; earlier no-file/no-external assertion withdrawn. No deliberate credential copy/use/share/network action, no auth changes. Names/command/time and known session location only in security-note.md. No evidence deletion/rotation without authority.

## Artifacts / next step
1091 evidence artifacts inventoried (excluding copied package/cache trees),19 disposablediagnostic source files with syntax identities; artifact-files.*, diagnostic-final-inventory.json, artifact-inventory.md, fixture-string-inventory.json. Product/tests never classified disposable. Official reference cache private unexecuted; Apple retrieval scope deviation retained/then authorised; no reference code copied.

Parent should treat this as a partial frozen implementation checkpoint, not review-ready/full acceptance. Continue strict current-source runner/test/fixture/old-substrate proofs and final stable witness ordering/cleanup audit before one fresh review and huge native13/14. No publication authority.
