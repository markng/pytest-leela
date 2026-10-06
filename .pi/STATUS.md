# pytest-leela — unkillable mutants kill-session (2026-06-18)

## Worker tasks remaining
1. Add targeted tests for 64 unkillable mutants
2. Verify with daemon

## Current state
- 64 unkillable mutants (killed=0, state=clean)
- 1552 killed mutants
- 43 dirty (being processed by daemon)
- 104 stale

## Source of truth
sqlite3 .leela/index.db "SELECT s.file, m.location_line, m.operator, m.replacement_op FROM mutants m JOIN symbols s ON m.symbol_id=s.id WHERE m.killed=0 AND m.state='clean' ORDER BY s.file, m.location_line"
