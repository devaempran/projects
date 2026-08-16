export * as SessionOrchestrator from "./decision"

export function shouldOrchestrate(input: {
  readonly enabled: boolean | undefined
  readonly agentOrchestrator: string | undefined
}): boolean {
  return input.enabled === true && typeof input.agentOrchestrator === "string" && input.agentOrchestrator.length > 0
}
