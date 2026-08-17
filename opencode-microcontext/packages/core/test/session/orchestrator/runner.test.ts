import { describe, expect, test } from "bun:test"
import { Effect, Layer } from "effect"
import type { LLMRequest } from "@opencode-ai/llm"
import { Database } from "@opencode-ai/core/database/database"
import { LayerNode } from "@opencode-ai/core/effect/layer-node"
import { AppNodeBuilder } from "@opencode-ai/core/effect/app-node-builder"
import { Project } from "@opencode-ai/core/project"
import { ProjectTable } from "@opencode-ai/core/project/sql"
import { AbsolutePath } from "@opencode-ai/core/schema"
import { SessionV2 } from "@opencode-ai/core/session"
import { SessionTable } from "@opencode-ai/core/session/sql"
import { OrchestratorState } from "@opencode-ai/core/session/orchestrator/state"
import { OrchestratorRunner } from "@opencode-ai/core/session/orchestrator/runner"
import { fakeClient, fakeModel, latestPromptText, toolCallOf } from "./lib"

const dbLayer = AppNodeBuilder.build(LayerNode.group([Database.node, OrchestratorState.node]))
const noTools = { run: () => Effect.succeed("") } satisfies OrchestratorRunner.RunInput["tools"]
const sessionID = SessionV2.ID.make("ses_orchestrator_runner")

const setup = Effect.gen(function* () {
  const { db } = yield* Database.Service
  yield* db
    .insert(ProjectTable)
    .values({ id: Project.ID.global, worktree: AbsolutePath.make("/project"), sandboxes: [] })
    .run()
    .pipe(Effect.orDie)
  yield* db
    .insert(SessionTable)
    .values({
      id: sessionID,
      project_id: Project.ID.global,
      slug: "orch",
      directory: "/project",
      title: "orch",
      version: "test",
    })
    .run()
    .pipe(Effect.orDie)
})

const runScenario = (
  objects: unknown[],
  runInput: Partial<OrchestratorRunner.RunInput>,
  requests?: Array<LLMRequest>,
) =>
  Effect.gen(function* () {
    yield* setup
    const result = yield* OrchestratorRunner.run({
      sessionID,
      model: fakeModel,
      prompt: "build X",
      tools: noTools,
      ...runInput,
    })
    const persisted = yield* (yield* OrchestratorState.Service).get(sessionID)
    return { result, persisted }
  }).pipe(Effect.scoped, Effect.provide(Layer.merge(dbLayer, fakeClient(objects, requests))), Effect.runPromise)

describe("OrchestratorRunner", () => {
  test("completes in one iteration", async () => {
    const { result, persisted } = await runScenario(
      [
        { subtasks: [{ id: 1, description: "do it", dependsOn: [] }] },
        toolCallOf("finish", { status: "done", result: "did it" }),
        { summary: "did it" },
        { complete: true, gaps: [], nextSubtasks: [] },
      ],
      {},
    )
    expect(result.status).toBe("complete")
    expect(result.iterations).toBe(1)
    expect(result.summary).toBe("did it")
    expect(persisted).toBeDefined()
    expect(persisted!.status).toBe("complete")
    expect(persisted!.iteration).toBe(1)
    expect(persisted!.data.subtasks[0]!.status).toBe("done")
  })

  test("loops to a second iteration then completes", async () => {
    const { result } = await runScenario(
      [
        { subtasks: [{ id: 1, description: "d1", dependsOn: [] }] },
        toolCallOf("finish", { status: "done", result: "r1" }),
        { summary: "s1 done" },
        { complete: false, gaps: ["need s2"], nextSubtasks: [{ id: "s2", description: "d2", dependsOn: [] }] },
        toolCallOf("finish", { status: "done", result: "r2" }),
        { summary: "all done" },
        { complete: true, gaps: [], nextSubtasks: [] },
      ],
      {},
    )
    expect(result.status).toBe("complete")
    expect(result.iterations).toBe(2)
    expect(result.summary).toBe("all done")
  })

  test("hits maxIterations without completing", async () => {
    const { result, persisted } = await runScenario(
      [
        { subtasks: [{ id: 1, description: "d1", dependsOn: [] }] },
        toolCallOf("finish", { status: "done", result: "r1" }),
        { summary: "partial" },
        { complete: false, gaps: ["more"], nextSubtasks: [] },
      ],
      { maxIterations: 1 },
    )
    expect(result.status).toBe("failed")
    expect(result.iterations).toBe(1)
    expect(result.gaps).toContain("more")
    expect(persisted).toBeDefined()
    expect(persisted!.status).toBe("failed")
  })

  test("a negative maxDecomposeDepth degrades to no decomposition and still runs the root node normally", async () => {
    // Before the clamp fix, maxNodesPerSubtree went negative, so `used >= maxNodesPerSubtree`
    // tripped on the very first node of every root before its worker was ever called --
    // every subtask failed and the reducer silently saw an empty results list. A negative
    // config value must instead degrade to "decomposition off" and run normally.
    const { result, persisted } = await runScenario(
      [
        { subtasks: [{ id: 1, description: "do it", dependsOn: [] }] },
        toolCallOf("finish", { status: "done", result: "did it" }),
        { summary: "did it" },
        { complete: true, gaps: [], nextSubtasks: [] },
      ],
      { maxDecomposeDepth: -1 },
    )
    expect(result.status).toBe("complete")
    expect(result.iterations).toBe(1)
    expect(result.summary).toBe("did it")
    expect(persisted!.data.subtasks[0]!).toMatchObject({ status: "done", result: "did it" })
  })

  test("decomposed subtask: children run to completion and only they feed the reducer", async () => {
    const requests: LLMRequest[] = []
    const { result, persisted } = await runScenario(
      [
        { subtasks: [{ id: 1, description: "root task", dependsOn: [] }] },
        toolCallOf("decompose", { subtasks: [{ description: "child a" }, { description: "child b" }] }),
        toolCallOf("finish", { status: "done", result: "a done" }),
        toolCallOf("finish", { status: "done", result: "b done" }),
        { summary: "combined" },
        { complete: true, gaps: [], nextSubtasks: [] },
      ],
      {},
      requests,
    )

    expect(result.status).toBe("complete")
    expect(result.summary).toBe("combined")

    const subtasks = persisted!.data.subtasks
    const parent = subtasks.find((s) => s.id === "s1")
    const child1 = subtasks.find((s) => s.id === "s1.1")
    const child2 = subtasks.find((s) => s.id === "s1.2")
    expect(parent).toMatchObject({ status: "decomposed" })
    expect(child1).toMatchObject({ status: "done", result: "a done", parentId: "s1", depth: 1 })
    expect(child2).toMatchObject({ status: "done", result: "b done", parentId: "s1", depth: 1 })

    // planner, s1's decompose step, s1.1's finish step, s1.2's finish step, then the reducer.
    const reducerRequest = requests[4]!
    const reducerPrompt = latestPromptText(reducerRequest)
    const resultLines = reducerPrompt.split("\n").filter((line) => line.startsWith("- ["))
    expect(resultLines.length).toBe(2)
    expect(reducerPrompt).toContain("[s1.1]")
    expect(reducerPrompt).toContain("[s1.2]")
    expect(reducerPrompt).not.toContain("[s1]")
  })
})
