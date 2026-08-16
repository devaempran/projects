import { describe, expect, test } from "bun:test"
import { Effect, Schema } from "effect"
import { LLM } from "../src"
import * as OpenAIChat from "../src/protocols/openai-chat"
import { Auth } from "../src/route"
import { Tool, toDefinitions } from "../src/tool"
import { it } from "./lib/effect"
import { dynamicResponse } from "./lib/http"
import { deltaChunk, finishChunk, toolCallChunk } from "./lib/openai-chunks"
import { sseEvents } from "./lib/sse"

type OpenAIChatBody = {
  readonly tool_choice?: unknown
  readonly tools?: ReadonlyArray<{
    readonly function: {
      readonly parameters: unknown
    }
  }>
}

const model = OpenAIChat.route
  .with({ endpoint: { baseURL: "https://api.openai.test/v1/" }, auth: Auth.bearer("test") })
  .model({ id: "gpt-4o-mini" })

const Json = Schema.fromJsonString(Schema.Unknown)
const decodeJson = Schema.decodeUnknownSync(Json)
const decodeBody = (text: string): OpenAIChatBody => decodeJson(text) as OpenAIChatBody

describe("Tool.make (dynamic JSON Schema)", () => {
  test("forwards JSON Schema and description through toDefinitions", () => {
    const jsonSchema = {
      type: "object" as const,
      properties: { city: { type: "string" } },
      required: ["city"],
    }
    const lookup = Tool.make({
      description: "Look up something",
      jsonSchema,
      execute: () => Effect.succeed({ ok: true }),
    })
    const [definition] = toDefinitions({ lookup })
    expect(definition?.name).toBe("lookup")
    expect(definition?.description).toBe("Look up something")
    expect(definition?.inputSchema).toEqual(jsonSchema)
  })

  test("execute receives the raw input untouched", async () => {
    const seen: unknown[] = []
    const tool = Tool.make({
      description: "echo",
      jsonSchema: { type: "object" },
      execute: (params) =>
        Effect.sync(() => {
          seen.push(params)
          return { ok: true }
        }),
    })
    const result = await Effect.runPromise(tool.execute({ hello: "world" }))
    expect(seen).toEqual([{ hello: "world" }])
    expect(result).toEqual({ ok: true })
  })
})

describe("LLM.generateObject", () => {
  it.effect("forces a synthetic tool call and decodes the input", () =>
    Effect.gen(function* () {
      const bodies: OpenAIChatBody[] = []
      const layer = dynamicResponse((input) =>
        Effect.sync(() => {
          bodies.push(decodeBody(input.text))
          return input.respond(
            sseEvents(
              toolCallChunk("call_1", "generate_object", '{"city":"Paris","temp":22}'),
              finishChunk("tool_calls"),
            ),
            { headers: { "content-type": "text/event-stream" } },
          )
        }),
      )

      const response = yield* LLM.generateObject({
        model,
        prompt: "Return a structured weather report.",
        schema: Schema.Struct({ city: Schema.String, temp: Schema.Number }),
      }).pipe(Effect.provide(layer))

      expect(response.object).toEqual({ city: "Paris", temp: 22 })
      expect(response.response.toolCalls).toHaveLength(1)
      expect(bodies).toHaveLength(1)
      expect(bodies[0].tool_choice).toEqual({ type: "function", function: { name: "generate_object" } })
      const tool = bodies[0].tools?.[0]
      expect(bodies[0].tools).toHaveLength(1)
      expect(tool).toMatchObject({
        type: "function",
        function: { name: "generate_object" },
      })
      const params = tool?.function.parameters as {
        readonly type?: unknown
        readonly required?: unknown
        readonly properties?: Record<string, unknown>
      }
      expect(params.type).toBe("object")
      expect(params.required).toEqual(["city", "temp"])
      expect(params.properties?.city).toMatchObject({ type: "string" })
      expect(params.properties?.temp).toBeDefined()
    }),
  )

  it.effect("accepts a raw JSON Schema and returns the input untouched", () =>
    Effect.gen(function* () {
      const bodies: OpenAIChatBody[] = []
      const layer = dynamicResponse((input) =>
        Effect.sync(() => {
          bodies.push(decodeBody(input.text))
          return input.respond(
            sseEvents(toolCallChunk("call_1", "generate_object", '{"name":"Ada","age":30}'), finishChunk("tool_calls")),
            { headers: { "content-type": "text/event-stream" } },
          )
        }),
      )

      const response = yield* LLM.generateObject({
        model,
        prompt: "Extract the user.",
        jsonSchema: {
          type: "object",
          properties: { name: { type: "string" }, age: { type: "number" } },
          required: ["name", "age"],
        },
      }).pipe(Effect.provide(layer))

      expect(response.object).toEqual({ name: "Ada", age: 30 })
      expect(bodies[0].tools?.[0]?.function.parameters).toEqual({
        type: "object",
        properties: { name: { type: "string" }, age: { type: "number" } },
        required: ["name", "age"],
      })
    }),
  )

  it.effect("fails when the model does not call the synthetic tool, carrying the raw response for debugging", () =>
    Effect.gen(function* () {
      const layer = dynamicResponse((input) =>
        Effect.sync(() =>
          input.respond(sseEvents({ id: "x", choices: [{ delta: { content: "no thanks" }, finish_reason: "stop" }] }), {
            headers: { "content-type": "text/event-stream" },
          }),
        ),
      )

      const result = yield* LLM.generateObject({
        model,
        prompt: "Return a structured value.",
        schema: Schema.Struct({ value: Schema.Number }),
      }).pipe(Effect.provide(layer), Effect.result)

      expect(result._tag).toBe("Failure")
      if (result._tag !== "Failure") return
      expect(result.failure.reason._tag).toBe("InvalidProviderOutput")
      if (result.failure.reason._tag !== "InvalidProviderOutput") return
      expect(result.failure.reason.raw).toContain("no thanks")
    }),
  )

  it.effect("fails with a decode error, carrying the raw tool arguments for debugging", () =>
    Effect.gen(function* () {
      const layer = dynamicResponse((input) =>
        Effect.sync(() =>
          input.respond(
            sseEvents(
              toolCallChunk("call_1", "generate_object", '{"value":"not-a-number"}'),
              finishChunk("tool_calls"),
            ),
            { headers: { "content-type": "text/event-stream" } },
          ),
        ),
      )

      const result = yield* LLM.generateObject({
        model,
        prompt: "Return a structured value.",
        schema: Schema.Struct({ value: Schema.Number }),
      }).pipe(Effect.provide(layer), Effect.result)

      expect(result._tag).toBe("Failure")
      if (result._tag !== "Failure") return
      expect(result.failure.reason._tag).toBe("InvalidProviderOutput")
      if (result.failure.reason._tag !== "InvalidProviderOutput") return
      expect(result.failure.reason.raw).toBe('{"value":"not-a-number"}')
    }),
  )

  // Ollama's OpenAI-compatible endpoint does not enforce `tool_choice`, so the
  // following exercise the leniency fallbacks `runGenerateObject` needs when
  // small local models don't call the forced tool by name.

  it.effect("accepts a single tool call under a different/hallucinated name", () =>
    Effect.gen(function* () {
      const layer = dynamicResponse((input) =>
        Effect.sync(() =>
          input.respond(
            sseEvents(
              toolCallChunk("call_1", "return_weather_report", '{"city":"Paris","temp":22}'),
              finishChunk("tool_calls"),
            ),
            { headers: { "content-type": "text/event-stream" } },
          ),
        ),
      )

      const response = yield* LLM.generateObject({
        model,
        prompt: "Return a structured weather report.",
        schema: Schema.Struct({ city: Schema.String, temp: Schema.Number }),
      }).pipe(Effect.provide(layer))

      expect(response.object).toEqual({ city: "Paris", temp: 22 })
    }),
  )

  it.effect("accepts a plain-text JSON response with no tool call", () =>
    Effect.gen(function* () {
      const layer = dynamicResponse((input) =>
        Effect.sync(() =>
          input.respond(
            sseEvents(
              deltaChunk({ role: "assistant", content: '{"city":"Paris","temp":22}' }),
              finishChunk("stop"),
            ),
            { headers: { "content-type": "text/event-stream" } },
          ),
        ),
      )

      const response = yield* LLM.generateObject({
        model,
        prompt: "Return a structured weather report.",
        schema: Schema.Struct({ city: Schema.String, temp: Schema.Number }),
      }).pipe(Effect.provide(layer))

      expect(response.object).toEqual({ city: "Paris", temp: 22 })
    }),
  )

  it.effect("accepts a JSON response fenced in a ```json code block", () =>
    Effect.gen(function* () {
      const layer = dynamicResponse((input) =>
        Effect.sync(() =>
          input.respond(
            sseEvents(
              deltaChunk({
                role: "assistant",
                content: '```json\n{"city":"Paris","temp":22}\n```',
              }),
              finishChunk("stop"),
            ),
            { headers: { "content-type": "text/event-stream" } },
          ),
        ),
      )

      const response = yield* LLM.generateObject({
        model,
        prompt: "Return a structured weather report.",
        schema: Schema.Struct({ city: Schema.String, temp: Schema.Number }),
      }).pipe(Effect.provide(layer))

      expect(response.object).toEqual({ city: "Paris", temp: 22 })
    }),
  )

  it.effect("accepts JSON embedded in surrounding prose", () =>
    Effect.gen(function* () {
      const layer = dynamicResponse((input) =>
        Effect.sync(() =>
          input.respond(
            sseEvents(
              deltaChunk({
                role: "assistant",
                content: 'Here is the result: {"city":"Paris","temp":22} hope that helps',
              }),
              finishChunk("stop"),
            ),
            { headers: { "content-type": "text/event-stream" } },
          ),
        ),
      )

      const response = yield* LLM.generateObject({
        model,
        prompt: "Return a structured weather report.",
        schema: Schema.Struct({ city: Schema.String, temp: Schema.Number }),
      }).pipe(Effect.provide(layer))

      expect(response.object).toEqual({ city: "Paris", temp: 22 })
    }),
  )

  it.effect("fails with the original error when the text has no parseable JSON", () =>
    Effect.gen(function* () {
      const layer = dynamicResponse((input) =>
        Effect.sync(() =>
          input.respond(
            sseEvents(
              deltaChunk({ role: "assistant", content: "the weather is nice, no structured data here {not json" }),
              finishChunk("stop"),
            ),
            { headers: { "content-type": "text/event-stream" } },
          ),
        ),
      )

      const result = yield* LLM.generateObject({
        model,
        prompt: "Return a structured weather report.",
        schema: Schema.Struct({ city: Schema.String, temp: Schema.Number }),
      }).pipe(Effect.provide(layer), Effect.result)

      expect(result._tag).toBe("Failure")
      if (result._tag !== "Failure") return
      expect(result.failure.reason._tag).toBe("InvalidProviderOutput")
      if (result.failure.reason._tag !== "InvalidProviderOutput") return
      expect(result.failure.reason.message).toContain("model did not call the forced `generate_object` tool")
      expect(result.failure.reason.raw).toContain("no structured data here")
    }),
  )

  it.effect("still surfaces a decode failure for a wrong-named tool call with invalid input", () =>
    Effect.gen(function* () {
      const layer = dynamicResponse((input) =>
        Effect.sync(() =>
          input.respond(
            sseEvents(
              toolCallChunk("call_1", "return_weather_report", '{"city":"Paris","temp":"warm"}'),
              finishChunk("tool_calls"),
            ),
            { headers: { "content-type": "text/event-stream" } },
          ),
        ),
      )

      const result = yield* LLM.generateObject({
        model,
        prompt: "Return a structured weather report.",
        schema: Schema.Struct({ city: Schema.String, temp: Schema.Number }),
      }).pipe(Effect.provide(layer), Effect.result)

      expect(result._tag).toBe("Failure")
      if (result._tag !== "Failure") return
      expect(result.failure.reason._tag).toBe("InvalidProviderOutput")
      if (result.failure.reason._tag !== "InvalidProviderOutput") return
      expect(result.failure.reason.message).toContain("tool input failed schema decode")
      expect(result.failure.reason.raw).toBe('{"city":"Paris","temp":"warm"}')
    }),
  )
})
