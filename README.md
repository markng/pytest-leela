# pytest-leela

**Type-aware mutation testing for Python.**

[![PyPI version](https://img.shields.io/pypi/v/pytest-leela)](https://pypi.org/project/pytest-leela/)
[![Python versions](https://img.shields.io/pypi/pyversions/pytest-leela)](https://pypi.org/project/pytest-leela/)
[![License](https://img.shields.io/pypi/l/pytest-leela)](https://github.com/markng/pytest-leela/blob/main/LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/markng/pytest-leela/python-package.yml)](https://github.com/markng/pytest-leela/actions)

---

## What it does

pytest-leela runs mutation testing inside your existing pytest session. It injects AST mutations
via import hooks (no temp files), maps each mutation to only the tests that cover that line, and
uses type annotations to skip mutations that can't possibly fail your tests.

It's opinionated: we target latest Python, favour speed over configurability, and integrate
with pytest without separate config files or runners. If that fits your workflow, great.
MIT licensed — fork it if it doesn't.

---

## Install

```
pip install pytest-leela
```

---

## Quick Start

**Run mutation testing on your whole test suite:**

```bash
pytest --leela
```

When no `--target` is given, pytest-leela auto-discovers source files:

1. Looks for a `target/` or `src/` directory (in that order) and mutates all `.py` files inside it
2. If neither exists, falls back to the project root (skipping `.venv`, `build`, `dist`, `__pycache__`, `node_modules`, `.git`, and similar non-source directories)

Test files (`test_*.py`, `*_test.py`, `conftest.py`, `tests.py`) are always excluded from mutation.

Exclude patterns from `[tool.pytest-leela]` (see [Configuration](#configuration)) are applied
after target discovery, so you can auto-discover `src/` while still excluding specific paths.

**Target specific modules (pass `--target` multiple times):**

```bash
pytest --leela --target myapp/models.py --target myapp/views.py
```

**Only mutate lines changed vs a branch:**

```bash
pytest --leela --diff main
```

**Limit CPU cores:**

```bash
pytest --leela --max-cores 4
```

**Cap memory usage:**

```bash
pytest --leela --max-memory 4096
```

**Combine flags:**

```bash
pytest --leela --diff main --max-cores 4 --max-memory 4096
```

**Generate an interactive HTML report:**

```bash
pytest --leela --leela-html report.html
```

**Benchmark optimization layers:**

```bash
pytest --leela-benchmark
```

---

## Features

- **Type-aware mutation pruning** — uses type annotations to skip mutations that can't possibly
  trip your tests (e.g. won't swap `+` to `-` on a `str` operand)
- **Per-test coverage mapping** — each mutant runs only the tests that exercise its lines,
  not the whole suite
- **In-process execution via import hooks** — mutations are applied via `sys.meta_path`,
  without rewriting source files; freshness checks may refresh local bytecode caches
- **Git diff mode** — `--diff <ref>` limits mutations to lines changed since that ref
- **Framework-aware** — clears Django URL caches between mutants so view reloads work correctly
- **Resource limits** — `--max-cores N` caps parallelism; `--max-memory MB` guards memory
- **HTML report** — `--leela-html` generates an interactive single-file report with source viewer, survivor navigation, and test source overlay
- **CI exit codes** — exits non-zero when mutants survive or error, so CI pipelines fail on incomplete kill rates
- **Benchmark mode** — `--leela-benchmark` measures the speedup from each optimization layer

---

## Configuration

Configure pytest-leela in your `pyproject.toml` under `[tool.pytest-leela]`:

```toml
[tool.pytest-leela]
exclude = [
    "*/migrations/*",
    "manage.py",
    "*/conftest.py",
]
operators = [
    "arithmetic",
    "comparison",
    "boolean",
    "return",
]
fail_on_error = true
```

### `exclude`

A list of glob patterns. Files whose path (relative to the project root) matches any pattern
are excluded from mutation. Uses `fnmatch` matching — `*` matches within a single directory,
`*/migrations/*` matches any `migrations` directory at any depth.

Default: `[]` (nothing excluded).

### `operators`

A list of operator category names controlling which mutation types are applied.
Use `"all"` as a shorthand to enable every category.

Default: `["arithmetic", "comparison", "boolean", "unary", "return"]`

| Category | What it mutates | Default |
|---|---|---|
| `arithmetic` | `+` `-` `*` `/` `//` `%` `**` | on |
| `comparison` | `==` `!=` `<` `<=` `>` `>=` `is` `in` and negations | on |
| `boolean` | `and` / `or` | on |
| `unary` | unary `-` `+` `not` | on |
| `return` | return values (`True`/`False`, `None`, literals, expressions) | on |
| `bitwise` | `&` `\|` `^` `<<` `>>` | off |
| `augmented_assign` | `+=` `-=` `*=` etc. | off |
| `ternary` | `x if cond else y` branch swaps | off |
| `control_flow` | `break` / `continue` swaps | off |
| `exception` | `except` handler broadening, body-to-raise | off |

### `fail_on_error`

Whether errored mutants fail the session (non-zero exit). Default: `true`.

See [Mutant statuses](#mutant-statuses). Set it to `false` only when you accept that some
mutants go untested, for example while fixing a known collection problem.

---

## Mutant statuses

Each mutant ends in exactly one of three states:

| Status | Meaning | Counts toward the score |
|---|---|---|
| **killed** | A call-phase test failure or mutant timeout, or a setup/teardown/collection/import failure confirmed by a passing clean control and recurrence of the same failure under the mutant | yes |
| **survived** | Tests ran and all of them passed | yes |
| **error** | No trustworthy verdict: an inconclusive setup/teardown/collection/import failure, pytest crash, deliberate skip/exit, abnormal exit without a test failure, or zero tests run (for example everything skipped). An inconclusive error may have zero **or positive** call-phase `tests_run` | no |

The mutation score is `killed / (killed + survived)`. Errors are listed separately, with their
reason, in the terminal report, the HTML report and the JSON output, and they fail the session
unless `fail_on_error = false`. An error means leela could not tell whether your tests catch the
mutant, so it is reported as neither a kill nor a survival.

The initial clean baseline proves only that the selected tests passed before mutation.
A setup, teardown, collection or import failure without a call-phase failure is initially
inconclusive, even if earlier tests passed. Leela runs at most **one clean unmutated control**
through the same selection and, only if that passes, **one additional mutant confirmation**.
It counts a kill only when the actual phase, node, exception identity/message and meaningful
visible chained cause recur. A failed control, different failure, surviving confirmation or
insufficient exception evidence remains **error**, with the original and control/confirmation
diagnostics. Ambient or flaky failures therefore do not automatically kill unrelated mutants.
These are bounds on attempts, not a hard wall-clock execution limit or perfect proof against
all nondeterminism.

This check preserves genuine mutation-caused target imports and test-module collection
failures, including module-level assertions such as `assert calc.RATIO == 5`; the killing
nodes name the files that failed to collect. A conftest's own check failing **during
collection** can also kill after this confirmation. Before the session starts, a failed
conftest import is eligible when the mutated target itself raised during import; a conftest's
own check without such a target import failure remains an error. A conftest that catches an
error and calls `pytest.exit()` or `pytest.skip()` remains an error, not a confirmed kill.

If a test caught the import error and the suite completed green, the mutant **survived**: no
test noticed it. If the mutant made the tests skip themselves (a module-level `pytest.skip`, a
`skipif` marker, or a conftest or test module that skips or calls `pytest.exit()` at import),
the mutant is an **error**: no test ran.

Known limits of these rules:

- **A module that can only be imported once per process.** If a test module imports something
  that refuses to load twice (for example it raises when a process-global registry is already
  installed), every re-import fails to collect. Through `pytest --leela` the outer session has
  already imported it, so the clean baseline fails and the run aborts. When `Engine.run` is called
  directly with `use_coverage=False` and a test directory, the baseline may be the first import
  and pass. Later re-import failures also fail the clean per-mutant control: those mutants
  are inconclusive **errors**, commonly with `tests_run: 0`, excluded from the score. Leela
  does not make non-idempotent registration safe to repeat in-process.
- **A deliberate exit hides every kill not yet reported.** When a conftest or a test module
  catches the target's import error and calls `pytest.exit()`, the inner run ends there and the
  mutant is an **error**. Tests run only after collection, so no test failure is ever reported,
  including a test that imports the target inside its body; nor is a sibling module's collection
  failure that pytest would have reached after the exit. A module that already failed to
  collect before the exit stops the run first (`-x`) can still kill after same-failure
  confirmation.

### Clean baseline before any mutant

Before the first mutant, leela runs the selected tests once with no mutation applied, through
the same in-process path every mutant uses. If anything fails or errors there, the run aborts
with `leela: aborted: tests do not pass with no mutation applied (...)` and a non-zero exit,
and no mutants are scored. Without this check, a test that fails for an unrelated reason (a
fixture that cannot run in-process, a missing service) would score every mutant as killed.

To make that baseline execute current source, engine entry and baseline preparation may remove
and recreate local CPython and pytest bytecode caches. Source file contents and mtimes are not
changed. Mutation preparation is narrower: it removes only the exact cached artifacts for the
selected test inputs, never same-prefix sibling modules. A warm indexed run still executes the
real baseline, but cache-hit mutants do not execute the mutant runner or mutation-preparation
step. This phase-local boundary is intentional; preserving every project `.pyc` across a whole
engine run is not part of the current experimental watcher contract.

### pytest-django

pytest-django blocks database access for the whole session and only restores it at
unconfigure, after leela has run. Leela lifts that outer block for the duration of its run
(through pytest-django's own `django_db_blocker.unblock()`), so database tests in the inner
sessions can open connections as they do in a normal run.

Django model and admin modules are never reloaded between mutants (unless one is itself a
`--target`), and neither is any project module they reference. Re-executing them registers
models and admin classes a second time, which Django rejects, and re-importing a module they
reference would split class identity (a kept model subclassing the old copy of a reloaded
mixin). The trade-off: if a model or admin module imports a function from your target at
module level, calls through that reference run the unmutated code, which shows up as a
survivor, never as a fake kill.

### Installed packages are never reloaded

Between mutants leela evicts the project's own modules so that mutated code is re-imported.
Installed packages are left loaded, even when the virtualenv sits inside the project directory
(uv's default `.venv`): anything under `sys.prefix`, `sys.base_prefix`, `site.getsitepackages()`,
`site.getusersitepackages()`, or a `site-packages` / `dist-packages` directory. Re-importing them
would break C extensions such as numpy, which CPython can load only once per process.

---

## Pragma Skip

Add `# leela: skip` to suppress mutations on individual lines:

```python
result = x + y  # leela: skip
```

To skip an entire file, place the pragma on line 1:

```python
# leela: skip
"""This module is excluded from mutation testing."""

def legacy_code():
    ...
```

The pragma is detected via Python's `tokenize` module, so it only matches actual comments —
not string literals or docstrings that happen to contain the text.

---

## HTML Report

`--leela-html report.html` generates a single self-contained HTML file with no external dependencies.

**What it shows:**
- Overall mutation score badge
- Per-file breakdown with kill/survive/error counts
- Source code viewer with syntax highlighting

**Interactive features:**
- Click any line to see mutant details (original → mutated code, status, relevant tests)
- Survivor navigation overlay — keyboard shortcuts: `n` next survivor, `p` previous, `l` list all, `Esc` close
- Test source overlay — click any test name to see its source code

Uses the Catppuccin Mocha dark theme.

---

## Experimental: Watcher / Discovery

> **Status: experimental.** The watcher subsystem is under active
> development and is not yet part of the stable public API. It is
> invoked via ``python -m``, not via a console script, and may
> change without notice.

The watcher tracks source and test changes in a SQLite index. This experimental
path is separate from `pytest --leela`; cache correctness and progress depend on
the matching engine/index implementation.

### Usage and interpreter selection

Run from the project root, using its **development environment**, with pytest-leela and that
project's configured test dependencies installed. For this repository:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest_leela.daemon --help
.venv/bin/python -m pytest_leela.daemon watch /path/to/project
.venv/bin/python -m pytest_leela.daemon status /path/to/project --json
```

The experimental mutation engine also uses the current working directory for
relative test IDs: run from the watched project root, not an unrelated checkout.

Coverage subprocesses use `sys.executable` (the invoking interpreter), with the
project root as cwd and normal pytest plugin discovery. There is **no automatic
venv selection** and no runtime installation of dependencies. `describe_*` suites
require pytest-describe in that environment with plugin autoload enabled. Missing
plugins, import failures, and failed tests are reported as collection failures,
not successful zero coverage. A genuinely empty test suite returns empty coverage;
passing tests that touch no tracked source return timings but no source lines.
The coverage subprocess has a 120-second timeout. It disables mutation plugin
recursion and project `addopts`, while retaining discovery/configuration settings.

Bare invocation defaults to watching the current directory. Use explicit `watch`
and a project path to avoid accidentally watching the wrong directory. `watch
--verbose` prints progress; `watch --observer` reports outstanding test work.

### Subcommands

* ``watch`` (default) — run the long-lived daemon. Polls the
  filesystem at ``--poll-interval`` seconds (default: 1.0) and
  re-analyzes dirty symbols after a debounce period
  (``--reanalyze-debounce``, default: 2.0 s). Use Ctrl-C to stop.
* ``status`` — read the index DB and print the current state
  without running any analysis. Exit code 0 means everything is
  covered; 1 means there is work to do; 2 means an error (e.g.,
  no index DB yet). Useful for CI status checks.

### Source discovery

The daemon discovers source files by walking the configured
``target_dirs`` (default: ``src/``). It skips directories that
typically contain non-source code:
``.venv``, ``venv``, ``.tox``, ``build``, ``dist``, ``.git``,
``.hg``, ``__pycache__``, ``node_modules``, ``.eggs``, ``target``,
``tests``, and ``.leela``.

### Known limitations

* **Experimental only.** No ``[project.scripts]`` entry point is
  declared — the daemon is reached via
  ``python -m pytest_leela.daemon``. It is not installed as a
  console script.
* **Polling-based.** The daemon uses ``os.path.getmtime`` polling
  rather than inotify/FSEvents, so it has a configurable poll
  interval but not instant reaction.
* **Persistent state.** The index DB (``.leela/index.db``) and
  logs are written under the project root. Add ``.leela/`` to
  your ``.gitignore``.
* **Shutdown drains work.** Ctrl-C stops polling and scheduling, then waits for
  the current analysis pass before closing its DB. Engine has no cancellation
  API; user tests can hang, so shutdown is not globally time-bounded. Use an
  external process timeout where a hard deadline is required.

---

## Requirements

- Python >= 3.12
- pytest >= 7.0

---

## License

MIT
