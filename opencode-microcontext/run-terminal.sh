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

# Local qwen3-coder model served via Ollama (registered in ~/.config/opencode/opencode.jsonc).
MODEL="${OPENCODE_MODEL:-ollama/qwen3-coder:latest}"

# Full input/output logging for every LLM API call (see
# packages/opencode/src/session/llm/io-log.ts). On by default for local dev;
# set OPENCODE_LLM_IO_LOG=0 to disable.
export OPENCODE_LLM_IO_LOG="${OPENCODE_LLM_IO_LOG:-1}"

echo "Running opencode on: $PROJECT_DIR" >&2
echo "Model:                $MODEL" >&2

cd "$SCRIPT_DIR"

bun run --cwd packages/opencode --conditions=browser src/index.ts \
  run --dir "$PROJECT_DIR" --model "$MODEL" "$@"
