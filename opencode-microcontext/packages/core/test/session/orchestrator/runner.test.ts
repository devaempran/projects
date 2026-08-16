import { describe, expect, test } from "bun:test"
import { Effect, Layer } from "effect"
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
import { fakeClient, fakeModel, toolCallOf } from "./lib"

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

const runScenario = (objects: unknown[], runInput: Partial<OrchestratorRunner.RunInput>) =>
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
  }).pipe(Effect.scoped, Effect.provide(Layer.merge(dbLayer, fakeClient(objects))), Effect.runPromise)

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
})
