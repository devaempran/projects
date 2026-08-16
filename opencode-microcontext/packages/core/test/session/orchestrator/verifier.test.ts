import { describe, expect, test } from "bun:test"
import { Effect } from "effect"
import { Verifier } from "@opencode-ai/core/session/orchestrator/verifier"
import { fakeClient, fakeModel } from "./lib"

describe("Verifier", () => {
  test("verify reports a complete verdict", async () => {
    const result = await Effect.runPromise(
      Verifier.verify({ model: fakeModel, task: "T", summary: "s" }).pipe(
        Effect.provide(fakeClient([{ complete: true, gaps: [], nextSubtasks: [] }])),
      ),
    )
    expect(result.complete).toBe(true)
  })

  test("verify reports an incomplete verdict with gaps and next subtasks", async () => {
    const result = await Effect.runPromise(
      Verifier.verify({ model: fakeModel, task: "T", summary: "s" }).pipe(
        Effect.provide(
          fakeClient([
            {
              complete: false,
              gaps: ["g"],
              nextSubtasks: [{ id: "s2", description: "d", dependsOn: [] }],
            },
          ]),
        ),
      ),
    )
    expect(result.complete).toBe(false)
    expect(result.gaps).toEqual(["g"])
    expect(result.nextSubtasks.length).toBe(1)
  })

  test("verify defaults gaps and nextSubtasks to [] when the model omits them", async () => {
    const result = await Effect.runPromise(
      Verifier.verify({ model: fakeModel, task: "T", summary: "s" }).pipe(
        Effect.provide(fakeClient([{ complete: true }])),
      ),
    )
    expect(result).toEqual({ complete: true, gaps: [], nextSubtasks: [] })
  })
})
