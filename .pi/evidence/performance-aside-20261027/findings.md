# Read-only Numba / AST performance aside

Owner asked whether Numba might accelerate Leela, specifically its tree work. This was bounded source inspection + analysis of ALREADY saved native timing data, not authorisation to introduce a dependency, rewrite AST representation or run another campaign. Current eleven-gap writer/review remains untouched; no R writes/tests/profiling load added.

## Proven source facts
- src/pytest_leela/import_hook.py::MutatingLoader.exec_module lines289–293: each mutated module load ast.parse(source), MutantApplier.visit(tree), ast.fix_missing_locations(tree), compile(tree). MutantApplier is a Python ast.NodeTransformer traversing Python AST objects/attributes/lists; not a flat numeric kernel. No parse-tree cache in that path.
- runner.py::InnerSession.run times module eviction + pytest.main + cleanup; pytest collection, plugins, fixtures, arbitrary user tests AND import-time AST work are inside the reported interval.
- engine.py::Engine.run iterates mutants serially; existing coverage-based selection/precomputed modules/prepared dependencies exist. plugin.py constructs Engine without an index; persistent result-cache logic is conditional on self.index. No blanket claim that no caching exists.
- benchmark.py::BenchmarkPlugin runs four complete configurations; it is optimisation-ablation testing, not cheap phase profiling. It was NOT run for this aside.

## Existing-data timing aggregation (not a CPU profile)
Input .pi/evidence/current-ci-selfcheck/attempt2/native.stdout, historical Oct24 interrupted run, SHA6d888b1fba37e7a34129d242394ed7bf92bcc77ff12cf103a0f5e8689baa8c77. Disposable inline stdlib Python/re regex matched ONLY completed native result lines with elapsed seconds. Correctness checks:1266unique ordinals exactly1..1266, every denominator1326, nonnegative durations; corroborated existing native phase report. No new standalone executable/tool authored, no outcome/score mapper or kill credit. Native tool/session stores exact inline source.

Reported mutant-session times sum10096.65s versus10510s whole-attempt wall (~96.07%); median0.22s. Timeout-flagged14 result lines report571.67s, not complete timeout/control costs and never CALL-kill credit. Top mutated TARGET modules by summed reported sessions: type_extractor4080.52s, runner2156.79s, engine996.89s, import_hook971.69s, index556.66s, models468.59s. These are target attribution, NOT function-CPU attribution. Returned result timing can omit extra control/confirmation sessions. Initial/partial final work also not exhaustively decomposed; no full-run score or totals inferred.

Crucial limitation:96% inside sessions does NOT prove96% in test bodies, or that AST work is unimportant. AST/import/pytest collection/setup/cleanup all occur there, and self-mutation tests themselves exercise Leela/mypy. This workload may not represent another application.

## Answer / next investigation (not implementation)
Numba can accelerate typed tree/graph algorithms; it is not a drop-in accelerator for CPython ast.AST/NodeTransformer. A Numba-friendly typed representation (often flat node-kind/parent/child/position integer arrays) would require adaptation and conversion/compilation overhead. First measure parse vs transform/walk vs fix-locations vs compile vs collection/setup/tests/eviction on a small representative cold/warm workload. Compare avoiding repeated parsing/full traversal/compilation and indexing node paths against the current approach, with mutation isolation correctness intact; caching/deep-copy may or may not beat C parsing. Only consider JIT after an actual substantial suitable kernel is identified. No measured Numba speedup, no installation/new dependency, no new performance implementation scope claimed.
