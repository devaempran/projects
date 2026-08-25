import { describe, expect, test } from "bun:test"
import { Effect } from "effect"
import type { LLMRequest } from "@opencode-ai/llm"
import { OrchestratorStructured } from "@opencode-ai/core/session/orchestrator/structured"
import { Verifier } from "@opencode-ai/core/session/orchestrator/verifier"
import { Planner } from "@opencode-ai/core/session/orchestrator/planner"
import { fakeClient, fakeModel } from "./lib"

describe("OrchestratorStructured.parseJsonString", () => {
  test("parses a JSON array or object held in a string", () => {
    expect(OrchestratorStructured.parseJsonString('[{"a":1}]')).toEqual([{ a: 1 }])
    expect(OrchestratorStructured.parseJsonString('  {"a":1}  ')).toEqual({ a: 1 })
  })

  test("leaves ordinary prose alone", () => {
    // The repair must not touch descriptions, summaries or results.
    expect(OrchestratorStructured.parseJsonString("Trace the first hop only.")).toBe("Trace the first hop only.")
    expect(OrchestratorStructured.parseJsonString("")).toBe("")
    expect(OrchestratorStructured.parseJsonString("123")).toBe("123")
  })

  test("returns the original string when it only looks like JSON", () => {
    // Handing the raw string back means the decode error names the real field instead of
    // surfacing a parse failure from inside the repair.
    expect(OrchestratorStructured.parseJsonString("[not json")).toBe("[not json")
  })

  test("passes non-strings through untouched", () => {
    const arr = [{ a: 1 }]
    expect(OrchestratorStructured.parseJsonString(arr)).toBe(arr)
    expect(OrchestratorStructured.parseJsonString(undefined)).toBe(undefined)
  })
})

describe("OrchestratorStructured.repairJsonStringKeys", () => {
  const repair = OrchestratorStructured.repairJsonStringKeys("nextSubtasks", "gaps")

  test("repairs only the named keys", () => {
    const raw = { complete: false, gaps: '["g1"]', nextSubtasks: '[{"id":"t1"}]', summary: '{"not":"touched"}' }
    expect(repair(raw)).toEqual({
      complete: false,
      gaps: ["g1"],
      nextSubtasks: [{ id: "t1" }],
      // Not in the key list, so it stays a string even though it parses as JSON.
      summary: '{"not":"touched"}',
    })
  })

  test("is identity when nothing needs repair", () => {
    const raw = { complete: true, gaps: [], nextSubtasks: [] }
    expect(repair(raw)).toBe(raw)
  })

  test("tolerates absent keys and non-objects", () => {
    expect(repair({ complete: true })).toEqual({ complete: true })
    expect(repair(null)).toBe(null)
    expect(repair([1, 2])).toEqual([1, 2])
  })
})

describe("Verifier: string-instead-of-array recovery", () => {
  test("decodes a verdict whose nextSubtasks arrived as a JSON string, without a retry", async () => {
    // The exact malformation qwen3.8:27b produced in llm-io 20260823T224539Z, which used to
    // fail the decode and cost a full extra verifier call to recover.
    const requests: Array<LLMRequest> = []
    const verdict = await Effect.runPromise(
      Verifier.verify({
        model: fakeModel,
        task: "T",
        summary: "S",
      }).pipe(
        Effect.provide(
          fakeClient(
            [
              {
                complete: false,
                gaps: ["g1"],
                nextSubtasks: JSON.stringify([
                  { id: "t1", description: "Trace only the first hop.", dependsOn: [], estimatedSteps: 6 },
                ]),
              },
            ],
            requests,
          ),
        ),
      ),
    )
    expect(verdict.complete).toBe(false)
    expect(verdict.nextSubtasks).toHaveLength(1)
    expect(verdict.nextSubtasks[0]!.id).toBe("t1")
    expect(verdict.nextSubtasks[0]!.estimatedSteps).toBe(6)
    // One request only: the repair happened before the decode, so no retry was needed.
    expect(requests).toHaveLength(1)
  })

  test("a stringified gaps array is recovered too", async () => {
    const verdict = await Effect.runPromise(
      Verifier.verify({ model: fakeModel, task: "T", summary: "S" }).pipe(
        Effect.provide(fakeClient([{ complete: false, gaps: '["only gap"]', nextSubtasks: [] }])),
      ),
    )
    expect(verdict.gaps).toEqual(["only gap"])
  })
})

describe("Planner: string-instead-of-array recovery", () => {
  test("decodes a plan whose subtasks arrived as a JSON string, without a retry", async () => {
    const requests: Array<LLMRequest> = []
    const plan = await Effect.runPromise(
      Planner.plan({ model: fakeModel, task: "T" }).pipe(
        Effect.provide(
          fakeClient(
            [{ subtasks: JSON.stringify([{ id: 1, description: "read one file", dependsOn: [], estimatedSteps: 3 }]) }],
            requests,
          ),
        ),
      ),
    )
    expect(plan.subtasks).toHaveLength(1)
    expect(plan.subtasks[0]!.id).toBe("s1")
    expect(plan.subtasks[0]!.estimatedSteps).toBe(3)
    expect(requests).toHaveLength(1)
  })
})

describe("no-synthesis-subtask instructions", () => {
  test("the planner is told not to create consolidation subtasks", () => {
    expect(Planner.SYSTEM).toContain("never create a subtask whose job is to synthesize")
    expect(Planner.SYSTEM).toContain("never mention another subtask by its id")
  })

  test("the verifier is told the same for its proposed subtasks", () => {
    expect(Verifier.SYSTEM).toContain("never propose a subtask that synthesizes")
    expect(Verifier.SYSTEM).toContain("never refer to another subtask by its id")
  })

  test("the verifier is told to narrow, not enlarge, a subtask that ran out of steps", () => {
    expect(Verifier.SYSTEM).toContain("NARROWER subtask, not a repeat of the same one with more steps")
  })
})
