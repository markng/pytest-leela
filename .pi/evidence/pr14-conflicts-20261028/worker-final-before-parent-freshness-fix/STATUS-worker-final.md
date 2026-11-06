# PR14 — paused safely; text conflicts resolved, verification failing/incomplete

Owner authorises resolving PR14 and pushing ONLY after required checks + one fresh independent review. Parent reserves commit/push; no main merge, force-push, PR comments/body edits, other-tree changes or unaccepted timeout adoption.

Checkout `/private/tmp/pytest-leela-pr14-conflicts.eNfDbA`, branch `fix/pr14-conflicts-20261028-185626`; HEAD `debd51d6bf19012e93637888cb07fb10260cf3e7`; MERGE_HEAD `ccd010f97938e3414c0078ab1de5d0c404c593f6`. All conflict-start markers removed from src/tests/uv.lock; semantic correctness NOT established. Staged index empty; preserve untracked main-added `tests/leela/describe_end_to_end.py`.

Parent intentionally interrupted worker38e01da6 after discovering native bash `timeout` means SECONDS: its argument600000 was not600seconds. Previous parent assertion of10minute bound was wrong. No subagent_command control granted, so exact PR run interrupted; native PAUSED/process-terminal observed, shell74224/python74225 absent. Separate timeout writer115 remains active/isolated. No source rollback. Before/after resolved tracked diff SHA `64bc13bf2d65b82f673f5e1d75738e06764376c66e8a997e509cd144cc259021` identical.

Preservation: canonical `.pi/evidence/pr14-conflicts-20261028/timeout-unit-containment-20261028T194339Z/` contains full resolved tracked patch, staged patch, status, merge parents, untracked end_to_end bytes (SHA2048f084958202a585c1b36235d34658d74946c49c57ebad9336092d7f54a022), and interrupted-tool-result.txt. Actual ordinary3.14 `python -m pytest -q` output reached28% with multiple F then `Command aborted`; NO completed suite/pass, no failure nodeIDs yet, no verified hang attribution. This is failing/incomplete evidence, NOT a gate.

Operational violation/correction needed: owned `.venv` created inside checkout, contrary to external-env constraint. Its3.14.8 Python symlink and editable .pth point THIS PR/src, not other tree. Current command is stopped; after verifying ALL owned descendants absent, move owned env reversibly outside source or create equivalent using declared/locked deps, verify prefix/import/dependency identities and use python -m commands. No deletion/shared env mutation/unapproved upgrades. Keep caches/evidence/HOME/TMP external. Do not hide third-party root-lint failures with exclusions.

Next: resume same governed writer ONLY from preserved state, no remerge/restart of completed resolution. Establish verified independent timeout/resource/private-TMP/log/exit containment with positive/nonzero controls; preserve original guard. Diagnose existing ordinary failures with bounded verbose fail-fast selections before any whole-suite repeat. Full mandatory native gates/coverage/mutation/source bindings still required; report exact blockers honestly. Checkpoint before long calls/compaction. Parent consumes actual final managed report and arranges ONE fresh review (original workflow failed before review and will not restart it). No commit/push yet.

## Resumed verification checkpoint
- Relocated owned in-source `.venv` reversibly to `/private/tmp/pr14-verify-38e01da6/venv`; verified `sys.prefix`, Python executable, pytest 9.1.1, and editable import resolves to this PR checkout. Source checkout no longer contains `.venv`.
- External bounded-run guard `/private/tmp/pr14-verify-38e01da6/run-bounded.sh` qualified with exit 0, propagated exit 7, and timeout exit 124. Private HOME/XDG/TMP/log directories created externally.
- Remaining known semantic failures from prior bounded focused suite: old PR collection-import assertion conflicts with main causal controls; index preflight error state; selected fresh-source cache case. Full suite prior run interrupted/incomplete.

## Verification progress checkpoint 2
- Corrected bounded wrapper to kill/reap the entire owned process group, including controlled direct-parent-exits/TERM-ignoring descendant; controls exit0, exit7, timeout124, and no remaining `sleep 100` passed.
- Reused exact read-only resource guard source copied from `/private/tmp/pi-leela-responsive-watch-20261022/.pi/evidence/final-current-ci/py313/resource_guard_poller.py`, SHA-256 `917ad76e...`; pure deadline/growth/free-space and real-du measurements of checkout/external root passed. No long native mutation campaign launched yet.
- Focused conflict files: 305 passed. Full ordinary fail-fast advanced from 255 to 348 to 433 to 444 passed while repairing daemon cwd/sys.path, target module eviction, project scope, integration fixtures; latest broad attempt timed out at 300s after 28%, so ordinary full gate remains incomplete/not ready.
- Current known targeted repairs pass: daemon progress, Django pinned registry E2E, index preflight error state, engine index cold cache, unresolved cache bypass.

## Final checkpoint
- Text conflicts remain fully removed; structural syntax/diff/lock checks pass; exact merge parents and empty index preserved.
- Focused merged conflict suite passed 305 tests. Engine integration suite passed 21 tests after baseline-aware fixture isolation. Mypy passed. Build and lock check passed.
- Full ordinary3.14 remains NOT green: several fail-fast iterations fixed real daemon/project-scope/Django/target-eviction/cache issues and reached 458 passes before a source-freshness interaction. Current bounded focused baseline-freshness suite has 5 passed/1 failed (`selected=True, changed_input=helper`: expected BaselineFailure not raised). No full ordinary rerun after this unresolved failure.
- Resource-guarded full run used copied SHA-matched poller over checkout+external task root; stopped on first test failure with guard clean (no growth/free-space violation). Native mutation, floor3.12,3.13, coverage100/CALL evidence and fresh independent review remain outstanding. NOT READY; no commit/push.
