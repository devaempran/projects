export * as Verifier from "./verifier"

import { Effect, Schema } from "effect"
import { LLMError, type LLMClientService, type Model } from "@opencode-ai/llm"
import { OrchestratorStructured } from "./structured"
import { Planner } from "./planner"
import { OrchestratorObserver } from "./observer"
import { LlmReport } from "./llm-report"

export const Verdict = Schema.Struct({
  complete: Schema.Boolean,
  // Small local models routinely omit empty arrays instead of emitting `[]`.
  gaps: Schema.Array(Schema.String).pipe(Schema.withDecodingDefaultKey(Effect.succeed([]))),
  nextSubtasks: Schema.Array(Planner.PlanSubtask).pipe(Schema.withDecodingDefaultKey(Effect.succeed([]))),
})
export type Verdict = typeof Verdict.Type

// The "no synthesis subtask" rule is the fix for the dominant waste in llm-io
// 20260823T224539Z: the verifier proposed "Synthesize the findings from s4-s6 into a single
// overview" as a subtask three times across two turns. A worker executes alone with a fresh
// context and CANNOT see sibling results, so each of those subtasks spent its whole budget
// searching the filesystem for the orchestrator's own bookkeeping -- literally
// `glob **/*s[5-8]*` and `glob **/*findings*` -- and then failed. Consolidation is the
// Reducer's job by construction, so the only correct number of synthesis subtasks is zero.
export const SYSTEM =
  "You verify whether the overall task is complete given the consolidated summary. If complete, set complete=true and leave gaps and nextSubtasks empty. If not complete, set complete=false, list the gaps, and propose the next subtasks (with ids and dependsOn) that would close them. For each proposed subtask also set estimatedSteps: how many tool calls a worker needs to finish it alone — 2-3 for reading or editing one known file, 5-8 for finding something whose location is unknown, 10-16 for surveying many files. A gap that a previous subtask ran out of steps on needs a NARROWER subtask, not a repeat of the same one with more steps. Every subtask must be independently answerable from the codebase alone: never propose a subtask that synthesizes, consolidates, summarizes, or combines the results of other subtasks, and never refer to another subtask by its id — each worker runs with a fresh context and cannot see any other subtask's output, and consolidating them is already handled after you."

export const buildPrompt = (input: { readonly task: string; readonly summary: string }): string =>
  [`Task:\n${input.task}`, `Consolidated summary:\n${input.summary}`, `Is the task complete? If not, propose the next subtasks.`].join("\n\n")

export const verify = (input: {
  readonly model: Model
  readonly task: string
  readonly summary: string
  readonly retries?: number
  readonly iteration?: number
  readonly observer?: OrchestratorObserver.Interface
}): Effect.Effect<Verdict, LLMError, LLMClientService> => {
  const observer = input.observer ?? OrchestratorObserver.noop
  return OrchestratorStructured.object({
    model: input.model,
    schema: Verdict,
    system: SYSTEM,
    prompt: buildPrompt(input),
    retries: input.retries,
    // Both array fields have been observed arriving as JSON strings from the local model.
    // Without this the whole verdict is rejected and a full extra verifier call is spent
    // recovering it; see `OrchestratorStructured.Options.repair`.
    repair: OrchestratorStructured.repairJsonStringKeys("nextSubtasks", "gaps"),
    reporter: LlmReport.reporterFor(observer, { role: "verifier", model: input.model, iteration: input.iteration }),
  })
}
