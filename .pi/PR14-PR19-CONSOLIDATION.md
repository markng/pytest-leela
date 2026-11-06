# Consolidate PR14 into surviving PR19

## Owner decision and authority

Owner selected **“Yes—keep #19”** on 2026-10-29 after the explicit confirmation question. Consolidate intended PR14/PR19 work into PR19, update its existing branch through ordinary non-force pushes after applicable gates, and close PR14 only after preservation, required checks and independent review pass. No merge into main, auto-merge, branch deletion, force-push, credential/provider changes, PR comments or title/body edits are authorized. Do not publish the separate local timeout changes as part of this consolidation.

PR14's governed writer 296311a4-e66e-4b6c-9464-22b0706f8bcb failed with OpenCode HTTP401 api_key_expired. Owner said **“I'll replace the key, then resume.”** Replacement readiness has NOT been confirmed. Do not retry, silently switch routes, start a replacement lane, or do foreground implementation as a workaround. Resume the known worker only after readiness, under the governed native protocol and revised acceptance/output contract. Preserve completed engineering; do not restart the whole qualification effort for missing prose.

## Pinned starting observations — refresh before publication

- PR14: https://github.com/markng/pytest-leela/pull/14 ; `pi/workstreams-8b2741-integration`; head `debd51d6bf19012e93637888cb07fb10260cf3e7`; open/conflicting.
- PR19: https://github.com/markng/pytest-leela/pull/19 ; `repair/pr14-import-identity-jf2a2e95`; head `b1bb565883c75356be1a493c64084607d5769f3f`; draft/mergeable/unstable. Its body references older eec68ef, NOT its current head. Last checked 3.13/3.14 builds cancelled; other listed checks successful. No complete CI claim.
- PR19 includes the watcher checkpoint, import-identity/package-compatibility repair, pinned-main integration, CLI heartbeat/progress, SQLite-readiness and heartbeat lifecycle fixes. These must not be overwritten by older PR14 code.
- Paused PR14 integration worktree `/private/tmp/pytest-leela-pr14-conflicts.eNfDbA`, branch `fix/pr14-conflicts-20261028-185626`, HEAD debd51d6 / MERGE_HEAD ccd010f9, empty index. Protected untracked `tests/leela/describe_end_to_end.py` SHA256 `2048f084958202a585c1b36235d34658d74946c49c57ebad9336092d7f54a022`.
- Immutable local preservation checkpoint `.pi/evidence/pr14-auth-blocker-20261029T041442Z/`: 33 source files + refs/status/worker STATUS; all copied hashes checked. Tracked patch SHA256 `90d8f5a7139d082c12991f1e611e2e573226b24b0fb18cac96fba03792fafd60`, still matched at 07:03. Latest engine test edits are not final-qualified.
- Canonical checkout `/Users/mng/Projects/pytest-leela`: owner's dirty tracked diff SHA256 `5a348a20f6bb93f8f30f5a98560e6a97800c2ef59ae86721275f056ce9ccaddb`, HEAD a729661b, empty index. Preserve it byte-for-byte; no reset/pull/source activation there.
- Separate R checkout `/private/tmp/pi-leela-responsive-watch-20261022` starts at the same b1bb565 PR19 head, but its uncommitted hard-timeout work is NOT published in PR19. Sole active R writer ca886c18-008a-46e1-85be-9afd5c225c41. No source/env writes, adoption, staging or publication from that tree. Inventory it separately and record deferred work; do not lose or silently fold it into this PR.

## Acceptance for the consolidation continuation

1. **C1 Preservation:** pin both current remote heads/base and account for committed and unpublished states. Retain immutable original patches/files/refs and source hashes. Do not destroy old evidence or branches.
2. **C2 Reconciliation:** produce a per-file/hunk ledger: present unchanged, superseded with rationale, carried into PR19, or deliberately deferred with an explicit preserved location. Include PR14's unpublished conflict/selector/cache fixes, PR19-only heartbeat/import/package repairs, newer main causal/fresh-source semantics, tests and packaging/CI changes. A commit-count comparison alone is insufficient. Resolve any genuine lost-work ambiguity before publication.
3. **C3 Candidate:** use an isolated integration checkout, one writer, based on the surviving PR19 branch. Preserve caller selectors, actual collected file/suffix origins, supported cache fixture, original git_diff abspath behavior and both branches' intended functionality. No unaccepted R timeout adoption, suppressions, skipped tests or unrelated rewrites.
4. **C4 Evidence:** select repository-native gates from actual config/CI. Bind all reused evidence to unchanged relevant inputs; rerun affected cases, not completed work merely for prose. Meet required changed-code/test/generated-code line, expanded statement, branch and mutation standards; inventory executable artifacts. Source-bound floor/3.13/3.14 tests, types/lint/build/lock/package checks and required CI must be honest. Obtain applicable focused independent review of the consolidated candidate; no automatic extra judge.
5. **C5 Publication and closure:** recheck remote heads/main for concurrent changes before a normal authorized PR19 branch update. Keep PR19 unmerged/draft until genuinely ready; no green claims from cancelled CI. Close PR14 only after the reconciliation ledger proves preservation and required gates/CI/review pass for the surviving candidate. No main merge, branch deletion or unapproved external text.

## Next step

While awaiting key readiness, parent may perform bounded read-only inventory and prepare this handoff. Do not start implementation via a substitute protocol/provider. On confirmed readiness inspect exact prior run eligibility, preserve current partial state, then revise acceptance and managed output binding through supported native resume. Never edit historical receipts to imply that they passed against the new candidate.
