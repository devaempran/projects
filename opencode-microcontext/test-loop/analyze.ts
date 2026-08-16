// Pure, side-effect-free parser for a single ~/.local/share/opencode/log/llm-io/*.jsonl
// file (see packages/opencode/src/session/llm/io-log.ts for the log schema).
//
// Goal: turn a file that can be hundreds of KB to multiple MB (one JSON object per LLM
// request/response) into a small stats+issues object that's cheap to hand to an LLM for
// triage. Every "request" line carries an `id` that the matching "response" line reuses,
// so we build an id -> role map on the fly and join responses back to their role without
// a second pass.
//
// Scope (see TEST_LOOP_RUNBOOK.md): code-level failures only. Answer-quality/hallucination
// problems from the local model are explicitly out of scope for this taxonomy.
import fs from "fs"

export interface LogIssue {
  category: string
  line?: number
  role?: string
  id?: string
  detail: string
}

export interface LogStats {
  lines: number
  byRole: Record<string, number>
  finishReasons: Record<string, number>
  totalUsage: { input: number; output: number; total: number }
  issues: LogIssue[]
}

const DETAIL_MAX_CHARS = 300

function truncate(value: string) {
  return value.length <= DETAIL_MAX_CHARS ? value : `${value.slice(0, DETAIL_MAX_CHARS)}…`
}

// Orchestrator call ids (see llm-report.ts / orchestrator-bridge.ts's llmCallTracker) are
// shaped `sessionID:role:subtaskId:step:iteration:attempt:seq`. structured.ts's toolCall()/
// object() already retry a flaky model internally (repair-note + retry, up to `retries`
// attempts) before ever surfacing a failure — a "-" placeholder fills subtaskId/step/
// iteration when not applicable. Session-seam ids (the streaming ai-sdk/native path) are
// plain UUIDs with no colons and aren't retried this way. Dropping the attempt segment
// (second-to-last) from a 7-part id gives a stable key identifying "the same logical
// decision point across retries" — everything else (plain UUIDs) is its own key.
function callKeyOf(id: string): string {
  const parts = id.split(":")
  if (parts.length !== 7) return id
  const [sessionID, role, subtaskId, step, iteration, , seq] = parts
  return [sessionID, role, subtaskId, step, iteration, seq].join(":")
}

interface ResponseAttempt {
  line: number
  role: string
  id: string
  error?: string
  aborted?: boolean
  finishReason?: string
}

export function analyzeLog(filePath: string): LogStats {
  const text = fs.readFileSync(filePath, "utf8")
  const lines = text.split("\n").filter((line) => line.trim().length > 0)

  const roleByID = new Map<string, string>()
  const byRole: Record<string, number> = {}
  const finishReasons: Record<string, number> = {}
  const totalUsage = { input: 0, output: 0, total: 0 }
  const issues: LogIssue[] = []
  // Retries of the same logical call land under the same key, in file order (the file is
  // append-only and a retry only ever happens after its preceding attempt finishes) — the
  // last entry per key is that call's final word.
  const attemptsByCallKey = new Map<string, ResponseAttempt[]>()
  let lastVerifierResponse: { line: number; output: string } | undefined

  lines.forEach((raw, index) => {
    const lineNo = index + 1
    let rec: any
    try {
      rec = JSON.parse(raw)
    } catch {
      issues.push({ category: "malformed-log-line", line: lineNo, detail: truncate(raw) })
      return
    }

    if (rec.type === "request") {
      // Two independent request shapes land in this file (see io-log.ts): the streaming
      // session seam has no `role` field (role lives one level up, as `agent`/`mode`), the
      // orchestrator seam's TextRequestRecord has `role` directly (planner/worker/reducer/
      // verifier/title). Fall back sensibly so every request still buckets somewhere.
      const role = rec.role ?? rec.agent ?? "unknown"
      if (typeof rec.id === "string") roleByID.set(rec.id, role)
      byRole[role] = (byRole[role] ?? 0) + 1
      return
    }

    if (rec.type === "response") {
      const role = roleByID.get(rec.id) ?? "unknown"

      if (rec.finishReason) {
        finishReasons[rec.finishReason] = (finishReasons[rec.finishReason] ?? 0) + 1
      }
      if (rec.usage) {
        totalUsage.input += rec.usage.input ?? 0
        totalUsage.output += rec.usage.output ?? 0
        totalUsage.total += rec.usage.total ?? 0
      }

      const key = callKeyOf(rec.id)
      const attempts = attemptsByCallKey.get(key) ?? []
      attempts.push({
        line: lineNo,
        role,
        id: rec.id,
        error: rec.error,
        aborted: rec.aborted === true,
        finishReason: rec.finishReason,
      })
      attemptsByCallKey.set(key, attempts)

      if (role === "verifier" && typeof rec.output === "string") {
        lastVerifierResponse = { line: lineNo, output: rec.output }
      }
      return
    }
  })

  // Only flag a call as failed/truncated if its LAST attempt ended that way — an error or
  // truncation that a later retry recovered from is exactly what structured.ts's retry +
  // repair-note loop exists for (see qwen3-coder-prompt-tests.md for why the local model
  // needs it), not a code bug.
  for (const attempts of attemptsByCallKey.values()) {
    const last = attempts[attempts.length - 1]
    const retrySuffix = attempts.length > 1 ? ` (after ${attempts.length} attempts)` : ""
    if (last.error) {
      issues.push({
        category: "llm-call-error",
        line: last.line,
        role: last.role,
        id: last.id,
        detail: truncate(String(last.error)) + retrySuffix,
      })
    }
    if (last.aborted) {
      issues.push({ category: "llm-call-aborted", line: last.line, role: last.role, id: last.id, detail: "call was aborted" })
    }
    if (last.finishReason === "length") {
      issues.push({
        category: "truncated-output",
        line: last.line,
        role: last.role,
        id: last.id,
        detail: `finishReason=length${retrySuffix}`,
      })
    }
  }

  // The orchestrator loop (core's SessionOrchestrator) keeps iterating until the verifier
  // reports complete=true or it hits maxIterations. If the LAST verifier call still says
  // complete=false, the run gave up without ever finishing the task.
  if (lastVerifierResponse) {
    try {
      const parsed = JSON.parse(lastVerifierResponse.output)
      if (parsed && parsed.complete === false) {
        issues.push({
          category: "orchestrator-incomplete",
          line: lastVerifierResponse.line,
          role: "verifier",
          detail: truncate(`verifier reported incomplete: gaps=${JSON.stringify(parsed.gaps ?? [])}`),
        })
      }
    } catch {
      // Verifier output didn't parse as JSON at all — a structured-output failure, but the
      // llm-call-error / retries machinery upstream already handles decode failures as
      // errors when they exhaust retries, so silently skip here rather than double-count.
    }
  }

  // malformed-log-line issues are pushed in file order during the scan; the rest are pushed
  // afterward in call-key insertion order. Sort by line so the result reads top-to-bottom.
  issues.sort((a, b) => (a.line ?? 0) - (b.line ?? 0))

  return { lines: lines.length, byRole, finishReasons, totalUsage, issues }
}
