# Current Django bootstrap and one child/native binding

## Current source, runtime and collection

Fresh run UUID: `fd19e1db4f8f4f63862b6b0f7ae02f26`; durable raw root `.pi/django-bootstrap-current/fd19e1db4f8f4f63862b6b0f7ae02f26/`. Source root `/Users/mng/Projects/pytest-leela-recovery/timeout-20261029T162630Z`, HEAD `b1bb565883c75356be1a493c64084607d5769f3f`; 17/17 `.pi/RESTORED-SOURCE.sha256` identities and empty index retained. Runtime `/Users/mng/Projects/pytest-leela-recovery/runtime-20261029/env313/bin/python`: Python3.13.11, pytest9.1.1, Django6.1.2, pytest-django4.14.0, editable THIS source. Installed Django source confirms `apps.get_models(include_auto_created=False, include_swapped=False)` from `django/apps/registry.py`, `AdminSite._registry` is a dict, and `AdminSite.register(self, model_or_iterable, admin_class=None, **options)` in `django/contrib/admin/sites.py`; raw `django-interface.txt`. No changed Django API was guessed.

The exact recovered wrapper/guard identities remain `run_check.py` `7432a1eb…` and `resource_guard_poller.py` `917ad76e…`. Guarded collection-only command with private recovery HOME/XDG/TMP:

`env313/bin/python -m pytest tests/leela/describe_end_to_end.py --collect-only -q`

It exited0 in the existing 30-second wrapper cap and collected 15 nodes in 0.01s. The exact selected ID exists:

`tests/leela/describe_end_to_end.py::describe_django_project::it_imports_models_admin_and_references_once_per_process`

Raw collection stdout/resource/guard/exit are under the UUID root. The 30-second outer cap was retained from the independently tested helper and is over 25x the observed 1.18s nested ordinary test / 1.61s outer result below, not an increase or task-runtime backstop; native inner mutant budgets stayed the product's printed 5.0s.

## Existing import-retention contract — freshly executed

The selected test ran once, unmodified, with `-vv -s` and a durable explicit `--basetemp` under the UUID root. Outer pytest exited0; nested pytest exited cleanly; the existing assertion observed `Overall: 3/3 killed (100.0%)` and `result.ret == 0`. The three actual generated pricing candidates were Return→None, Add→Sub and Add→Mult; each printed killed, timeout=no, around 0.31–0.32s. This is the existing generated-project test outcome, **not** claimed as complete source/native CALL qualification.

At the point that existing test passed, `imports.log` had exactly twenty rows from four actual PIDs `24641`, `24649`, `24652`, `24656`. Each PID executed exactly once each of `shop.admin`, `shop.backoffice`, `shop.mixins`, `shop.models`, `stock.models`. The exact ordinary prefix is preserved at `ordinary-imports-prefix-20.log`; generated source, tox.ini, test, target and full ordinary output remain under `outer-basetemp/it_imports_models_admin_and_references_once_per_process0/` and `django-import-contract-*.log`. Subsequent explicitly separated native diagnostic appended ten rows from its clean/mutant PIDs; those are preserved as `native-diagnostic-appended-imports-10.log`, and the live 30-row log is not relabeled as ordinary-only. `.pi/django-bootstrap-current.json` records the four ordinary PIDs, modules, paths and generated-file SHA-256 inventory.

## Fresh affected child/native binding — one current canonical case

A disposable source-bound diagnostic derived the current DEFAULT native denominator directly from generated `shop/pricing.py`, rather than reusing old IDs91/92. Current denominator is three; current canonical Add→Sub is ID1 at line2/column11 in module `shop.pricing`, source SHA-256 recorded in `native-binding.json`. Selection was exactly `tests/test_shop.py::test_registry_and_identity_hold`; `test_times` 0.31 seconds yielded the product's budgeted isolated route.

Clean control: child PID24735, one call report, outcome passed, no runner error/failures. Affected run: job/invocation `8e31acb7a9c540419550566913d2ec0e-mutant`, child PID24737. Native `MutatingLoader` emitted a completed `loader-end` for exact module/file/source/mutant with `applied=true`, `ast_changed=true`; one executed module frame's code SHA equals the independently compiled expected-mutated module SHA. The same invocation and PID emitted `phase=call`, `outcome=failed`, node ID equal to parent `MutantResult.killing_tests`. Parent result was killed, error null, timeout absent, tests_run1. Thus one fresh child native AST/module/source/job is joined to the real failed CALL without replaying subject code or inferring an edge.

Raw proof: UUID-root `native-binding.json`, `native-events-8e31acb7a9c540419550566913d2ec0e.jsonl`, `native-binding-*.{log,resources.log,guard.log,exit}`. Observer source `.pi/recovered-helpers/passive_timeout_observer.py` SHA `9facc5f2…`; it wraps the actual loader and observes the one native execution, not a second subject replay. The generated project's later ten import rows are explicitly classified diagnostic, not silently merged with the import-contract twenty.

## Executable artifact inventory and proportional controls

New `.pi/django-bootstrap-current/django_native_binding.py` SHA `7afc6e59f84b96faf66bbb1a02adfa65383cba19a0f10f2e30883faf5d05fead` and `diagnostic_controls.py` SHA `34077c1cc64f701b4896c966beed8ffbe8a4fd20910b11ab25985d3c8bec6d90` are disposable single-investigation evidence diagnostics, not reusable enforcement/product tooling. They preserve exact current source/job identities and are not adopted. Controls passed: both compile; positive artifact schema and native join; clean-PID and wrong-job nonjoin; exact source/mutant/applied/code-SHA requirements; failed CALL/nodeid join; a negative non-Django/missing-target project is rejected before activation; wrapper resource/cleanup gate exited0. `diagnostic-controls.txt` says PASS. No product/test source changed. Existing runtime-generated Django source remains executable code under the original strict task; this slice proves the existing ordinary contract and one current native case only, not all generated fixture strict gates.

## Honest boundary and next gap

Proved now: guarded current collection; exact existing import test with actual four-PID/twenty-execution contract; one fresh current source-bound child native Add→Sub case with clean control and genuine failed CALL. Unqualified: Return→None and Add→Mult from the ordinary summary are not individually joined here; broader historical generated DEFAULT330 (129 unqualified), generated ALL, remaining Django bootstrap native bindings, expanded statement 1861/1886, affected runner/loader substrates and three missing original T4 receipts. No full suite, mypy, build, native13/14 campaign or historical replay was run. Original feature remains incomplete.

Next bounded engineering step: use the same source/job/PID/native-schema method, with a fresh UUID and clean control, for one remaining current generated pricing candidate (Return→None first, because deleting construction/result paths is higher observer-risk), then apply the recovered schema to a genuinely Django-generated module candidate rather than claiming the pricing target closes all twenty bootstrap executions. Preserve relevant source/input hashes and do not union documentary historical scores.
