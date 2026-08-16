import { describe, expect, test } from "bun:test"
import { Effect } from "effect"
import { type ToolResultValue } from "@opencode-ai/llm"
import { AgentV2 } from "@opencode-ai/core/agent"
import { SessionV2 } from "@opencode-ai/core/session"
import { SessionMessage } from "@opencode-ai/core/session/message"
import { ToolRegistry } from "@opencode-ai/core/tool/registry"
import { OrchestratorToolRunner } from "@opencode-ai/core/session/orchestrator/tool-runner"

const fakeMaterialization = (
  impl: (name: string, input: unknown) => ToolResultValue,
): ToolRegistry.Materialization => ({
  definitions: [],
  settle: (execute) => Effect.succeed({ result: impl(execute.call.name, execute.call.input) }),
})

const makeRunner = (materialization: ToolRegistry.Materialization) =>
  OrchestratorToolRunner.make({
    sessionID: SessionV2.ID.make("ses_tool_runner"),
    agent: AgentV2.ID.make("build"),
    assistantMessageID: SessionMessage.ID.create(),
    materialization,
  })

describe("OrchestratorToolRunner", () => {
  test("translates a text result to a plain string and forwards name/input", async () => {
    let seenName: string | undefined
    let seenInput: unknown
    const runner = makeRunner(
      fakeMaterialization((name, input) => {
        seenName = name
        seenInput = input
        return { type: "text", value: "hello" }
      }),
    )
    const output = await Effect.runPromise(runner.run({ tool: "echo", input: { a: 1 } }))
    expect(output).toBe("hello")
    expect(seenName).toBe("echo")
    expect(seenInput).toEqual({ a: 1 })
  })

  test("translates an error result with an Error prefix", async () => {
    const runner = makeRunner(fakeMaterialization(() => ({ type: "error", value: "boom" })))
    const output = await Effect.runPromise(runner.run({ tool: "fail", input: {} }))
    expect(output).toBe("Error: boom")
  })

  test("translates a json result to serialized JSON", async () => {
    const runner = makeRunner(fakeMaterialization(() => ({ type: "json", value: { a: 1 } })))
    const output = await Effect.runPromise(runner.run({ tool: "structured", input: {} }))
    expect(output).toBe('{"a":1}')
  })
})
