import { describe, expect, test } from "bun:test"
import { ContextBuilder } from "@opencode-ai/core/session/orchestrator/context-builder"

describe("ContextBuilder", () => {
  test("build with no observations", () => {
    const packet = ContextBuilder.build({
      task: "T",
      subtask: { id: "s1", description: "D" },
      observations: [],
    })
    expect(packet).toContain("T")
    expect(packet).toContain("s1")
    expect(packet).toContain("D")
    expect(packet).toContain("(none yet)")
  })

  test("build renders observations", () => {
    const packet = ContextBuilder.build({
      task: "T",
      subtask: { id: "s1", description: "D" },
      observations: [{ tool: "read", output: "abc" }],
    })
    expect(packet).toContain("read => abc")
  })

  test("build without a tool catalog says none are available", () => {
    const packet = ContextBuilder.build({
      task: "T",
      subtask: { id: "s1", description: "D" },
      observations: [],
    })
    expect(packet).toContain("(none available)")
  })

  test("build renders the tool catalog by exact name so models don't invent names", () => {
    const packet = ContextBuilder.build({
      task: "T",
      subtask: { id: "s1", description: "D" },
      observations: [],
      tools: [{ name: "read", description: "Read a file" }],
    })
    expect(packet).toContain("- read: Read a file")
  })

  test("build truncates a single oversized observation instead of embedding it whole", () => {
    const huge = "x".repeat(10_000)
    const packet = ContextBuilder.build({
      task: "T",
      subtask: { id: "s1", description: "D" },
      observations: [{ tool: "glob", output: huge }],
    })
    expect(packet.length).toBeLessThan(huge.length)
    expect(packet).toContain("truncated 6000 more characters")
  })

  test("build keeps small observations intact and untruncated", () => {
    const packet = ContextBuilder.build({
      task: "T",
      subtask: { id: "s1", description: "D" },
      observations: [{ tool: "read", output: "small output" }],
    })
    expect(packet).toContain("1. read => small output")
    expect(packet).not.toContain("truncated")
  })

  test("build drops the oldest observations once the total budget is exceeded, keeping the most recent in full", () => {
    // Each observation is under the per-entry cap on its own, but three together
    // exceed the total observations budget.
    const observations = [
      { tool: "glob", output: "a".repeat(4_000) },
      { tool: "glob", output: "b".repeat(4_000) },
      { tool: "glob", output: "c".repeat(4_000) },
    ]
    const packet = ContextBuilder.build({ task: "T", subtask: { id: "s1", description: "D" }, observations })
    expect(packet).toContain("earlier observation")
    expect(packet).not.toContain("a".repeat(4_000))
    expect(packet).toContain("c".repeat(4_000))
  })

  test("build never drops the most recent observation, even under heavy total pressure from many entries", () => {
    const observations = Array.from({ length: 6 }, (_, i) => ({ tool: "glob", output: `${i}`.repeat(4_000) }))
    const packet = ContextBuilder.build({ task: "T", subtask: { id: "s1", description: "D" }, observations })
    expect(packet).toContain("6. glob =>")
    expect(packet).toContain("5".repeat(100))
    expect(packet).not.toContain("0".repeat(100))
  })
})
