export * as LlmReport from "./llm-report"

import type { Model } from "@opencode-ai/llm"
import { Token } from "../../util/token"
import { Usage } from "../../util/usage"
import { OrchestratorObserver } from "./observer"
import { OrchestratorStructured } from "./structured"

/** Correlation fields known at a call site — everything a reporter needs besides the per-attempt data. */
export interface Correlation {
  readonly role: "planner" | "worker" | "reducer" | "verifier"
  readonly model: Model
  readonly subtaskId?: string
  readonly step?: number
  readonly iteration?: number
}

const stringify = (value: unknown): string => {
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

/** Adapt an `OrchestratorObserver` into a `structured.ts` reporter for one call site. */
export const reporterFor = (
  observer: OrchestratorObserver.Interface,
  correlation: Correlation,
): OrchestratorStructured.Reporter => {
  const contextWindow = correlation.model.route.defaults.limits?.context
  return (event) => {
    switch (event.phase) {
      case "start":
        return observer.llmCallStarted({
          role: correlation.role,
          subtaskId: correlation.subtaskId,
          step: correlation.step,
          iteration: correlation.iteration,
          attempt: event.attempt,
          model: correlation.model.id,
          system: event.system,
          prompt: event.prompt,
          contextWindow,
          estimatedInputTokens: Token.estimate((event.system ?? "") + event.prompt),
        })
      case "finish":
        return observer.llmCallFinished({
          role: correlation.role,
          subtaskId: correlation.subtaskId,
          step: correlation.step,
          iteration: correlation.iteration,
          attempt: event.attempt,
          durationMs: event.durationMs,
          output: stringify(event.output),
          finishReason: event.finishReason,
          usage: Usage.summarize(event.usage),
        })
      case "error":
        return observer.llmCallFinished({
          role: correlation.role,
          subtaskId: correlation.subtaskId,
          step: correlation.step,
          iteration: correlation.iteration,
          attempt: event.attempt,
          durationMs: event.durationMs,
          error: event.error,
          output: event.output,
        })
    }
  }
}
