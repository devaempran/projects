export * as Usage from "./usage"

import type { Usage as LLMUsage } from "@opencode-ai/llm"

const safe = (value: number | undefined) => Math.max(0, Number.isFinite(value) ? (value ?? 0) : 0)

/** Normalize a provider's raw `Usage` into a flat, always-defined shape. */
export const summarize = (usage: LLMUsage | undefined) => {
  const input = safe(usage?.nonCachedInputTokens)
  const output = safe(usage?.visibleOutputTokens)
  const reasoning = safe(usage?.reasoningTokens)
  const cacheRead = safe(usage?.cacheReadInputTokens)
  const cacheWrite = safe(usage?.cacheWriteInputTokens)
  return {
    input,
    output,
    reasoning,
    cacheRead,
    cacheWrite,
    total: input + output + reasoning,
  }
}
