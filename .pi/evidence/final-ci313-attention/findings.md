# Current CI13 runaway candidate — bounded parent diagnosis / containment

On owner's contextual 'what next?' question, parent checked existing native output/process rather than treating model inactivity alone as a stall.

Proven: current command collected1662 and enumerated1324. Ordinal470/1324 targets engine.py:415 Compare:Is,135selected tests, displayed timeout286.7s. Previous469completed. SAME candidate heartbeat persisted to elapsed3011s (>50min); nativePID50136 was alive at96.3%CPU before intervention. Root .pi/evidence/final-current-ci/py313/native.stdout lines1046ff bind identity/heartbeat. CPU liveness therefore did not establish candidate progress.

Proven source limitation: runner.py::_TimeoutPlugin.pytest_runtest_protocol (330–338) checks an event only at test-protocol entry. InnerSession.run lines657–675 starts threading.Timer(timeout_seconds, self.timed_out.set), then in-process pytest.main. Timer sets a flag, not an interruption of a nonreturning test/collection. No signal-alarm diagnosis was established or asserted. Exact stuck Python frame/test, and why this witness reaches it, UNKNOWN.

Bounded readonly native /usr/bin/sample50136 1second/10ms succeeded; file native-stall-sample.txt has C interpreter frames, NOT Python-level function profiling. Not evidence that AST/typechecking consumes the walltime. Pre-intervention raw/resource/run-script snapshots and SHA saved in this directory; active original output/evidence remains child-owned and not rewritten.

Original stop-on-quality/deadline authority applied to owned invocation only. Child steer queued e535cc8a-31b4-46e2-b7c0-0d32db07ad70: preserve actual outcome, no rerun/14/fix, finish honest F1/F2/F3. Queue receipt not compliance proof. Parent reverified exact native command/path before single SIGINT50136. Process remained after SIGINT at0%CPU with no child PIDs observed; reverified identity and escalated only PID50136 to SIGTERM. containment.txt records scope; final native/pipeline/guard exits remain to be read from child. Do not infer terminal cleanup from signal-send success.

Disposition: this full current13 attempt INTERRUPTED/INCOMPLETE, never a timeout/CALLkill, final score or passingCI. Native14 remains deferred. All prior reviewed source/test proof/ordinary suites are untouched; no product/test/config/dependency writes by parent. Monitor was ACTUALLY28800s rather than planned~21300s, tool21600s; neither rescued per-mutant286.7s bound. Record actual values.

Recommended next scope: fix and verify non-cooperative per-mutant timeout enforcement first, preserving correct phase/error semantics and child cleanup; then resume required full gates and profile genuine slow tail. This could materially expand execution architecture beyond the reviewed eleven gaps, so obtain owner scope approval before implementing it. No automatic approval inferred from 'what next?'.
