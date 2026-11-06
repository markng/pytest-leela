#!/bin/bash
# Single-use command receipt. No retries or result mapping.
set -u
set -o pipefail
R=/private/tmp/pi-leela-responsive-watch-20261022
E="$R/.pi/evidence/final-current-ci/py313"
PY="$R/.pi/evidence/local-recovery-20261027/environments/env313/bin/python"
GUARD="$R/.pi/evidence/current-ci-selfcheck/attempt2/resource_guard.py"
TMPDIR=$(cat "$E/owned-tmp.txt")
export R E PY TMPDIR
cd "$R" || exit 97
export PATH="$(dirname "$PY"):/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="$E/home" XDG_CONFIG_HOME="$E/xdg" XDG_CACHE_HOME="$E/cache"
export MYPY_CACHE_DIR="$E/mypy-cache" PYTHONUNBUFFERED=1
export PYTEST_ADDOPTS="-o cache_dir=$E/pytest-cache"
unset PYTHONPYCACHEPREFIX PYTHONDONTWRITEBYTECODE
START=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
printf 'start=%s\n' "$START" > "$E/run-times.txt"
# Guard startup handshake BEFORE native; fail-closed if guard cannot establish.
"$PY" -B "$E/launcher.py" "$PY" "$GUARD" "$$" "$R" "$TMPDIR" "$E/resources.log" 28800 "$E" > "$E/launcher.log" 2>&1 &
LAUNCHER=$!
for i in $(seq 1 360); do
  grep -qE 'GUARD_READY|GUARD_STARTUP_FAILURE|GUARD_STARTUP_TIMEOUT|PARENT_NOT_RUNNING' "$E/launcher.log" 2>/dev/null && break
  sleep 0.5
done
if ! grep -q 'GUARD_READY' "$E/launcher.log" 2>/dev/null; then
  echo "GUARD_NOT_READY native NOTRUN" | tee "$E/notrun.txt"
  kill "$LAUNCHER" 2>/dev/null || true
  wait "$LAUNCHER" 2>/dev/null || true
  printf 'end=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$E/run-times.txt"
  exit 20
fi
printf 'guard_ready=1 launcher=%s\n' "$LAUNCHER" >> "$E/run-times.txt"
"$PY" -c "
targets = [f for f in __import__('glob').glob('src/pytest_leela/*.py')
           if not f.endswith('__init__.py')]
print(' '.join(f'--target {t}' for t in sorted(targets)))
" 2> "$E/target-generator.stderr" | tee "$E/target-args.txt" | xargs /bin/bash -c '
"$PY" -m pytest --leela --leela-html "$E/native.html" "$@"
rc=$?
printf "%s\n" "$rc" > "$E/native-exit.txt"
exit "$rc"
' native-pytest > "$E/native.stdout" 2> "$E/native.stderr"
STATUSES=("${PIPESTATUS[@]}")
# Stop and reap the guard via the launcher.
kill "$LAUNCHER" 2>/dev/null || true
wait "$LAUNCHER" 2>/dev/null || true
printf 'generator=%s\ntee=%s\nxargs=%s\n' "${STATUSES[0]}" "${STATUSES[1]}" "${STATUSES[2]}" > "$E/pipeline-status.txt"
printf 'end=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$E/run-times.txt"
cat "$E/run-times.txt" "$E/pipeline-status.txt"
if test -f "$E/native-exit.txt"; then printf 'native_exit='; cat "$E/native-exit.txt"; fi
echo "=== launcher.log ==="; cat "$E/launcher.log"
echo "=== guard-exit.txt ==="; cat "$E/guard-exit.txt" 2>/dev/null || echo absent
echo "=== native.stdout tail ==="; tail -40 "$E/native.stdout"
echo "=== native.stderr tail ==="; tail -20 "$E/native.stderr"
for s in "${STATUSES[@]}"; do if test "$s" -ne 0; then exit "$s"; fi; done
