# CURRENT — bounded baseline isolation complete; original R NOT COMPLETE

Authority/scope: owner-approved fresh retry isolated only `tests/leela/describe_end_to_end.py::describe_clean_project::it_kills_every_mutant_and_passes`; no production/test/config/lock/env-package changes authorized or made.

Result: **PROVEN diagnostic placement cause, not product defect.** Guarded exact env313 + trace with external basetemp `/Users/mng/Projects/pytest-leela-recovery/runtime-20261029/fresh-baseline-20261030T201541Z/basetemp` passed (outer 1 passed; nested baseline CALL setup/call/teardown passed; 3/3 mutants CALL-failed). Matched guarded basetemp under `R/.pi/...` failed: pytest 9.1.1 adopted `R/pyproject.toml` and root `R`; normalized selector became `.pi/.../test_calc.py::test_double` while worker cwd remained generated project; collection empty, exit `USAGE_ERROR` 4, outer exit 1.

Writes: `.pi/baseline-isolation-current.md/json`, `.pi/baseline-isolation-source.sha256`, and raw `.pi/baseline-isolation/fresh-baseline-20261030T201541Z/**`; external generated project under approved runtime root. Disposable trace plugin SHA `ed4d031a…`; unchanged guard SHAs `7432a1eb…` / `917ad76e…`. Initial location-sensitive helper-path failure retained; successful retry used identical helper bytes at verified original location.

Latest validation: HEAD `b1bb565…`, index empty, current formatter17 and baseline source manifests PASS, `git diff --check` PASS, reports nonempty, no guard STOP/no owned processes. Prescription: future diagnostic pytester basetemp outside R and bridge evidence back; no root/config flags or source fix.

Remaining: whole original T2 generated/native/expanded/substrate domains, broad gates, Python 3.12/3.14, review, and three lost T4 receipts remain. Prior 34 producer CALL jobs were not rerun. Next step belongs to parent scheduling; this bounded criterion is complete.
