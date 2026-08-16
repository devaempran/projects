export * as OrchestratorStructured from "./structured"

import { Effect, Schema } from "effect"
import {
  InvalidProviderOutputReason,
  LLM,
  LLMError,
  type LLMClientService,
  type Model,
  type ToolDefinition,
  type Usage as LLMUsage,
} from "@opencode-ai/llm"

/**
 * Recursively drop `null` values from model output. Small local models routinely
 * emit `null` for optional fields that are simply absent, but Effect's `optional()`
 * accepts `undefined`/missing — not `null` — and rejects the whole object. Removing
 * nulls (treating them as "absent") lets these fields decode while genuinely missing
 * required fields still fail as they should.
 */
export const stripNulls = (value: unknown): unknown => {
  if (value === null) return undefined
  if (Array.isArray(value)) return value.map(stripNulls).filter((item) => item !== undefined)
  if (typeof value === "object") {
    const result: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      const cleaned = stripNulls(item)
      if (cleaned !== undefined) result[key] = cleaned
    }
    return result
  }
  return value
}

export type ReporterEvent =
  | { readonly phase: "start"; readonly attempt: number; readonly system?: string; readonly prompt: string }
  | {
      readonly phase: "finish"
      readonly attempt: number
      readonly durationMs: number
      readonly output: unknown
      readonly usage?: LLMUsage
      readonly finishReason?: string
    }
  | {
      readonly phase: "error"
      readonly attempt: number
      readonly durationMs: number
      readonly error: string
      readonly output?: string
    }

export type Reporter = (event: ReporterEvent) => Effect.Effect<void>

export interface Options<S extends Schema.Top> {
  readonly model: Model
  readonly schema: S
  readonly prompt: string
  readonly system?: string
  readonly maxTokens?: number
  readonly temperature?: number
  readonly topP?: number
  readonly retries?: number
  readonly reporter?: Reporter
}

/** Fire a reporter event without letting a reporter failure break the main flow. */
const report = (reporter: Reporter | undefined, event: ReporterEvent) =>
  reporter ? reporter(event).pipe(Effect.ignore) : Effect.void

/** Pull whatever raw provider output survived onto an `InvalidProviderOutput` failure, for debugging. */
const rawOutputOf = (error: LLMError): string | undefined =>
  error.reason._tag === "InvalidProviderOutput" ? error.reason.raw : undefined

const safeJson = (value: unknown): string => {
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

export const object = <S extends Schema.Top>(
  options: Options<S>,
): Effect.Effect<Schema.Schema.Type<S>, LLMError, LLMClientService> =>
  Effect.gen(function* () {
    // Convert the Effect schema to the JSON Schema the model is guided by. This is
    // the same conversion strict mode does internally; we use the dynamic
    // `jsonSchema` mode so we receive the model's RAW output and can leniently
    // clean + decode it ourselves (see stripNulls) instead of failing on the
    // provider's strict decode.
    const document = Schema.toJsonSchemaDocument(options.schema)
    const jsonSchema =
      Object.keys(document.definitions).length === 0
        ? document.schema
        : { ...document.schema, $defs: document.definitions }
    const decode = Schema.decodeUnknownEffect(options.schema)
    const maxAttempts = (options.retries ?? 2) + 1

    // Feed the previous failure back into the prompt on retry, instead of blindly
    // re-sending an identical request a model already failed to satisfy (e.g. it
    // ignored the forced tool call, or filled in a field that doesn't decode).
    let repairNote: string | undefined
    let attempt = 0
    while (true) {
      attempt++
      const prompt = repairNote === undefined ? options.prompt : `${options.prompt}\n\n${repairNote}`
      const start = Date.now()
      yield* report(options.reporter, { phase: "start", attempt, system: options.system, prompt })
      const outcome = yield* Effect.gen(function* () {
        const response = yield* LLM.generateObject({
          model: options.model,
          system: options.system,
          prompt,
          jsonSchema,
          generation: {
            ...(options.maxTokens === undefined ? {} : { maxTokens: options.maxTokens }),
            temperature: options.temperature ?? 0,
            ...(options.topP === undefined ? {} : { topP: options.topP }),
          },
        })
        yield* report(options.reporter, {
          phase: "finish",
          attempt,
          durationMs: Date.now() - start,
          output: response.object,
          usage: response.usage,
          finishReason: response.response.finishReason,
        })
        return yield* decode(stripNulls(response.object)).pipe(
          Effect.mapError(
            (error) =>
              new LLMError({
                module: "LLM",
                method: "generateObject",
                reason: new InvalidProviderOutputReason({
                  message: `orchestrator structured decode failed: ${error.message}`,
                  raw: safeJson(response.object),
                }),
              }),
          ),
        ) as Effect.Effect<Schema.Schema.Type<S>, LLMError>
      }).pipe(
        Effect.tapError((error) =>
          report(options.reporter, {
            phase: "error",
            attempt,
            durationMs: Date.now() - start,
            error: error.message,
            output: rawOutputOf(error),
          }),
        ),
        Effect.result,
      )

      if (outcome._tag === "Success") return outcome.success
      if (attempt >= maxAttempts) return yield* Effect.fail(outcome.failure)
      const raw = rawOutputOf(outcome.failure)
      repairNote = [
        "Your previous tool call was rejected.",
        `Error: ${outcome.failure.message}`,
        raw !== undefined ? `Your previous output was:\n${raw}` : undefined,
        `The required schema is:\n${safeJson(jsonSchema)}`,
        "Call the tool again with corrected arguments that match the schema exactly. Do not add fields, do not omit required fields, and do not include any text outside the tool call.",
      ]
        .filter((line): line is string => line !== undefined)
        .join("\n\n")
    }
  })

export interface ToolCallOptions {
  readonly model: Model
  readonly tools: ReadonlyArray<ToolDefinition>
  readonly prompt: string
  readonly system?: string
  readonly maxTokens?: number
  readonly temperature?: number
  readonly topP?: number
  readonly retries?: number
  readonly reporter?: Reporter
}

export interface ToolCallResult {
  readonly name: string
  readonly input: unknown
}

/** Strip ```/```json markdown fences a model may wrap prose-JSON in. */
const stripCodeFences = (text: string): string => text.replace(/```(?:json)?/gi, "")

/**
 * Find the first balanced `{...}` substring in `text`, tolerating braces that appear
 * inside quoted strings. Returns `undefined` when no balanced object is found.
 */
const firstBalancedJsonObject = (text: string): string | undefined => {
  const start = text.indexOf("{")
  if (start === -1) return undefined
  let depth = 0
  let inString = false
  let escaped = false
  for (let i = start; i < text.length; i++) {
    const ch = text[i]
    if (inString) {
      if (escaped) escaped = false
      else if (ch === "\\") escaped = true
      else if (ch === '"') inString = false
      continue
    }
    if (ch === '"') inString = true
    else if (ch === "{") depth++
    else if (ch === "}") {
      depth--
      if (depth === 0) return text.slice(start, i + 1)
    }
  }
  return undefined
}

/**
 * Best-effort recovery for models that describe a tool call in plain text instead of
 * emitting a native tool call (observed with qwen3-coder over Ollama, which does not
 * enforce `tool_choice`). Deliberately conservative: only recognizes a single JSON
 * object naming one of the offered tools; anything else is left for the normal
 * error + retry path.
 */
const salvageToolCall = (text: string, toolNames: ReadonlySet<string>): ToolCallResult | undefined => {
  const block = firstBalancedJsonObject(stripCodeFences(text))
  if (block === undefined) return undefined
  let parsed: unknown
  try {
    parsed = JSON.parse(block)
  } catch {
    return undefined
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return undefined
  const record = parsed as Record<string, unknown>
  const name = record.name ?? record.tool
  if (typeof name !== "string" || !toolNames.has(name)) return undefined
  const input = record.arguments ?? record.input ?? record.parameters ?? {}
  if (typeof input !== "object" || input === null || Array.isArray(input)) return undefined
  return { name, input }
}

/**
 * Force the model to call exactly one of `tools` directly — via native tool-choice
 * "required", not a synthetic wrapper tool — and report which one it picked. Retries
 * with a repair note when the model responds without calling any tool, calls an
 * unrecognized tool name, or (as a last resort before failing) when a tool call can be
 * salvaged from the model's plain-text response.
 */
export const toolCall = (options: ToolCallOptions): Effect.Effect<ToolCallResult, LLMError, LLMClientService> =>
  Effect.gen(function* () {
    const toolNames = options.tools.map((tool) => tool.name)
    const toolNameSet = new Set(toolNames)
    const maxAttempts = (options.retries ?? 2) + 1
    let repairNote: string | undefined
    let attempt = 0
    while (true) {
      attempt++
      const prompt = repairNote === undefined ? options.prompt : `${options.prompt}\n\n${repairNote}`
      const start = Date.now()
      yield* report(options.reporter, { phase: "start", attempt, system: options.system, prompt })
      const outcome = yield* Effect.gen(function* () {
        const response = yield* LLM.generate(
          LLM.request({
            model: options.model,
            system: options.system,
            prompt,
            tools: options.tools,
            toolChoice: "required",
            generation: {
              ...(options.maxTokens === undefined ? {} : { maxTokens: options.maxTokens }),
              temperature: options.temperature ?? 0,
              ...(options.topP === undefined ? {} : { topP: options.topP }),
            },
          }),
        )
        const result = yield* Effect.gen(function* () {
          const call = response.toolCalls[0]
          if (!call) {
            const salvaged = salvageToolCall(response.text, toolNameSet)
            if (salvaged) return salvaged
            return yield* new LLMError({
              module: "LLM",
              method: "toolCall",
              reason: new InvalidProviderOutputReason({
                message: `model responded without calling any of the available tools: ${toolNames.join(", ")}`,
                raw: response.text || `(empty response, finishReason: ${response.finishReason})`,
              }),
            })
          }
          if (!toolNameSet.has(call.name))
            return yield* new LLMError({
              module: "LLM",
              method: "toolCall",
              reason: new InvalidProviderOutputReason({
                message: `model called unknown tool "${call.name}"; available tools: ${toolNames.join(", ")}`,
                raw: safeJson(call.input),
              }),
            })
          return { name: call.name, input: call.input } as ToolCallResult
        })
        // Carry usage/finishReason out alongside the tool call result — `response` only
        // exists in this scope, and the "finish" report below (built from `outcome.success`)
        // needs them to match what `object()` already reports for planner/reducer/verifier.
        return { result, usage: response.usage, finishReason: response.finishReason }
      }).pipe(
        Effect.tapError((error) =>
          report(options.reporter, {
            phase: "error",
            attempt,
            durationMs: Date.now() - start,
            error: error.message,
            output: rawOutputOf(error),
          }),
        ),
        Effect.result,
      )

      if (outcome._tag === "Success") {
        yield* report(options.reporter, {
          phase: "finish",
          attempt,
          durationMs: Date.now() - start,
          output: outcome.success.result,
          usage: outcome.success.usage,
          finishReason: outcome.success.finishReason,
        })
        return outcome.success.result
      }
      if (attempt >= maxAttempts) return yield* Effect.fail(outcome.failure)
      // Include a snippet of what the model actually said last time — otherwise this
      // repair note is a constant string, and since generation runs at temperature 0
      // the next attempt gets a byte-identical prompt and fails the exact same way
      // (observed in practice: attempts 2 and 3 were byte-for-byte identical retries).
      const raw = rawOutputOf(outcome.failure)
      const rawSnippet = raw !== undefined ? raw.slice(0, 500) : undefined
      repairNote = outcome.failure.message.startsWith("model responded without calling")
        ? [
            `Your previous response did not call a tool: ${outcome.failure.message}`,
            rawSnippet !== undefined ? `Your previous response was:\n${rawSnippet}` : undefined,
            "Respond with a native tool call only — do not describe the call in text.",
          ]
            .filter((line): line is string => line !== undefined)
            .join("\n")
        : [
            `Your previous response did not call one of the available tools: ${outcome.failure.message}`,
            rawSnippet !== undefined ? `Your previous response was:\n${rawSnippet}` : undefined,
            `You must call exactly one of the tools listed under "Available tools" with arguments matching its parameters.`,
          ]
            .filter((line): line is string => line !== undefined)
            .join("\n")
    }
  })
