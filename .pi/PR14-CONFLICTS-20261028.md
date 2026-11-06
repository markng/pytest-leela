# PR14 conflict resolution — owner-authorised task

## Outcome / authority
Owner: "Take a look at PR14 ... resolve [conflicts], then tell me its state of readiness (or is that the PR you're already working on?)".
Owner separately approved via ask_user: **Yes, update PR14 after passing checks**; push only reviewed/checked resolution, no force-push or merge into main. Parent owns final commit/push decision. No PR comments/body edits or human-facing GitHub messages without approval.

PR14 is the same indexed-watcher feature area, but NOT the unpublished timeout/proof worktree. Do not silently fold unfinished timeout fixes into this PR or change its scope. Preserve both current main's causal setup/collection evidence fix and PR14's indexed watcher/native hardening.

## Isolated writer ownership
- Worktree: `/private/tmp/pytest-leela-pr14-conflicts.eNfDbA`
- Local branch: `fix/pr14-conflicts-20261028-185626`
- PR head: `pi/workstreams-8b2741-integration`, frozen SHA `debd51d6bf19012e93637888cb07fb10260cf3e7`.
- Target main: `ccd010f97938e3414c0078ab1de5d0c404c593f6` (includes PR17 `19e690a` causal evidence fix).
- Worktree starts clean at PR head. Parent created it manually and safely; canonical dirty main was NOT checked out/reset/pulled. Shared repository refs are not permission to touch canonical source.
- Canonical `/Users/mng/Projects/pytest-leela` owner tracked diff remains `5a348a20f6bb93f8f30f5a98560e6a97800c2ef59ae86721275f056ce9ccaddb`, local main HEAD a729661..., dirty26files/untracked work preserved.
- Separate timeout writer `1156139e-1899-4eb0-ae52-7d38967dcca4` owns `/private/tmp/pi-leela-responsive-watch-20261022` only; coordination sent. No edits/copies/adoption across task trees. No old agent/session/transcript reads (credential incident); fresh context using git/source/sanitized task notes only.

## Proven conflict baseline
Read gh PR metadata. PR OPEN/notdraft; mergeable CONFLICTING/mergeState DIRTY. Existing five successful checks attach to debd51d (October10), not merged current main; historical, not readiness proof.
Fetch only updated remote tracking refs, no dirty pull. `git merge-tree --write-tree --name-only PRhead main` found seven conflicts:
- src/pytest_leela/engine.py
- src/pytest_leela/models.py
- src/pytest_leela/runner.py
- tests/leela/describe_benchmark.py
- tests/leela/describe_engine.py
- tests/leela/describe_runner.py
- uv.lock
Other files auto-merge and still need interaction review. No scoped AGENTS.md/CLAUDE.md found in the clean PR tree.

## Resolution / acceptance
1. Merge exact frozen main into this isolated PR branch without committing. Resolve semantically, not wholesale ours/theirs. Preserve main PR17 causal failure evidence, uncached fresh-source fixes, current0.9 metadata/dependencies, Django/floor support, plus PR indexed generation/publication/cache/watch/native hardening. Check model/runner/engine and all callsites/tests together. Do not weaken tests, skip mutants, change scoring/fault model, add suppressions/exclusions or adopt unaccepted timeout implementation.
2. Resolve lockfile against actual merged manifests, retain current-main dependency versions unless PR has a justified unique requirement; no unrelated upgrade/downgrade. Verify with configured pinned uv check; no handwaved lock correctness.
3. Read merged manifests/CI and run repo-native gates in correctly source-bound environments: floor3.12/pytest7 with exact two configured warning exceptions;3.13/3.14 ordinary tests; fatalflake8, mypy, lockcheck/build; native mutation selfcheck as configured. New/changed executable logic requires100lines/statements/branches and native genuine CALL kill evidence before readiness. Do not claim old PR green checks or other worktree results qualify this merge. All slow/dangerous mutation runs independently time/resource-contained, truthful exit status, private owned caches/TMP, no timeout/error kill credit for strict metrics. No unguarded known runaway route. If a required gate cannot safely finish, preserve partial and report not-ready, not suppressed green.
4. One fresh read-only independent review of resolved/auto-merged interactions and actual readiness evidence. Parent then commits/pushes ONLY if all required gates/review permit. Before push recheck remote PR head/main; never force or overwrite concurrent branch changes. No main merge.

## Operational constraints
No source changes in canonical/timeout/other worktrees, no shared environment/package mutation, no credentials/auth/global/model changes, no broad env inspection, no uploads/private logs. Owner handles credential rotation. Keep evidence/new task environments OUTSIDE source checkout to avoid contaminating native root lint. Prefer pinned available read-only/offline caches, verify actual source/import/artifact identity; never mistake editable timeout-worktree imports for this PR. New isolated task environments may use existing declared/locked dependencies only; no new services/spending/dependency changes or credential work. Report missing dependency/auth/license/security or unapproved-material-scope blockers, no bypass.

Only this PR writer touches this worktree. Do not commit/push or leave staged/unmerged entries at handoff; merge may stage automatically, so unstage only task-owned resolved paths without discarding file contents; preserve merge-parent identity/state in evidence for parent. Never git reset --hard/clean, no hook bypass. Report exact changed paths/diff, tests/source/env bindings, results/errors, remaining readiness blockers and desired merge parents. Passing tests are not adoption or remote CI green. Docs: diffcheck/applicable checks. Runtime/fixture/bootstrap code is code; disposable single-task diagnostics proportionate syntax/controls/identity inventory, not maintained tools by label.

## Review/readiness reporting
Clearly distinguish: working-tree conflicts resolved; local verification; independent review; committed/pushed head; GitHub mergeability/required CI/review status; unpublished timeout/proof follow-up. No full readiness claim until concrete required evidence. Do not add a numerical score loop. Parent tells owner readiness, no automatic merge/publication beyond the explicitly authorised PR branch update.
