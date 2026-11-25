# Full mutation CI fan-out

The public plugin and default `Engine.run` remain sequential. CI now prepares
one complete native manifest per Python leg, executes two independent hosted
shards, then retains the existing `build (3.13)` / `build (3.14)` final check
names. `lockfile`, `pytest-floor` and the release publisher are not changed.

## Contract

`python -m ci.mutations plan` calls the real native `Engine.prepare`: ordinary
selection, typed default operators, full target inventory, coverage-selected
node IDs, original timeout inputs and a proven unmutated baseline. It does not
invent killed rows. An independently regenerated complete catalog is checked
again on workers and aggregation, so rebinding a truncated manifest cannot
turn partial coverage into success. Unknown/new sites use an explicit per-file
prior median for LPT weights; an explicit retained target-file mean is the
provisional scheduling fallback while Mini's raw site data is unreachable.
Those are scheduling estimates, NOT inferred per-ID measurements. Identity
aliases in old text logs affect weights only, never merge native mutant IDs.

Each shard has its own fresh Ubuntu runner/source checkout/import state/files
and DB fixtures. There is no xdist or nested mutation parallelism. All rows,
including errors and native timeouts, are retained. Raw native test phase IDs
may repeat a selected node (call plus teardown); they are not duplicate mutants.
Native control/confirmation behavior is preserved without invasive observers;
separate control/confirmation counts remain explicitly unknown, not zero. The CI gate rejects any
survivor, error or timeout, missing/duplicate artifact or ID, wrong source/run/
attempt/Python/runtime, unproven baseline, invalid timing or test-selection
drift. No default-library classification is changed. Incomplete jobs never
publish a complete result; upload absence and the final always-run aggregate
fail closed. Partial `rerun failed jobs` cannot mix old-attempt plans/results;
use a fresh complete workflow attempt.

Artifact names are scoped to Python and current run attempt; downloads use only
the current run. Dependency pins are reconstructed from a validated plan,
not executed from an unchecked artifact requirements file. Runtime evidence
includes Python build, GIL state, image build, exact package versions/code
fingerprint and candidate library bytes. Normal quality checks still run.

## First Ubuntu locked proof

An early bounded STEP in each existing prepare job installs uv0.11.19 in an
isolated tool environment and runs `uv sync --locked` plus `uv run --locked`
with dev+django extras in a separate runner-temp environment. It captures uv,
Python/executable, pytest/location, uv.lock SHA256 and real toy native plan,
shard, serial and aggregate verdicts from the actual CI entry point. The toy
fixture rejects a missing shard too. Its proof and overlap are not full-suite
or public-wheel evidence. The pip-resolved quality/full mutation lane remains
separate; a prior separate lock check or pip CI pass is NOT this locked proof.
No additional smoke job is introduced. Mac mutation execution is excluded.

## Actual exact-source serial validation

Normal PR/main CI fans out; it is not held permanently behind a serial run.
Before accepting this implementation, after a noncompeting CI admission use
the same workflow/branch with manual input `serial_parity=true` (manual default
true). That run executes a REAL full serial reference per Python alongside
both shards, all consuming the identical manifest, test vectors, runtime and
timeout inputs. The final checks require every reference row and exact per-ID
verdict parity, plus observed actual mutant-interval overlap. No labels, fake
serial statuses or historical-source reference substitute are accepted.

Manual dispatch must target the exact candidate commit/ref. If the provider
cannot dispatch the existing workflow on a candidate ref before merge, report
that concrete control-plane limitation; do not waive parity or change main to
make a test possible.

## Timing evidence

Result JSON retains per-mutant real whole-runner elapsed, errors/timeouts and
UTC intervals, worker execution elapsed and plan/baseline preparation costs.
The aggregator reports both summed execution/runner seconds and the observed
shard envelope. Cross-host UTC overlap is not a clock-sync proof; corroborate
with provider job intervals.

After terminal CI, retain the read-only Actions job metadata (current run and
attempt): job started_at/completed_at for prepare, both shards, reference,
aggregate and unrelated unchanged jobs. These durations include action setup
and teardown, unlike worker Python timings. Report the shard-pair envelope
and sum separately from the serial-job duration and from the complete workflow
wall. The serial-validation workflow intentionally waits for its serial
reference; do NOT present that end-to-end duration as production fan-out wall
or subtract hypothetical costs. Also retain normal PR workflow elapsed.

Source, graph, synthetic protocol controls and native toy tests are not actual
full CI completion. No merge until normal exact-source quality/review/CI and
serial-parity evidence, outside the protected #51 release lane. This source
change neither tags nor publishes/releases a package or modifies #51.
