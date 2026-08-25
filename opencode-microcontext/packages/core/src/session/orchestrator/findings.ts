export * as Findings from "./findings"

import type { ContextBuilder } from "./context-builder"

/**
 * Deterministic salvage of whatever a worker learned, for the paths where it never
 * authored a summary of its own.
 *
 * This exists because of the concrete failure it fixes: a subtask that exhausted its step
 * budget returned `"Reached max steps (8) without finishing"` and nothing else, so the
 * Reducer was handed a cap message in place of the nine tool results the worker had
 * actually collected. Every subtask failing that way produced an empty consolidated
 * summary, the Verifier found the task incomplete, and the run burned all three iterations
 * to deliver nothing — even though the information needed had already been read off disk.
 *
 * The worker's first choice on a forced exit is still a model-authored wrap-up (see
 * `worker.ts`'s checkpoint call), because a summary beats raw tool output. This is the
 * fallback for when that call also fails: it needs no model, so it cannot fail in turn.
 */

/** Prefix marking a synthetic guardrail message the worker fed back to itself, not a real tool result. */
const GUARD_PREFIX = "("

/** Guardrail feedback (`(not re-run) …`, `(rejected) …`, `(no new information) …`) is worker
 * bookkeeping, not a finding — including it would present the model's own scolding to the
 * Reducer as though it were evidence. */
export const isGuardOutput = (output: string): boolean =>
  output.startsWith(GUARD_PREFIX) && /^\((not re-run|rejected|no new information)\)/.test(output)

/** Per-finding and total caps, so a salvaged digest can't itself blow the Reducer's context. */
export const MAX_FINDING_CHARS = 600
export const MAX_FINDINGS_TOTAL_CHARS = 2_400

const clip = (text: string, max: number): string =>
  text.length <= max ? text : `${text.slice(0, max).trimEnd()}…`

const oneLine = (text: string): string => text.replace(/\s+/g, " ").trim()

export interface DigestInput {
  readonly observations: ReadonlyArray<ContextBuilder.Observation>
  /** Free-text progress notes the model volunteered (e.g. via `request_steps`), which are
   *  already summaries and so are listed ahead of raw tool output. */
  readonly notes?: ReadonlyArray<string>
}

/**
 * Render the salvaged findings, most-recent tool results last (reading order), de-duplicated
 * by output so a repeated result is reported once. Returns `""` when there is genuinely
 * nothing to hand over — the caller uses that to choose between a `partial` and a `failed`
 * status rather than emitting an empty "findings:" section.
 */
export const digest = (input: DigestInput): string => {
  const lines: string[] = []
  for (const note of input.notes ?? []) {
    const text = oneLine(note)
    if (text.length > 0) lines.push(`- (worker note) ${clip(text, MAX_FINDING_CHARS)}`)
  }

  const seen = new Set<string>()
  for (const observation of input.observations) {
    if (isGuardOutput(observation.output)) continue
    const text = oneLine(observation.output)
    if (text.length === 0 || seen.has(text)) continue
    seen.add(text)
    lines.push(`- ${observation.tool}: ${clip(text, MAX_FINDING_CHARS)}`)
  }
  if (lines.length === 0) return ""

  // Drop from the front when over budget: the later observations are the ones the worker
  // reached after narrowing its search, so they are the more specific findings.
  let total = lines.reduce((sum, line) => sum + line.length + 1, 0)
  let start = 0
  while (total > MAX_FINDINGS_TOTAL_CHARS && start < lines.length - 1) {
    total -= lines[start]!.length + 1
    start++
  }
  const kept = lines.slice(start)
  const omitted = start > 0 ? [`- […${start} earlier finding${start === 1 ? "" : "s"} omitted for space]`] : []
  return [...omitted, ...kept].join("\n")
}

/**
 * Compose the result text for a subtask that exited without a model-authored summary:
 * why it stopped, followed by the salvaged findings when there are any.
 */
export const salvagedResult = (input: DigestInput & { readonly reason: string }): { readonly result: string; readonly hasFindings: boolean } => {
  const findings = digest(input)
  if (findings.length === 0) return { result: `${input.reason} No findings were gathered.`, hasFindings: false }
  return { result: `${input.reason} Partial findings gathered before stopping:\n${findings}`, hasFindings: true }
}
