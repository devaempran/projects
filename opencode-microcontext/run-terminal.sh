#!/bin/bash
set -e

# One-shot, non-interactive request against the current project directory:
#   ./run-terminal.sh "Explain the high level flow of this code base"
#
# Unlike run-local.sh (which boots the TUI/server), this uses `opencode run`,
# which sends a single prompt, streams the response to stdout, and exits.

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <message>" >&2
  exit 1
fi

PROJECT_DIR="$PWD"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Local qwen3.8:27b model served via Ollama (registered in ~/.config/opencode/opencode.jsonc).
MODEL="${OPENCODE_MODEL:-ollama/qwen3.8:27b}"

# Full input/output logging for every LLM API call (see
# packages/opencode/src/session/llm/io-log.ts). On by default for local dev;
# set OPENCODE_LLM_IO_LOG=0 to disable.
export OPENCODE_LLM_IO_LOG="${OPENCODE_LLM_IO_LOG:-1}"

# The orchestrator UI that run-local.sh advertises (http://HOST:PORT/orchestrator) is NOT
# reachable during a plain `opencode run`. That command talks to an in-process server through
# a fake "http://opencode.internal" base URL and never binds a TCP port -- its `--port` option
# is declared in yargs but never read anywhere in cli/cmd/run.ts. So nothing is listening and
# the browser gets connection-refused.
#
# To watch a one-shot run live in the orchestrator UI, start ./run-local.sh in another
# terminal and point this script at that server:
#   OPENCODE_ATTACH=http://127.0.0.1:7654 ./run-terminal.sh "..."
# Use 127.0.0.1, not localhost: the server binds IPv4 only and browsers on Windows try ::1 first.
ATTACH_ARGS=()
if [ -n "${OPENCODE_ATTACH:-}" ]; then
  ATTACH_ARGS=(--attach "$OPENCODE_ATTACH")
fi

echo "Running opencode on: $PROJECT_DIR" >&2
echo "Model:                $MODEL" >&2
if [ -n "${OPENCODE_ATTACH:-}" ]; then
  echo "Attached to server:   $OPENCODE_ATTACH" >&2
  echo "Orchestrator UI:      ${OPENCODE_ATTACH}/orchestrator" >&2
fi

cd "$SCRIPT_DIR"

bun run --cwd packages/opencode --conditions=browser src/index.ts \
  run --dir "$PROJECT_DIR" --model "$MODEL" "${ATTACH_ARGS[@]}" "$@"
