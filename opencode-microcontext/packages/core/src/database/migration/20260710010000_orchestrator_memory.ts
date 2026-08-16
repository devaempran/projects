import { Effect } from "effect"
import type { DatabaseMigration } from "../migration"

export default {
  id: "20260710010000_orchestrator_memory",
  up(tx) {
    return Effect.gen(function* () {
      yield* tx.run(`
        CREATE TABLE \`orchestrator_memory\` (
          \`session_id\` text NOT NULL,
          \`key\` text NOT NULL,
          \`value\` text NOT NULL,
          \`time_created\` integer NOT NULL,
          \`time_updated\` integer NOT NULL,
          PRIMARY KEY(\`session_id\`, \`key\`),
          CONSTRAINT \`fk_orchestrator_memory_session_id_session_id_fk\` FOREIGN KEY (\`session_id\`) REFERENCES \`session\`(\`id\`) ON DELETE CASCADE
        );
      `)
    })
  },
} satisfies DatabaseMigration.Migration
