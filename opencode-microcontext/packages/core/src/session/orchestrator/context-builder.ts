export * as ContextBuilder from "./context-builder"

import { Schema } from "effect"
import type { JsonSchema } from "@opencode-ai/llm"

export const Observation = Schema.Struct({ tool: Schema.String, output: Schema.String })
export type Observation = typeof Observation.Type

export interface ToolCatalogEntry {
  readonly name: string
  readonly description: string
  /** Raw JSON schema for the tool's arguments, when known. Falls back to a permissive schema when absent. */
  readonly inputSchema?: JsonSchema
}

export interface BuildInput {
  readonly task: string
  readonly subtask: { readonly id: string; readonly description: string }
  readonly observations: ReadonlyArray<Observation>
  readonly tools?: ReadonlyArray<ToolCatalogEntry>
}

// Tool results are unbounded in principle (a `glob`/`grep`/`read` call can return
// megabytes). A single oversized observation can push the whole packet past what's
// left of a small model's context window, leaving too few tokens for the model to
// even close a well-formed tool call (observed in practice: a `glob("**")` result
// once pushed a worker step to 82% of a 32768-token window, after which the model
// stopped producing valid tool calls on every retry). Cap each observation, and the
// total observations block, so a single noisy tool call can't blow the budget.
const MAX_OBSERVATION_CHARS = 4_000
const MAX_OBSERVATIONS_TOTAL_CHARS = 12_000

const renderObservation = (o: Observation): string =>
  o.output.length <= MAX_OBSERVATION_CHARS
    ? o.output
    : `${o.output.slice(0, MAX_OBSERVATION_CHARS)}\n... [truncated ${o.output.length - MAX_OBSERVATION_CHARS} more characters]`

/**
 * Render observations most-recent-first-priority: everything is numbered in
 * original order, but once the rendered block would exceed the total budget the
 * oldest entries collapse into a one-line note instead of being dropped silently —
 * the most recent observations (closest to the next decision) are always kept in full.
 */
const renderObservations = (observations: ReadonlyArray<Observation>): string => {
  if (observations.length === 0) return "(none yet)"
  const rendered = observations.map((o, i) => `${i + 1}. ${o.tool} => ${renderObservation(o)}`)
  let total = rendered.reduce((sum, text) => sum + text.length, 0)
  let start = 0
  while (total > MAX_OBSERVATIONS_TOTAL_CHARS && start < rendered.length - 1) {
    total -= rendered[start].length
    start++
  }
  const kept = rendered.slice(start)
  return start === 0 ? kept.join("\n") : [`[${start} earlier observation${start === 1 ? "" : "s"} omitted for space]`, ...kept].join("\n")
}

/**
 * Build a fresh, minimal prompt packet for one worker step. Deliberately small:
 * the overall task for orientation, THIS subtask, and only the observations
 * gathered so far this run. No conversation history.
 */
export const build = (input: BuildInput): string => {
  const observations = renderObservations(input.observations)
  // Tool names must match these exactly — models otherwise default to generic
  // conventions (e.g. "read_file") that don't exist here.
  const tools =
    !input.tools || input.tools.length === 0
      ? "(none available)"
      : input.tools.map((t) => `- ${t.name}: ${t.description}`).join("\n")
  return [
    `Overall task:\n${input.task}`,
    `Your subtask (${input.subtask.id}):\n${input.subtask.description}`,
    `Available tools (call one of these by name):\n${tools}`,
    `Observations so far:\n${observations}`,
    `Decide the next action. Call one of the available tools to gather info or make progress, or call finish when the subtask is complete.`,
  ].join("\n\n")
}
