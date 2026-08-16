export * as OrchestratorMemory from "./memory"

import { and, asc, eq } from "drizzle-orm"
import { Context, Effect, Layer } from "effect"
import { Database } from "../../database/database"
import { makeLocationNode } from "../../effect/app-node"
import { SessionSchema } from "../schema"
import { OrchestratorMemoryTable } from "../sql"

export interface Entry {
  readonly key: string
  readonly value: string
}

export interface Interface {
  readonly set: (sessionID: SessionSchema.ID, key: string, value: string) => Effect.Effect<void>
  readonly get: (sessionID: SessionSchema.ID, key: string) => Effect.Effect<string | undefined>
  readonly list: (sessionID: SessionSchema.ID) => Effect.Effect<ReadonlyArray<Entry>>
  readonly remove: (sessionID: SessionSchema.ID, key: string) => Effect.Effect<void>
}

export class Service extends Context.Service<Service, Interface>()("@opencode/v2/OrchestratorMemory") {}

const layer = Layer.effect(
  Service,
  Effect.gen(function* () {
    const { db } = yield* Database.Service

    const set = Effect.fn("OrchestratorMemory.set")(function* (
      sessionID: SessionSchema.ID,
      key: string,
      value: string,
    ) {
      yield* db
        .insert(OrchestratorMemoryTable)
        .values({ session_id: sessionID, key, value })
        .onConflictDoUpdate({
          target: [OrchestratorMemoryTable.session_id, OrchestratorMemoryTable.key],
          set: { value },
        })
        .run()
        .pipe(Effect.orDie)
    })

    const get = Effect.fn("OrchestratorMemory.get")(function* (sessionID: SessionSchema.ID, key: string) {
      const rows = yield* db
        .select()
        .from(OrchestratorMemoryTable)
        .where(and(eq(OrchestratorMemoryTable.session_id, sessionID), eq(OrchestratorMemoryTable.key, key)))
        .all()
        .pipe(Effect.orDie)
      return rows[0]?.value
    })

    const list = Effect.fn("OrchestratorMemory.list")(function* (sessionID: SessionSchema.ID) {
      const rows = yield* db
        .select()
        .from(OrchestratorMemoryTable)
        .where(eq(OrchestratorMemoryTable.session_id, sessionID))
        .orderBy(asc(OrchestratorMemoryTable.key))
        .all()
        .pipe(Effect.orDie)
      return rows.map((row) => ({ key: row.key, value: row.value }) satisfies Entry)
    })

    const remove = Effect.fn("OrchestratorMemory.remove")(function* (sessionID: SessionSchema.ID, key: string) {
      yield* db
        .delete(OrchestratorMemoryTable)
        .where(and(eq(OrchestratorMemoryTable.session_id, sessionID), eq(OrchestratorMemoryTable.key, key)))
        .run()
        .pipe(Effect.orDie)
    })

    return Service.of({ set, get, list, remove })
  }),
)

export const node = makeLocationNode({ service: Service, layer, deps: [Database.node] })
