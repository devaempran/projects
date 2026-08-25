import { describe, expect, test } from "bun:test"
import { Effect } from "effect"
import type { LLMRequest } from "@opencode-ai/llm"
import { WorkerExecutor } from "@opencode-ai/core/session/orchestrator/worker"
import { OrchestratorObserver } from "@opencode-ai/core/session/orchestrator/observer"
import { fakeClient, fakeModel, latestPromptText, toolCallOf } from "./lib"

const makeToolRunner = (outputs: string[]) => {
  const calls: { tool: string; input: unknown }[] = []
  const queue = [...outputs]
  const runner = {
    run: (call: { tool: string; input: unknown }) => {
      calls.push(call)
      return Effect.succeed(queue.shift() ?? "")
    },
  } satisfies WorkerExecutor.ToolRunner
  return { calls, runner }
}

/** Collects every observer event kind the budget/audit work introduced. */
const recordingObserver = () => {
  const events: Array<{ kind: string; data: unknown }> = []
  const observer: OrchestratorObserver.Interface = {
    ...OrchestratorObserver.noop,
    subtaskStarted: (data) => {
      events.push({ kind: "started", data })
      return Effect.void
    },
    workerStep: (data) => {
      events.push({ kind: "step", data })
      return Effect.void
    },
    noProgressDetected: (data) => {
      events.push({ kind: "no-progress", data })
      return Effect.void
    },
    checkpointReached: (data) => {
      events.push({ kind: "checkpoint", data })
      return Effect.void
    },
    stepsExtended: (data) => {
      events.push({ kind: "extended", data })
      return Effect.void
    },
    subtaskFinished: (data) => {
      events.push({ kind: "finished", data })
      return Effect.void
    },
  }
  return { events, observer, of: (kind: string) => events.filter((e) => e.kind === kind).map((e) => e.data as any) }
}

describe("worker step budget: model-chosen size", () => {
  test("a planner estimate sizes the subtask above the configured default", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner([])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "survey the whole codebase" },
        tools: runner,
        maxSteps: 8,
        estimatedSteps: 15,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })], requests))),
    )
    // The budget is shown to the model, which is how it knows to pace itself -- with the old
    // invisible cap it had no way to tell it was one step from being cut off.
    expect(latestPromptText(requests[0]!)).toContain("Step 1 of 15")
  })

  test("the final step's packet says outright that it is the last one", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner(["o1"])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 2,
        maxStepExtensions: 0,
        toolCatalog: [{ name: "a", description: "A tool" }],
      }).pipe(
        Effect.provide(
          fakeClient(
            [toolCallOf("a", { n: 1 }), toolCallOf("finish", { status: "done", result: "ok" })],
            requests,
          ),
        ),
      ),
    )
    expect(latestPromptText(requests[0]!)).toContain("One step remains after this one")
    expect(latestPromptText(requests[1]!)).toContain("This is your LAST step")
  })

  test("a wildly optimistic estimate is clamped to the hard ceiling", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner([])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        estimatedSteps: 500,
        hardStepCeiling: 12,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })], requests))),
    )
    expect(latestPromptText(requests[0]!)).toContain("Step 1 of 12")
  })
})

describe("worker step budget: worker-requested extensions", () => {
  test("a productive worker can buy more steps and keeps going", async () => {
    const { calls, runner } = makeToolRunner(["first", "second", "third"])
    const { observer, of } = recordingObserver()
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 2,
        hardStepCeiling: 10,
        maxStepExtensions: 1,
        toolCatalog: [{ name: "a", description: "A tool" }],
        observer,
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("a", { n: 1 }),
            toolCallOf("a", { n: 2 }),
            // Budget exhausted with novel progress on both steps -> `request_steps` is offered.
            toolCallOf("request_steps", {
              additionalSteps: 3,
              progressSoFar: "located the entry point",
              remainingWork: "trace the two callers",
            }),
            toolCallOf("a", { n: 3 }),
            toolCallOf("finish", { status: "done", result: "traced both callers" }),
          ]),
        ),
      ),
    )
    expect(result.status).toBe("done")
    expect(result.result).toBe("traced both callers")
    // 2 steps, then +3 granted; steps 3 and 4 are the extra tool call and the `finish` that
    // uses it -- so 4 of the 5 budgeted steps were spent, 3 of them on real tool calls.
    expect(result.steps).toEqual({ used: 4, budget: 5, extensions: 1 })
    expect(calls.length).toBe(3)
    expect(of("extended")).toEqual([
      { subtaskId: "s1", granted: 3, budget: 5, extensions: 1, reason: "trace the two callers" },
    ])
  })

  test("the checkpoint prompt tells the model it may ask for more steps only when it may", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner(["progress"])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 1,
        hardStepCeiling: 6,
        maxStepExtensions: 1,
        toolCatalog: [{ name: "a", description: "A tool" }],
      }).pipe(
        Effect.provide(
          fakeClient(
            [toolCallOf("a", { n: 1 }), toolCallOf("finish", { status: "done", result: "ok" })],
            requests,
          ),
        ),
      ),
    )
    // The checkpoint call is the 2nd request. `request_steps` is in its tool set and the
    // catalog tool is not -- re-offering the catalog here reliably tempts one more
    // exploratory call the worker cannot afford.
    const names = requests[1]!.tools?.map((t) => t.name) ?? []
    expect(names).toContain("request_steps")
    expect(names).toContain("finish")
    expect(names).not.toContain("a")
  })

  test("request_steps is withheld once the extension allowance is spent", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner(["progress"])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 1,
        maxStepExtensions: 0,
        toolCatalog: [{ name: "a", description: "A tool" }],
      }).pipe(
        Effect.provide(
          fakeClient(
            [toolCallOf("a", { n: 1 }), toolCallOf("finish", { status: "done", result: "ok" })],
            requests,
          ),
        ),
      ),
    )
    const names = requests[1]!.tools?.map((t) => t.name) ?? []
    expect(names).not.toContain("request_steps")
    expect(latestPromptText(requests[1]!)).toContain("cannot get more steps")
  })

  test("a granted extension can never push past the hard ceiling", async () => {
    const { runner } = makeToolRunner(["a", "b"])
    const observed = recordingObserver()
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 2,
        hardStepCeiling: 3,
        maxStepExtensions: 2,
        toolCatalog: [{ name: "a", description: "A tool" }],
        observer: observed.observer,
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("a", { n: 1 }),
            toolCallOf("a", { n: 2 }),
            toolCallOf("request_steps", { additionalSteps: 8, progressSoFar: "p", remainingWork: "w" }),
            toolCallOf("finish", { status: "done", result: "done at the ceiling" }),
          ]),
        ),
      ),
    )
    // Asked for 8 with a budget of 2 and a ceiling of 3: only 1 is grantable.
    expect(observed.of("extended")[0]).toMatchObject({ granted: 1, budget: 3 })
    expect(result.steps?.budget).toBe(3)
  })
})

describe("worker step budget: loop auditing", () => {
  test("a stalled worker is cut off and cannot buy more steps", async () => {
    const { calls, runner } = makeToolRunner(["only useful result"])
    const { observer, of } = recordingObserver()
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 20,
        noProgressLimit: 3,
        maxStepExtensions: 2,
        toolCatalog: [{ name: "a", description: "A tool" }],
        observer,
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("a", { n: 1 }), // novel
            toolCallOf("a", { n: 1 }), // repeat 1
            toolCallOf("a", { n: 1 }), // repeat 2
            toolCallOf("a", { n: 1 }), // repeat 3 -> stall limit hit, loop breaks
            toolCallOf("finish", { status: "failed", result: "went in circles" }),
          ]),
        ),
      ),
    )
    // Cut off at step 4 out of a 20-step budget: the audit, not the cap, ended this.
    expect(result.steps?.used).toBe(4)
    expect(of("no-progress")).toEqual([{ subtaskId: "s1", stalledSteps: 3, step: 4 }])
    // The checkpoint fired but `request_steps` was NOT offered -- a loop cannot buy steps.
    expect(of("checkpoint")).toEqual([
      { subtaskId: "s1", reason: "no-progress", step: 4, budget: 20, extendable: false },
    ])
    // Only the first, distinct call actually ran.
    expect(calls.length).toBe(1)
    expect(result.status).toBe("partial")
    expect(result.result).toContain("went in circles")
  })

  test("an A-B-A-B oscillation is detected instead of consuming the whole budget", async () => {
    const { calls, runner } = makeToolRunner(["A", "B"])
    const { observer, of } = recordingObserver()
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 20,
        noProgressLimit: 3,
        toolCatalog: [{ name: "a", description: "A tool" }],
        observer,
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("a", { p: "x" }), // novel
            toolCallOf("a", { p: "y" }), // novel
            toolCallOf("a", { p: "x" }), // repeat 1
            toolCallOf("a", { p: "y" }), // repeat 2
            toolCallOf("a", { p: "x" }), // repeat 3 -> cut off
            toolCallOf("finish", { status: "failed", result: "oscillated between x and y" }),
          ]),
        ),
      ),
    )
    expect(of("no-progress")).toHaveLength(1)
    expect(result.steps?.used).toBe(5)
    expect(calls.length).toBe(2)
  })

  test("the repeat nudge names the step the call was first made at", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner(["out"])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 4,
        toolCatalog: [{ name: "a", description: "A tool" }],
      }).pipe(
        Effect.provide(
          fakeClient(
            [
              toolCallOf("a", { p: "x" }),
              toolCallOf("a", { p: "y" }),
              toolCallOf("a", { p: "x" }),
              toolCallOf("finish", { status: "done", result: "ok" }),
            ],
            requests,
          ),
        ),
      ),
    )
    expect(latestPromptText(requests[3]!)).toContain("at step 1")
  })

  test("a repeated result nudges the model even though the call itself was new", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner(["No matches found", "No matches found"])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 4,
        toolCatalog: [{ name: "grep", description: "Search" }],
      }).pipe(
        Effect.provide(
          fakeClient(
            [
              toolCallOf("grep", { q: "aa" }),
              toolCallOf("grep", { q: "bb" }),
              toolCallOf("finish", { status: "done", result: "ok" }),
            ],
            requests,
          ),
        ),
      ),
    )
    expect(latestPromptText(requests[2]!)).toContain("added no new information")
  })
})

describe("worker checkpoint packet consistency", () => {
  test("the checkpoint packet lists exactly the tools its schema offers, and none from the catalog", async () => {
    // Regression guard for a contradiction seen in a live trace: the packet rendered
    // "Available tools: (none available)" while the tool layer was offering `finish`. That
    // lands on the one call that decides whether any findings survive the subtask.
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner(["progress"])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 1,
        hardStepCeiling: 6,
        maxStepExtensions: 1,
        toolCatalog: [{ name: "grep", description: "Search the codebase" }],
      }).pipe(
        Effect.provide(
          fakeClient(
            [toolCallOf("grep", { q: "x" }), toolCallOf("finish", { status: "done", result: "ok" })],
            requests,
          ),
        ),
      ),
    )
    const packet = latestPromptText(requests[1]!)
    expect(packet).not.toContain("(none available)")
    expect(packet).toContain("- finish:")
    expect(packet).toContain("- request_steps:")
    // The real catalog stays withheld so the model isn't tempted into one more exploratory call.
    expect(packet).not.toContain("- grep:")
  })

  test("request_steps is absent from the listing when no extension is available", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner(["progress"])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 1,
        maxStepExtensions: 0,
        toolCatalog: [{ name: "grep", description: "Search the codebase" }],
      }).pipe(
        Effect.provide(
          fakeClient(
            [toolCallOf("grep", { q: "x" }), toolCallOf("finish", { status: "done", result: "ok" })],
            requests,
          ),
        ),
      ),
    )
    const packet = latestPromptText(requests[1]!)
    expect(packet).toContain("- finish:")
    expect(packet).not.toContain("- request_steps:")
  })
})
