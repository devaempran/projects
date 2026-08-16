import { describe, expect, test } from "bun:test"
import { Effect, Layer } from "effect"
import { Database } from "@opencode-ai/core/database/database"
import { LayerNode } from "@opencode-ai/core/effect/layer-node"
import { AppNodeBuilder } from "@opencode-ai/core/effect/app-node-builder"
import { Project } from "@opencode-ai/core/project"
import { ProjectTable } from "@opencode-ai/core/project/sql"
import { AbsolutePath } from "@opencode-ai/core/schema"
import { AgentV2 } from "@opencode-ai/core/agent"
import { SessionV2 } from "@opencode-ai/core/session"
import { SessionTable } from "@opencode-ai/core/session/sql"
import { SessionMessage } from "@opencode-ai/core/session/message"
import { ToolRegistry } from "@opencode-ai/core/tool/registry"
import { OrchestratorState } from "@opencode-ai/core/session/orchestrator/state"
import { SessionOrchestrator } from "@opencode-ai/core/session/orchestrator/entry"
import { fakeClient, fakeModel, toolCallOf } from "./lib"

const dbLayer = AppNodeBuilder.build(LayerNode.group([Database.node, OrchestratorState.node]))
const sessionID = SessionV2.ID.make("ses_orchestrator_entry")

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

const materialization: ToolRegistry.Materialization = {
  definitions: [],
  settle: () => Effect.succeed({ result: { type: "text", value: "" } }),
}

describe("SessionOrchestrator.runLive", () => {
  test("runs the pipeline, emits the rendered summary, and persists state", async () => {
    const objects = [
      { subtasks: [{ id: 1, description: "d", dependsOn: [] }] },
      toolCallOf("finish", { status: "done", result: "done" }),
      { summary: "all good" },
      { complete: true, gaps: [], nextSubtasks: [] },
    ]
    const captured: string[] = []
    const { result, persisted } = await Effect.gen(function* () {
      yield* setup
      const result = yield* SessionOrchestrator.runLive({
        sessionID,
        agent: AgentV2.ID.make("build"),
        model: fakeModel,
        prompt: "build X",
        materialization,
        assistantMessageID: SessionMessage.ID.create(),
        emit: (t) =>
          Effect.sync(() => {
            captured.push(t)
          }),
      })
      const persisted = yield* (yield* OrchestratorState.Service).get(sessionID)
      return { result, persisted }
    }).pipe(Effect.scoped, Effect.provide(Layer.merge(dbLayer, fakeClient(objects))), Effect.runPromise)

    expect(result.status).toBe("complete")
    expect(captured.length).toBe(1)
    expect(captured[0]).toContain("all good")
    expect(captured[0]).toContain("complete")
    expect(persisted).toBeDefined()
    expect(persisted!.status).toBe("complete")
  })
})
