import { describe, expect, test } from "bun:test"
import { Effect } from "effect"
import { Planner } from "@opencode-ai/core/session/orchestrator/planner"
import { fakeClient, fakeModel } from "./lib"

describe("Planner", () => {
  test("buildPrompt includes the task", () => {
    expect(Planner.buildPrompt("build a todo app")).toContain("build a todo app")
  })

  test("plan decodes the structured plan from the model, normalizing numeric ids to canonical string ids", async () => {
    const raw = {
      subtasks: [
        { id: 1, description: "make model", dependsOn: [] },
        { id: 2, description: "make ui", dependsOn: [1] },
      ],
    }
    const result = await Effect.runPromise(
      Planner.plan({ model: fakeModel, task: "x" }).pipe(Effect.provide(fakeClient([raw]))),
    )
    expect(result).toEqual({
      subtasks: [
        { id: "s1", description: "make model", dependsOn: [] },
        { id: "s2", description: "make ui", dependsOn: ["s1"] },
      ],
    })
  })

  test("plan defaults a subtask's dependsOn to [] when the model omits it", async () => {
    const result = await Effect.runPromise(
      Planner.plan({ model: fakeModel, task: "x" }).pipe(
        Effect.provide(fakeClient([{ subtasks: [{ id: 1, description: "make model" }] }])),
      ),
    )
    expect(result).toEqual({ subtasks: [{ id: "s1", description: "make model", dependsOn: [] }] })
  })

  test("plan drops a dangling dependency and proceeds best-effort after exhausting the retry", async () => {
    // Both attempts reference a numeric id (99) that doesn't exist in the plan — an
    // inconsistent raw plan should never throw; it should normalize best-effort,
    // dropping the dangling dep.
    const dangling = { subtasks: [{ id: 1, description: "make model", dependsOn: [99] }] }
    const result = await Effect.runPromise(
      Planner.plan({ model: fakeModel, task: "x" }).pipe(Effect.provide(fakeClient([dangling, dangling]))),
    )
    expect(result).toEqual({ subtasks: [{ id: "s1", description: "make model", dependsOn: [] }] })
  })

  test("plan retries once when the raw plan is invalid, then succeeds", async () => {
    const invalid = { subtasks: [{ id: 1, description: "make model", dependsOn: [99] }] }
    const valid = {
      subtasks: [
        { id: 1, description: "make model", dependsOn: [] },
        { id: 2, description: "make ui", dependsOn: [1] },
      ],
    }
    const result = await Effect.runPromise(
      Planner.plan({ model: fakeModel, task: "x" }).pipe(Effect.provide(fakeClient([invalid, valid]))),
    )
    expect(result).toEqual({
      subtasks: [
        { id: "s1", description: "make model", dependsOn: [] },
        { id: "s2", description: "make ui", dependsOn: ["s1"] },
      ],
    })
  })
})

describe("Planner.normalize", () => {
  test("reassigns canonical string ids by array order and remaps dependsOn", () => {
    const raw = {
      subtasks: [
        { id: 5, description: "a", dependsOn: [] },
        { id: 3, description: "b", dependsOn: [5] },
        { id: 7, description: "c", dependsOn: [5, 3] },
      ],
    }
    expect(Planner.normalize(raw)).toEqual({
      subtasks: [
        { id: "s1", description: "a", dependsOn: [] },
        { id: "s2", description: "b", dependsOn: ["s1"] },
        { id: "s3", description: "c", dependsOn: ["s1", "s2"] },
      ],
    })
  })

  test("drops dangling deps that don't reference a known raw id", () => {
    const raw = { subtasks: [{ id: 1, description: "a", dependsOn: [999] }] }
    expect(Planner.normalize(raw)).toEqual({ subtasks: [{ id: "s1", description: "a", dependsOn: [] }] })
  })

  test("dedupes dependsOn entries that map to the same canonical id", () => {
    const raw = {
      subtasks: [
        { id: 1, description: "a", dependsOn: [] },
        { id: 2, description: "b", dependsOn: [1, 1] },
      ],
    }
    expect(Planner.normalize(raw)).toEqual({
      subtasks: [
        { id: "s1", description: "a", dependsOn: [] },
        { id: "s2", description: "b", dependsOn: ["s1"] },
      ],
    })
  })
})

describe("Planner.validateRaw", () => {
  test("accepts a consistent raw plan", () => {
    const raw = { subtasks: [{ id: 1, description: "a", dependsOn: [] }] }
    expect(Planner.validateRaw(raw)).toBeUndefined()
  })

  test("rejects an empty plan", () => {
    expect(Planner.validateRaw({ subtasks: [] })).toContain("no subtasks")
  })

  test("rejects duplicate ids", () => {
    const raw = {
      subtasks: [
        { id: 1, description: "a", dependsOn: [] },
        { id: 1, description: "b", dependsOn: [] },
      ],
    }
    expect(Planner.validateRaw(raw)).toContain("duplicate")
  })

  test("rejects a dangling dependency", () => {
    const raw = { subtasks: [{ id: 1, description: "a", dependsOn: [42] }] }
    expect(Planner.validateRaw(raw)).toContain("unknown id 42")
  })
})
