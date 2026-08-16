// Dev-only JSONL log of the full input/output of every LLM API call.
//
// Enable with OPENCODE_LLM_IO_LOG=1. Writes one JSON line per event to
// ~/.local/share/opencode/log/llm-io/<timestamp>-<pid>.jsonl: a "request"
// line when a call is issued (system prompt, messages, tools, params) and a
// matching "response" line — keyed by the same `id` — once the stream
// finishes, errors, or is aborted (assembled text/reasoning/tool calls,
// usage, finish reason).
//
// There are two independent dispatch seams that issue LLM calls (see
// session/llm/AGENTS.md and session/orchestrator-bridge.ts), so this module
// exposes two request/response pairs: `request`/`accumulator`/`record`/
// `response` for the streaming ai-sdk/native seam in ../llm.ts, and
// `requestText`/`responseText` for the orchestrator's planner/worker/
// reducer/verifier calls, which arrive as plain strings via
// `OrchestratorObserver` rather than a `LLMEvent` stream. Every call from
// either seam lands in the same file so a single trace covers a whole turn.
//
// Lazy-initialized like ../../cli/cmd/run/trace.ts: the first call decides
// whether logging is active based on the env var, and the target file is
// reused for the lifetime of the process.
//
// Never logs `headers` — those carry provider auth. Everything else here is
// exactly what was sent to / received from the provider, unredacted, so only
// turn this on for local debugging.
import fs from "fs"
import path from "path"
import { Global } from "@opencode-ai/core/global"
import { LLMResponse, type LLMEvent } from "@opencode-ai/llm"
import { errorMessage } from "@/util/error"
import type { ModelMessage, Tool } from "ai"

let state: { write(record: unknown): void } | false | undefined

function stamp() {
  return new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d+Z$/, "Z")
}

function file() {
  return path.join(Global.Path.log, "llm-io", `${stamp()}-${process.pid}.jsonl`)
}

function envEnabled() {
  const value = process.env.OPENCODE_LLM_IO_LOG?.trim().toLowerCase()
  return !!value && value !== "0" && value !== "false"
}

function sink() {
  if (state !== undefined) return state || undefined
  if (!envEnabled()) {
    state = false
    return undefined
  }
  const target = file()
  fs.mkdirSync(path.dirname(target), { recursive: true })
  state = {
    write(record: unknown) {
      fs.appendFileSync(target, JSON.stringify(record) + "\n")
    },
  }
  return state
}

export function enabled() {
  return sink() !== undefined
}

export function id() {
  return crypto.randomUUID()
}

export interface RequestRecord {
  readonly id: string
  readonly sessionID: string
  readonly parentSessionID?: string
  readonly providerID: string
  readonly modelID: string
  readonly agent: string
  readonly mode: string
  readonly small: boolean
  readonly system: readonly string[]
  readonly messages: readonly ModelMessage[]
  readonly tools: Record<string, Tool>
  readonly toolChoice?: "auto" | "required" | "none"
  readonly retries?: number
  readonly params: {
    readonly temperature?: number
    readonly topP?: number
    readonly topK?: number
    readonly maxOutputTokens?: number
    readonly options: Record<string, unknown>
  }
}

export function request(record: RequestRecord) {
  const s = sink()
  if (!s) return
  s.write({
    time: new Date().toISOString(),
    type: "request",
    source: "session" as const,
    ...record,
    tools: Object.fromEntries(
      Object.entries(record.tools).map(([name, tool]) => [
        name,
        { description: tool.description, inputSchema: tool.inputSchema },
      ]),
    ),
  })
}

// The orchestrator (session/orchestrator-bridge.ts) dispatches its own
// planner/worker/reducer/verifier calls straight through @opencode-ai/llm's
// LLMClient, bypassing the ai-sdk/native seam above entirely — it never goes
// through `request`/`accumulator`/`response`. Its `OrchestratorObserver`
// hooks already hand us a fully-formed system/prompt string and output
// string per call (rather than ModelMessage[]/LLMEvent), so these text
// variants log that shape directly instead of forcing it through the
// streaming-accumulator machinery above.
export interface TextRequestRecord {
  readonly id: string
  readonly sessionID: string
  readonly role: string
  readonly subtaskId?: string
  readonly step?: number
  readonly iteration?: number
  readonly attempt: number
  readonly modelID: string
  readonly contextWindow?: number
  readonly estimatedInputTokens?: number
  readonly system?: string
  readonly prompt: string
}

export function requestText(record: TextRequestRecord) {
  const s = sink()
  if (!s) return
  s.write({ time: new Date().toISOString(), type: "request", source: "orchestrator" as const, ...record })
}

export interface TextResponseRecord {
  readonly id: string
  readonly durationMs: number
  readonly output?: string
  readonly error?: string
  readonly finishReason?: string
  readonly usage?: Record<string, number>
}

export function responseText(record: TextResponseRecord) {
  const s = sink()
  if (!s) return
  s.write({ time: new Date().toISOString(), type: "response", ...record })
}

export function accumulator() {
  return LLMResponse.empty()
}
export type Accumulator = ReturnType<typeof accumulator>

export function record(acc: Accumulator, event: LLMEvent) {
  return LLMResponse.reduce(acc, event)
}

export function response(requestID: string, acc: Accumulator, error?: unknown) {
  const s = sink()
  if (!s) return
  const completed = LLMResponse.complete(acc)
  s.write({
    time: new Date().toISOString(),
    type: "response",
    id: requestID,
    finishReason: completed?.finishReason,
    usage: completed?.usage ?? acc.usage,
    message: completed?.message ?? acc.message,
    aborted: completed === undefined && error === undefined,
    error: error === undefined ? undefined : errorMessage(error),
  })
}

export * as LLMIOLog from "./io-log"
