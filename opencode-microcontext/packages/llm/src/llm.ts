import { Effect, JsonSchema, Schema } from "effect"
import { LLMClient } from "./route/client"
import {
  GenerationOptions,
  HttpOptions,
  InvalidProviderOutputReason,
  LLMError,
  LLMEvent,
  LLMRequest,
  LLMResponse,
  Message,
  type ModelInput as SchemaModelInput,
  SystemPart,
  ToolChoice,
  ToolDefinition,
  type ContentPart,
  ToolResultPart,
} from "./schema"
import { make as makeTool, toDefinitions, type ToolSchema } from "./tool"

export type ModelInput = SchemaModelInput

export type MessageInput = Message.Input

export type ToolChoiceInput = ToolChoice.Input
export type ToolChoiceMode = ToolChoice.Mode

export type ToolResultInput = Parameters<typeof ToolResultPart.make>[0]

/** Input accepted by `LLM.request`, normalized into the canonical `LLMRequest` class. */
export type RequestInput = Omit<
  ConstructorParameters<typeof LLMRequest>[0],
  "system" | "messages" | "tools" | "toolChoice" | "generation" | "http" | "providerOptions"
> & {
  readonly system?: string | SystemPart | ReadonlyArray<SystemPart>
  readonly prompt?: string | ContentPart | ReadonlyArray<ContentPart>
  readonly messages?: ReadonlyArray<Message | MessageInput>
  readonly tools?: ReadonlyArray<ToolDefinition.Input>
  readonly toolChoice?: ToolChoiceInput
  readonly generation?: GenerationOptions.Input
  readonly providerOptions?: ConstructorParameters<typeof LLMRequest>[0]["providerOptions"]
  readonly http?: HttpOptions.Input
}

export const generate = LLMClient.generate

export const stream = LLMClient.stream

export const requestInput = (input: LLMRequest): RequestInput => ({
  ...LLMRequest.input(input),
})

export const request = (input: RequestInput) => {
  const {
    system: requestSystem,
    prompt,
    messages,
    tools,
    toolChoice: requestToolChoice,
    generation: requestGeneration,
    providerOptions: requestProviderOptions,
    http: requestHttp,
    ...rest
  } = input
  return new LLMRequest({
    ...rest,
    system: SystemPart.content(requestSystem),
    messages: [...(messages?.map(Message.make) ?? []), ...(prompt === undefined ? [] : [Message.user(prompt)])],
    tools: tools?.map(ToolDefinition.make) ?? [],
    toolChoice: requestToolChoice ? ToolChoice.make(requestToolChoice) : undefined,
    generation: requestGeneration === undefined ? undefined : GenerationOptions.make(requestGeneration),
    providerOptions: requestProviderOptions,
    http: requestHttp === undefined ? undefined : HttpOptions.make(requestHttp),
  })
}

export const updateRequest = (input: LLMRequest, patch: Partial<RequestInput>) =>
  request({ ...requestInput(input), ...patch })

const GENERATE_OBJECT_TOOL_NAME = "generate_object"

const GENERATE_OBJECT_TOOL_DESCRIPTION = "Return the structured result by calling this tool."

type GenerateObjectBase = Omit<RequestInput, "tools" | "toolChoice" | "responseFormat">

export class GenerateObjectResponse<T> {
  constructor(
    readonly object: T,
    readonly response: LLMResponse,
  ) {}

  get events() {
    return this.response.events
  }

  get usage() {
    return this.response.usage
  }
}

export interface GenerateObjectOptions<S extends ToolSchema<any>> extends GenerateObjectBase {
  readonly schema: S
}

export interface GenerateObjectDynamicOptions extends GenerateObjectBase {
  /** Raw JSON Schema object describing the expected output shape. */
  readonly jsonSchema: JsonSchema.JsonSchema
}

/** Best-effort text description of what a provider actually returned, for error diagnostics. */
const describeResponse = (response: LLMResponse): string => {
  const calls = response.toolCalls.map((call) => `${call.name}(${safeJson(call.input)})`).join(", ")
  const parts: string[] = []
  if (calls) parts.push(`tool calls: ${calls}`)
  if (response.text) parts.push(`text: ${response.text}`)
  return parts.length > 0 ? parts.join(" | ") : `(empty response, finishReason: ${response.finishReason})`
}

const safeJson = (value: unknown): string => {
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

/**
 * Result of trying to recover the forced tool's input from a response that
 * may not actually contain it under the expected name/shape.
 */
type RecoveredInput = { readonly found: true; readonly value: unknown } | { readonly found: false }

const notFound: RecoveredInput = { found: false }

/**
 * Returns the first balanced `{...}` or `[...]` substring in `text`, scanning
 * left to right. Used to pull a JSON value out of prose without assuming the
 * model wrapped it in anything recognizable.
 */
const findBalancedJsonBlock = (text: string): string | undefined => {
  for (let start = 0; start < text.length; start++) {
    const open = text[start]
    if (open !== "{" && open !== "[") continue
    const close = open === "{" ? "}" : "]"
    let depth = 0
    for (let end = start; end < text.length; end++) {
      if (text[end] === open) depth++
      else if (text[end] === close) {
        depth--
        if (depth === 0) return text.slice(start, end + 1)
      }
    }
  }
  return undefined
}

/**
 * Ollama's OpenAI-compatible endpoint does not enforce `tool_choice`, so
 * small local models frequently ignore the forced `generate_object` tool call
 * entirely and answer in plain text instead — sometimes raw JSON, sometimes
 * fenced in a ```json block, sometimes with prose wrapped around it (e.g.
 * "Here is the result: {...} hope that helps"). This tries, in roughly
 * decreasing order of confidence, to recover a JSON value from that text and
 * returns the first candidate that parses.
 */
const extractJsonFromText = (text: string): RecoveredInput => {
  const candidates: string[] = []
  const fence = /```(?:json)?\s*([\s\S]*?)```/gi
  for (const match of text.matchAll(fence)) if (match[1] !== undefined) candidates.push(match[1])
  candidates.push(text)
  const balanced = findBalancedJsonBlock(text)
  if (balanced !== undefined) candidates.push(balanced)
  for (const candidate of candidates) {
    const trimmed = candidate.trim()
    if (!trimmed) continue
    try {
      return { found: true, value: JSON.parse(trimmed) }
    } catch {
      continue
    }
  }
  return notFound
}

/**
 * Recovers the input meant for the forced `generate_object` tool call. Tries,
 * in order: (1) an actual call to the forced name, (2) — since providers like
 * Ollama don't enforce `tool_choice` and small models often rename the tool —
 * the sole tool call in the response, whatever it's named, as long as its
 * input is a non-null object, (3) a JSON value embedded in the response text.
 */
const resolveGenerateObjectInput = (response: LLMResponse): RecoveredInput => {
  const named = response.toolCalls.find(
    (event) => LLMEvent.is.toolCall(event) && event.name === GENERATE_OBJECT_TOOL_NAME,
  )
  if (named && LLMEvent.is.toolCall(named)) return { found: true, value: named.input }
  if (response.toolCalls.length === 1) {
    const [only] = response.toolCalls
    if (only && typeof only.input === "object" && only.input !== null) return { found: true, value: only.input }
  }
  return extractJsonFromText(response.text)
}

const runGenerateObject = Effect.fn("LLM.generateObject")(function* (
  options: GenerateObjectBase,
  tool: ReturnType<typeof makeTool>,
) {
  const baseRequest = request(options)
  const generateRequest = LLMRequest.update(baseRequest, {
    tools: toDefinitions({ [GENERATE_OBJECT_TOOL_NAME]: tool }),
    toolChoice: ToolChoice.named(GENERATE_OBJECT_TOOL_NAME),
  })
  const response = yield* LLMClient.generate(generateRequest)
  const recovered = resolveGenerateObjectInput(response)
  if (!recovered.found)
    return yield* new LLMError({
      module: "LLM",
      method: "generateObject",
      reason: new InvalidProviderOutputReason({
        message: `generateObject: model did not call the forced \`${GENERATE_OBJECT_TOOL_NAME}\` tool`,
        raw: describeResponse(response),
      }),
    })
  const object = yield* tool._decode(recovered.value).pipe(
    Effect.mapError(
      (error) =>
        new LLMError({
          module: "LLM",
          method: "generateObject",
          reason: new InvalidProviderOutputReason({
            message: `generateObject: tool input failed schema decode: ${error.message}`,
            raw: safeJson(recovered.value),
          }),
        }),
    ),
  )
  return new GenerateObjectResponse(object, response)
})

/**
 * Run a model and decode its output against `schema`. Works on every protocol
 * because it forces a synthetic tool call internally — provider-native JSON
 * modes are intentionally avoided so behaviour is uniform.
 *
 * Two input modes:
 *
 * 1. `schema: EffectSchema<T>` — `.object` is decoded and typed as `T`.
 *    Decode failures surface as `LLMError`.
 * 2. `jsonSchema: JsonSchema.JsonSchema` — `.object` is `unknown`. Use when
 *    the schema is only available at runtime (MCP, plugin manifests). Caller validates.
 */
export function generateObject<S extends ToolSchema<any>>(
  options: GenerateObjectOptions<S>,
): Effect.Effect<GenerateObjectResponse<Schema.Schema.Type<S>>, LLMError>
export function generateObject(
  options: GenerateObjectDynamicOptions,
): Effect.Effect<GenerateObjectResponse<unknown>, LLMError>
export function generateObject(options: GenerateObjectOptions<ToolSchema<any>> | GenerateObjectDynamicOptions) {
  if ("schema" in options) {
    const { schema, ...rest } = options
    return runGenerateObject(
      rest,
      makeTool({
        description: GENERATE_OBJECT_TOOL_DESCRIPTION,
        parameters: schema,
        success: Schema.Unknown as ToolSchema<unknown>,
        execute: () => Effect.void,
      }),
    )
  }
  const { jsonSchema, ...rest } = options
  return runGenerateObject(
    rest,
    makeTool({
      description: GENERATE_OBJECT_TOOL_DESCRIPTION,
      jsonSchema,
      execute: () => Effect.void,
    }),
  )
}
