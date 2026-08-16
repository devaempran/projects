#!/usr/bin/env bash
# Runs the probes from qwen3-coder-prompt-tests.md against a local Ollama
# qwen3-coder model and records every input/output pair to a markdown file.
set -euo pipefail

OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434/v1/chat/completions}"
MODEL="${OLLAMA_MODEL:-qwen3-coder:latest}"
OUT="${1:-qwen3-coder-prompt-results.md}"

PROBE1_RUNS="${PROBE1_RUNS:-5}"
PROBE2_RUNS="${PROBE2_RUNS:-5}"
PROBE4_RUNS="${PROBE4_RUNS:-3}"

echo "# qwen3-coder probe results" > "$OUT"
echo "" >> "$OUT"
echo "Model: \`$MODEL\` via \`$OLLAMA_URL\`" >> "$OUT"
echo "Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> "$OUT"
echo "" >> "$OUT"

# $1 = probe title, $2 = payload (JSON with $MODEL already substituted), $3 = run number (optional)
run_probe() {
  local title="$1"
  local payload="$2"
  local run_no="${3:-}"
  local heading="## ${title}"
  if [[ -n "$run_no" ]]; then
    heading="${heading} (run ${run_no})"
  fi

  echo "$heading" >> "$OUT"
  echo "" >> "$OUT"
  echo "### Input" >> "$OUT"
  echo '```json' >> "$OUT"
  echo "$payload" >> "$OUT"
  echo '```' >> "$OUT"
  echo "" >> "$OUT"

  local response
  response="$(curl -s "$OLLAMA_URL" -H "Content-Type: application/json" -d "$payload")"

  echo "### Output" >> "$OUT"
  echo '```json' >> "$OUT"
  echo "$response" >> "$OUT"
  echo '```' >> "$OUT"
  echo "" >> "$OUT"

  echo "Ran: ${title}${run_no:+ (run $run_no)}"
}

PROBE1_PAYLOAD=$(cat <<JSON
{
  "model": "$MODEL",
  "messages": [
    {"role":"system","content":"You are a focused worker executing ONE subtask with a small context. At each step, call exactly one of the tools listed under \"Available tools\" in the prompt. When the subtask is complete or cannot proceed, call \`finish\` instead. Keep results concise."},
    {"role":"user","content":"Overall task:\nCreate a md file mapping the high-level flow of the code base.\n\nYour subtask (s1):\nIdentify the main components and modules of the codebase.\n\nAvailable tools (call one of these by name):\n- glob: Find files matching a glob pattern\n- read: Read a file from disk\n- finish: Call this when the subtask is complete or cannot proceed\n\nObservations so far:\n1. glob => packages/core, packages/llm, packages/opencode, packages/server, packages/tui\n\nDecide the next action. Call one of the available tools to gather info or make progress, or call finish when the subtask is complete."}
  ],
  "tools": [
    {"type":"function","function":{"name":"glob","description":"Find files matching a glob pattern","parameters":{"type":"object","properties":{"pattern":{"type":"string"}},"required":["pattern"]}}},
    {"type":"function","function":{"name":"read","description":"Read a file from disk","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"finish","description":"Call this when the subtask is complete or cannot proceed. Set result to a concise summary, and status to \"failed\" if it could not be completed.","parameters":{"type":"object","properties":{"status":{"type":"string","enum":["done","failed"]},"result":{"type":"string"}},"required":["status","result"]}}}
  ],
  "tool_choice": "required"
}
JSON
)

PROBE2_PAYLOAD=$(cat <<JSON
{
  "model": "$MODEL",
  "messages": [
    {"role":"system","content":"You are a focused worker executing ONE subtask. Respond ONLY with a single tool call — never with plain text. Call exactly one of the tools listed under \"Available tools\", with valid JSON arguments. Never invent a tool name. When the subtask is complete or cannot proceed, call finish."},
    {"role":"user","content":"Overall task:\nCreate a md file mapping the high-level flow of the code base.\n\nYour subtask (s1):\nIdentify the main components and modules of the codebase.\n\nAvailable tools (call one of these by name):\n- glob: Find files matching a glob pattern\n- read: Read a file from disk\n- finish: Call this when the subtask is complete or cannot proceed\n\nObservations so far:\n1. glob => packages/core, packages/llm, packages/opencode, packages/server, packages/tui\n\nDecide the next action. Call one of the available tools to gather info or make progress, or call finish when the subtask is complete."}
  ],
  "tools": [
    {"type":"function","function":{"name":"glob","description":"Find files matching a glob pattern","parameters":{"type":"object","properties":{"pattern":{"type":"string"}},"required":["pattern"]}}},
    {"type":"function","function":{"name":"read","description":"Read a file from disk","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"finish","description":"Call this when the subtask is complete or cannot proceed. Set result to a concise summary, and status to \"failed\" if it could not be completed.","parameters":{"type":"object","properties":{"status":{"type":"string","enum":["done","failed"]},"result":{"type":"string"}},"required":["status","result"]}}}
  ],
  "tool_choice": "required"
}
JSON
)

PROBE3A_PAYLOAD=$(cat <<JSON
{
  "model": "$MODEL",
  "messages": [
    {"role":"system","content":"Report subtask completion by calling the finish tool. status must be exactly \"done\" or \"failed\" — if work is still in progress, that counts as \"failed\"."},
    {"role":"user","content":"Subtask: write unit tests for the parser. Progress: 3 of 7 tests written, work is still ongoing. Report the status."}
  ],
  "tools": [
    {"type":"function","function":{"name":"finish","description":"Report the subtask outcome.","parameters":{"type":"object","properties":{"status":{"type":"string","enum":["done","failed"]},"result":{"type":"string"}},"required":["status","result"]}}}
  ],
  "tool_choice": "required"
}
JSON
)

PROBE3B_PAYLOAD=$(cat <<JSON
{
  "model": "$MODEL",
  "messages": [
    {"role":"system","content":"Report subtask completion by calling the finish tool. status must be exactly \"done\" or \"failed\"."},
    {"role":"user","content":"Subtask: write unit tests for the parser. Progress: 3 of 7 tests written, work is still ongoing. Report the status."}
  ],
  "tools": [
    {"type":"function","function":{"name":"finish","description":"Report the subtask outcome.","parameters":{"type":"object","properties":{"status":{"type":"string","enum":["done","failed"]},"result":{"type":"string"}},"required":["status","result"]}}}
  ],
  "tool_choice": "required"
}
JSON
)

PROBE4_PAYLOAD=$(cat <<JSON
{
  "model": "$MODEL",
  "messages": [
    {"role":"system","content":"You are a planner. Decompose the task into subtasks."},
    {"role":"user","content":"Decompose: create a md file mapping the high-level flow of the codebase."}
  ],
  "response_format": {"type":"json_schema","json_schema":{"name":"plan","schema":{
    "type":"object",
    "properties":{"subtasks":{"type":"array","items":{
      "type":"object",
      "properties":{"id":{"type":"string"},"description":{"type":"string"},"dependsOn":{"type":"array","items":{"type":"string"}}},
      "required":["id","description","dependsOn"],
      "additionalProperties":false
    }}},
    "required":["subtasks"],
    "additionalProperties":false
  }}}
}
JSON
)

for i in $(seq 1 "$PROBE1_RUNS"); do
  run_probe "Probe 1 — narrate vs tool call in worker context" "$PROBE1_PAYLOAD" "$i"
done

for i in $(seq 1 "$PROBE2_RUNS"); do
  run_probe "Probe 2 — explicit tool-call-only instruction" "$PROBE2_PAYLOAD" "$i"
done

run_probe "Probe 3a — enum discipline (with in-progress-is-failed sentence)" "$PROBE3A_PAYLOAD"
run_probe "Probe 3b — enum discipline (without in-progress-is-failed sentence)" "$PROBE3B_PAYLOAD"

for i in $(seq 1 "$PROBE4_RUNS"); do
  run_probe "Probe 4 — grammar-constrained structured output" "$PROBE4_PAYLOAD" "$i"
done

echo "" >> "$OUT"
echo "Note: Probe 5 (in-opencode end-to-end) is not a standalone HTTP call — run it manually via run-local.sh and check the logs/orchestrator_state table as described in qwen3-coder-prompt-tests.md." >> "$OUT"

echo "Done. Results written to $OUT"
