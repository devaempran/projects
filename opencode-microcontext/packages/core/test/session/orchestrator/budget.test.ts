import { describe, expect, test } from "bun:test"
import { WorkerBudget } from "@opencode-ai/core/session/orchestrator/budget"

describe("WorkerBudget.resolveLimits", () => {
  test("falls back to the previous flat default when nothing is configured", () => {
    const limits = WorkerBudget.resolveLimits()
    expect(limits.softBudget).toBe(8)
    expect(limits.hardCeiling).toBe(24)
    expect(limits.maxExtensions).toBe(2)
    expect(limits.noProgressLimit).toBe(3)
    expect(limits.estimated).toBe(false)
  })

  test("a model estimate may exceed the configured default -- that is the point of estimating", () => {
    const limits = WorkerBudget.resolveLimits({ softBudget: 8, estimatedSteps: 14 })
    expect(limits.softBudget).toBe(14)
    expect(limits.estimated).toBe(true)
  })

  test("a model estimate is clamped up to the floor and down to the hard ceiling", () => {
    expect(WorkerBudget.resolveLimits({ minBudget: 3, estimatedSteps: 1 }).softBudget).toBe(3)
    expect(WorkerBudget.resolveLimits({ hardCeiling: 10, estimatedSteps: 99 }).softBudget).toBe(10)
  })

  test("the configured default is NOT raised to the estimate floor", () => {
    // `minStepsPerWorker` guards against a bad *model* estimate. An operator who deliberately
    // configures `maxStepsPerWorker: 2` means it, and silently overriding that would make the
    // knob a lie.
    const limits = WorkerBudget.resolveLimits({ softBudget: 2, minBudget: 5 })
    expect(limits.softBudget).toBe(2)
  })

  test("an inconsistent config cannot produce a ceiling below the soft budget", () => {
    // Otherwise the very first checkpoint would report "ceiling reached" at step 1.
    const limits = WorkerBudget.resolveLimits({ softBudget: 12, hardCeiling: 4 })
    expect(limits.hardCeiling).toBeGreaterThanOrEqual(limits.softBudget)
  })

  test("garbage and negative inputs degrade to the defaults rather than a zero budget", () => {
    const limits = WorkerBudget.resolveLimits({
      softBudget: -5,
      hardCeiling: Number.NaN,
      maxExtensions: -1,
      noProgressLimit: 0,
    })
    expect(limits.softBudget).toBe(1)
    expect(limits.hardCeiling).toBeGreaterThanOrEqual(1)
    expect(limits.maxExtensions).toBe(0)
    expect(limits.noProgressLimit).toBe(1)
  })

  test("hardCeiling == softBudget with maxExtensions 0 reproduces the old fixed cap", () => {
    const limits = WorkerBudget.resolveLimits({ softBudget: 8, hardCeiling: 8, maxExtensions: 0 })
    expect(limits.softBudget).toBe(8)
    expect(limits.hardCeiling).toBe(8)
    expect(WorkerBudget.canExtend({
      audit: { steps: 1, novel: 1, stalled: 0, worstStall: 0, novelSinceGrant: 1 },
      extensionsUsed: 0,
      budget: 8,
      limits,
    })).toBe(false)
  })
})

describe("WorkerBudget.grantSize", () => {
  test("honours a modest request", () => {
    expect(WorkerBudget.grantSize({ requested: 4, budget: 8, hardCeiling: 24 })).toBe(4)
  })

  test("caps a greedy request at the per-grant maximum", () => {
    expect(WorkerBudget.grantSize({ requested: 500, budget: 8, hardCeiling: 100 })).toBe(
      WorkerBudget.MAX_EXTENSION_GRANT,
    )
  })

  test("never grants past the hard ceiling", () => {
    expect(WorkerBudget.grantSize({ requested: 8, budget: 22, hardCeiling: 24 })).toBe(2)
    expect(WorkerBudget.grantSize({ requested: 8, budget: 24, hardCeiling: 24 })).toBe(0)
  })
})

describe("WorkerBudget.makeAudit", () => {
  test("distinct calls with distinct results all count as progress", () => {
    const audit = WorkerBudget.makeAudit()
    expect(audit.record({ tool: "grep", input: { q: "a" } }, "hit a").outcome).toBe("novel")
    expect(audit.record({ tool: "grep", input: { q: "b" } }, "hit b").outcome).toBe("novel")
    expect(audit.state()).toMatchObject({ steps: 2, novel: 2, stalled: 0 })
  })

  test("key order does not make a repeated call look novel", () => {
    const audit = WorkerBudget.makeAudit()
    audit.record({ tool: "grep", input: { pattern: "x", path: "y" } }, "out")
    const verdict = audit.record({ tool: "grep", input: { path: "y", pattern: "x" } }, "out")
    expect(verdict.outcome).toBe("repeat-call")
    expect(verdict.firstSeenAtStep).toBe(1)
  })

  test("catches an A-B-A-B oscillation, which the old single-slot guard could not", () => {
    // This is the concrete failure the previous `lastCall` comparison missed: neither call is
    // a repeat of the *immediately preceding* one, so the guard never fired and the worker
    // burned its whole budget alternating between two calls.
    const audit = WorkerBudget.makeAudit()
    const a = { tool: "read", input: { path: "a" } }
    const b = { tool: "read", input: { path: "b" } }
    expect(audit.record(a, "A").outcome).toBe("novel")
    expect(audit.record(b, "B").outcome).toBe("novel")
    expect(audit.record(a, "A").outcome).toBe("repeat-call")
    expect(audit.record(b, "B").outcome).toBe("repeat-call")
    expect(audit.state().stalled).toBe(2)
  })

  test("a different call returning an already-seen result is not progress", () => {
    // A model widening its grep pattern every step while every variant returns the same
    // "no matches" is emitting a novel *call* each time; only the output reveals the stall.
    const audit = WorkerBudget.makeAudit()
    audit.record({ tool: "grep", input: { q: "aa" } }, "No matches found")
    const verdict = audit.record({ tool: "grep", input: { q: "bb" } }, "No matches found")
    expect(verdict.outcome).toBe("repeat-output")
    expect(audit.state().novel).toBe(1)
  })

  test("empty and placeholder output counts as a stalled step", () => {
    const audit = WorkerBudget.makeAudit()
    expect(audit.record({ tool: "glob", input: { p: "1" } }, "   ").outcome).toBe("empty")
    expect(audit.record({ tool: "glob", input: { p: "2" } }, "[]").outcome).toBe("empty")
    expect(audit.state()).toMatchObject({ novel: 0, stalled: 2 })
  })

  test("a novel step resets the stall streak but the worst streak is retained", () => {
    const audit = WorkerBudget.makeAudit()
    audit.record({ tool: "a", input: 1 }, "one")
    audit.record({ tool: "a", input: 1 }, "one")
    audit.record({ tool: "a", input: 1 }, "one")
    expect(audit.state().stalled).toBe(2)
    audit.record({ tool: "a", input: 2 }, "two")
    expect(audit.state()).toMatchObject({ stalled: 0, worstStall: 2 })
  })

  test("peek classifies a call without consuming a step", () => {
    const audit = WorkerBudget.makeAudit()
    audit.record({ tool: "a", input: 1 }, "one")
    expect(audit.peek({ tool: "a", input: 1 })).toEqual({ outcome: "repeat-call", firstSeenAtStep: 1 })
    expect(audit.peek({ tool: "a", input: 2 })).toEqual({ outcome: "novel" })
    expect(audit.state().steps).toBe(1)
  })
})

describe("WorkerBudget.canExtend", () => {
  const limits = WorkerBudget.resolveLimits({ softBudget: 8, hardCeiling: 24, maxExtensions: 2 })

  test("granted when the worker produced new information since its last grant", () => {
    expect(
      WorkerBudget.canExtend({
        audit: { steps: 8, novel: 5, stalled: 1, worstStall: 1, novelSinceGrant: 5 },
        extensionsUsed: 0,
        budget: 8,
        limits,
      }),
    ).toBe(true)
  })

  test("refused when nothing new was found since the last grant -- a loop cannot buy steps", () => {
    expect(
      WorkerBudget.canExtend({
        audit: { steps: 8, novel: 3, stalled: 8, worstStall: 8, novelSinceGrant: 0 },
        extensionsUsed: 1,
        budget: 16,
        limits,
      }),
    ).toBe(false)
  })

  test("refused once the extension count is spent", () => {
    expect(
      WorkerBudget.canExtend({
        audit: { steps: 20, novel: 9, stalled: 0, worstStall: 1, novelSinceGrant: 3 },
        extensionsUsed: 2,
        budget: 20,
        limits,
      }),
    ).toBe(false)
  })

  test("refused at the hard ceiling however much progress was made", () => {
    expect(
      WorkerBudget.canExtend({
        audit: { steps: 24, novel: 24, stalled: 0, worstStall: 0, novelSinceGrant: 24 },
        extensionsUsed: 0,
        budget: 24,
        limits,
      }),
    ).toBe(false)
  })
})
