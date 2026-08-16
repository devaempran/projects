import { describe, expect, test } from "bun:test"
import { Effect, Exit, Schema } from "effect"
import { ToolDefinition, type LLMRequest } from "@opencode-ai/llm"
import { OrchestratorStructured } from "@opencode-ai/core/session/orchestrator/structured"
import { fakeClient, fakeModel, latestPromptText, textResponseOf, toolCallOf } from "./lib"

const schema = Schema.Struct({ n: Schema.Number })

describe("OrchestratorStructured.object", () => {
  test("decodes a valid forced tool-call result", async () => {
    const result = await Effect.runPromise(
      OrchestratorStructured.object({ model: fakeModel, schema, prompt: "x" }).pipe(
        Effect.provide(fakeClient([{ n: 42 }])),
      ),
    )
    expect(result).toEqual({ n: 42 })
  })

  test("retries on decode failure and succeeds", async () => {
    const result = await Effect.runPromise(
      OrchestratorStructured.object({ model: fakeModel, schema, prompt: "x", retries: 1 }).pipe(
        Effect.provide(fakeClient([{ bad: true }, { n: 7 }])),
      ),
    )
    expect(result).toEqual({ n: 7 })
  })

  test("feeds the previous failure back into the retry prompt", async () => {
    const requests: Array<LLMRequest> = []
    const result = await Effect.runPromise(
      OrchestratorStructured.object({ model: fakeModel, schema, prompt: "original prompt", retries: 1 }).pipe(
        Effect.provide(fakeClient([{ bad: true }, { n: 9 }], requests)),
      ),
    )
    expect(result).toEqual({ n: 9 })
    expect(requests).toHaveLength(2)
    expect(latestPromptText(requests[0]!)).toBe("original prompt")
    const retryPrompt = latestPromptText(requests[1]!)
    expect(retryPrompt).toContain("original prompt")
    expect(retryPrompt).toContain("Your previous tool call was rejected")
    expect(retryPrompt).toContain(`"bad":true`)
    expect(retryPrompt).toContain("The required schema is")
  })

  test("fails after exhausting retries", async () => {
    const exit = await Effect.runPromiseExit(
      OrchestratorStructured.object({ model: fakeModel, schema, prompt: "x", retries: 2 }).pipe(
        Effect.provide(fakeClient([{ bad: true }, { bad: true }, { bad: true }])),
      ),
    )
    expect(Exit.isFailure(exit)).toBe(true)
  })
})

describe("OrchestratorStructured.toolCall", () => {
  const tools = [new ToolDefinition({ name: "listFiles", description: "List files", inputSchema: { type: "object" } })]

  test("retries when the model calls an unknown (hallucinated) tool, then succeeds", async () => {
    const requests: Array<LLMRequest> = []
    const result = await Effect.runPromise(
      OrchestratorStructured.toolCall({ model: fakeModel, tools, prompt: "x", retries: 1 }).pipe(
        Effect.provide(
          fakeClient(
            [toolCallOf("bash", { cmd: "ls" }), toolCallOf("listFiles", { path: "." })],
            requests,
          ),
        ),
      ),
    )
    expect(result).toEqual({ name: "listFiles", input: { path: "." } })
    expect(requests).toHaveLength(2)
    const retryPrompt = latestPromptText(requests[1]!)
    expect(retryPrompt).toContain('model called unknown tool "bash"')
    expect(retryPrompt).toContain("listFiles")
  })

  test("salvages a tool call embedded as JSON text without burning a retry", async () => {
    const requests: Array<LLMRequest> = []
    const text = 'Sure, here is the call:\n```json\n{"name":"listFiles","arguments":{"path":"."}}\n```'
    const result = await Effect.runPromise(
      OrchestratorStructured.toolCall({ model: fakeModel, tools, prompt: "x", retries: 1 }).pipe(
        Effect.provide(fakeClient([textResponseOf(text)], requests)),
      ),
    )
    expect(result).toEqual({ name: "listFiles", input: { path: "." } })
    expect(requests).toHaveLength(1)
  })

  test("does not salvage text describing an unrecognized tool, and reports available names", async () => {
    const requests: Array<LLMRequest> = []
    const text = 'LLM.toolCall: {"name":"bash","arguments":{"cmd":"ls"}}'
    const exit = await Effect.runPromiseExit(
      OrchestratorStructured.toolCall({ model: fakeModel, tools, prompt: "x", retries: 0 }).pipe(
        Effect.provide(fakeClient([textResponseOf(text)], requests)),
      ),
    )
    expect(Exit.isFailure(exit)).toBe(true)
    expect(requests).toHaveLength(1)
  })

  test("fails after exhausting retries when the model never calls a valid tool", async () => {
    const exit = await Effect.runPromiseExit(
      OrchestratorStructured.toolCall({ model: fakeModel, tools, prompt: "x", retries: 1 }).pipe(
        Effect.provide(fakeClient([textResponseOf("no idea"), textResponseOf("still no idea")])),
      ),
    )
    expect(Exit.isFailure(exit)).toBe(true)
  })
})
