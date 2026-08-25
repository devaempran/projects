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
  /** Set when this subtask is a child produced by a parent's `decompose` call — one line
   *  of lineage ("Parent subtask: <parent description>. Your slice: <child description>")
   *  so the child gets a fresh, tightly-scoped context instead of inherited history. */
  readonly parentContext?: string
  /** Current step and the steps budgeted for this subtask. Surfaced to the model so it can
   *  pace itself: with a flat, invisible cap the model had no way to know it was about to be
   *  cut off, so it kept opening new lines of investigation right up to the last step and
   *  never spent one consolidating. */
  readonly budget?: { readonly step: number; readonly total: number }
  /** A one-off instruction for this step only — the repetition/stall nudge, or the
   *  wrap-up-or-request-more-steps checkpoint. Rendered last so it is the closest text to
   *  the model's decision. */
  readonly notice?: string
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
 * The structural backstop for a synthesis subtask that the planner/verifier prompts failed to
 * suppress. Observed failure: given "Synthesize the findings from s4-s6", the worker assumed
 * s4-s6 must be files and spent all 8 steps on `glob **\/*s[5-8]*`, `glob **\/*findings*` and
 * re-reading README/main.py, looking on disk for state that only exists in the orchestrator.
 * Telling the worker plainly that sibling output is unreachable turns that from an 8-step
 * dead end into a 1-step `finish`, which is also a far more useful signal to the Reducer.
 */
const ISOLATION_NOTE =
  "Note: you are one of several independent subtasks. You cannot see any other subtask's results, and ids like `s2` or `t3` refer to sibling subtasks, NOT to files or directories — never search the filesystem for them. If this subtask asks you to synthesize, consolidate, or combine other subtasks' findings, that is not possible here and is handled automatically after all subtasks finish: call `finish` immediately, reporting only what you can establish from the codebase yourself."

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
    ...(input.parentContext !== undefined ? [`Parent subtask (yours is one slice of it):\n${input.parentContext}`] : []),
    ...(input.budget !== undefined ? [renderBudget(input.budget)] : []),
    `Available tools (call one of these by name):\n${tools}`,
    `Observations so far:\n${observations}`,
    ISOLATION_NOTE,
    `Decide the next action. Call one of the available tools to gather info or make progress, or call finish when the subtask is complete.`,
    ...(input.notice !== undefined ? [input.notice] : []),
  ].join("\n\n")
}

/** One line telling the model where it is in its budget, plus an explicit consolidate
 *  instruction on the final step so the step is spent on an answer rather than a new lead. */
const renderBudget = (budget: { readonly step: number; readonly total: number }): string => {
  const remaining = budget.total - budget.step
  const pacing =
    remaining <= 0
      ? " This is your LAST step — call finish now with whatever you have."
      : remaining === 1
        ? " One step remains after this one; start consolidating."
        : ""
  return `Step ${budget.step} of ${budget.total} for this subtask.${pacing}`
}
