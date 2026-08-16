export * as TaskIntake from "./intake"

import { Schema } from "effect"

export const TaskSpec = Schema.Struct({ task: Schema.String })
export type TaskSpec = typeof TaskSpec.Type

/** Normalize a raw user prompt into a TaskSpec. Deterministic; no model call. */
export const fromPrompt = (prompt: string): TaskSpec => ({ task: prompt.trim() })
