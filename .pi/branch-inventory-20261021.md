# pytest-leela live branch/status inventory — 2026-10-21

Read-only GitHub API/PR and local git inventory. No fetch/pull/checkout/merge/delete or new implementation. 24 remote branches including main; 4 open PRs. GitHub main ccd010f9 has version0.9.0; canonical local main remains a729661 (0.8.0) with26 modified tracked files and additional untracked source/tests/metadata. Preserve that work; no pull/reset into it.

## Relevant current lines
| Branch | Head | PR/status | Meaning |
|---|---|---|---|
| main | ccd010f9 | default | 0.9.0 manifest;16 source modules, no daemon/index modules |
| repair/pr14-import-identity-jf2a2e95 | 2faea53b | #19 OPEN/DRAFT, UNSTABLE | newer indexed-watcher/import identity/package compatibility integration;4 commits ahead main; both3.13/3.14 CI builds failed, floor/lock/CodeQL passed |
| fix/heartbeat-stop-ci-20261020 | 6a36bc02 | no PR | reviewed heartbeat safety regression directly atop PR19 head;5 commits ahead main; local native gates green, no feature-branch remote CI run |
| pi/workstreams-8b2741-integration | debd51d6 | #14 OPEN, DIRTY/conflicts | older experimental indexed watcher/hardened mutation integration; historical head checks green, diverged2ahead/7behind currentmain |
| fix/package-init-module-identity-rv | d3b0099e | no PR | reviewed/pushed0.8 RV tool compatibility snapshot, includes eac14 annotation prerequisite;2ahead/7behind main; do not blindly merge this old-base branch into0.9 |
| fix/remove-bytecode | c1ff0640 | #7 OPEN | April bytecode-removal/Python3.14 CI proposal; old CodeQL checks green, no current full validation implied |
| sigalrm-timeout | 6c076d5b | #5 OPEN | April SIGALRM timeout approach; historical3.13/3.14 builds failed |

## Seven archival WIP branches (not finished feature acceptance)
Common prefix `wip/cleanup-20261019-`, suffix `-59889a0a`:
- annotations (17175e79)
- cache-index (fe8919f4)
- correctness (d137a65d)
- integration (8fd4a400)
- security (f59cb1da)
- state-hygiene (3232b288)
- watcher-discovery (c03ab63a)

## Eight retained branches whose PRs merged
- experiment/assignment-dataflow — #4
- feat/0.3.0-configurable-operators — #1
- fix/crashed-mutants-are-errors-not-kills — #12
- fix/diff-excludes-test-files — #2
- fix/issue13-setup-error-not-kill — #17
- fix/mutating-loader-file-attr — #10
- release/0.9.0-ubuntu-validation-bffc2b11 — #16
- working-tree-diff — #11

Do not equate merged PR with tip ancestry/deletion readiness: most branch comparisons diverge (history/squash differences). issue13 and release0.9 are ancestors of main; this inventory is NOT a blanket deletion authorization.

## Two retained branches with closed, unmerged PRs
- fix/exclude-bytecode-self-mutation — #6
- python-3.14-ci — #3

## Additional local-only primary-checkout refs
Six `pi/workstreams-8b2741-*` placeholders (annotations/cache-index/correctness/security/state-hygiene/watcher-discovery) all remain at0.8 basea729661. Also subprocess-isolation and subprocess-isolation-real experiments. Other task-private clones are not one canonical git worktree registry; package/heartbeat remote branches above are the delivered snapshots.

## Current activity / next recommendation
This session's package and heartbeat fixes are reviewed and pushed, not merged/released. Recent intercom was read-only installed mypy metadata sharing, not new Leela implementation.

Fresh owning-RV-parent reply: actual RV init/current qualification NOT finished/adopted; no scoped app commit/push during offline phase. Historical product60 proof is not current aggregate/init-live qualification. Initial new runtime48 passed canonical checking; test-only context change requalification hit ENOSPC. Recovery paused for owner-approved cache cleanup. Remaining proof gaps include current helper binding and intended-vs-actual fixture/reconcile AST deltas. No new Leela-tool defect/request established; source/loader/wheel unchanged. These are peer reports, not our fresh RV rerun.

Recommended NEXT, not executed/authorized by this status request: converge the0.9 integration line. First advance PR19 with the already-reviewed heartbeat child6a36bc0 (or equivalent scoped integration), trigger fresh main-base PR Ubuntu CI, diagnose actual remaining failures, then resolve PR14/19 supersession and historical PRs. Keep RV0.8 snapshot separate; port relevant fixes deliberately rather than merge all old-base history. Preserve local dirty checkout before any future synchronization. Only after stable integration should new coarse-operator/survivor-first work start (queued Pith774c1359, explicitly future work). The P8 __file__ ticket is stale-looking because its PR10 merged; do not implement again merely from open status. No task statuses or branches changed by this inventory.
