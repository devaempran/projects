export * as OrchestratorObserver from "./observer"

import { Effect } from "effect"

/**
 * Emit port for observing the orchestrator flow as it runs. Kept as a plain set
 * of `Effect<void>` callbacks so the orchestrator pipeline stays decoupled from
 * the event bus — the runner seam wires these to `EventV2.publish`. All methods
 * default to no-ops (see {@link noop}) so orchestration behavior is unchanged
 * when nothing is observing.
 */
export interface Interface {
  readonly planStarted: (data: { readonly task: string }) => Effect.Effect<void>
  readonly planned: (data: {
    readonly subtasks: ReadonlyArray<{
      readonly id: string
      readonly description: string
      readonly dependsOn: ReadonlyArray<string>
      /** The planner's own step estimate for this subtask, before clamping. */
      readonly estimatedSteps?: number
    }>
  }) => Effect.Effect<void>
  readonly iterationStarted: (data: {
    readonly iteration: number
    readonly maxIterations: number
  }) => Effect.Effect<void>
  /**
   * The pending execution queue changed: a subtask was popped to run, or a `decompose`
   * pushed children. `queue` is in pop order (next to run first), so a client can render the
   * real DFS stack instead of guessing an order from statuses. Emitted on every change
   * rather than diffed, because the whole queue is small and a mid-run client attaching to a
   * non-durable stream needs a full snapshot, not a delta it has no base for.
   */
  readonly queueChanged: (data: {
    readonly queue: ReadonlyArray<{
      readonly id: string
      readonly description: string
      readonly depth: number
      readonly parentId?: string
    }>
    readonly active?: string
    readonly completed: number
  }) => Effect.Effect<void>
  readonly subtaskStarted: (data: {
    readonly subtaskId: string
    readonly description: string
    // Set only for a child node minted by a `decompose` call, so a client attaching
    // mid-run can place the node correctly in the tree without waiting for a
    // `subtaskDecomposed` event it may have missed.
    readonly parentId?: string
    readonly depth?: number
    /** Soft step budget this subtask starts with, after clamping. */
    readonly budget?: number
    /** Absolute cap the budget can be extended to. */
    readonly hardCeiling?: number
    /** True when `budget` came from a model-authored estimate rather than the config default. */
    readonly estimated?: boolean
  }) => Effect.Effect<void>
  // Emitted when a worker chose to split its subtask; the parent makes no further LLM
  // calls and produces no result of its own.
  readonly subtaskDecomposed: (data: {
    readonly subtaskId: string
    readonly children: ReadonlyArray<{
      readonly id: string
      readonly description: string
      readonly depth: number
      readonly estimatedSteps?: number
    }>
  }) => Effect.Effect<void>
  readonly workerStep: (data: {
    readonly subtaskId: string
    readonly step: number
    readonly contextPacket: string
    /** The budget this step counts against, so the view can show "step 3 of 8" live. */
    readonly budget?: number
    readonly extensions?: number
  }) => Effect.Effect<void>
  /**
   * `noProgressLimit` consecutive steps produced no new information, so the subtask is being
   * cut off regardless of remaining budget. Surfaced because it is the audit signal that
   * matters most when tuning the limits: a run full of these means the model is looping, not
   * that the budget was too small.
   */
  readonly noProgressDetected: (data: {
    readonly subtaskId: string
    readonly stalledSteps: number
    readonly step: number
  }) => Effect.Effect<void>
  /** The worker hit a forced stop and is being given its one wrap-up call. `extendable` says
   *  whether `request_steps` was offered — i.e. whether it had earned an extension. */
  readonly checkpointReached: (data: {
    readonly subtaskId: string
    readonly reason: "budget-exhausted" | "no-progress" | "ceiling-reached"
    readonly step: number
    readonly budget: number
    readonly extendable: boolean
  }) => Effect.Effect<void>
  /** A `request_steps` call was granted. */
  readonly stepsExtended: (data: {
    readonly subtaskId: string
    readonly granted: number
    readonly budget: number
    readonly extensions: number
    readonly reason: string
  }) => Effect.Effect<void>
  readonly observation: (data: {
    readonly subtaskId: string
    readonly tool: string
    readonly output: string
  }) => Effect.Effect<void>
  readonly subtaskFinished: (data: {
    readonly subtaskId: string
    // `partial` is a subtask that was cut off but salvaged real findings — see
    // `WorkerExecutor.WorkerResult`.
    readonly status: "done" | "failed" | "partial"
    readonly result: string
    readonly steps?: { readonly used: number; readonly budget: number; readonly extensions: number }
  }) => Effect.Effect<void>
  readonly reduced: (data: { readonly iteration: number; readonly summary: string }) => Effect.Effect<void>
  readonly verified: (data: {
    readonly iteration: number
    readonly complete: boolean
    readonly gaps: ReadonlyArray<string>
  }) => Effect.Effect<void>
  readonly finished: (data: {
    readonly status: "complete" | "failed"
    readonly iterations: number
  }) => Effect.Effect<void>
  readonly llmCallStarted: (data: {
    readonly role: "planner" | "worker" | "reducer" | "verifier"
    readonly subtaskId?: string
    readonly step?: number
    readonly iteration?: number
    readonly attempt: number
    readonly model: string
    readonly system?: string
    readonly prompt: string
    readonly contextWindow?: number
    readonly estimatedInputTokens: number
  }) => Effect.Effect<void>
  readonly llmCallFinished: (data: {
    readonly role: "planner" | "worker" | "reducer" | "verifier"
    readonly subtaskId?: string
    readonly step?: number
    readonly iteration?: number
    readonly attempt: number
    readonly durationMs: number
    readonly output?: string
    readonly error?: string
    readonly finishReason?: string
    readonly usage?: {
      readonly input: number
      readonly output: number
      readonly reasoning: number
      readonly cacheRead: number
      readonly cacheWrite: number
      readonly total: number
    }
  }) => Effect.Effect<void>
}

export const noop: Interface = {
  planStarted: () => Effect.void,
  planned: () => Effect.void,
  iterationStarted: () => Effect.void,
  queueChanged: () => Effect.void,
  subtaskStarted: () => Effect.void,
  subtaskDecomposed: () => Effect.void,
  workerStep: () => Effect.void,
  noProgressDetected: () => Effect.void,
  checkpointReached: () => Effect.void,
  stepsExtended: () => Effect.void,
  observation: () => Effect.void,
  subtaskFinished: () => Effect.void,
  reduced: () => Effect.void,
  verified: () => Effect.void,
  finished: () => Effect.void,
  llmCallStarted: () => Effect.void,
  llmCallFinished: () => Effect.void,
}
