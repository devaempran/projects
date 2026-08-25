export * as ConfigExperimental from "./experimental"

import { Schema } from "effect"
import { Catalog } from "../catalog"
import { Policy as PolicyV2 } from "../policy"

// Each core domain exports the policy actions it supports. Adding an action to
// this union makes it valid in authored config while keeping Policy generic.
export const PolicyAction = Schema.Union([Catalog.PolicyActions])

export class Policy extends Schema.Class<Policy>("ConfigV2.Experimental.Policy")({
  ...PolicyV2.Info.fields,
  action: PolicyAction,
}) {}

export class Orchestrator extends Schema.Class<Orchestrator>("ConfigV2.Experimental.Orchestrator")({
  enabled: Schema.Boolean.pipe(Schema.optional),
  maxIterations: Schema.Number.pipe(Schema.optional),
  // ---- worker step budget ------------------------------------------------------------
  // The budget is three layers, not one number (see `session/orchestrator/budget.ts`):
  // a soft budget the planner estimates per subtask and the worker can ask to extend, a
  // progress gate that only grants an extension to a worker producing new information, and
  // a flat hard ceiling nothing can raise. Setting `hardStepCeiling` equal to
  // `maxStepsPerWorker` with `maxStepExtensions: 0` reproduces the old fixed-cap behaviour.
  //
  // Default soft budget, used for a subtask whose `estimatedSteps` the planner omitted.
  // Default 8 — the value that used to be the flat cap for every subtask.
  maxStepsPerWorker: Schema.Number.pipe(Schema.optional),
  // Floor a model-authored `estimatedSteps` is clamped up to, so a planner that estimates
  // `1` for a real subtask can't strand a worker with too few steps to read one file and
  // report on it. Default 3.
  minStepsPerWorker: Schema.Number.pipe(Schema.optional),
  // Absolute per-subtask step cap, extensions included. This is the actual infinite-loop
  // backstop; every other bound is advisory. Default 24.
  hardStepCeiling: Schema.Number.pipe(Schema.optional),
  // How many times one worker may successfully call `request_steps`. Each grant is at most 8
  // steps and is refused unless the worker produced new information since its last grant.
  // Default 2; `0` disables worker-requested extensions entirely.
  maxStepExtensions: Schema.Number.pipe(Schema.optional),
  // Consecutive steps producing no new information (a repeated call, a repeated result, or an
  // empty result) before a subtask is cut off regardless of remaining budget. This is what
  // makes a raised ceiling safe, and it terminates a looping worker sooner than the old flat
  // cap did. Default 3.
  noProgressLimit: Schema.Number.pipe(Schema.optional),
  // Max recursion depth for worker self-decomposition (the `decompose` tool). A node at
  // `depth >= maxDecomposeDepth` does not get `decompose` in its tool catalog at all; `0`
  // disables the `decompose` tool entirely. Default is `1`.
  maxDecomposeDepth: Schema.Number.pipe(Schema.optional),
}) {}

export class Experimental extends Schema.Class<Experimental>("ConfigV2.Experimental")({
  policies: Policy.pipe(Schema.Array, Schema.optional),
  orchestrator: Orchestrator.pipe(Schema.optional),
}) {}
