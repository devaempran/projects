export * as Reducer from "./reducer"

import { Effect, Schema } from "effect"
import { LLMError, type LLMClientService, type Model } from "@opencode-ai/llm"
import { OrchestratorStructured } from "./structured"
import { WorkerExecutor } from "./worker"
import { OrchestratorObserver } from "./observer"
import { LlmReport } from "./llm-report"

export const Reduction = Schema.Struct({ summary: Schema.String })
export type Reduction = typeof Reduction.Type

export const SYSTEM =
  "You consolidate subtask results into a single concise, factual summary of what was accomplished and the current state of the task. Subtasks marked `partial` were cut off before finishing but their findings are real — use them, and say what remains unknown rather than dropping them. Cite file paths exactly as they appear in the subtask results; never reconstruct a path or module name from a class or symbol name. If no subtask established a fact, say so instead of inferring it."

export const buildPrompt = (input: {
  readonly task: string
  readonly results: ReadonlyArray<WorkerExecutor.WorkerResult>
}): string =>
  [
    `Task:\n${input.task}`,
    `Subtask results:`,
    ...input.results.map((r) => `- [${r.subtaskId}] (${r.status}) ${r.result}`),
    `Produce a consolidated summary.`,
  ].join("\n")

export const reduce = (input: {
  readonly model: Model
  readonly task: string
  readonly results: ReadonlyArray<WorkerExecutor.WorkerResult>
  readonly retries?: number
  readonly iteration?: number
  readonly observer?: OrchestratorObserver.Interface
}): Effect.Effect<Reduction, LLMError, LLMClientService> => {
  const observer = input.observer ?? OrchestratorObserver.noop
  return OrchestratorStructured.object({
    model: input.model,
    schema: Reduction,
    system: SYSTEM,
    prompt: buildPrompt(input),
    retries: input.retries,
    reporter: LlmReport.reporterFor(observer, { role: "reducer", model: input.model, iteration: input.iteration }),
  })
}
