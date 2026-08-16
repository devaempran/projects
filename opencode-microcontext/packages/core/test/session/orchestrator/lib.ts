import { LLMClient, LLMEvent, LLMRequest, LLMResponse, Model, type LLMClientShape } from "@opencode-ai/llm"
import * as OpenAIChat from "@opencode-ai/llm/protocols/openai-chat"
import { Effect, Layer } from "effect"

export const fakeModel = Model.make({ id: "fake", provider: "fake", route: OpenAIChat.route })

const DIRECT_TOOL_CALL = Symbol("directToolCall")

interface DirectToolCall {
  readonly [DIRECT_TOOL_CALL]: true
  readonly name: string
  readonly input: unknown
}

/**
 * Tag a queue item as a direct tool call (e.g. worker steps that call real tools like
 * `bash`/`finish`), instead of the default `generate_object` wrapper `fakeClient` uses
 * for plain objects (planner/reducer/verifier decisions).
 */
export const toolCallOf = (name: string, input: unknown): DirectToolCall => ({ [DIRECT_TOOL_CALL]: true, name, input })

const isDirectToolCall = (value: unknown): value is DirectToolCall =>
  typeof value === "object" && value !== null && DIRECT_TOOL_CALL in value

const TEXT_RESPONSE = Symbol("textResponse")

interface TextResponse {
  readonly [TEXT_RESPONSE]: true
  readonly text: string
}

/**
 * Tag a queue item as a plain-text response with no native tool call — for exercising
 * the `OrchestratorStructured.toolCall` salvage path, where a model describes a call in
 * prose/JSON instead of emitting a real tool call (observed with Ollama-hosted models
 * that don't enforce `tool_choice`).
 */
export const textResponseOf = (text: string): TextResponse => ({ [TEXT_RESPONSE]: true, text })

const isTextResponse = (value: unknown): value is TextResponse =>
  typeof value === "object" && value !== null && TEXT_RESPONSE in value

// Returns a LLMClient layer whose `generate` yields one tool call per call, pulling
// inputs from the queue in order. Plain objects are wrapped as a forced `generate_object`
// call (the `OrchestratorStructured.object` convention); items tagged via `toolCallOf`
// are emitted as a direct call to that tool by name (the `OrchestratorStructured.toolCall`
// convention worker steps use); items tagged via `textResponseOf` are emitted as a
// plain-text response with no tool call at all. `requests`, if given, collects each raw
// LLMRequest so tests can assert on what was actually sent (e.g. a repair note fed back
// into a retry prompt).
export const fakeClient = (objects: ReadonlyArray<unknown>, requests?: Array<LLMRequest>) => {
  const queue = [...objects]
  return Layer.succeed(
    LLMClient.Service,
    LLMClient.Service.of({
      prepare: () => Effect.die("unused"),
      stream: (() => Effect.die("unused")) as unknown as LLMClientShape["stream"],
      generate: ((request: LLMRequest) => {
        requests?.push(request)
        const item = queue.shift()
        if (isTextResponse(item))
          return Effect.succeed(
            LLMResponse.fromEvents([
              LLMEvent.textStart({ id: "text-1" }),
              LLMEvent.textDelta({ id: "text-1", text: item.text }),
              LLMEvent.textEnd({ id: "text-1" }),
              LLMEvent.finish({ reason: "stop" }),
            ])!,
          )
        const call = isDirectToolCall(item) ? { name: item.name, input: item.input } : { name: "generate_object", input: item }
        return Effect.succeed(
          LLMResponse.fromEvents([
            LLMEvent.toolCall({ id: "call-1", name: call.name, input: call.input }),
            LLMEvent.finish({ reason: "stop" }),
          ])!,
        )
      }) as unknown as LLMClientShape["generate"],
    }),
  )
}

/** Extract the concatenated text content of the latest user message in a captured request. */
export const latestPromptText = (request: LLMRequest): string =>
  request.messages
    .at(-1)
    ?.content.filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n") ?? ""
