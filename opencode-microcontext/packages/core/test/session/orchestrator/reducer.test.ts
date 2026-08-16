import { describe, expect, test } from "bun:test"
import { Effect } from "effect"
import { Reducer } from "@opencode-ai/core/session/orchestrator/reducer"
import { fakeClient, fakeModel } from "./lib"

describe("Reducer", () => {
  test("buildPrompt includes subtask id and result", () => {
    const prompt = Reducer.buildPrompt({
      task: "T",
      results: [{ subtaskId: "s1", status: "done", result: "r1" }],
    })
    expect(prompt).toContain("s1")
    expect(prompt).toContain("r1")
  })

  test("reduce decodes the structured summary from the model", async () => {
    const result = await Effect.runPromise(
      Reducer.reduce({ model: fakeModel, task: "T", results: [] }).pipe(
        Effect.provide(fakeClient([{ summary: "merged" }])),
      ),
    )
    expect(result).toEqual({ summary: "merged" })
  })
})
