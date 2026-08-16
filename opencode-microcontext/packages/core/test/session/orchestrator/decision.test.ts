import { test, expect } from "bun:test"
import { shouldOrchestrate } from "../../../src/session/orchestrator/decision"

test("orchestrates when enabled and agent orchestrator is a non-empty string", () => {
  expect(shouldOrchestrate({ enabled: true, agentOrchestrator: "react" })).toBe(true)
})

test("does not orchestrate when agent orchestrator is undefined", () => {
  expect(shouldOrchestrate({ enabled: true, agentOrchestrator: undefined })).toBe(false)
})

test("does not orchestrate when agent orchestrator is an empty string", () => {
  expect(shouldOrchestrate({ enabled: true, agentOrchestrator: "" })).toBe(false)
})

test("does not orchestrate when disabled", () => {
  expect(shouldOrchestrate({ enabled: false, agentOrchestrator: "react" })).toBe(false)
})

test("does not orchestrate when enabled is undefined", () => {
  expect(shouldOrchestrate({ enabled: undefined, agentOrchestrator: "react" })).toBe(false)
})
