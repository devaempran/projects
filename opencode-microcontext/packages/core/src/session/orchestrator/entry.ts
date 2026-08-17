export * as SessionOrchestrator from "./entry"

import { Effect } from "effect"
import { LLMError, type LLMClientService, type Model } from "@opencode-ai/llm"
import { AgentV2 } from "../../agent"
import { SessionMessage } from "../message"
import { SessionSchema } from "../schema"
import { ToolRegistry } from "../../tool/registry"
import { OrchestratorState } from "./state"
import { OrchestratorRunner } from "./runner"
import { OrchestratorToolRunner } from "./tool-runner"
import { OrchestratorObserver } from "./observer"

export { OrchestratorObserver }

// Tools a worker subtask is never offered, even if the caller's permission ruleset would
// otherwise allow them. `todowrite` lets the model mark its own internal todo item
// "completed" and stop there -- it reliably confuses that with calling `finish` (see
// test-loop/model-issues.md, runs 14 & 18: 5 occurrences across 2 runs, in some cases after
// already finding the answer). A worker executes one bounded subtask with no need for its
// own todo list, so excluding it here removes the confusion at the schema level -- the tool
// is simply not in what the model's tool-call is constrained to choose from -- rather than
// relying on a prompt instruction to keep the model from reaching for it.
const WORKER_EXCLUDED_TOOLS = new Set(["todowrite"])

// Re-export the Stage 0 decision helper so callers use one import.
export { shouldOrchestrate } from "./decision"

/** Extract the most recent user prompt text from a loaded session context. */
export const latestUserText = (context: ReadonlyArray<{ readonly type: string }>): string | undefined => {
  for (let i = context.length - 1; i >= 0; i--) {
    const message = context[i] as { type: string; text?: string }
    if (message.type === "user" && typeof message.text === "string") return message.text
  }
  return undefined
}

export const render = (result: OrchestratorRunner.RunResult): string => {
  const header =
    result.status === "complete"
      ? `Task complete after ${result.iterations} iteration(s).`
      : `Task incomplete after ${result.iterations} iteration(s).`
  const gaps =
    result.gaps.length > 0 ? `\n\nRemaining gaps:\n${result.gaps.map((g) => `- ${g}`).join("\n")}` : ""
  return `${header}\n\n${result.summary}${gaps}`
}

export interface RunLiveInput {
  readonly sessionID: SessionSchema.ID
  readonly agent: AgentV2.ID
  readonly model: Model
  readonly prompt: string
  readonly materialization: ToolRegistry.Materialization
  readonly assistantMessageID: SessionMessage.ID
  readonly emit: (text: string) => Effect.Effect<void>
  readonly maxIterations?: number
  readonly maxStepsPerWorker?: number
  readonly maxDecomposeDepth?: number
  readonly observer?: OrchestratorObserver.Interface
}

export const runLive = (
  input: RunLiveInput,
): Effect.Effect<OrchestratorRunner.RunResult, LLMError, LLMClientService | OrchestratorState.Service> =>
  Effect.gen(function* () {
    const tools = OrchestratorToolRunner.make({
      sessionID: input.sessionID,
      agent: input.agent,
      assistantMessageID: input.assistantMessageID,
      materialization: input.materialization,
    })
    const toolCatalog = input.materialization.definitions
      .filter((d) => !WORKER_EXCLUDED_TOOLS.has(d.name))
      .map((d) => ({
        name: d.name,
        description: d.description,
        inputSchema: d.inputSchema,
      }))
    const result = yield* OrchestratorRunner.run({
      sessionID: input.sessionID,
      model: input.model,
      prompt: input.prompt,
      tools,
      toolCatalog,
      maxIterations: input.maxIterations,
      maxStepsPerWorker: input.maxStepsPerWorker,
      maxDecomposeDepth: input.maxDecomposeDepth,
      observer: input.observer,
    })
    yield* input.emit(render(result))
    return result
  })
