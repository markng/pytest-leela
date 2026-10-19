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
- **In-process execution via import hooks** — mutations applied via `sys.meta_path`, zero
  filesystem writes, fast loop
- **Git diff mode** — `--diff <ref>` limits mutations to lines changed since that ref
- **Framework-aware** — clears Django URL caches between mutants so view reloads work correctly
- **Resource limits** — `--max-cores N` caps parallelism; `--max-memory MB` guards memory
- **HTML report** — `--leela-html` generates an interactive single-file report with source viewer, survivor navigation, and test source overlay
- **CI exit codes** — exits non-zero when mutants survive, so CI pipelines fail on incomplete kill rates
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
- Per-file breakdown with kill/survive/timeout counts
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
