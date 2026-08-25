import { describe, expect, test } from "bun:test"
import { Findings } from "@opencode-ai/core/session/orchestrator/findings"

describe("Findings.digest", () => {
  test("returns nothing when there is nothing to salvage", () => {
    expect(Findings.digest({ observations: [] })).toBe("")
    expect(Findings.digest({ observations: [{ tool: "grep", output: "   " }] })).toBe("")
  })

  test("lists each tool result once, tagged with the tool that produced it", () => {
    const digest = Findings.digest({
      observations: [
        { tool: "glob", output: "src/index.ts" },
        { tool: "read", output: "export const main = () => {}" },
      ],
    })
    expect(digest).toContain("- glob: src/index.ts")
    expect(digest).toContain("- read: export const main = () => {}")
  })

  test("model-authored notes lead, ahead of raw tool output", () => {
    const digest = Findings.digest({
      observations: [{ tool: "read", output: "raw file text" }],
      notes: ["The entry point is src/index.ts"],
    })
    const lines = digest.split("\n")
    expect(lines[0]).toContain("worker note")
    expect(lines[0]).toContain("The entry point is src/index.ts")
    expect(lines[1]).toContain("raw file text")
  })

  test("drops the worker's own guardrail feedback -- it is bookkeeping, not evidence", () => {
    // Passing these through would present the model's own scolding to the Reducer as a finding.
    const digest = Findings.digest({
      observations: [
        { tool: "read", output: "real content" },
        { tool: "read", output: "(not re-run) You already made this exact call at step 1 — try something else." },
        { tool: "decompose", output: "(rejected) decompose needs 2-4 subtasks." },
      ],
    })
    expect(digest).toContain("real content")
    expect(digest).not.toContain("not re-run")
    expect(digest).not.toContain("rejected")
  })

  test("de-duplicates identical results", () => {
    const digest = Findings.digest({
      observations: [
        { tool: "grep", output: "No matches found" },
        { tool: "grep", output: "No matches found" },
      ],
    })
    expect(digest.split("\n")).toHaveLength(1)
  })

  test("clips an oversized single result rather than letting it blow the Reducer's context", () => {
    const digest = Findings.digest({ observations: [{ tool: "read", output: "x".repeat(10_000) }] })
    expect(digest.length).toBeLessThan(Findings.MAX_FINDING_CHARS + 100)
  })

  test("over the total budget it drops the OLDEST findings and says how many", () => {
    // The later observations are the ones the worker reached after narrowing its search, so
    // they are the more specific findings and the ones worth keeping.
    const observations = Array.from({ length: 40 }, (_, i) => ({
      tool: "read",
      output: `finding number ${i} ${"y".repeat(200)}`,
    }))
    const digest = Findings.digest({ observations })
    expect(digest).toContain("earlier finding")
    expect(digest).toContain("finding number 39")
    expect(digest).not.toContain("finding number 0 ")
    expect(digest.length).toBeLessThan(Findings.MAX_FINDINGS_TOTAL_CHARS + 200)
  })
})

describe("Findings.salvagedResult", () => {
  test("reports the stop reason and flags that findings exist", () => {
    const { result, hasFindings } = Findings.salvagedResult({
      reason: "Ran out of steps.",
      observations: [{ tool: "read", output: "the config lives in opencode.jsonc" }],
    })
    expect(hasFindings).toBe(true)
    expect(result).toContain("Ran out of steps.")
    expect(result).toContain("opencode.jsonc")
  })

  test("says so explicitly when nothing was gathered, so the Reducer isn't guessing", () => {
    const { result, hasFindings } = Findings.salvagedResult({ reason: "Ran out of steps.", observations: [] })
    expect(hasFindings).toBe(false)
    expect(result).toContain("No findings were gathered")
  })
})
