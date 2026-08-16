import { describe, expect } from "bun:test"
import { Effect } from "effect"
import { Database } from "@opencode-ai/core/database/database"
import { LayerNode } from "@opencode-ai/core/effect/layer-node"
import { AppNodeBuilder } from "@opencode-ai/core/effect/app-node-builder"
import { Project } from "@opencode-ai/core/project"
import { ProjectTable } from "@opencode-ai/core/project/sql"
import { AbsolutePath } from "@opencode-ai/core/schema"
import { SessionV2 } from "@opencode-ai/core/session"
import { SessionTable, OrchestratorStateTable } from "@opencode-ai/core/session/sql"
import { OrchestratorState } from "@opencode-ai/core/session/orchestrator/state"
import { testEffect } from "../../lib/effect"

const it = testEffect(AppNodeBuilder.build(LayerNode.group([Database.node, OrchestratorState.node])))
const sessionID = SessionV2.ID.make("ses_orchestrator_test")

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
      slug: "orchestrator",
      directory: "/project",
      title: "orchestrator",
      version: "test",
    })
    .run()
    .pipe(Effect.orDie)
})

describe("OrchestratorState", () => {
  it.effect("persists, updates, upserts and removes orchestrator state", () =>
    Effect.gen(function* () {
      yield* setup
      const { db } = yield* Database.Service
      const state = yield* OrchestratorState.Service

      // 1. fresh session returns undefined
      expect(yield* state.get(sessionID)).toBeUndefined()

      // 2. set + get roundtrip (incl. nested subtask)
      const initial: OrchestratorState.Info = {
        sessionID,
        status: "planning",
        iteration: 0,
        maxIterations: 5,
        data: {
          task: "build X",
          subtasks: [{ id: "s1", description: "do a", status: "pending", dependsOn: [] }],
          gaps: [],
        },
      }
      yield* state.set(initial)
      expect(yield* state.get(sessionID)).toEqual(initial)
      expect((yield* db.select().from(OrchestratorStateTable).all().pipe(Effect.orDie)).length).toBe(1)

      // 3. update: bump iteration and mark subtask done with result
      const updated = yield* state.update(sessionID, (current) => ({
        ...current,
        iteration: 1,
        data: {
          ...current.data,
          subtasks: current.data.subtasks.map((subtask) =>
            subtask.id === "s1" ? { ...subtask, status: "done" as const, result: "did a" } : subtask,
          ),
        },
      }))
      expect(updated).toEqual({
        sessionID,
        status: "planning",
        iteration: 1,
        maxIterations: 5,
        data: {
          task: "build X",
          subtasks: [{ id: "s1", description: "do a", status: "done", dependsOn: [], result: "did a" }],
          gaps: [],
        },
      })
      expect(yield* state.get(sessionID)).toEqual(updated)

      // 4. set again with different status confirms upsert (still one row)
      const completed: OrchestratorState.Info = { ...updated!, status: "complete" }
      yield* state.set(completed)
      expect((yield* db.select().from(OrchestratorStateTable).all().pipe(Effect.orDie)).length).toBe(1)
      expect(yield* state.get(sessionID)).toEqual(completed)

      // 5. remove
      yield* state.remove(sessionID)
      expect(yield* state.get(sessionID)).toBeUndefined()
      expect((yield* db.select().from(OrchestratorStateTable).all().pipe(Effect.orDie)).length).toBe(0)
    }),
  )
})
