import { describe, expect, test } from "bun:test"
import { TaskGraph } from "@opencode-ai/core/session/orchestrator/task-graph"

describe("TaskGraph.order", () => {
  test("returns dependencies before dependents", () => {
    const ordered = TaskGraph.order([
      { id: "s2", description: "", dependsOn: ["s1"] },
      { id: "s1", description: "", dependsOn: [] },
    ])
    expect(ordered.map((s) => s.id)).toEqual(["s1", "s2"])
  })

  test("ignores unknown dependency ids", () => {
    const ordered = TaskGraph.order([{ id: "s1", description: "", dependsOn: ["ghost"] }])
    expect(ordered.map((s) => s.id)).toEqual(["s1"])
  })

  test("terminates on cycles and returns each node exactly once", () => {
    const ordered = TaskGraph.order([
      { id: "a", description: "", dependsOn: ["b"] },
      { id: "b", description: "", dependsOn: ["a"] },
    ])
    expect(ordered.length).toBe(2)
    expect(new Set(ordered.map((s) => s.id))).toEqual(new Set(["a", "b"]))
  })
})
