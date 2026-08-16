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

export const SYSTEM =
  "You verify whether the overall task is complete given the consolidated summary. If complete, set complete=true and leave gaps and nextSubtasks empty. If not complete, set complete=false, list the gaps, and propose the next subtasks (with ids and dependsOn) that would close them."

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
    reporter: LlmReport.reporterFor(observer, { role: "verifier", model: input.model, iteration: input.iteration }),
  })
}
