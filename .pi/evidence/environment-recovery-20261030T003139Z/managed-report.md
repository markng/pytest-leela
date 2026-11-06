# Isolated environment and containment recovery

The full Markdown inventory, commands, package fingerprints and residual losses are at `/Users/mng/Projects/pytest-leela-recovery/timeout-20261029T162630Z/.pi/environment-readiness.md`; machine-readable absolute references are in `.pi/environment-readiness.json`. The owner-approved environment reconstruction and real bounded containment controls passed. This is **not** original feature completion or publication readiness.

CURRENT STATUS: three NEW isolated environments are ready for bounded ordinary use, with matching Python/pytest matrix and editable THIS checkout. Seven real bounded guard controls and a small env313 positive/failure-detection pair passed; original raw/T4 receipt losses and full feature gates remain. Next command is the source-bound env313 Django collection-only command recorded in STATUS.

```acceptance-report
{
  "criteriaSatisfied": [
    {"id":"ENV1-pinned-isolation","status":"satisfied","evidence":"env312 Python3.12.12/pytest7.0.0; env313 Python3.13.11/pytest9.1.1; env314 Python3.14.8/pytest9.1.1; installed active lock pins verified, actual editable direct URL binds this checkout; complete distribution/file SHA manifests and freeze paths in .pi/environment-readiness.json."},
    {"id":"ENV2-real-containment-readiness","status":"satisfied","evidence":"Byte-identical guard hashes and expected layout; seven real bounded positive/nonzero/timeout/cleanup/growth/deadline/missing-root checks with owned PID disappearance, raw logs in .pi/environment-recovery; one small ordinary positive 2 passed and failure-detection 1 passed."},
    {"id":"ENV3-ready-next-step-not-feature-done","status":"satisfied","evidence":".pi/environment-readiness.md and JSON include exact runtime/source/artifact identities, remaining old raw/T4 losses, and unexecuted next collection-only command; STATUS explicitly original task NOT COMPLETE."}
  ],
  "changedFiles": [".pi/STATUS.md", ".pi/environment-readiness.json", ".pi/environment-readiness.md", ".pi/environment-recovery/", ".pi/evidence/timeout-enforcement-fix/run_check.py", ".pi/evidence/final-current-ci/py313/resource_guard_poller.py", "/Users/mng/Projects/pytest-leela-recovery/runtime-20261029/"],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {"command":"installed Python -m venv --without-pip env312/env313/env314; uv pip install --python each --no-python-downloads -c runtime/constraintsNNN.txt -b runtime/build-constraints.txt -e THIS[dev,django] hatchling==1.32.4 flake8","result":"passed","summary":"Three guarded, isolated installs; exact commands and outputs retained."},
    {"command":"python3.13 .pi/environment-recovery/containment_controls.py","result":"passed","summary":"Seven bounded real controls; owned process PIDs gone."},
    {"command":"env313/bin/python -m pytest tests/leela/describe_a_timeout_engine_contract.py -q","result":"passed","summary":"2 passed."},
    {"command":"env313/bin/python -m pytest tests/leela/describe_runner_timeout.py::test_inner_session_worker_preserves_real_call_failure -q","result":"passed","summary":"1 passed; observes real failed mutant behavior within normal contract test."},
    {"command":"test \"$(git rev-parse HEAD)\" = b1bb565883c75356be1a493c64084607d5769f3f && test -z \"$(git diff --cached --name-only)\" && shasum -a256 -c .pi/RESTORED-SOURCE.sha256 && git diff --check","result":"passed","summary":"HEAD/index preserved; 17 source hashes OK; no whitespace errors."},
    {"command":"test -s .pi/environment-readiness.json && test -s .pi/environment-readiness.md; test -f .pi/STATUS.md && test -n \"$(find .pi/STATUS.md -mmin -5)\"","result":"passed","summary":"Both artifacts present; STATUS fresh."}
  ],
  "validationOutput": ["env312: 30 distributions; env313/env314: 28 each; complete installed-file SHA manifests in .pi/environment-recovery.", "Seven real bounded containment controls passed; env313 ordinary positive 2 passed and failure detection 1 passed; source/index/readiness gates passed."],
  "residualRisks": ["The rebuilt environments are new, not byte-identical historical ones; explicitly recorded unknown-only transitives were selected and hashed.", "Original raw ledgers and three older T4 receipts missing; full ordinary suites, coverage/statement/branch, mutation campaigns, lint/mypy/build and independent feature review remain unrun on these new environments.", "No unsafe mutant was run; guard readiness must be reverified for each later campaign."],
  "noStagedFiles": true,
  "diffSummary": "Only recovery evidence, byte-identical placed guard copies, disposable one-investigation control, and separately rooted isolated environments; no production/config/lock changes.",
  "reviewFindings": [],
  "manualNotes": "Pin-gap decision from supervisor resolved only unknown required transitive/build/lint versions; existing lock pins preserved except approved pytest matrix. Disposable control SHA a11694f69d046ce0ede715a5bcb8cbc8514e9efbf5ac255405d55983ecbff6e4. No skill/profile or publication edits. Next unexecuted command: /Users/mng/Projects/pytest-leela-recovery/runtime-20261029/env313/bin/python -m pytest tests/leela/describe_end_to_end.py --collect-only -q under private HOME/XDG/TMP."
}
```
