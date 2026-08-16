export * as OrchestratorEvent from "./orchestrator-event"

import { Schema } from "effect"
import { Event } from "./event"
import { DateTimeUtcFromMillis, NonNegativeInt } from "./schema"
import { SessionID } from "./session-id"

// Live (non-durable) events describing the small-context ReAct orchestrator flow.
// They are emitted only while an orchestrated run is in progress and stream to
// clients over the existing SSE endpoint; they are intentionally NOT durable, so
// a client that connects mid-run only sees events from that point forward. Durable
// orchestrator state remains in the `orchestrator_state` table (see core).

const Base = {
  timestamp: DateTimeUtcFromMillis,
  sessionID: SessionID,
}

const SubtaskInfo = Schema.Struct({
  id: Schema.String,
  description: Schema.String,
  dependsOn: Schema.Array(Schema.String),
}).annotate({ identifier: "session.next.orchestrator.subtask" })

const Role = Schema.Literals(["planner", "worker", "reducer", "verifier"])

export const PlanStarted = Event.define({
  type: "session.next.orchestrator.plan.started",
  schema: { ...Base, task: Schema.String },
})
export type PlanStarted = typeof PlanStarted.Type

export const Planned = Event.define({
  type: "session.next.orchestrator.planned",
  schema: { ...Base, subtasks: Schema.Array(SubtaskInfo) },
})
export type Planned = typeof Planned.Type

export const IterationStarted = Event.define({
  type: "session.next.orchestrator.iteration.started",
  schema: { ...Base, iteration: NonNegativeInt, maxIterations: NonNegativeInt },
})
export type IterationStarted = typeof IterationStarted.Type

export const SubtaskStarted = Event.define({
  type: "session.next.orchestrator.subtask.started",
  schema: { ...Base, subtaskId: Schema.String, description: Schema.String },
})
export type SubtaskStarted = typeof SubtaskStarted.Type

// The freshly built minimal context packet handed to the worker for one step —
// this is the "context put for each step" surfaced in the live view.
export const WorkerStep = Event.define({
  type: "session.next.orchestrator.worker.step",
  schema: { ...Base, subtaskId: Schema.String, step: NonNegativeInt, contextPacket: Schema.String },
})
export type WorkerStep = typeof WorkerStep.Type

export const Observation = Event.define({
  type: "session.next.orchestrator.observation",
  schema: { ...Base, subtaskId: Schema.String, tool: Schema.String, output: Schema.String },
})
export type Observation = typeof Observation.Type

export const SubtaskFinished = Event.define({
  type: "session.next.orchestrator.subtask.finished",
  schema: {
    ...Base,
    subtaskId: Schema.String,
    status: Schema.Literals(["done", "failed"]),
    result: Schema.String,
  },
})
export type SubtaskFinished = typeof SubtaskFinished.Type

export const Reduced = Event.define({
  type: "session.next.orchestrator.reduced",
  schema: { ...Base, iteration: NonNegativeInt, summary: Schema.String },
})
export type Reduced = typeof Reduced.Type

export const Verified = Event.define({
  type: "session.next.orchestrator.verified",
  schema: { ...Base, iteration: NonNegativeInt, complete: Schema.Boolean, gaps: Schema.Array(Schema.String) },
})
export type Verified = typeof Verified.Type

export const Finished = Event.define({
  type: "session.next.orchestrator.finished",
  schema: {
    ...Base,
    status: Schema.Literals(["complete", "failed"]),
    iterations: NonNegativeInt,
  },
})
export type Finished = typeof Finished.Type

// Fired immediately before an LLM call is dispatched for a given orchestrator role,
// carrying the exact prompt/system text and an up-front token estimate so the live
// view can show what's about to be sent without waiting for a response.
export const LlmCallStarted = Event.define({
  type: "session.next.orchestrator.llm.call.started",
  schema: {
    ...Base,
    role: Role,
    subtaskId: Schema.String.pipe(Schema.optional),
    step: NonNegativeInt.pipe(Schema.optional),
    iteration: NonNegativeInt.pipe(Schema.optional),
    attempt: NonNegativeInt,
    model: Schema.String,
    system: Schema.String.pipe(Schema.optional),
    prompt: Schema.String,
    contextWindow: NonNegativeInt.pipe(Schema.optional),
    estimatedInputTokens: NonNegativeInt,
  },
})
export type LlmCallStarted = typeof LlmCallStarted.Type

// Fired once an LLM call completes (successfully or not), carrying the output text
// and actual token usage so the live view can reconcile against the earlier estimate.
export const LlmCallFinished = Event.define({
  type: "session.next.orchestrator.llm.call.finished",
  schema: {
    ...Base,
    role: Role,
    subtaskId: Schema.String.pipe(Schema.optional),
    step: NonNegativeInt.pipe(Schema.optional),
    iteration: NonNegativeInt.pipe(Schema.optional),
    attempt: NonNegativeInt,
    durationMs: NonNegativeInt,
    output: Schema.String.pipe(Schema.optional),
    error: Schema.String.pipe(Schema.optional),
    finishReason: Schema.String.pipe(Schema.optional),
    usage: Schema.Struct({
      input: NonNegativeInt,
      output: NonNegativeInt,
      reasoning: NonNegativeInt,
      cacheRead: NonNegativeInt,
      cacheWrite: NonNegativeInt,
      total: NonNegativeInt,
    }).pipe(Schema.optional),
  },
})
export type LlmCallFinished = typeof LlmCallFinished.Type

export const Definitions = Event.inventory(
  PlanStarted,
  Planned,
  IterationStarted,
  SubtaskStarted,
  WorkerStep,
  Observation,
  SubtaskFinished,
  Reduced,
  Verified,
  Finished,
  LlmCallStarted,
  LlmCallFinished,
)
