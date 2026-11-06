// Disposable task-specific launch specification; no credentials/history ingestion.
const closure = await runs.run("timeout-proof-closure-clean-context", {
  agent: "mutation-gap-closer",
  context: "fresh",
  cwd: "/private/tmp/pi-leela-responsive-watch-20261022",
  task: args.task,
  timeoutMs: 36000000,
  toolTimeoutMs: 3600000,
  checkpointBeforeDeadlineMs: 5400000,
  output: "responsive-watch/timeout-proof-closure-clean-context.md",
  outputMode: "file-only",
  acceptance: {
    level: "verified",
    criteria: [
      {id:"T1-hard-timeout-correctness",must:"Complete current-source bounded lifecycle and native stable-witness qualification using genuine behavioral CALL evidence, safe background execution, cancellation, IPC, descendant cleanup and original deadline semantics.",evidence:["tests-added","commands-run","validation-output","manual-notes","residual-risks"],severity:"required"},
      {id:"T2-strict-current-source-proof",must:"All remaining new/changed product, tests, reusable helpers and runtime fixture/bootstrap code have100% executable lines/expanded statements/branches and native DEFAULT+ALL genuine CALL kills with full source/loader/function-or-module/selection/control/phase binding; requalify affected prior substrate proofs. No suppressions, skips, fake kills or stale-score unions.",evidence:["changed-files","tests-added","commands-run","validation-output","manual-notes","residual-risks"],severity:"required"},
      {id:"T3-current-regression-gates",must:"Reuse only explicitly unchanged relevant-input evidence or refresh current ordinary312floor/313/314, configured mypy13/14, exact fatal-lint root versus byte-bound-export domains, offline source-bound build and package identities. Whole native13/14 remains deferred to parent review, never claimed green.",evidence:["commands-run","validation-output","manual-notes","residual-risks"],severity:"required"},
      {id:"T4-preservation-safe-handoff",must:"Preserve protected WIP/config/lock/env/index/new14test/canonical source and all historical evidence, honour owner-approved POSIX metadata exception, maintain complete artifact inventory and fresh STATUS, no staging/publication or secret/history replay.",evidence:["changed-files","commands-run","no-staged-files","diff-summary","manual-notes","residual-risks"],severity:"required"}
    ],
    evidence:["changed-files","tests-added","commands-run","validation-output","residual-risks","no-staged-files","diff-summary","manual-notes"],
    verify:[
      {id:"owner-scope-preservation",command:"test \"$(shasum -a 256 .pi/final-ci-preserved-inputs.sha256 | cut -d ' ' -f 1)\" = d24b5c1667543d182b96e238ab0bca408b738c93a055bf055f39a73e0e06c3ec && grep -E '  (src/pytest_leela/index.py|tests/leela/describe_ci_mutation_regressions.py|uv.lock)$' .pi/final-ci-preserved-inputs.sha256 | shasum -a 256 -c && test \"$(shasum -a 256 pyproject.toml | cut -d ' ' -f 1)\" = 13fbdb20e9def83d856c86546bcef39e22faa5e3212b659b4be941b6a7c6abc1 && shasum -a 256 -c .pi/evidence/timeout-enforcement-fix/protected-nonowned-revised.sha256",timeoutMs:15000,allowFailure:false},
      {id:"canonical-head-index-diff",command:"test \"$(git -C /Users/mng/Projects/pytest-leela diff --binary | shasum -a 256 | cut -d ' ' -f 1)\" = 5a348a20f6bb93f8f30f5a98560e6a97800c2ef59ae86721275f056ce9ccaddb && test \"$(git rev-parse HEAD)\" = b1bb565883c75356be1a493c64084607d5769f3f && git diff --check && git diff --cached --quiet",timeoutMs:10000,allowFailure:false},
      {id:"status-md-fresh",command:"test -f .pi/STATUS.md && test -n \"$(find .pi/STATUS.md -mmin -5)\"",timeoutMs:5000,allowFailure:false}
    ],
    review:{required:false},
    stopRules:[
      "Only necessary timeout seam, scoped tests/helpers/docs/evidence; protected WIP/lock/config/env/canonical frozen except already approved POSIX classifier.",
      "No old session/transcript/output history ingestion, broad environment reads, credential/auth/log modifications, uploads, sharing, network/install, model/global changes, staging or publication.",
      "No unsafe fork/thread injection/broad signalling, unguarded dangerous mutants, false timeout/bootstrap/setup/collection credit, suppressions or incomplete-score perfection.",
      "No automatic whole native13/14 or extra reviewers; parent owns the single independent review after readiness. Actual infrastructure/deadline failure stops the lane pending supervisor disposition."
    ]
  }
});
return {runId:closure.runId,ok:closure.ok,outputReference:closure.outputReference,outputPathMapping:closure.outputPathMapping,artifactPaths:closure.artifactPaths,output:closure.output};
