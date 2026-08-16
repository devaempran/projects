export * as OrchestratorToolRunner from "./tool-runner"

import { Effect } from "effect"
import { LLMEvent, type ToolResultValue } from "@opencode-ai/llm"
import { AgentV2 } from "../../agent"
import { SessionMessage } from "../message"
import { SessionSchema } from "../schema"
import { ToolRegistry } from "../../tool/registry"
import { WorkerExecutor } from "./worker"

const resultToString = (r: ToolResultValue): string => {
  switch (r.type) {
    case "text":
      return typeof r.value === "string" ? r.value : JSON.stringify(r.value)
    case "json":
      return JSON.stringify(r.value)
    case "error":
      return `Error: ${typeof r.value === "string" ? r.value : JSON.stringify(r.value)}`
    case "content":
      return (r.value as ReadonlyArray<{ readonly type: string; readonly text?: string }>)
        .map((c) => (c.type === "text" ? (c.text ?? "") : JSON.stringify(c)))
        .join("\n")
    default:
      return JSON.stringify(r)
  }
}

export interface Deps {
  readonly sessionID: SessionSchema.ID
  readonly agent: AgentV2.ID
  readonly assistantMessageID: SessionMessage.ID
  readonly materialization: ToolRegistry.Materialization
}

export const make = (deps: Deps): WorkerExecutor.ToolRunner => {
  let counter = 0
  return {
    run: ({ tool, input }) =>
      deps.materialization
        .settle({
          sessionID: deps.sessionID,
          agent: deps.agent,
          assistantMessageID: deps.assistantMessageID,
          call: LLMEvent.toolCall({ id: `${deps.assistantMessageID}_tool_${++counter}`, name: tool, input }),
        })
        .pipe(
          Effect.map((settlement) => resultToString(settlement.result)),
          Effect.orDie,
        ),
  }
}
