import { describe, expect, test } from "bun:test"
import { Effect } from "effect"
import { WorkerExecutor } from "@opencode-ai/core/session/orchestrator/worker"
import { fakeClient, fakeModel, toolCallOf } from "./lib"

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
    expect(result).toEqual({ subtaskId: "s1", status: "done", result: "answer" })
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
    expect(result).toEqual({ subtaskId: "s1", status: "done", result: "answer" })
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

  test("fails after max steps exhausted", async () => {
    // Two distinct calls (not a repeat of each other) -- see the repetition-guardrail test
    // below for what happens when they're identical.
    const { calls, runner } = makeToolRunner(["o1", "o2"])
    const result = await Effect.runPromise(
      WorkerExecutor.run({
        model: fakeModel,
        task: "T",
        subtask: { id: "s1", description: "D" },
        tools: runner,
        maxSteps: 2,
        toolCatalog: [{ name: "a", description: "A tool" }],
      }).pipe(
        Effect.provide(fakeClient([toolCallOf("a", { n: 1 }), toolCallOf("a", { n: 2 })])),
      ),
    )
    expect(result.subtaskId).toBe("s1")
    expect(result.status).toBe("failed")
    expect(result.result.toLowerCase()).toContain("max steps")
    expect(calls.length).toBe(2)
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
})
