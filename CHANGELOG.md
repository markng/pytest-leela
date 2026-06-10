# Changelog

## 0.8.0 — 2026-06-10

### Added

- **Working-tree diff support in `--diff` mode.** `git diff base...HEAD`
  (three-dot) only spans committed history, so `--diff HEAD` against
  uncommitted edits previously produced an empty diff, 0 mutants tested,
  and a silent exit 0 — a hollow quality gate. `changed_files` and
  `changed_lines` now take the **union** of the committed range
  (`base...HEAD`) and the working-tree/index diff (`base`, two-dot), so
  `--diff HEAD` captures staged and unstaged edits, and `--diff main`
  continues to capture all commits since main.

- **Zero-mutant warning when `--diff` is active.** `RunResult` gains a
  `diff_base` field. When `--diff` is active and `mutants_tested == 0`,
  `format_terminal_report` emits a prominent `WARNING` so operators know
  the gate produced no signal rather than silently passing. (Zero mutants
  on a full non-diff run is still silent — that is expected for empty
  codebases.)

- **Monorepo repo-root path normalization.** Git reports file paths
  relative to the repository root, but pytest's cwd may be a
  subdirectory. A new `_get_repo_root()` helper resolves git-reported
  paths against the real repo root rather than cwd, preventing
  double-subdir corruption (e.g. `services/api/services/api/x.py`).
  `_parse_diff_hunks` gains an optional `repo_root` parameter; callers
  that pass diff text directly continue to work with the cwd-relative
  fallback.

- **Mutation-hardening tests for `git_diff`.** All 41 mutations in
  `git_diff.py` are now killed (100%), up from 32/42 (76.2%) before
  this release. New test scenarios cover the union-branch paths,
  `_get_repo_root` success/failure pins, and the zero-mutant warning
  firing condition.

## 0.7.1 — 2026-04-27

### Fixed

- **`MutatingLoader` now populates `__file__`, `__loader__`, and `__spec__`
  before executing mutated source.** Previously, any target module that
  referenced `__file__` at module scope (e.g. `BASE_DIR = Path(__file__).resolve().parent`
  in Django settings, asset path lookups via `Path(__file__).parent / 'static' / ...`,
  or `pkg_resources.resource_filename(__name__, ...)`) raised
  `NameError: name '__file__' is not defined` during the mutated import.
  The harness counted that import failure as "no test killed this mutant"
  and reported a false-positive **SURVIVED**, even when the existing tests
  would have caught the mutation. The loader now mirrors the attribute
  population that `importlib._bootstrap_external.SourceFileLoader` performs
  via `_init_module_attrs`. Reported and root-caused in
  pith-task `0b048dd4-ac87-4e22-9c8b-789ae2b1bebb`.

  Mutants that were silently absorbed as SURVIVED on `__file__`-using
  modules will now flip to KILLED — the tests were always correct; only
  the harness was reporting wrongly. Downstream projects that introduced
  workarounds to dodge this bug (e.g. extracting `__file__`-touching
  code into helper modules, or routing asset lookups through framework
  finders solely to avoid the false positives) can revert those
  workarounds in a follow-up — they are no longer load-bearing for a
  green leela run.
