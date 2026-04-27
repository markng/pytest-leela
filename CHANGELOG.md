# Changelog

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
