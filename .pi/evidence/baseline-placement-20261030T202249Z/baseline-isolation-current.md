# Fresh bounded baseline isolation — proven diagnostic placement cause

## Scope and result

Only `tests/leela/describe_end_to_end.py::describe_clean_project::it_kills_every_mutant_and_passes` was exercised using Python 3.13.11 / pytest 9.1.1 / Django 6.1.2 from the approved `env313`. No production, test, config, lock, index, CI-test, environment-package, or preserved source bytes were changed.

**Proven:** putting pytester's generated basetemp below repository root `R` causes pytest 9.1.1 to search upward and adopt `R/pyproject.toml` with `rootpath=R`. The outer coverage selector is then normalized as an `R`-relative node ID beginning `.pi/...`. The timeout worker keeps the generated-project cwd but receives that selector; its inner `pytest.main()` collects zero items and exits `USAGE_ERROR` (4). This is a diagnostic artifact-placement failure, not a product defect.

**Matched external placement:** with the same source, env, node, trace plugin, and guarded protocol, but basetemp at `/Users/mng/Projects/pytest-leela-recovery/runtime-20261029/fresh-baseline-20261030T201541Z/basetemp`, the nested project had no inherited config, `rootpath=cwd=<generated project>`, collected `test_calc.py::test_double`, passed baseline CALL, and completed 3/3 genuine CALL-detected mutants. Outer result: `1 passed`, exit 0.

**Matched under-R control:** basetemp at `R/.pi/baseline-isolation/fresh-baseline-20261030T201541Z/under-r-basetemp` reproduced the earlier failure. Nested root/config were `R` / `R/pyproject.toml`. Worker argument was `.pi/baseline-isolation/.../test_calc.py::test_double`; worker collection was empty, exit 4, and Leela aborted the baseline. Outer node failed, exit 1.

Prescription: keep generated pytester basetemp and diagnostic runtime output outside `R`; bridge only raw evidence/manifests into `.pi`. Do not add `--rootdir`, toy config, or product/test changes to mask this evidence-placement cause.

## Exact protocol evidence

- External raw log: `.pi/baseline-isolation/fresh-baseline-20261030T201541Z/outer.log`
- External protocol JSON: `.pi/baseline-isolation/fresh-baseline-20261030T201541Z/protocol/`
- Matched under-R raw log: `.pi/baseline-isolation/fresh-baseline-20261030T201541Z/original-placement.log`
- Matched under-R protocol JSON: `.pi/baseline-isolation/fresh-baseline-20261030T201541Z/protocol-original-placement/`
- Commands/cwd/basetemp: `command.txt`, `original-placement-command.txt`
- Guard/resource logs: `resources.log`, `guard.log`, `original-placement-resources.log`, `original-placement-guard.log`
- Raw identity manifest: `raw.sha256`
- Structured complete summary: `.pi/baseline-isolation-current.json`

External nested baseline protocol: cwd/root/item path are the external generated project, node ID `test_calc.py::test_double`, setup/call/teardown all passed, exit 0. Under-R worker protocol: cwd is the generated project, root is `R`, config is `R/pyproject.toml`, passed selector is `.pi/.../test_calc.py::test_double`, collection `[]`, exit 4.

## Containment and diagnostic artifact classification

The runs used unchanged `.pi/evidence/timeout-enforcement-fix/run_check.py` SHA-256 `7432a1eb…` and `resource_guard_poller.py` SHA-256 `917ad76e…`, with 120-second command cap, 512000 KiB growth cap, and minimum 1 GiB free. No STOP occurred and no owned process remained.

The trace plugin is a disposable single-investigation diagnostic, SHA-256 `ed4d031a…`. It compiled and observed supported pytest hooks in positive and negative matched controls. It records only root/cwd/config/args/item path/node ID/report phase/outcome/exit. It is not product or reusable tooling.

Retained failed attempt: using identical `run_check.py` bytes copied to `.pi/recovered-helpers` failed before the command because its location-sensitive `parents[2]` resolved one directory above `R`, so its guard source path did not exist. The failure is preserved in the run's initial `guard.log`; retry used the identical helper at its verified original location.

## Honest remaining work

This bounded stage does not complete T2 or the original task. Generated/native/expanded-statement/substrate domains, ordinary gates, Python 3.12/3.14 validation, review, and three lost T4 receipts remain. The prior 34 producer CALL jobs were not rerun.
