import { describe, test, expect } from "bun:test"
import { Schema } from "effect"
import { Config } from "@opencode-ai/core/config"

const decode = Schema.decodeUnknownSync(Config.Info)

describe("Config orchestrator", () => {
  test("decodes experimental orchestrator and per-agent orchestrator fields", () => {
    const info = decode({
      experimental: { orchestrator: { enabled: true, maxIterations: 5 } },
      agents: { react: { orchestrator: "react", description: "x" } },
    })

    expect(info.experimental?.orchestrator?.enabled).toBe(true)
    expect(info.experimental?.orchestrator?.maxIterations).toBe(5)
    expect(info.agents?.react?.orchestrator).toBe("react")
    expect(info.agents?.react?.description).toBe("x")
  })

  test("decodes a config without orchestrator fields (backward compat)", () => {
    const info = decode({
      agents: { react: { description: "x" } },
    })

    expect(info.experimental?.orchestrator).toBeUndefined()
    expect(info.agents?.react?.orchestrator).toBeUndefined()
  })
})
