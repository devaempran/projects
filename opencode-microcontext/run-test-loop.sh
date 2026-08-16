#!/usr/bin/env bash
# One cycle of the test loop described in TEST_LOOP_RUNBOOK.md:
#   ./run-test-loop.sh ["<prompt>"]
#
# Runs run-terminal.sh, deterministically identifies the one llm-io log file that run
# produced, and hands everything off to test-loop/record-run.ts, which prints a single
# compact JSON verdict to stdout. That JSON object is the only thing worth reading per
# cycle — never the raw log file.
#
# By default this exercises opencode against its own repo. Set OPENCODE_TEST_TARGET_DIR to
# point `opencode run` at a different project instead (e.g. a real, unrelated codebase) —
# everything else (log dir, test-loop/ bookkeeping) still resolves relative to this repo.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TARGET_DIR="${OPENCODE_TEST_TARGET_DIR:-$SCRIPT_DIR}"
if [ ! -d "$TARGET_DIR" ]; then
  echo "error: OPENCODE_TEST_TARGET_DIR '$TARGET_DIR' is not a directory" >&2
  exit 1
fi

PROMPT="${1:-Explain the high level flow of this code base}"
LOG_DIR="${OPENCODE_LLM_IO_LOG_DIR:-$HOME/.local/share/opencode/log/llm-io}"
mkdir -p "$LOG_DIR"

# Snapshot before/after rather than `ls -t | head -1`: the new file that appears during
# this run *is* this run's file, unambiguously, regardless of clock resolution or PID.
before=$(ls -1 "$LOG_DIR" 2>/dev/null | sort)

STDOUT_FILE=$(mktemp)
set +e
(cd "$TARGET_DIR" && "$SCRIPT_DIR/run-terminal.sh" "$PROMPT") >"$STDOUT_FILE" 2>&1
EXIT_CODE=$?
set -e

after=$(ls -1 "$LOG_DIR" 2>/dev/null | sort)
NEW_FILES=$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after"))
NEW_FILE_COUNT=$(printf '%s\n' "$NEW_FILES" | grep -c . || true)

if [ "$NEW_FILE_COUNT" -eq 0 ]; then
  LOG_FILE=""
elif [ "$NEW_FILE_COUNT" -eq 1 ]; then
  LOG_FILE="$LOG_DIR/$NEW_FILES"
else
  # Concurrent writers shouldn't happen in this workflow, but don't guess silently if it does.
  echo "warning: ${NEW_FILE_COUNT} new log files appeared, taking the most recently modified" >&2
  LOG_FILE=$(printf '%s\n' "$NEW_FILES" | sed "s|^|$LOG_DIR/|" | xargs ls -t | head -1)
fi

bun run test-loop/record-run.ts \
  --prompt "$PROMPT" \
  --exit-code "$EXIT_CODE" \
  --stdout-file "$STDOUT_FILE" \
  --log-file "${LOG_FILE:-}"

rm -f "$STDOUT_FILE"
