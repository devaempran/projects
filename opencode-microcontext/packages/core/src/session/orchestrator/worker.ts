export * as WorkerExecutor from "./worker"

import { Effect, Schema } from "effect"
import { LLMError, Tool, toDefinitions, type LLMClientService, type Model } from "@opencode-ai/llm"
import { OrchestratorStructured } from "./structured"
import { ContextBuilder } from "./context-builder"
import { OrchestratorObserver } from "./observer"
import { LlmReport } from "./llm-report"

const FINISH_TOOL_NAME = "finish"

const FINISH_TOOL_DESCRIPTION =
  'Call this when the subtask is complete or cannot proceed. Set `result` to a concise summary. Use `status: "done"` once you have an answer -- a confirmed negative result (e.g. "no LICENSE file exists in this repo") is a complete, valid answer, not a reason to keep searching. Use `status: "failed"` only when you could not investigate enough to reach any conclusion.'

// Every catalog tool gets a permissive schema — the real ToolRegistry adapter that will
// carry per-tool parameter shapes lands in a later stage; `ToolRunner.run` already takes
// `input: unknown`, so this loses nothing today.
const PERMISSIVE_INPUT_SCHEMA = { type: "object" } as const

/** Build the real, directly-callable tool set for one worker step: the task's tool catalog plus `finish`. */
const buildToolDefinitions = (toolCatalog: ReadonlyArray<ContextBuilder.ToolCatalogEntry> | undefined) => {
  const tools: Record<string, ReturnType<typeof Tool.make>> = {}
  for (const entry of toolCatalog ?? []) {
    tools[entry.name] = Tool.make({
      description: entry.description,
      jsonSchema: entry.inputSchema ?? PERMISSIVE_INPUT_SCHEMA,
      execute: () => Effect.void,
    })
  }
  tools[FINISH_TOOL_NAME] = Tool.make({
    description: FINISH_TOOL_DESCRIPTION,
    jsonSchema: {
      type: "object",
      properties: {
        status: { type: "string", enum: ["done", "failed"] },
        result: { type: "string" },
      },
      required: ["status", "result"],
    },
    execute: () => Effect.void,
  })
  return toDefinitions(tools)
}

export const WorkerResult = Schema.Struct({
  subtaskId: Schema.String,
  status: Schema.Literals(["done", "failed"]),
  result: Schema.String,
})
export type WorkerResult = typeof WorkerResult.Type

// Injected port for executing a tool call. The real ToolRegistry adapter lands in a later stage.
export interface ToolRunner {
  readonly run: (call: { readonly tool: string; readonly input: unknown }) => Effect.Effect<string>
}

/** Sort object keys (recursively) so two structurally-identical calls compare equal
 * regardless of the JSON key order the model happened to emit. */
const canonicalize = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (value !== null && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((acc, key) => {
        acc[key] = canonicalize((value as Record<string, unknown>)[key])
        return acc
      }, {})
  }
  return value
}

type Call = { readonly tool: string; readonly input: unknown }
const sameCall = (a: Call, b: Call): boolean =>
  a.tool === b.tool && JSON.stringify(canonicalize(a.input)) === JSON.stringify(canonicalize(b.input))

export const SYSTEM =
  `You are a focused worker executing ONE subtask with a small context. Respond with ONLY a single tool call — never prose, and never describe a call in text. Call the tool directly through the tool-calling mechanism; never wrap it in text tags like "<function=...>" or "<tool_call>". At each step, call exactly one of the tools listed under "Available tools" in the prompt, with arguments that make progress on the subtask — never invent a tool name, even a plausible-sounding one. Prefer \`glob\`/\`grep\` over \`bash\` for finding files or text — \`bash\` results are not filtered against .gitignore/node_modules and can bury real results in noise. If your last action produced no useful result, do not repeat the same call — try a different tool, pattern, or path. When you have an answer, or you have thoroughly confirmed something does not exist, or you cannot make further progress, call \`${FINISH_TOOL_NAME}\` and report that. Keep results concise.`

export interface RunInput {
  readonly model: Model
  readonly task: string
  readonly subtask: { readonly id: string; readonly description: string }
  readonly tools: ToolRunner
  readonly toolCatalog?: ReadonlyArray<ContextBuilder.ToolCatalogEntry>
  readonly maxSteps?: number
  readonly observer?: OrchestratorObserver.Interface
}

export const run = (input: RunInput): Effect.Effect<WorkerResult, LLMError, LLMClientService> =>
  Effect.gen(function* () {
    const maxSteps = input.maxSteps ?? 8
    const observer = input.observer ?? OrchestratorObserver.noop
    const observations: ContextBuilder.Observation[] = []
    const toolDefinitions = buildToolDefinitions(input.toolCatalog)
    let lastCall: Call | undefined
    const finish = (result: WorkerResult) =>
      observer
        .subtaskFinished({ subtaskId: result.subtaskId, status: result.status, result: result.result })
        .pipe(Effect.as(result))
    yield* observer.subtaskStarted({ subtaskId: input.subtask.id, description: input.subtask.description })
    for (let step = 1; step <= maxSteps; step++) {
      const packet = ContextBuilder.build({
        task: input.task,
        subtask: input.subtask,
        observations,
        tools: input.toolCatalog,
      })
      yield* observer.workerStep({ subtaskId: input.subtask.id, step, contextPacket: packet })
      // A worker-side LLM.Error (e.g. the model exhausted its retries calling an unknown
      // tool, or never called a tool at all) is folded into a normal "failed" finish
      // decision here, rather than left to propagate — mirrors the max-steps-exhausted
      // handling below, so one bad worker step ends this subtask instead of crashing the
      // whole orchestrator run (runner.ts still has other subtasks/reducer/verifier to run).
      const decision: OrchestratorStructured.ToolCallResult = yield* OrchestratorStructured.toolCall({
        model: input.model,
        tools: toolDefinitions,
        system: SYSTEM,
        prompt: packet,
        reporter: LlmReport.reporterFor(observer, {
          role: "worker",
          model: input.model,
          subtaskId: input.subtask.id,
          step,
        }),
      }).pipe(
        Effect.catchTag("LLM.Error", (error) =>
          Effect.succeed({
            name: FINISH_TOOL_NAME,
            input: { status: "failed", result: `LLM call failed: ${error.message}` },
          }),
        ),
      )
      if (decision.name === FINISH_TOOL_NAME) {
        const finishInput = (decision.input ?? {}) as { status?: string; result?: string }
        return yield* finish({
          subtaskId: input.subtask.id,
          status: finishInput.status === "failed" ? "failed" : "done",
          result: finishInput.result ?? "",
        })
      }
      // tool action
      const tool = decision.name
      const call: Call = { tool, input: decision.input }
      // The model repeating the exact same (tool, input) it just tried is a common failure
      // mode with this local model (see test-loop/model-issues.md, run 16's s3: 8 of 8 steps
      // were the byte-identical call) — it would produce the same result again, so skip
      // re-running the tool and spend the step nudging toward something different instead.
      const isRepeat = lastCall !== undefined && sameCall(lastCall, call)
      lastCall = call
      const output = isRepeat
        ? "(not re-run) This is the exact same tool call, with the exact same arguments, as your previous step -- it will produce the same result. Try a different tool, a different input, or call `finish` if you cannot make progress."
        : yield* input.tools.run({ tool, input: decision.input })
      observations.push({ tool, output })
      yield* observer.observation({ subtaskId: input.subtask.id, tool, output })
    }
    return yield* finish({
      subtaskId: input.subtask.id,
      status: "failed",
      result: `Reached max steps (${maxSteps}) without finishing`,
    })
  })
