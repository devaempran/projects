import { describe, expect } from "bun:test"
import { Effect } from "effect"
import { Database } from "@opencode-ai/core/database/database"
import { LayerNode } from "@opencode-ai/core/effect/layer-node"
import { AppNodeBuilder } from "@opencode-ai/core/effect/app-node-builder"
import { Project } from "@opencode-ai/core/project"
import { ProjectTable } from "@opencode-ai/core/project/sql"
import { AbsolutePath } from "@opencode-ai/core/schema"
import { SessionV2 } from "@opencode-ai/core/session"
import { SessionTable } from "@opencode-ai/core/session/sql"
import { OrchestratorMemory } from "@opencode-ai/core/session/orchestrator/memory"
import { testEffect } from "../../lib/effect"

const it = testEffect(AppNodeBuilder.build(LayerNode.group([Database.node, OrchestratorMemory.node])))
const sessionID = SessionV2.ID.make("ses_orchestrator_memory_test")

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

describe("OrchestratorMemory", () => {
  it.effect("sets, gets, lists, upserts and removes memory entries", () =>
    Effect.gen(function* () {
      yield* setup
      const memory = yield* OrchestratorMemory.Service

      // 1. fresh key returns undefined
      expect(yield* memory.get(sessionID, "k")).toBeUndefined()

      // 2. set two entries, get each, list both ordered by key
      yield* memory.set(sessionID, "file:a.ts", "summary A")
      yield* memory.set(sessionID, "file:b.ts", "summary B")
      expect(yield* memory.get(sessionID, "file:a.ts")).toBe("summary A")
      expect(yield* memory.get(sessionID, "file:b.ts")).toBe("summary B")
      expect(yield* memory.list(sessionID)).toEqual([
        { key: "file:a.ts", value: "summary A" },
        { key: "file:b.ts", value: "summary B" },
      ])

      // 3. upsert existing key
      yield* memory.set(sessionID, "file:a.ts", "summary A2")
      expect(yield* memory.get(sessionID, "file:a.ts")).toBe("summary A2")
      expect((yield* memory.list(sessionID)).length).toBe(2)

      // 4. remove
      yield* memory.remove(sessionID, "file:a.ts")
      expect(yield* memory.get(sessionID, "file:a.ts")).toBeUndefined()
      expect(yield* memory.list(sessionID)).toEqual([{ key: "file:b.ts", value: "summary B" }])
    }),
  )
})
