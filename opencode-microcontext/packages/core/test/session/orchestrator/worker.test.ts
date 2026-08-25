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

describe("WorkerExecutor", () => {
  test("finishes immediately without using a tool", async () => {
    const { calls, runner } = makeToolRunner([])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "answer" })]))),
    )
    expect(result).toMatchObject({ subtaskId: "s1", status: "done", result: "answer" })
    expect(result.steps).toEqual({ used: 1, budget: 8, extensions: 0 })
    expect(calls.length).toBe(0)
  })

  test("finish with an unrecognized status coerces to done", async () => {
    const { calls, runner } = makeToolRunner([])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "running", result: "answer" })]))),
    )
    expect(result).toMatchObject({ subtaskId: "s1", status: "done", result: "answer" })
    expect(calls.length).toBe(0)
  })

  test("uses a tool then finishes", async () => {
    const { calls, runner } = makeToolRunner(["file-contents"])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        toolCatalog: [{ name: "read", description: "Read a file" }],
      }).pipe(
        Effect.provide(
          fakeClient([toolCallOf("read", { path: "x" }), toolCallOf("finish", { status: "done", result: "done" })]),
        ),
      ),
    )
    expect(result.status).toBe("done")
    expect(calls.length).toBe(1)
    expect(calls[0]!.tool).toBe("read")
    expect(calls[0]!.input).toEqual({ path: "x" })
  })

  test("budget exhaustion gets a wrap-up call, and the model's summary becomes the result", async () => {
    // The old behavior discarded everything on exhaustion and returned only
    // "Reached max steps (2) without finishing". The worker now spends one extra call asking
    // the model to consolidate, so a cut-off subtask still contributes what it learned.
    const { calls, runner } = makeToolRunner(["o1", "o2"])
    const result = await Effect.runPromise(
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
          fakeClient([
            toolCallOf("a", { n: 1 }),
            toolCallOf("a", { n: 2 }),
            toolCallOf("finish", { status: "failed", result: "read o1 and o2; ran out of steps before concluding" }),
          ]),
        ),
      ),
    )
    expect(result.subtaskId).toBe("s1")
    // `partial`, not `failed`: it was cut off but it established something.
    expect(result.status).toBe("partial")
    expect(result.result).toContain("read o1 and o2")
    expect(result.steps).toEqual({ used: 2, budget: 2, extensions: 0 })
    expect(calls.length).toBe(2)
  })

  test("a wrap-up call that itself fails falls back to salvaging the observations", async () => {
    // The deterministic digest is the backstop for the backstop: if the model cannot even
    // author a wrap-up, the tool results it already collected still reach the Reducer.
    const { runner } = makeToolRunner(["licence lives at LICENSE.md", "no matches for FooBar"])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 2,
        maxStepExtensions: 0,
        toolCatalog: [{ name: "a", description: "A tool" }],
        // Only two responses queued, so the wrap-up call gets no valid tool call and errors.
      }).pipe(Effect.provide(fakeClient([toolCallOf("a", { n: 1 }), toolCallOf("a", { n: 2 })]))),
    )
    expect(result.status).toBe("partial")
    expect(result.result).toContain("licence lives at LICENSE.md")
    expect(result.result).toContain("no matches for FooBar")
  })

  test("a subtask that gathered nothing at all still reports failed, not partial", async () => {
    const { runner } = makeToolRunner(["", ""])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 2,
        maxStepExtensions: 0,
        noProgressLimit: 5,
        toolCatalog: [{ name: "a", description: "A tool" }],
      }).pipe(Effect.provide(fakeClient([toolCallOf("a", { n: 1 }), toolCallOf("a", { n: 2 })]))),
    )
    expect(result.status).toBe("failed")
    expect(result.result).toContain("No findings were gathered")
  })

  test("does not re-run an exact repeat of the previous call", async () => {
    const { calls, runner } = makeToolRunner(["o1"])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 3,
        toolCatalog: [{ name: "a", description: "A tool" }],
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("a", { pattern: "x", path: "y" }),
            // Same call, keys in a different order -- must still compare equal.
            toolCallOf("a", { path: "y", pattern: "x" }),
            toolCallOf("finish", { status: "done", result: "done" }),
          ]),
        ),
      ),
    )
    expect(result.status).toBe("done")
    // Only the first (distinct) call actually ran; the repeat was intercepted.
    expect(calls.length).toBe(1)
  })

  test("a hallucinated tool name not in the catalog fails the subtask, not the whole run", async () => {
    // structured.ts retries 3 times (all identical here) before giving up; that retry
    // exhaustion must fold into a normal "failed" WorkerResult rather than escape as an
    // unhandled Effect failure — otherwise one bad worker step takes down the entire
    // orchestrator run instead of just this subtask (see runner.ts, which still has other
    // subtasks/reducer/verifier to run on a "failed" result).
    const { calls, runner } = makeToolRunner([])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        toolCatalog: [{ name: "read", description: "Read a file" }],
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("bash", { cmd: "ls" }),
            toolCallOf("bash", { cmd: "ls" }),
            toolCallOf("bash", { cmd: "ls" }),
          ]),
        ),
      ),
    )
    expect(result.subtaskId).toBe("s1")
    expect(result.status).toBe("failed")
    expect(result.result.toLowerCase()).toContain("unknown tool")
    expect(calls.length).toBe(0)
  })

  test("decompose with valid children returns a decomposed result without using the ToolRunner", async () => {
    const { calls, runner } = makeToolRunner([])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        toolCatalog: [{ name: "read", description: "Read a file" }],
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("decompose", { subtasks: [{ description: "child one" }, { description: "child two" }] }),
          ]),
        ),
      ),
    )
    expect(result).toMatchObject({
      subtaskId: "s1",
      status: "decomposed",
      result: "Decomposed into 2 subtasks",
    })
    expect(result.children).toEqual([
      { description: "child one", estimatedSteps: undefined },
      { description: "child two", estimatedSteps: undefined },
    ])
    expect(calls.length).toBe(0)
  })

  test("decompose is present in the tool catalog at depth 0 but absent at the max decompose depth", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner([])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        depth: 0,
        maxDecomposeDepth: 1,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })], requests))),
    )
    expect(requests).toHaveLength(1)
    expect(requests[0]!.tools?.map((t) => t.name)).toContain("decompose")

    requests.length = 0
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        depth: 1,
        maxDecomposeDepth: 1,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })], requests))),
    )
    expect(requests).toHaveLength(1)
    expect(requests[0]!.tools?.map((t) => t.name)).not.toContain("decompose")
  })

  test("the prompt's Available tools list names decompose whenever the tool-calling layer offers it", async () => {
    // Regression: the tool-calling layer (`requests[0].tools`, asserted above) and the
    // human-readable "Available tools" prompt text are built separately. Before this fix, the
    // prompt text was built from the raw tool catalog only and never mentioned `finish` or
    // `decompose`, even while the system prompt tells the model to call only what's "listed
    // under Available tools in the prompt" -- so a model that follows the prompt text
    // literally never called `decompose`.
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner([])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        depth: 0,
        maxDecomposeDepth: 1,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })], requests))),
    )
    const packet = latestPromptText(requests[0]!)
    expect(packet).toContain("Available tools")
    expect(packet).toContain("decompose:")
    expect(packet).toContain("finish:")

    requests.length = 0
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        depth: 1,
        maxDecomposeDepth: 1,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })], requests))),
    )
    const deeperPacket = latestPromptText(requests[0]!)
    expect(deeperPacket).not.toContain("decompose:")
    expect(deeperPacket).toContain("finish:")
  })

  test("maxDecomposeDepth: 0 omits decompose even at depth 0", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner([])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        depth: 0,
        maxDecomposeDepth: 0,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })], requests))),
    )
    expect(requests).toHaveLength(1)
    expect(requests[0]!.tools?.map((t) => t.name)).not.toContain("decompose")
  })

  test("a malformed decompose call does not end the subtask -- the loop continues and a later finish resolves normally", async () => {
    const { calls, runner } = makeToolRunner([])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 3,
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("decompose", { subtasks: [{ description: "only one" }] }),
            toolCallOf("finish", { status: "done", result: "answer" }),
          ]),
        ),
      ),
    )
    expect(result).toMatchObject({ subtaskId: "s1", status: "done", result: "answer" })
    expect(calls.length).toBe(0)
  })

  test("decompose carries each child's step estimate through to the runner", async () => {
    const { runner } = makeToolRunner([])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
      }).pipe(
        Effect.provide(
          fakeClient([
            toolCallOf("decompose", {
              subtasks: [
                { description: "narrow slice", estimatedSteps: 3 },
                { description: "broad survey", estimatedSteps: 14 },
              ],
            }),
          ]),
        ),
      ),
    )
    expect(result.children).toEqual([
      { description: "narrow slice", estimatedSteps: 3 },
      { description: "broad survey", estimatedSteps: 14 },
    ])
  })

  test("subtaskStarted forwards parentId/depth for a child node minted by decompose", async () => {
    const { runner } = makeToolRunner([])
    const started: Array<{ subtaskId: string; description: string; parentId?: string; depth?: number }> = []
    const observer: OrchestratorObserver.Interface = {
      ...OrchestratorObserver.noop,
      subtaskStarted: (data) => {
        started.push(data)
        return Effect.void
      },
    }
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1.1", description: "child slice" },
        tools: runner,
        parentId: "s1",
        depth: 1,
        observer,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })]))),
    )
    expect(started).toHaveLength(1)
    expect(started[0]).toMatchObject({
      subtaskId: "s1.1",
      description: "child slice",
      parentId: "s1",
      depth: 1,
    })
  })

  test("subtaskStarted has no parentId/depth for a top-level node", async () => {
    const { runner } = makeToolRunner([])
    const started: Array<{ subtaskId: string; description: string; parentId?: string; depth?: number }> = []
    const observer: OrchestratorObserver.Interface = {
      ...OrchestratorObserver.noop,
      subtaskStarted: (data) => {
        started.push(data)
        return Effect.void
      },
    }
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        observer,
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })]))),
    )
    expect(started).toHaveLength(1)
    expect(started[0]).toMatchObject({
      subtaskId: "s1",
      description: "D",
      parentId: undefined,
      depth: undefined,
      // The resolved budget is announced up front so the live view can show "step n of N"
      // from the very first step rather than after the fact.
      budget: 8,
      hardCeiling: 24,
      estimated: false,
    })
  })

  test("a repeated identical malformed decompose call gets the sharpened repeat message, not the bland one again", async () => {
    const requests: Array<LLMRequest> = []
    const { calls, runner } = makeToolRunner([])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 3,
      }).pipe(
        Effect.provide(
          fakeClient(
            [
              toolCallOf("decompose", { subtasks: [{ description: "only one" }] }),
              // Byte-identical malformed decompose call again.
              toolCallOf("decompose", { subtasks: [{ description: "only one" }] }),
              toolCallOf("finish", { status: "done", result: "answer" }),
            ],
            requests,
          ),
        ),
      ),
    )
    expect(result).toMatchObject({ subtaskId: "s1", status: "done", result: "answer" })
    expect(calls.length).toBe(0)
    // The 3rd request's packet is built from the observations accumulated after both
    // rejected decompose attempts -- it must contain the sharpened repeat wording, not
    // just the generic "needs 2-4 subtasks" rejection twice over.
    expect(requests).toHaveLength(3)
    const packet = latestPromptText(requests[2]!)
    expect(packet).toContain("the same decompose call you already made at step 1")
    expect(packet.toLowerCase()).toContain("finish")
  })

  test("parentContext appears in the context packet handed to the model", async () => {
    const requests: Array<LLMRequest> = []
    const { runner } = makeToolRunner([])
    await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1.1", description: "D" },
        tools: runner,
        parentContext: "Parent subtask: broad thing",
      }).pipe(Effect.provide(fakeClient([toolCallOf("finish", { status: "done", result: "ok" })], requests))),
    )
    expect(requests).toHaveLength(1)
    expect(latestPromptText(requests[0]!)).toContain("Parent subtask: broad thing")
  })
})
