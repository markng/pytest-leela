"""Bundled guidance printed by ``pytest --skill``."""

SKILL_MARKDOWN = """---
name: pytest-leela
description: Write or improve Python tests using pytest-leela mutation results.
---

# Mutation testing with Leela

1. Run the ordinary tests first. Fix any failure before interpreting mutants.
2. Focus on the change: `pytest --leela --diff origin/main` or
   `pytest --leela --target src/example.py`. Add `--leela-jsonl events.jsonl`
   (a new file) for machine-readable completion events.
3. For a survivor, read the original code and the exact mutant. Find an input
   or state where their observable behavior differs. Add the smallest test of
   the real contract, not an assertion about implementation or the mutation.
4. Assert outputs, side effects, and error paths that matter. For a branch,
   include inputs on both sides of the decision. Make fixtures deterministic;
   replace slow external boundaries with fakes where the contract allows.
5. Re-run the focused ordinary and mutation tests. An error is not a kill:
   resolve collection, fixture, or environment problems before scoring.
   If no valid input distinguishes a mutant, explain why instead of adding
   a brittle test or weakening the gate.
"""
