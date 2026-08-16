export * as OrchestratorRunner from "./runner"

import { Effect } from "effect"
import { LLMError, type LLMClientService, type Model } from "@opencode-ai/llm"
import { SessionSchema } from "../schema"
import { OrchestratorState } from "./state"
import { OrchestratorSchema } from "./schema"
import { TaskIntake } from "./intake"
import { Planner } from "./planner"
import { TaskGraph } from "./task-graph"
import { WorkerExecutor } from "./worker"
import { Reducer } from "./reducer"
import { Verifier } from "./verifier"
import { OrchestratorObserver } from "./observer"

export interface RunInput {
  readonly sessionID: SessionSchema.ID
  readonly model: Model
  readonly prompt: string
  readonly tools: WorkerExecutor.ToolRunner
  readonly toolCatalog?: WorkerExecutor.RunInput["toolCatalog"]
  readonly maxIterations?: number
  readonly maxStepsPerWorker?: number
  readonly observer?: OrchestratorObserver.Interface
}

export interface RunResult {
  readonly status: OrchestratorSchema.Status
  readonly iterations: number
  readonly summary: string
  readonly gaps: ReadonlyArray<string>
}

export const run = (
  input: RunInput,
): Effect.Effect<RunResult, LLMError, LLMClientService | OrchestratorState.Service> =>
  Effect.gen(function* () {
    const state = yield* OrchestratorState.Service
    const observer = input.observer ?? OrchestratorObserver.noop
    const maxIterations = input.maxIterations ?? 3
    const spec = TaskIntake.fromPrompt(input.prompt)

    const persist = (data: {
      status: OrchestratorSchema.Status
      iteration: number
      subtasks: OrchestratorSchema.Subtask[]
      reduced?: string
      gaps: string[]
    }) =>
      state.set({
        sessionID: input.sessionID,
        status: data.status,
        iteration: data.iteration,
        maxIterations,
        data: { task: spec.task, subtasks: data.subtasks, reduced: data.reduced, gaps: data.gaps },
      })

    yield* persist({ status: "planning", iteration: 0, subtasks: [], gaps: [] })
    yield* observer.planStarted({ task: spec.task })
    const plan = yield* Planner.plan({ model: input.model, task: spec.task, observer })
    yield* observer.planned({
      subtasks: plan.subtasks.map((s) => ({ id: s.id, description: s.description, dependsOn: s.dependsOn })),
    })
    let pending = TaskGraph.order(plan.subtasks)
    let iteration = 0
    let summary = ""
    let gaps: string[] = []

    while (true) {
      iteration++
      yield* persist({
        status: "working",
        iteration,
        subtasks: pending.map((s) => ({
          id: s.id,
          description: s.description,
          status: "pending",
          dependsOn: s.dependsOn,
        })),
        gaps,
      })
      yield* observer.iterationStarted({ iteration, maxIterations })
      const results: WorkerExecutor.WorkerResult[] = []
      for (const subtask of pending) {
        const r = yield* WorkerExecutor.run({
          model: input.model,
          task: spec.task,
          subtask,
          tools: input.tools,
          toolCatalog: input.toolCatalog,
          maxSteps: input.maxStepsPerWorker,
          observer,
        })
        results.push(r)
      }
      const subtaskState: OrchestratorSchema.Subtask[] = pending.map((s) => {
        const r = results.find((x) => x.subtaskId === s.id)
        return {
          id: s.id,
          description: s.description,
          status: r ? r.status : "failed",
          dependsOn: s.dependsOn,
          result: r?.result,
        }
      })
      yield* persist({ status: "reducing", iteration, subtasks: subtaskState, gaps })
      const reduction = yield* Reducer.reduce({ model: input.model, task: spec.task, results, iteration, observer })
      summary = reduction.summary
      yield* observer.reduced({ iteration, summary })
      yield* persist({ status: "verifying", iteration, subtasks: subtaskState, reduced: summary, gaps })
      const verdict = yield* Verifier.verify({ model: input.model, task: spec.task, summary, iteration, observer })
      gaps = [...verdict.gaps]
      yield* observer.verified({ iteration, complete: verdict.complete, gaps })
      if (verdict.complete) {
        yield* persist({ status: "complete", iteration, subtasks: subtaskState, reduced: summary, gaps })
        yield* observer.finished({ status: "complete", iterations: iteration })
        return { status: "complete", iterations: iteration, summary, gaps }
      }
      if (iteration >= maxIterations) {
        yield* persist({ status: "failed", iteration, subtasks: subtaskState, reduced: summary, gaps })
        yield* observer.finished({ status: "failed", iterations: iteration })
        return { status: "failed", iterations: iteration, summary, gaps }
      }
      pending = TaskGraph.order(verdict.nextSubtasks)
    }
  })
