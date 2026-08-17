export * as Planner from "./planner"

import { Effect, Schema } from "effect"
import { LLMError, type LLMClientService, type Model } from "@opencode-ai/llm"
import { OrchestratorStructured } from "./structured"
import { OrchestratorObserver } from "./observer"
import { LlmReport } from "./llm-report"

export const PlanSubtask = Schema.Struct({
  id: Schema.String,
  description: Schema.String,
  // Small local models routinely omit an empty dependsOn instead of emitting `[]`.
  dependsOn: Schema.Array(Schema.String).pipe(Schema.withDecodingDefaultKey(Effect.succeed([]))),
})
export type PlanSubtask = typeof PlanSubtask.Type

export const Plan = Schema.Struct({ subtasks: Schema.Array(PlanSubtask) })
export type Plan = typeof Plan.Type

/**
 * Model-facing shape: numeric ids instead of strings. Ollama's grammar-constrained
 * decoder has been observed intermittently corrupting string id values (e.g. every id
 * decoded as `",1"`, `",2"` — a leading comma leaked into the string), which silently
 * breaks exact-string dependency matching downstream with no error. Numeric ids tighten
 * the grammar and avoid that failure mode. The model output is never used directly —
 * see `normalize` below, which reassigns canonical string ids (`s1..sN`) by array order.
 */
export const PlanSubtaskRaw = Schema.Struct({
  id: Schema.Number,
  description: Schema.String,
  dependsOn: Schema.Array(Schema.Number).pipe(Schema.withDecodingDefaultKey(Effect.succeed([]))),
})
export type PlanSubtaskRaw = typeof PlanSubtaskRaw.Type

export const PlanRaw = Schema.Struct({ subtasks: Schema.Array(PlanSubtaskRaw) })
export type PlanRaw = typeof PlanRaw.Type

// Atomicity line addresses diagnosed planner over-decomposition (test-loop/model-issues.md
// runs 30 & 32): the planner split one atomic write/edit call into 3 dependsOn-chained
// read/modify/save subtasks, producing a doubled comment and wasted subtasks. `write`,
// `edit`, and `apply_patch` already read, modify, and save in one call, so splitting them
// only adds noise.
export const SYSTEM =
  "You are a planner for a small-context coding agent. Decompose the task into the smallest set of independent, concrete subtasks. Give each subtask a numeric id (1, 2, 3, …), a one-sentence description, and list the ids it depends on in dependsOn (empty if none). Prefer few subtasks; avoid overlap. A single file creation or edit that one write, edit, or apply_patch call can accomplish is exactly one subtask — never split a file mutation into separate read/modify/save steps; those tools already read, modify, and save in one call."

/** Pure prompt builder — unit-testable without a model. */
export const buildPrompt = (task: string): string =>
  `Decompose the following task into subtasks.\n\n<task>\n${task}\n</task>`

/**
 * Reassign canonical string ids (`s1..sN`, by array order) over a raw model plan and
 * remap every `dependsOn` entry through the resulting id map. Dependencies that don't
 * resolve to a known raw id — a sign of decoder corruption or a model-invented id — are
 * dropped rather than propagated, since a silently wrong DAG is worse than a missing
 * edge. Duplicate dependencies (after remapping) are deduped.
 *
 * Pure and exported for unit testing independent of any model/Effect plumbing.
 */
export const normalize = (raw: PlanRaw): Plan => {
  const idOf = new Map<number, string>()
  raw.subtasks.forEach((subtask, index) => idOf.set(subtask.id, `s${index + 1}`))
  return {
    subtasks: raw.subtasks.map((subtask, index) => {
      const seen = new Set<string>()
      const dependsOn: string[] = []
      for (const dep of subtask.dependsOn) {
        const mapped = idOf.get(dep)
        if (mapped === undefined || seen.has(mapped)) continue
        seen.add(mapped)
        dependsOn.push(mapped)
      }
      return { id: `s${index + 1}`, description: subtask.description, dependsOn }
    }),
  }
}

/**
 * Describes why a raw plan was rejected, for logging/repair purposes. Undefined means
 * the raw plan is internally consistent (non-empty, unique ids, no dangling deps).
 */
export const validateRaw = (raw: PlanRaw): string | undefined => {
  if (raw.subtasks.length === 0) return "plan has no subtasks"
  const ids = raw.subtasks.map((s) => s.id)
  const idSet = new Set(ids)
  if (idSet.size !== ids.length) return `duplicate subtask ids: ${ids.join(", ")}`
  for (const subtask of raw.subtasks)
    for (const dep of subtask.dependsOn)
      if (!idSet.has(dep)) return `subtask ${subtask.id} depends on unknown id ${dep}`
  return undefined
}

/**
 * Call the model for a raw plan and validate it; retry once (total 2 attempts) when
 * validation fails. If the raw plan is still invalid after the retry, proceed
 * best-effort with whatever came back last — `normalize` already drops dangling deps —
 * and log a warning describing what was wrong instead of failing the run.
 */
const fetchRawPlan = (
  input: { readonly model: Model; readonly task: string; readonly maxTokens?: number; readonly retries?: number },
  observer: OrchestratorObserver.Interface,
): Effect.Effect<PlanRaw, LLMError, LLMClientService> =>
  Effect.gen(function* () {
    const attempts = 2
    let raw: PlanRaw
    let issue: string | undefined
    let attempt = 0
    do {
      attempt++
      raw = yield* OrchestratorStructured.object({
        model: input.model,
        schema: PlanRaw,
        system: SYSTEM,
        prompt: buildPrompt(input.task),
        maxTokens: input.maxTokens,
        retries: input.retries,
        reporter: LlmReport.reporterFor(observer, { role: "planner", model: input.model }),
      })
      issue = validateRaw(raw)
    } while (issue !== undefined && attempt < attempts)

    if (issue !== undefined)
      yield* Effect.logWarning("orchestrator planner: raw plan failed validation after retry; proceeding best-effort", {
        issue,
        raw,
      })

    return raw
  })

export const plan = (input: {
  readonly model: Model
  readonly task: string
  readonly maxTokens?: number
  readonly retries?: number
  readonly observer?: OrchestratorObserver.Interface
}): Effect.Effect<Plan, LLMError, LLMClientService> => {
  const observer = input.observer ?? OrchestratorObserver.noop
  return Effect.gen(function* () {
    const raw = yield* fetchRawPlan(input, observer)
    return normalize(raw)
  })
}
