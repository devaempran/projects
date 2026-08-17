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
    }>
  }) => Effect.Effect<void>
  readonly iterationStarted: (data: {
    readonly iteration: number
    readonly maxIterations: number
  }) => Effect.Effect<void>
  readonly subtaskStarted: (data: {
    readonly subtaskId: string
    readonly description: string
    // Set only for a child node minted by a `decompose` call, so a client attaching
    // mid-run can place the node correctly in the tree without waiting for a
    // `subtaskDecomposed` event it may have missed.
    readonly parentId?: string
    readonly depth?: number
  }) => Effect.Effect<void>
  // Emitted when a worker chose to split its subtask; the parent makes no further LLM
  // calls and produces no result of its own.
  readonly subtaskDecomposed: (data: {
    readonly subtaskId: string
    readonly children: ReadonlyArray<{ readonly id: string; readonly description: string; readonly depth: number }>
  }) => Effect.Effect<void>
  readonly workerStep: (data: {
    readonly subtaskId: string
    readonly step: number
    readonly contextPacket: string
  }) => Effect.Effect<void>
  readonly observation: (data: {
    readonly subtaskId: string
    readonly tool: string
    readonly output: string
  }) => Effect.Effect<void>
  readonly subtaskFinished: (data: {
    readonly subtaskId: string
    readonly status: "done" | "failed"
    readonly result: string
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
  subtaskStarted: () => Effect.void,
  subtaskDecomposed: () => Effect.void,
  workerStep: () => Effect.void,
  observation: () => Effect.void,
  subtaskFinished: () => Effect.void,
  reduced: () => Effect.void,
  verified: () => Effect.void,
  finished: () => Effect.void,
  llmCallStarted: () => Effect.void,
  llmCallFinished: () => Effect.void,
}
