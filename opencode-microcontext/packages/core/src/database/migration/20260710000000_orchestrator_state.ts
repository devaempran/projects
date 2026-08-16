import { Effect } from "effect"
import type { DatabaseMigration } from "../migration"

export default {
  id: "20260710000000_orchestrator_state",
  up(tx) {
    return Effect.gen(function* () {
      yield* tx.run(`
        CREATE TABLE \`orchestrator_state\` (
          \`session_id\` text PRIMARY KEY NOT NULL,
          \`status\` text NOT NULL,
          \`iteration\` integer DEFAULT 0 NOT NULL,
          \`max_iterations\` integer NOT NULL,
          \`data\` text NOT NULL,
          \`time_created\` integer NOT NULL,
          \`time_updated\` integer NOT NULL,
          CONSTRAINT \`fk_orchestrator_state_session_id_session_id_fk\` FOREIGN KEY (\`session_id\`) REFERENCES \`session\`(\`id\`) ON DELETE CASCADE
        );
      `)
    })
  },
} satisfies DatabaseMigration.Migration
