export * as OrchestratorState from "./state"

import { eq } from "drizzle-orm"
import { Context, Effect, Layer, Schema } from "effect"
import { Database } from "../../database/database"
import { makeLocationNode } from "../../effect/app-node"
import { SessionSchema } from "../schema"
import { OrchestratorStateTable } from "../sql"
import { OrchestratorSchema } from "./schema"

export interface Info {
  readonly sessionID: SessionSchema.ID
  readonly status: OrchestratorSchema.Status
  readonly iteration: number
  readonly maxIterations: number
  readonly data: OrchestratorSchema.Data
}

export interface Interface {
  readonly get: (sessionID: SessionSchema.ID) => Effect.Effect<Info | undefined>
  readonly set: (info: Info) => Effect.Effect<void>
  readonly update: (sessionID: SessionSchema.ID, fn: (current: Info) => Info) => Effect.Effect<Info | undefined>
  readonly remove: (sessionID: SessionSchema.ID) => Effect.Effect<void>
}

export class Service extends Context.Service<Service, Interface>()("@opencode/v2/OrchestratorState") {}

const layer = Layer.effect(
  Service,
  Effect.gen(function* () {
    const { db } = yield* Database.Service

    const set = Effect.fn("OrchestratorState.set")(function* (info: Info) {
      yield* db
        .insert(OrchestratorStateTable)
        .values({
          session_id: info.sessionID,
          status: info.status,
          iteration: info.iteration,
          max_iterations: info.maxIterations,
          data: info.data,
        })
        .onConflictDoUpdate({
          target: OrchestratorStateTable.session_id,
          set: {
            status: info.status,
            iteration: info.iteration,
            max_iterations: info.maxIterations,
            data: info.data,
          },
        })
        .run()
        .pipe(Effect.orDie)
    })

    const get = Effect.fn("OrchestratorState.get")(function* (sessionID: SessionSchema.ID) {
      const rows = yield* db
        .select()
        .from(OrchestratorStateTable)
        .where(eq(OrchestratorStateTable.session_id, sessionID))
        .all()
        .pipe(Effect.orDie)
      const row = rows[0]
      if (!row) return undefined
      return {
        sessionID: row.session_id,
        status: row.status,
        iteration: row.iteration,
        maxIterations: row.max_iterations,
        data: Schema.decodeUnknownSync(OrchestratorSchema.Data)(row.data),
      } satisfies Info
    })

    const update = Effect.fn("OrchestratorState.update")(function* (
      sessionID: SessionSchema.ID,
      fn: (current: Info) => Info,
    ) {
      const current = yield* get(sessionID)
      if (!current) return undefined
      const next = fn(current)
      yield* set(next)
      return next
    })

    const remove = Effect.fn("OrchestratorState.remove")(function* (sessionID: SessionSchema.ID) {
      yield* db
        .delete(OrchestratorStateTable)
        .where(eq(OrchestratorStateTable.session_id, sessionID))
        .run()
        .pipe(Effect.orDie)
    })

    return Service.of({ get, set, update, remove })
  }),
)

export const node = makeLocationNode({ service: Service, layer, deps: [Database.node] })
