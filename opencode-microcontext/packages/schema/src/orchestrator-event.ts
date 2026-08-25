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
  parentId: Schema.String.pipe(Schema.optional),
  depth: NonNegativeInt.pipe(Schema.optional),
  // The step estimate whoever created this subtask (planner, verifier, or a parent's
  // `decompose`) assigned to it, before clamping. Shown in the live view so an
  // under/over-estimating planner is visible rather than inferred from step counts.
  estimatedSteps: NonNegativeInt.pipe(Schema.optional),
}).annotate({ identifier: "session.next.orchestrator.subtask" })

// One entry in the pending execution queue. Unlike SubtaskInfo this carries no dependsOn:
// by the time a node is queued its dependencies are already ordered into the queue itself.
const QueuedSubtask = Schema.Struct({
  id: Schema.String,
  description: Schema.String,
  depth: NonNegativeInt,
  parentId: Schema.String.pipe(Schema.optional),
}).annotate({ identifier: "session.next.orchestrator.queued-subtask" })

// Step accounting for a finished subtask: what it used, what it was budgeted, and how many
// extensions it earned along the way.
const StepUsage = Schema.Struct({
  used: NonNegativeInt,
  budget: NonNegativeInt,
  extensions: NonNegativeInt,
}).annotate({ identifier: "session.next.orchestrator.step-usage" })

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

// The pending execution queue changed -- a node was popped to run, a `decompose` pushed
// children, or a node reached a terminal state. Carries the whole queue in pop order rather
// than a delta: the stream is non-durable, so a client attaching mid-run has no base state to
// apply a delta to, and the queue is small enough that a full snapshot is cheaper than the
// bookkeeping to make deltas correct.
export const QueueChanged = Event.define({
  type: "session.next.orchestrator.queue.changed",
  schema: {
    ...Base,
    queue: Schema.Array(QueuedSubtask),
    // The subtask that was just popped and is now executing, when the change was a pop.
    active: Schema.String.pipe(Schema.optional),
    completed: NonNegativeInt,
  },
})
export type QueueChanged = typeof QueueChanged.Type

export const SubtaskStarted = Event.define({
  type: "session.next.orchestrator.subtask.started",
  schema: {
    ...Base,
    subtaskId: Schema.String,
    description: Schema.String,
    parentId: Schema.String.pipe(Schema.optional),
    depth: NonNegativeInt.pipe(Schema.optional),
    // Resolved soft step budget, the ceiling it can be extended to, and whether the budget
    // came from a model estimate or the config default.
    budget: NonNegativeInt.pipe(Schema.optional),
    hardCeiling: NonNegativeInt.pipe(Schema.optional),
    estimated: Schema.Boolean.pipe(Schema.optional),
  },
})
export type SubtaskStarted = typeof SubtaskStarted.Type

// Fired when a worker chose to decompose its subtask into children instead of calling a
// tool or `finish`; the parent contributes no result to the reducer.
export const SubtaskDecomposed = Event.define({
  type: "session.next.orchestrator.subtask.decomposed",
  schema: { ...Base, subtaskId: Schema.String, children: Schema.Array(SubtaskInfo) },
})
export type SubtaskDecomposed = typeof SubtaskDecomposed.Type

// The freshly built minimal context packet handed to the worker for one step —
// this is the "context put for each step" surfaced in the live view.
export const WorkerStep = Event.define({
  type: "session.next.orchestrator.worker.step",
  schema: {
    ...Base,
    subtaskId: Schema.String,
    step: NonNegativeInt,
    contextPacket: Schema.String,
    budget: NonNegativeInt.pipe(Schema.optional),
    extensions: NonNegativeInt.pipe(Schema.optional),
  },
})
export type WorkerStep = typeof WorkerStep.Type

// Consecutive steps produced no new information, so the subtask is being cut off regardless
// of its remaining budget. This is the loop-detection signal: a run full of these means the
// model is going in circles, not that the budget was too small.
export const NoProgressDetected = Event.define({
  type: "session.next.orchestrator.no-progress",
  schema: { ...Base, subtaskId: Schema.String, stalledSteps: NonNegativeInt, step: NonNegativeInt },
})
export type NoProgressDetected = typeof NoProgressDetected.Type

// The worker hit a forced stop and is getting its one wrap-up call. `extendable` records
// whether `request_steps` was offered, i.e. whether the progress gate let it ask for more.
export const CheckpointReached = Event.define({
  type: "session.next.orchestrator.checkpoint",
  schema: {
    ...Base,
    subtaskId: Schema.String,
    reason: Schema.Literals(["budget-exhausted", "no-progress", "ceiling-reached"]),
    step: NonNegativeInt,
    budget: NonNegativeInt,
    extendable: Schema.Boolean,
  },
})
export type CheckpointReached = typeof CheckpointReached.Type

// A worker's `request_steps` call was granted.
export const StepsExtended = Event.define({
  type: "session.next.orchestrator.steps.extended",
  schema: {
    ...Base,
    subtaskId: Schema.String,
    granted: NonNegativeInt,
    budget: NonNegativeInt,
    extensions: NonNegativeInt,
    reason: Schema.String,
  },
})
export type StepsExtended = typeof StepsExtended.Type

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
    // `partial` is a subtask cut off before finishing that still salvaged real findings.
    status: Schema.Literals(["done", "failed", "partial"]),
    result: Schema.String,
    steps: StepUsage.pipe(Schema.optional),
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
  QueueChanged,
  SubtaskStarted,
  SubtaskDecomposed,
  WorkerStep,
  NoProgressDetected,
  CheckpointReached,
  StepsExtended,
  Observation,
  SubtaskFinished,
  Reduced,
  Verified,
  Finished,
  LlmCallStarted,
  LlmCallFinished,
)
