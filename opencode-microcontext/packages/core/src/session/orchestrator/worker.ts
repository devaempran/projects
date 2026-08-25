export * as WorkerExecutor from "./worker"

import { Effect, Schema } from "effect"
import { LLMError, Tool, toDefinitions, type LLMClientService, type Model } from "@opencode-ai/llm"
import { OrchestratorStructured } from "./structured"
import { ContextBuilder } from "./context-builder"
import { OrchestratorObserver } from "./observer"
import { LlmReport } from "./llm-report"
import { WorkerBudget } from "./budget"
import { Findings } from "./findings"

const FINISH_TOOL_NAME = "finish"
const DECOMPOSE_TOOL_NAME = "decompose"
const REQUEST_STEPS_TOOL_NAME = "request_steps"
const MAX_DECOMPOSE_CHILDREN = 4
const MIN_DECOMPOSE_CHILDREN = 2

const FINISH_TOOL_DESCRIPTION =
  'Call this when the subtask is complete or cannot proceed. Set `result` to a concise summary. Use `status: "done"` once you have an answer -- a confirmed negative result (e.g. "no LICENSE file exists in this repo") is a complete, valid answer, not a reason to keep searching. Use `status: "failed"` only when you could not investigate enough to reach any conclusion.'

const DECOMPOSE_TOOL_DESCRIPTION =
  "Call this ONLY when the subtask is genuinely too broad to investigate in the remaining steps. Split it into 2-4 smaller, independent, concrete slices that together fully cover it -- each slice runs with its own fresh context and its own step budget. Do not use it for a subtask a single tool call could satisfy."

const REQUEST_STEPS_TOOL_DESCRIPTION =
  "Call this ONLY at a step-budget checkpoint, and only when you are making real progress and a few more steps would let you finish. In `progressSoFar`, state concretely what you have already established -- it is recorded and used even if the request is denied. In `remainingWork`, state exactly what the extra steps will be spent on. A request is denied automatically if your recent steps produced no new information, so if you are stuck, call finish instead."

// Every catalog tool gets a permissive schema — the real ToolRegistry adapter that will
// carry per-tool parameter shapes lands in a later stage; `ToolRunner.run` already takes
// `input: unknown`, so this loses nothing today.
const PERMISSIVE_INPUT_SCHEMA = { type: "object" } as const

const finishTool = () =>
  Tool.make({
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

const decomposeTool = () =>
  Tool.make({
    description: DECOMPOSE_TOOL_DESCRIPTION,
    jsonSchema: {
      type: "object",
      properties: {
        subtasks: {
          type: "array",
          minItems: MIN_DECOMPOSE_CHILDREN,
          maxItems: MAX_DECOMPOSE_CHILDREN,
          items: {
            type: "object",
            properties: {
              description: { type: "string" },
              // Lets the parent size each slice it creates, the same way the planner sizes
              // its own subtasks. Optional: an omitted estimate falls back to the configured
              // default, so a model that ignores the field costs nothing.
              estimatedSteps: { type: "integer", minimum: 1, maximum: WorkerBudget.DEFAULT_HARD_CEILING },
            },
            required: ["description"],
          },
        },
      },
      required: ["subtasks"],
    },
    execute: () => Effect.void,
  })

const requestStepsTool = (grantCap: number) =>
  Tool.make({
    description: REQUEST_STEPS_TOOL_DESCRIPTION,
    jsonSchema: {
      type: "object",
      properties: {
        additionalSteps: { type: "integer", minimum: 1, maximum: Math.max(1, grantCap) },
        progressSoFar: { type: "string" },
        remainingWork: { type: "string" },
      },
      required: ["additionalSteps", "progressSoFar", "remainingWork"],
    },
    execute: () => Effect.void,
  })

/** Build the real, directly-callable tool set for one working step: the task's tool catalog
 * plus `finish` and (when `canDecompose`) `decompose`. */
const buildToolDefinitions = (
  toolCatalog: ReadonlyArray<ContextBuilder.ToolCatalogEntry> | undefined,
  canDecompose: boolean,
) => {
  const tools: Record<string, ReturnType<typeof Tool.make>> = {}
  for (const entry of toolCatalog ?? []) {
    tools[entry.name] = Tool.make({
      description: entry.description,
      jsonSchema: entry.inputSchema ?? PERMISSIVE_INPUT_SCHEMA,
      execute: () => Effect.void,
    })
  }
  tools[FINISH_TOOL_NAME] = finishTool()
  if (canDecompose) tools[DECOMPOSE_TOOL_NAME] = decomposeTool()
  return toDefinitions(tools)
}

/** The working-step prompt's tool catalog: the task's tool catalog plus `finish` and (when
 *  `canDecompose`) `decompose` -- mirrors `buildToolDefinitions`, which builds the real,
 *  directly-callable set handed to the tool-calling layer. Without this, the prompt's
 *  "Available tools" list omits `finish`/`decompose` while the tool-calling layer offers them
 *  anyway, and the system prompt tells the model to call only what's "listed under Available
 *  tools" -- the same contradiction `checkpointCatalog` exists to avoid at the checkpoint step.
 *  A model that follows the prompt text literally (observed with local/small models more than
 *  frontier ones) never calls `decompose` if it never sees it named here. */
const workingCatalog = (
  toolCatalog: ReadonlyArray<ContextBuilder.ToolCatalogEntry> | undefined,
  canDecompose: boolean,
): ContextBuilder.ToolCatalogEntry[] => [
  ...(toolCatalog ?? []),
  { name: FINISH_TOOL_NAME, description: FINISH_TOOL_DESCRIPTION },
  ...(canDecompose ? [{ name: DECOMPOSE_TOOL_NAME, description: DECOMPOSE_TOOL_DESCRIPTION }] : []),
]

/**
 * The checkpoint step's tool set. Deliberately *narrower* than a working step: the model is
 * out of steps, so offering it the full catalog again would just invite another exploratory
 * call it cannot afford. `finish` is always present; `request_steps` only when an extension
 * has actually been earned, so the model is never shown a door that is already locked.
 */
const buildCheckpointDefinitions = (input: { readonly canExtend: boolean; readonly grantCap: number }) => {
  const tools: Record<string, ReturnType<typeof Tool.make>> = { [FINISH_TOOL_NAME]: finishTool() }
  if (input.canExtend) tools[REQUEST_STEPS_TOOL_NAME] = requestStepsTool(input.grantCap)
  return toDefinitions(tools)
}

export const DecomposeChild = Schema.Struct({
  description: Schema.String,
  estimatedSteps: Schema.Number.pipe(Schema.optional),
})
export type DecomposeChild = typeof DecomposeChild.Type

export const WorkerResult = Schema.Struct({
  subtaskId: Schema.String,
  // `partial` is new: a subtask that was cut off but did gather usable findings. It used to
  // be reported as a flat `failed` carrying only "Reached max steps (N) without finishing",
  // which threw the findings away and told the Reducer nothing. Keeping it distinct from
  // `failed` lets the Reducer weigh it as evidence and the Verifier see that the ground was
  // partly covered.
  status: Schema.Literals(["done", "failed", "partial", "decomposed"]),
  result: Schema.String,
  // present only when status === "decomposed": the child slices the model asked for.
  // Child ids are minted by the runner, never by the model.
  children: Schema.Array(DecomposeChild).pipe(Schema.optional),
  /** Step accounting for this subtask, for the live view and for post-run analysis. */
  steps: Schema.Struct({
    used: Schema.Number,
    budget: Schema.Number,
    extensions: Schema.Number,
  }).pipe(Schema.optional),
})
export type WorkerResult = typeof WorkerResult.Type

// Injected port for executing a tool call. The real ToolRegistry adapter lands in a later stage.
export interface ToolRunner {
  readonly run: (call: { readonly tool: string; readonly input: unknown }) => Effect.Effect<string>
}

export const SYSTEM =
  `You are a focused worker executing ONE subtask with a small context. Respond with ONLY a single tool call — never prose, and never describe a call in text. Call the tool directly through the tool-calling mechanism; never wrap it in text tags like "<function=...>" or "<tool_call>". At each step, call exactly one of the tools listed under "Available tools" in the prompt, with arguments that make progress on the subtask — never invent a tool name, even a plausible-sounding one. Prefer \`glob\`/\`grep\` over \`bash\` for finding files or text — \`bash\` results are not filtered against .gitignore/node_modules and can bury real results in noise. You have a limited step budget, shown each step; spend early steps gathering and the last step answering. If your last action produced no useful result, do not repeat the same call — try a different tool, pattern, or path. When you have an answer, or you have thoroughly confirmed something does not exist, or you cannot make further progress, call \`${FINISH_TOOL_NAME}\` and report that. Keep results concise.`

export interface RunInput {
  readonly model: Model
  readonly task: string
  readonly subtask: { readonly id: string; readonly description: string }
  readonly tools: ToolRunner
  readonly toolCatalog?: ReadonlyArray<ContextBuilder.ToolCatalogEntry>
  /** Default soft step budget when the subtask carries no estimate (config `maxStepsPerWorker`). */
  readonly maxSteps?: number
  /** Model-authored per-subtask step estimate, from the planner, verifier, or a parent's `decompose`. */
  readonly estimatedSteps?: number
  /** Floor a model-authored estimate is clamped up to (config `minStepsPerWorker`). */
  readonly minSteps?: number
  /** Absolute cap on total steps including granted extensions (config `hardStepCeiling`). */
  readonly hardStepCeiling?: number
  /** Successful `request_steps` grants allowed for this subtask (config `maxStepExtensions`). */
  readonly maxStepExtensions?: number
  /** Consecutive no-progress steps that abort the subtask early (config `noProgressLimit`). */
  readonly noProgressLimit?: number
  readonly observer?: OrchestratorObserver.Interface
  /** This node's nesting depth. Top-level (planner-produced) subtasks are depth 0. */
  readonly depth?: number
  /** Set when this subtask is a child minted by a parent's `decompose` call -- the
   * parent subtask's id, forwarded to `observer.subtaskStarted` so a client attaching
   * mid-run can place this node in the tree. */
  readonly parentId?: string
  /** A node at `depth >= maxDecomposeDepth` does not get `decompose` in its tool catalog at all. Default 1; 0 disables `decompose` entirely. */
  readonly maxDecomposeDepth?: number
  /** Set when this subtask is a child produced by a parent's `decompose` call — the parent's description, for the lineage line. */
  readonly parentContext?: string
}

export const run = (input: RunInput): Effect.Effect<WorkerResult, LLMError, LLMClientService> =>
  Effect.gen(function* () {
    const limits = WorkerBudget.resolveLimits({
      softBudget: input.maxSteps,
      minBudget: input.minSteps,
      hardCeiling: input.hardStepCeiling,
      maxExtensions: input.maxStepExtensions,
      noProgressLimit: input.noProgressLimit,
      estimatedSteps: input.estimatedSteps,
    })
    const observer = input.observer ?? OrchestratorObserver.noop
    const observations: ContextBuilder.Observation[] = []
    // Model-volunteered progress summaries (from `request_steps`). Kept separate from tool
    // observations so the salvage digest can lead with them — they are already condensed.
    const notes: string[] = []
    const audit = WorkerBudget.makeAudit()
    const canDecompose = (input.depth ?? 0) < (input.maxDecomposeDepth ?? 1)
    const toolDefinitions = buildToolDefinitions(input.toolCatalog, canDecompose)

    let budget = limits.softBudget
    let step = 0
    let extensions = 0
    // Carries the guardrail/stall nudge into the *next* step's prompt.
    let notice: string | undefined

    const stepsInfo = () => ({ used: step, budget, extensions })

    // `observer.subtaskFinished` reports the three terminal leaf statuses; `decomposed` is
    // terminal for the parent but reported separately (by the runner, via
    // `subtaskDecomposed`), so it never reaches here.
    const finish = (result: {
      status: "done" | "failed" | "partial"
      result: string
    }): Effect.Effect<WorkerResult> => {
      const value: WorkerResult = {
        subtaskId: input.subtask.id,
        status: result.status,
        result: result.result,
        steps: stepsInfo(),
      }
      return observer
        .subtaskFinished({
          subtaskId: input.subtask.id,
          status: result.status,
          result: result.result,
          steps: stepsInfo(),
        })
        .pipe(Effect.as(value))
    }

    /** Exit without a model-authored summary: salvage the observations so the Reducer still
     *  receives evidence rather than a bare cap message. */
    const salvage = (reason: string) => {
      const { result, hasFindings } = Findings.salvagedResult({ observations, notes, reason })
      return finish({ status: hasFindings ? "partial" : "failed", result })
    }

    const observe = (tool: string, output: string) =>
      Effect.gen(function* () {
        observations.push({ tool, output })
        yield* observer.observation({ subtaskId: input.subtask.id, tool, output })
      })

    yield* observer.subtaskStarted({
      subtaskId: input.subtask.id,
      description: input.subtask.description,
      parentId: input.parentId,
      depth: input.depth,
      budget,
      hardCeiling: limits.hardCeiling,
      estimated: limits.estimated,
    })

    // Set by the stall detector to break out of the working loop before the budget runs
    // out; `undefined` at the end of the loop means the budget was the binding constraint.
    let earlyExit: WorkerBudget.ExitReason | undefined

    while (true) {
      // ---- working steps -------------------------------------------------------------
      earlyExit = undefined
      while (step < budget) {
        step++
        const packet = ContextBuilder.build({
          task: input.task,
          subtask: input.subtask,
          observations,
          tools: workingCatalog(input.toolCatalog, canDecompose),
          parentContext: input.parentContext,
          budget: { step, total: budget },
          notice,
        })
        notice = undefined
        yield* observer.workerStep({
          subtaskId: input.subtask.id,
          step,
          contextPacket: packet,
          budget,
          extensions,
        })
        // A worker-side LLM.Error (e.g. the model exhausted its retries calling an unknown
        // tool, or never called a tool at all) is folded into a normal "failed" finish
        // decision here, rather than left to propagate — so one bad worker step ends this
        // subtask instead of crashing the whole orchestrator run (runner.ts still has other
        // subtasks/reducer/verifier to run).
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
          const text = finishInput.result ?? ""
          // A `failed` finish with no explanation is exactly the case that used to reach the
          // Reducer as an empty line. Attach the salvaged findings so the run keeps whatever
          // the worker did learn on its way to giving up.
          if (finishInput.status === "failed") {
            const { result, hasFindings } = Findings.salvagedResult({
              observations,
              notes,
              reason: text.length > 0 ? text : "The worker reported it could not complete this subtask.",
            })
            return yield* finish({ status: hasFindings ? "partial" : "failed", result })
          }
          return yield* finish({ status: "done", result: text })
        }

        if (decision.name === DECOMPOSE_TOOL_NAME) {
          const children = parseDecomposeChildren(decision.input)
          if (children.length < MIN_DECOMPOSE_CHILDREN) {
            // A malformed `decompose` participates in the same repeat auditing as a real
            // tool call — otherwise a model that keeps emitting the identical malformed call
            // gets the same bland rejection every step and burns its whole budget.
            const verdict = audit.record({ tool: DECOMPOSE_TOOL_NAME, input: decision.input }, "")
            const output =
              verdict.outcome === "repeat-call"
                ? `(rejected) This is the same decompose call you already made at step ${verdict.firstSeenAtStep}; it was rejected then and will be rejected again. Make a real tool call that makes progress, or call \`${FINISH_TOOL_NAME}\`.`
                : `(rejected) decompose needs ${MIN_DECOMPOSE_CHILDREN}-${MAX_DECOMPOSE_CHILDREN} subtasks, each with a non-empty description. Continue with a real tool call, or call \`${FINISH_TOOL_NAME}\`.`
            yield* observe(DECOMPOSE_TOOL_NAME, output)
            if (yield* isStalled()) {
              earlyExit = "no-progress"
              break
            }
            continue
          }
          return {
            subtaskId: input.subtask.id,
            status: "decomposed",
            result: `Decomposed into ${children.length} subtasks`,
            children,
            steps: stepsInfo(),
          } satisfies WorkerResult
        }

        // ---- real tool action ---------------------------------------------------------
        const call = { tool: decision.name, input: decision.input }
        // Re-running a call whose exact (tool, input) was already tried produces the same
        // result by definition, so skip the execution and spend the step nudging toward
        // something different. Unlike the previous single-slot guard this catches a repeat at
        // any distance, so an A→B→A→B oscillation trips on its third step instead of
        // consuming the whole budget in silence.
        const preview = audit.peek(call)
        const output =
          preview.outcome === "repeat-call"
            ? `(not re-run) You already made this exact call, with these exact arguments, at step ${preview.firstSeenAtStep} — it will produce the same result. Try a different tool, a different input, or call \`${FINISH_TOOL_NAME}\`.`
            : yield* input.tools.run(call)
        const verdict = audit.record(call, output)
        yield* observe(decision.name, output)
        if (verdict.outcome === "repeat-output")
          notice = `Note: that call returned the same result as an earlier one, so it added no new information. Change approach or call \`${FINISH_TOOL_NAME}\`.`
        else if (verdict.outcome === "empty")
          notice = `Note: that call returned nothing. Do not retry it as-is — change tool, pattern, or path, or call \`${FINISH_TOOL_NAME}\`.`

        if (yield* isStalled()) {
          earlyExit = "no-progress"
          break
        }
      }

      // ---- budget checkpoint ---------------------------------------------------------
      const outcome = yield* wrapUpOrSalvage(
        earlyExit ?? (budget >= limits.hardCeiling ? "ceiling-reached" : "budget-exhausted"),
      )
      if (outcome.kind === "result") return outcome.result
      budget = Math.min(limits.hardCeiling, budget + outcome.granted)
      extensions++
      audit.markExtensionGranted()
      notes.push(outcome.progressSoFar)
      notice = `Your step budget was extended by ${outcome.granted} to ${budget}. Spend them on: ${outcome.remainingWork}`
      yield* observer.stepsExtended({
        subtaskId: input.subtask.id,
        granted: outcome.granted,
        budget,
        extensions,
        reason: outcome.remainingWork,
      })
    }

    // ---- helpers -------------------------------------------------------------------

    /**
     * The step-count-independent loop detector, and the reason the hard ceiling can safely
     * be three times the old flat cap: `noProgressLimit` consecutive steps that produced no
     * new information end the subtask immediately, whatever its remaining budget. A stuck
     * worker now stops at step 3 rather than grinding to step 8, and only a worker that keeps
     * surfacing new information is allowed to keep spending.
     */
    function isStalled(): Effect.Effect<boolean> {
      return Effect.gen(function* () {
        const state = audit.state()
        if (state.stalled < limits.noProgressLimit) return false
        yield* observer.noProgressDetected({
          subtaskId: input.subtask.id,
          stalledSteps: state.stalled,
          step,
        })
        return true
      })
    }

    /**
     * The one exit ramp every forced stop funnels through. Gives the model a final call in
     * which it can either author its own summary (much better input for the Reducer than raw
     * tool output) or — when it has earned one — buy more steps. Anything else, including a
     * dead LLM, falls through to the deterministic salvage digest, which cannot fail.
     */
    function wrapUpOrSalvage(
      reason: WorkerBudget.ExitReason,
    ): Effect.Effect<
      | { readonly kind: "result"; readonly result: WorkerResult }
      | {
          readonly kind: "extended"
          readonly granted: number
          readonly progressSoFar: string
          readonly remainingWork: string
        },
      never,
      LLMClientService
    > {
      return Effect.gen(function* () {
        const state = audit.state()
        // A stalled worker is never extendable, even if it produced novel results earlier in
        // the run: `canExtend`'s `novelSinceGrant > 0` is a "did you earn this" check, and a
        // worker that has just spent `noProgressLimit` steps going in circles has not. This
        // is the invariant that makes the raised ceiling safe — a loop cannot buy steps.
        const extendable =
          reason === "budget-exhausted" &&
          WorkerBudget.canExtend({ audit: state, extensionsUsed: extensions, budget, limits })
        const grantCap = WorkerBudget.grantSize({
          requested: undefined,
          budget,
          hardCeiling: limits.hardCeiling,
        })
        const reasonText = WorkerBudget.exitReasonText(reason, { budget, stalled: state.stalled })
        const instruction = extendable
          ? `${reasonText} Call \`${FINISH_TOOL_NAME}\` now with everything you established, even if it is incomplete — a partial finding is far more useful than nothing. If and only if you are actively making progress and up to ${grantCap} more steps would let you finish, call \`${REQUEST_STEPS_TOOL_NAME}\` instead.`
          : `${reasonText} You cannot get more steps. Call \`${FINISH_TOOL_NAME}\` now and report everything you established, even if it is incomplete — a partial finding is far more useful than nothing.`

        yield* observer.checkpointReached({
          subtaskId: input.subtask.id,
          reason,
          step,
          budget,
          extendable,
        })

        const packet = ContextBuilder.build({
          task: input.task,
          subtask: input.subtask,
          observations,
          // The real catalog is withheld -- re-listing it here reliably tempts the model into
          // one more exploratory call it cannot afford -- but the packet must still describe
          // the tools it *can* call. Passing `undefined` rendered "Available tools: (none
          // available)" while the tool-calling layer was simultaneously offering `finish`, and
          // that contradiction lands on the single most important call of the subtask: the one
          // that decides whether any findings survive.
          tools: checkpointCatalog(extendable),
          parentContext: input.parentContext,
          notice: instruction,
        })
        const decision = yield* OrchestratorStructured.toolCall({
          model: input.model,
          tools: buildCheckpointDefinitions({ canExtend: extendable, grantCap }),
          system: SYSTEM,
          prompt: packet,
          reporter: LlmReport.reporterFor(observer, {
            role: "worker",
            model: input.model,
            subtaskId: input.subtask.id,
            step,
          }),
        }).pipe(Effect.catchTag("LLM.Error", () => Effect.succeed(undefined)))

        if (decision?.name === REQUEST_STEPS_TOOL_NAME && extendable) {
          const request = (decision.input ?? {}) as {
            additionalSteps?: number
            progressSoFar?: string
            remainingWork?: string
          }
          const granted = WorkerBudget.grantSize({
            requested: request.additionalSteps,
            budget,
            hardCeiling: limits.hardCeiling,
          })
          if (granted > 0)
            return {
              kind: "extended",
              granted,
              progressSoFar: typeof request.progressSoFar === "string" ? request.progressSoFar : "",
              remainingWork:
                typeof request.remainingWork === "string" && request.remainingWork.trim().length > 0
                  ? request.remainingWork
                  : "finishing the subtask",
            } as const
        }

        if (decision?.name === FINISH_TOOL_NAME) {
          const finishInput = (decision.input ?? {}) as { status?: string; result?: string }
          const text = (finishInput.result ?? "").trim()
          if (text.length > 0) {
            // `done` is respected here. Being forced to consolidate is not the same as being
            // incomplete: the common shape of this path is a worker that kept opening new
            // leads because nothing told it to stop, and produces a perfectly complete answer
            // the moment it is asked for one. Anything other than `done` becomes `partial`,
            // which is still a real contribution to the Reducer — unlike the bare
            // "Reached max steps" this replaces.
            return {
              kind: "result",
              result: yield* finish({ status: finishInput.status === "done" ? "done" : "partial", result: text }),
            } as const
          }
        }

        return { kind: "result", result: yield* salvage(reasonText) } as const
      })
    }
  })

/**
 * The tool listing rendered into a checkpoint packet: exactly the synthetic tools the
 * checkpoint's schema offers, and nothing from the task catalog. Kept in step with
 * `buildCheckpointDefinitions` -- the prompt text and the tool schema must agree, or the model
 * is told it has no tools while being required to call one.
 */
const checkpointCatalog = (extendable: boolean): ContextBuilder.ToolCatalogEntry[] => [
  { name: FINISH_TOOL_NAME, description: FINISH_TOOL_DESCRIPTION },
  ...(extendable ? [{ name: REQUEST_STEPS_TOOL_NAME, description: REQUEST_STEPS_TOOL_DESCRIPTION }] : []),
]

/** Parse and sanitize the children of a `decompose` call: non-empty descriptions only,
 *  truncated to the hard child cap, with each child's optional step estimate carried through. */
const parseDecomposeChildren = (raw: unknown): DecomposeChild[] => {
  const input = (raw ?? {}) as { subtasks?: Array<{ description?: string; estimatedSteps?: unknown }> }
  return (input.subtasks ?? [])
    .map((subtask) => ({
      description: typeof subtask?.description === "string" ? subtask.description.trim() : "",
      estimatedSteps:
        typeof subtask?.estimatedSteps === "number" && Number.isFinite(subtask.estimatedSteps)
          ? Math.max(1, Math.floor(subtask.estimatedSteps))
          : undefined,
    }))
    .filter((child) => child.description.length > 0)
    .slice(0, MAX_DECOMPOSE_CHILDREN)
}
