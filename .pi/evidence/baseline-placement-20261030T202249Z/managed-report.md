# Fresh baseline isolation recovery

## Result

**PROVEN:** the selected baseline failure was caused by placing pytester's generated basetemp beneath repository `R`, not by a product defect.

For `tests/leela/describe_end_to_end.py::describe_clean_project::it_kills_every_mutant_and_passes`, the guarded external-placement run used exact env313 and a minimal supported pytest protocol trace. Nested cwd/root/item were the generated project, selector was `test_calc.py::test_double`, setup/call/teardown passed, and the outer test passed with 3/3 genuine CALL-detected mutants.

The same traced node with basetemp under `R/.pi` reproduced the failure: pytest 9.1.1 searched upward, selected `R/pyproject.toml` and `rootpath=R`; normalization supplied `.pi/.../test_calc.py::test_double` to an isolated worker whose cwd was the generated project. Worker collection was empty and exit status was `USAGE_ERROR` (4), so baseline aborted and the outer node failed.

Prescription: put future generated pytester basetemp/runtime diagnostic output outside `R` and bridge raw evidence/manifests back into `.pi`. Do not add toy config, `--rootdir`, or product/test changes to mask this placement failure.

## Evidence

- Summary: `.pi/baseline-isolation-current.md`
- Structured evidence: `.pi/baseline-isolation-current.json`
- Source identities: `.pi/baseline-isolation-source.sha256`
- Raw run: `.pi/baseline-isolation/fresh-baseline-20261030T201541Z/`
- External raw log/protocol: `outer.log`, `protocol/`
- Under-R matched control: `original-placement.log`, `protocol-original-placement/`
- Raw hashes: `raw.sha256`
- Current state: `.pi/STATUS.md`

Changed files are evidence/STATUS only; no production, tests, config, lock, index, CI test, or environment packages changed. No tests were added or updated. Index is empty and source manifests pass.

An initial failed attempt is preserved: identical `run_check.py` bytes at `.pi/recovered-helpers` resolved its location-sensitive relative root incorrectly and could not locate the guard. The successful run used the identical SHA at its verified original location `.pi/evidence/timeout-enforcement-fix/run_check.py`.

Remaining original T2/generated/native/expanded/substrate work, broad gates, 3.12/3.14, review, and lost T4 receipts are unchanged. Prior 34 producer CALL jobs were not rerun.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "B1-bounded-baseline-cause",
      "status": "satisfied",
      "evidence": "One guarded traced external-basetemp run passed and one same-node/same-env/same-trace under-R matched control reproduced exit 4. Protocol JSON proves exact cwd/root/config/item.path/nodeid/phase mapping and the placement cause."
    },
    {
      "id": "B2-preserved-source-and-required-report",
      "status": "satisfied",
      "evidence": "All formatter17 and baseline source identities pass; HEAD is b1bb565, index empty, config/index/CI identities preserved; current MD/JSON/source manifest/STATUS/raw evidence and this routed report exist. Original unfinished T2/gates/review/T4 are explicitly retained."
    }
  ],
  "changedFiles": [
    ".pi/STATUS.md",
    ".pi/baseline-isolation-current.md",
    ".pi/baseline-isolation-current.json",
    ".pi/baseline-isolation-source.sha256",
    ".pi/baseline-isolation/fresh-baseline-20261030T201541Z/**",
    "/Users/mng/.pi/agent/sessions/--Users-mng-Projects-pytest-leela--/subagent-artifacts/outputs/44fdcd94-649d-4102-8c38-27209bbd0598/responsive-watch/fresh-baseline-isolation-recovery.md"
  ],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "env313 .pi/evidence/timeout-enforcement-fix/run_check.py ... pytest selected-node --basetemp=<external runtime>/basetemp",
      "result": "passed",
      "summary": "Guarded selected node passed; nested baseline setup/call/teardown passed and 3/3 mutants were CALL-detected."
    },
    {
      "command": "env313 .pi/evidence/timeout-enforcement-fix/run_check.py ... pytest selected-node --basetemp=R/.pi/.../under-r-basetemp",
      "result": "failed",
      "summary": "Expected matched failure control: worker collected zero items and exited USAGE_ERROR 4; outer node failed."
    },
    {
      "command": "source-preserved verification: HEAD/index/manifests/git diff --check",
      "result": "passed",
      "summary": "HEAD exact, no staged files, current formatter17 and baseline manifests pass, diff check clean."
    },
    {
      "command": "initial .pi/recovered-helpers/run_check.py invocation",
      "result": "failed",
      "summary": "Retained infrastructure attempt: location-sensitive helper resolved guard source above R; identical verified helper bytes at original location were then used successfully."
    }
  ],
  "validationOutput": [
    "External placement: rootpath/cwd generated project, inipath null, item test_calc.py::test_double, baseline CALL passed, outer exit 0.",
    "Under-R placement: rootpath R, inipath R/pyproject.toml, worker selector .pi/.../test_calc.py::test_double, collection empty, exit 4, outer exit 1.",
    "No guard STOP and no owned PIDs remained.",
    "All17 source/index/config/env history preserved; no staged files."
  ],
  "residualRisks": [
    "Original T2 generated/native/expanded/substrate quality remains unfinished.",
    "Broad ordinary gates, Python 3.12/3.14, independent review, and three lost T4 receipts remain.",
    "Prior 34 producer CALL jobs were not rerun."
  ],
  "noStagedFiles": true,
  "diffSummary": "Evidence-only bounded diagnosis; no source/test/config/lock changes.",
  "reviewFindings": [
    "no blocker for bounded B1/B2; whole original task remains incomplete"
  ],
  "manualNotes": "STATUS now records the proven placement cause and exact remaining obligations."
}
```
