import { describe, expect, test } from "bun:test"
import { Schema } from "effect"
import { TaskIntake } from "@opencode-ai/core/session/orchestrator/intake"

describe("TaskIntake", () => {
  test("fromPrompt trims the raw prompt", () => {
    expect(TaskIntake.fromPrompt("  build a todo app \n")).toEqual({ task: "build a todo app" })
  })

  test("fromPrompt output decodes as a valid TaskSpec", () => {
    const spec = TaskIntake.fromPrompt("  build a todo app \n")
    expect(Schema.decodeUnknownSync(TaskIntake.TaskSpec)(spec)).toEqual({ task: "build a todo app" })
  })
})
