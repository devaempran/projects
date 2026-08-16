#!/bin/bash
set -e

PROJECT_DIR="$PWD"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Pin the server to a known host/port so the orchestrator URL is stable.
# Without --port the TUI uses an internal worker transport that a browser
# can't reach, so we bind a real HTTP server here.
HOST="${OPENCODE_HOST:-127.0.0.1}"
PORT="${OPENCODE_PORT:-7654}"

# Local qwen3-coder model served via Ollama (registered in ~/.config/opencode/opencode.jsonc).
MODEL="${OPENCODE_MODEL:-ollama/qwen3-coder:latest}"

ORCH_URL="http://${HOST}:${PORT}/orchestrator"
if [ -n "$OPENCODE_SERVER_PASSWORD" ]; then
  ORCH_URL="${ORCH_URL}?password=${OPENCODE_SERVER_PASSWORD}"
fi

# Full input/output logging for every LLM API call (see
# packages/opencode/src/session/llm/io-log.ts). On by default for local dev;
# set OPENCODE_LLM_IO_LOG=0 to disable.
export OPENCODE_LLM_IO_LOG="${OPENCODE_LLM_IO_LOG:-1}"

echo "Running opencode on: $PROJECT_DIR"
echo "Model:                $MODEL"
echo "Orchestrator UI:      $ORCH_URL"
if [ "$OPENCODE_LLM_IO_LOG" != "0" ] && [ "$OPENCODE_LLM_IO_LOG" != "false" ]; then
  echo "LLM I/O log:          ~/.local/share/opencode/log/llm-io/"
fi

cd "$SCRIPT_DIR"

bun run --cwd packages/opencode --conditions=browser src/index.ts \
  "$PROJECT_DIR" --hostname "$HOST" --port "$PORT" --model "$MODEL"
