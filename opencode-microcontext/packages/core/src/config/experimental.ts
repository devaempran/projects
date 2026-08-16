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
}) {}

export class Experimental extends Schema.Class<Experimental>("ConfigV2.Experimental")({
  policies: Policy.pipe(Schema.Array, Schema.optional),
  orchestrator: Orchestrator.pipe(Schema.optional),
}) {}
