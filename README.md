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

## Requirements

- Python >= 3.12
- pytest >= 7.0

---

## License

MIT
