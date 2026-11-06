# Active: timeout proof closure — fresh clean-context writer (1e071adb)

Deadline 03:29:39Z; checkpoint 01:59:39Z. Now 18:29Z. Sole writer. Disk 5.7Gi free.

## Done this session
- Read all briefs; froze understanding. Source = frozen-final (runner 5a98f19).
- Test repairs in describe_runner.py:
  * Split flaky-collection conjunction + no-unsupported-evidence (targets 1026).
  * Added it_still_kills_when_the_recurring_failure_lacks_a_stable_fingerprint
    (no-fingerprint recurrence must still kill; targets 1015 promotion path).
  * Added it_requires_matching_fingerprint_evence (survived when no recurrence).
  All describe_runner 203 PASS.
- Diagnosed 1015/1026: under mutants, worker JSON round-trip normalizes
  fingerprints to lists, so _FailureEvidence equality holds; both are genuine
  survivors (verified via direct worker runs). Documented as contract
  equivalents pending owner decision — no waiver claimed.

## Cursor / remaining
1. Rerun runner changed-function native DEFAULT (63) to completion with
   per-mutant partition + live guard; capture survivors. Then ALL scope.
2. Private component native refresh (worker/guardian/process) on frozen source.
3. Testcode native for describe_runner/timeout_process/runner_timeout/
   engine_contract + e2e Django test domain.
4. T1: engine.py:415 Compare:Is current-source selection + bounded genuine
   replay (early-CALL kill expected).
5. Final gates refresh (focused coverage, mypy, ordinary affected).

Ledger: .pi/evidence/timeout-enforcement-fix/continuation-clean-context/
