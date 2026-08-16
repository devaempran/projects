import { describe, expect, test } from "bun:test"
import { DateTime, Schema } from "effect"
import { EventManifest } from "@opencode-ai/schema/event-manifest"
import { OrchestratorEvent } from "@opencode-ai/schema/orchestrator-event"
import { OpenCodeEvent } from "../src/groups/event"

// Guards the seam that makes the /orchestrator live page work: orchestrator flow events
// must be present in the server-visible manifest (so the SSE handler forwards them) and
// must encode through the exact OpenCodeEvent schema that handler uses.
describe("orchestrator events", () => {
  test("all events are registered in the server-visible manifest", () => {
    const serverTypes = new Set(Array.from(EventManifest.ServerDefinitions).map((d) => d.type))
    for (const def of OrchestratorEvent.Definitions) {
      expect(serverTypes.has(def.type)).toBe(true)
    }
    expect(OrchestratorEvent.Definitions.length).toBeGreaterThan(0)
  })

  test("a worker-step event encodes through the SSE OpenCodeEvent schema", () => {
    const encoded = Schema.encodeUnknownSync(OpenCodeEvent)({
      id: "evt_test",
      type: "session.next.orchestrator.worker.step" as const,
      data: {
        sessionID: "ses_test",
        timestamp: DateTime.makeUnsafe(0),
        subtaskId: "s1",
        step: 2,
        contextPacket: "Overall task:\nDo the thing",
      },
    }) as { data: { step: number; timestamp: unknown } }
    expect(encoded.data.step).toBe(2)
    expect(typeof encoded.data.timestamp).toBe("number")
  })

  test("a llm-call-finished event encodes through the SSE OpenCodeEvent schema", () => {
    const encoded = Schema.encodeUnknownSync(OpenCodeEvent)({
      id: "evt_test",
      type: "session.next.orchestrator.llm.call.finished" as const,
      data: {
        sessionID: "ses_test",
        timestamp: DateTime.makeUnsafe(0),
        role: "worker" as const,
        subtaskId: "s1",
        step: 1,
        attempt: 1,
        durationMs: 500,
        output: "some result text",
        usage: {
          input: 100,
          output: 20,
          reasoning: 0,
          cacheRead: 0,
          cacheWrite: 0,
          total: 120,
        },
      },
    }) as { data: { durationMs: number; usage: { total: number } } }
    expect(encoded.data.durationMs).toBe(500)
    expect(encoded.data.usage.total).toBe(120)
  })
})
