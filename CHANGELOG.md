# Changelog

## Unreleased

### Fixed

- **Installed packages in a virtualenv inside the project are no longer
  evicted between mutants.** Leela evicted every module whose `__file__`
  sat under the cwd (and, before a run, everything under the temp dir).
  With uv's default `.venv` inside the project that included third-party
  packages, so C extensions such as numpy failed to re-import with
  `ImportError: cannot load module more than once per process`. That
  crash was scored as a **kill**, so every mutant reported killed,
  including mutants no test caught. Eviction now skips anything under
  `sys.prefix`, `sys.base_prefix`, `sys.exec_prefix`,
  `site.getsitepackages()`, `site.getusersitepackages()` or a
  `site-packages` / `dist-packages` directory, while still evicting the
  project's own modules so mutated code reloads.

  **Scores reported by earlier versions may be inflated** for projects
  whose virtualenv lives inside the project directory. Re-run to get an
  honest number.

- **Database tests under pytest-django can run inside leela's inner
  sessions.** pytest-django blocks DB access at configure time and only
  restores it at unconfigure, which is after leela runs. Each inner
  session's `DjangoDbBlocker` recorded that outer blocking wrapper as the
  "real" `ensure_connection`, so `django_db_blocker.unblock()` could
  never reach the database: every DB test errored in setup, and each
  setup error was scored as a **kill**, including for mutants no test
  could detect. Leela now lifts the outer block (via pytest-django's own
  `unblock()`) while it runs. Scores from earlier versions on
  pytest-django projects with DB tests may be inflated.

- **Django model and admin modules are no longer reloaded between
  mutants.** Re-executing them re-registered models ("Reloading models is
  not advised") and admin classes (`AlreadyRegistered`), and re-entered
  import cycles that only resolve in Django's app-loading order. Any
  project module they reference is kept too, so a kept model never ends
  up subclassing a stale copy of a reloaded mixin.

- **The summary no longer reports a negative "pruned by type analysis"
  count.** A typed rule can add operators the untyped rule lacks (int
  `+` gains `//`), and `count_pruned` subtracted list lengths, so such
  points counted as negative pruning ("-1 pruned"). It now counts only
  untyped mutations the typed rule dropped.

### Added

- **Clean baseline before any mutant.** The selected tests run once with
  no mutation applied, through the same in-process path as every mutant.
  If anything fails or errors, the run aborts with a message naming the
  failing tests and a non-zero exit, instead of scoring every mutant as
  killed.

- **A third mutant status, `error`.** A mutant whose inner run crashed,
  exited abnormally (usage error, nothing collected, interrupted) with no
  test failure or collection failure, or ran zero tests, was previously
  reported as killed (crash) or **SURVIVED** (`tests_run: 0`). It is now
  an error: reported with its reason in the terminal, HTML and JSON
  reports, excluded from the mutation score, and failing the session by
  default. `MutantResult` gains `error` and a `status` of `"killed"`,
  `"survived"` or `"error"`; `RunResult` gains `errors` and
  `mutants_scored`. Timeouts remain kills.

- **A test module that fails to collect under the mutant is a kill.**
  The clean baseline shows it collects unmutated, so the failure is the
  mutant's doing: the target raising at import, or a test module's own
  module-level check failing. Those modules are the killing tests.
  `MutatingLoader` records exceptions raised by the mutated source, and a
  conftest that fails to import because of one is a kill too. A
  conftest's own failing check, or a conftest that calls `pytest.exit()`
  at import, is an `error`: it is not attributed to the mutant. A run that
  stays green or empty is not a kill: an import caught by the test is
  SURVIVED, and a module-level skip, a skip marker or a conftest that
  skips is an `error`, because no test ran. A conftest raising `Skipped`
  at import no longer crashes the whole leela run: `Skipped` is a
  `BaseException`, outside what inner runs caught.
  0.8.0's verdict for a mutant that broke the target's import depended
  on the layout. With the virtualenv and the temp dir outside the
  project, it was SURVIVED with `tests_run: 0`. With the virtualenv
  inside the project, re-importing installed packages could crash the
  inner run, and the mutant was KILLED as `<crashed>`, a verdict
  indistinguishable from leela failing to test it.

- **`--leela-benchmark` no longer raises `TypeError`.** It called the
  target-discovery helpers without the `python_files` patterns they
  require.

- **The `django` extra requires `pytest-django>=4.10`.** 4.10 is the
  first release whose `pytest_unconfigure` restores the session's
  database block, which leela's nested sessions depend on.

- **`fail_on_error` option** in `[tool.pytest-leela]` (default `true`).
  Set it to `false` to keep errored mutants from failing the session.

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
