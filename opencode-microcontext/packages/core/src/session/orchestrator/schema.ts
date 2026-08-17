export * as OrchestratorSchema from "./schema"

import { Schema } from "effect"

export const Status = Schema.Literals(["planning", "working", "reducing", "verifying", "complete", "failed"])
export type Status = typeof Status.Type

export const Subtask = Schema.Struct({
  id: Schema.String,
  description: Schema.String,
  status: Schema.Literals(["pending", "running", "done", "failed", "decomposed"]),
  dependsOn: Schema.Array(Schema.String),
  result: Schema.String.pipe(Schema.optional),
  // `parentId`/`depth` express *nesting* — a decomposed parent's children — which is
  // distinct from `dependsOn`'s DAG ordering. A child's `parentId` points at the subtask
  // that decomposed into it, and `depth` counts recursion levels (0 for planner-produced
  // top-level subtasks); neither participates in `TaskGraph.order()`.
  parentId: Schema.String.pipe(Schema.optional),
  depth: Schema.Number.pipe(Schema.optional),
})
export type Subtask = typeof Subtask.Type

export const Data = Schema.Struct({
  task: Schema.String,
  subtasks: Schema.Array(Subtask),
  reduced: Schema.String.pipe(Schema.optional),
  gaps: Schema.Array(Schema.String),
})
export type Data = typeof Data.Type
