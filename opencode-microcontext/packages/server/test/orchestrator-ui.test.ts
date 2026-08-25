import { describe, expect, test } from "bun:test"
import { HttpRouter } from "effect/unstable/http"
import { OrchestratorUiRoute } from "../src/orchestrator-ui"

// packages/server had no tests at all prior to this file. There is no existing
// route-testing idiom to follow within this package, so this exercises
// `OrchestratorUiRoute` directly via `HttpRouter.toWebHandler` (the same primitive
// `packages/server/src/routes.ts:webHandler` uses to serve the real app) rather than
// pulling in the heavier HttpApi/auth scaffolding used by `packages/opencode`'s tests,
// which this route has no dependency on.

function request(path: string) {
  const handler = HttpRouter.toWebHandler(OrchestratorUiRoute).handler
  return Promise.resolve(handler(new Request(new URL(path, "http://localhost"))))
}

describe("orchestrator-ui", () => {
  test("serves the page with text/html content-type", async () => {
    const response = await request("/orchestrator")
    expect(response.status).toBe(200)
    expect(response.headers.get("content-type")).toContain("text/html")
  })

  test("contains the #now live-activity panel and the #tasks tree panel", async () => {
    const response = await request("/orchestrator")
    const body = await response.text()
    expect(body).toContain('id="now"')
    expect(body).toContain('id="tasks"')
  })

  test("handles the subtask.decomposed event and styles decomposed subtasks", async () => {
    const response = await request("/orchestrator")
    const body = await response.text()
    expect(body).toContain('case "subtask.decomposed"')
    expect(body).toContain(".badge.decomposed")
  })

  test("interpolated text is escaped via esc()", async () => {
    const response = await request("/orchestrator")
    const body = await response.text()
    // esc() must be defined, and used to guard every interpolation of untrusted
    // event-sourced text (ids, descriptions, tool names, model names, etc.) so the
    // renderer can't be tricked into injecting raw HTML from a malicious/malformed
    // SSE payload.
    expect(body).toMatch(/function esc\(v\)/)
    const escCallSites = body.match(/esc\(/g) ?? []
    // Comfortably more than one call site — this is a smoke check, not an exhaustive
    // audit, but a page that only defines esc() without using it broadly would fail.
    expect(escCallSites.length).toBeGreaterThan(10)
  })

  test("extracts a single self-contained <script> body with no external assets", async () => {
    const response = await request("/orchestrator")
    const body = await response.text()
    const scripts = [...body.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/g)]
    expect(scripts.length).toBe(1)
    expect(scripts[0][1] ?? "").not.toContain("src=")
  })
})

// --- Behavioral tests for the embedded client-side event handler ---------------------------
//
// The page is a single self-contained PAGE template literal of inline JS with no module
// boundary, so the only way to get real behavioral coverage (as opposed to weaker
// string-matching on the served HTML) is to pull the <script> body out of the response and
// evaluate it in a sandbox, then drive its `handle(type, data)` SSE dispatcher with synthetic
// event sequences. The sandbox below stubs just enough of `document` (getElementById /
// createElement returning permissive fake elements) for `render()` to run without throwing;
// assertions are made against the handler's actual session state via the exposed `ensure()`
// accessor rather than against rendered DOM/HTML, since the state is what fixes 2 and 3 are
// about.

type FakeElement = {
  className: string
  textContent: string
  innerHTML: string
  value: string
  onclick: (() => void) | null
  onchange: ((e: unknown) => void) | null
  children: FakeElement[]
  classList: { add: (c: string) => void; remove: (c: string) => void; contains: (c: string) => boolean }
  appendChild: (child: FakeElement) => FakeElement
}

function makeFakeElement(): FakeElement {
  const el: FakeElement = {
    className: "",
    textContent: "",
    innerHTML: "",
    value: "",
    onclick: null,
    onchange: null,
    children: [],
    classList: { add() {}, remove() {}, contains: () => false },
    appendChild(child) {
      el.children.push(child)
      return child
    },
  }
  return el
}

type OrchestratorSandbox = {
  handle: (type: string, d: any) => void
  ensure: (id: string) => any
}

async function loadOrchestratorSandbox(): Promise<OrchestratorSandbox> {
  const response = await request("/orchestrator")
  const body = await response.text()
  const match = body.match(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/)
  if (!match) throw new Error("no <script> body found in served page")
  // Strip the trailing `connect();` call so the sandbox never tries to open a real
  // EventSource — we drive `handle()` directly with synthetic events instead.
  const scriptBody = match[1].replace(/\nconnect\(\);\s*$/, "\n")

  const byId = new Map<string, FakeElement>()
  const fakeDocument = {
    getElementById(id: string) {
      if (!byId.has(id)) byId.set(id, makeFakeElement())
      return byId.get(id)
    },
    createElement(_tag: string) {
      return makeFakeElement()
    },
  }

  // `handle` and `ensure` are top-level function declarations in the script body; returning
  // them from the generated function exposes them (and their shared closure over `sessions`)
  // to the test without otherwise restructuring or duplicating the page's own logic.
  const factory = new Function("document", "location", scriptBody + "\nreturn { handle, ensure };")
  return factory(fakeDocument, { search: "" }) as OrchestratorSandbox
}

describe("orchestrator-ui client event handler", () => {
  test("subtask.started reusing a terminal (decomposed) id starts a fresh subtask instead of merging into the stale one", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-reuse-decomposed"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.started", {
      sessionID,
      subtaskId: "s1",
      description: "first incarnation",
      parentId: null,
      depth: 0,
    })
    handle("session.next.orchestrator.subtask.decomposed", {
      sessionID,
      subtaskId: "s1",
      children: [{ id: "s1.1", description: "child", parentId: "s1", depth: 1 }],
    })

    const before = ensure(sessionID)
    expect(before.subtasks.get("s1").status).toBe("decomposed")
    expect(before.subtasks.get("s1").children).toEqual(["s1.1"])

    // A later iteration's verifier reuses id "s1" for an unrelated new subtask.
    handle("session.next.orchestrator.subtask.started", {
      sessionID,
      subtaskId: "s1",
      description: "second incarnation",
      parentId: null,
      depth: 0,
    })

    const after = ensure(sessionID)
    const reused = after.subtasks.get("s1")
    expect(reused.status).toBe("running")
    expect(reused.description).toBe("second incarnation")
    expect(reused.children).toEqual([])
    expect(reused.result).toBe("")
    expect(reused.steps).toEqual([])
    expect(reused.observations).toEqual([])
    // The old child is still a tracked entry (nothing deletes it), but it must no longer
    // hang off the reused node's children, or the tree would render a phantom subtree.
    expect(after.subtasks.has("s1.1")).toBe(true)
  })

  test("subtask.started reusing a terminal id that was a child unlinks it from its old parent when restarted as a root", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-reuse-child"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.started", {
      sessionID,
      subtaskId: "p1",
      description: "parent",
      parentId: null,
      depth: 0,
    })
    handle("session.next.orchestrator.subtask.decomposed", {
      sessionID,
      subtaskId: "p1",
      children: [{ id: "c1", description: "child", parentId: "p1", depth: 1 }],
    })
    handle("session.next.orchestrator.subtask.finished", {
      sessionID,
      subtaskId: "c1",
      status: "done",
      result: "ok",
    })

    expect(ensure(sessionID).subtasks.get("p1").children).toEqual(["c1"])
    expect(ensure(sessionID).subtasks.get("c1").status).toBe("done")

    // "c1" is reused as an unrelated root-level subtask in a later iteration.
    handle("session.next.orchestrator.subtask.started", {
      sessionID,
      subtaskId: "c1",
      description: "unrelated new root subtask",
      parentId: null,
      depth: 0,
    })

    const s = ensure(sessionID)
    expect(s.subtasks.get("p1").children).toEqual([])
    expect(s.subtasks.get("c1").parentId).toBeNull()
    expect(s.subtasks.get("c1").status).toBe("running")
  })

  test("finished clears activeSubtaskId and marks any still-running LLM call as no longer in flight", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-finished-clears"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.started", {
      sessionID,
      subtaskId: "s1",
      description: "d",
      parentId: null,
      depth: 0,
    })
    handle("session.next.orchestrator.llm.call.started", {
      sessionID,
      role: "worker",
      subtaskId: "s1",
      step: 1,
      iteration: 1,
      attempt: 1,
      model: "m",
    })

    const mid = ensure(sessionID)
    expect(mid.activeSubtaskId).toBe("s1")
    expect([...mid.calls.values()].some((c: any) => c.status === "running")).toBe(true)

    // The orchestrator dies mid-step: neither subtask.finished nor subtask.decomposed ever
    // arrives, but the run-level `finished` event does.
    handle("session.next.orchestrator.finished", { sessionID, status: "failed" })

    const after = ensure(sessionID)
    expect(after.activeSubtaskId).toBeNull()
    expect([...after.calls.values()].some((c: any) => c.status === "running")).toBe(false)
  })

  test("iteration.started acts as a per-iteration failsafe clearing stale active indicators", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-iteration-failsafe"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.iteration.started", { sessionID, iteration: 1, maxIterations: 3 })
    handle("session.next.orchestrator.subtask.started", {
      sessionID,
      subtaskId: "s1",
      description: "d",
      parentId: null,
      depth: 0,
    })
    handle("session.next.orchestrator.llm.call.started", {
      sessionID,
      role: "worker",
      subtaskId: "s1",
      step: 1,
      iteration: 1,
      attempt: 1,
      model: "m",
    })

    const mid = ensure(sessionID)
    expect(mid.activeSubtaskId).toBe("s1")
    expect([...mid.calls.values()].some((c: any) => c.status === "running")).toBe(true)

    // No subtask.finished / llm.call.finished ever arrives for iteration 1, but iteration 2
    // starts anyway.
    handle("session.next.orchestrator.iteration.started", { sessionID, iteration: 2, maxIterations: 3 })

    const after = ensure(sessionID)
    expect(after.activeSubtaskId).toBeNull()
    expect([...after.calls.values()].some((c: any) => c.status === "running")).toBe(false)
  })
})

describe("orchestrator-ui pipeline: queue / active / finished", () => {
  test("the page renders the three pipeline lanes and a separate decomposition tree", async () => {
    const response = await request("/orchestrator")
    const body = await response.text()
    // The queue used to be something you inferred from badge colors in a single tree; these
    // three lanes plus a structure-only tree are the whole point of the layout.
    expect(body).toContain("Queue · up next")
    expect(body).toContain(".lane.queued")
    expect(body).toContain(".lane.active")
    expect(body).toContain(".lane.finished")
    expect(body).toContain('"Nothing queued."')
    expect(body).toContain('"Nothing finished yet."')
    expect(body).toContain('id="tree"')
    expect(body).toContain('case "queue.changed"')
  })

  test("queue.changed populates the queue in pop order and marks the popped subtask active", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-queue"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.queue.changed", {
      sessionID,
      queue: [
        { id: "s1", description: "first", depth: 0 },
        { id: "s2", description: "second", depth: 0 },
        { id: "s3", description: "third", depth: 0 },
      ],
      completed: 0,
    })

    let s = ensure(sessionID)
    expect(s.queue.map((q: any) => q.id)).toEqual(["s1", "s2", "s3"])
    expect(s.completed).toBe(0)
    // Every queued entry is materialized so a client attaching mid-run can show descriptions
    // for subtasks whose subtask.started it never received.
    expect(s.subtasks.get("s1").description).toBe("first")

    handle("session.next.orchestrator.queue.changed", {
      sessionID,
      queue: [
        { id: "s2", description: "second", depth: 0 },
        { id: "s3", description: "third", depth: 0 },
      ],
      active: "s1",
      completed: 0,
    })

    s = ensure(sessionID)
    expect(s.activeSubtaskId).toBe("s1")
    expect(s.queue.map((q: any) => q.id)).toEqual(["s2", "s3"])
  })

  test("children pushed by a decompose appear ahead of the parent's own sibling", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-queue-dfs"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.queue.changed", {
      sessionID,
      queue: [{ id: "s2", description: "sibling", depth: 0 }],
      active: "s1",
      completed: 0,
    })
    handle("session.next.orchestrator.subtask.decomposed", {
      sessionID,
      subtaskId: "s1",
      children: [
        { id: "s1.1", description: "slice one", parentId: "s1", depth: 1 },
        { id: "s1.2", description: "slice two", parentId: "s1", depth: 1 },
      ],
    })
    handle("session.next.orchestrator.queue.changed", {
      sessionID,
      queue: [
        { id: "s1.1", description: "slice one", depth: 1, parentId: "s1" },
        { id: "s1.2", description: "slice two", depth: 1, parentId: "s1" },
        { id: "s2", description: "sibling", depth: 0 },
      ],
      completed: 0,
    })

    const s = ensure(sessionID)
    // Depth-first: the children run before the parent's sibling.
    expect(s.queue.map((q: any) => q.id)).toEqual(["s1.1", "s1.2", "s2"])
    expect(s.queue[0].depth).toBe(1)
    expect(s.subtasks.get("s1").children).toEqual(["s1.1", "s1.2"])
  })

  test("finished subtasks are tracked in completion order, not planner order", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-finished-order"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.planned", {
      sessionID,
      subtasks: [
        { id: "s1", description: "a", dependsOn: [] },
        { id: "s2", description: "b", dependsOn: [] },
      ],
    })
    handle("session.next.orchestrator.subtask.finished", { sessionID, subtaskId: "s2", status: "done", result: "b ok" })
    handle("session.next.orchestrator.subtask.finished", { sessionID, subtaskId: "s1", status: "done", result: "a ok" })

    expect(ensure(sessionID).finishedOrder).toEqual(["s2", "s1"])
  })

  test("a decomposed parent counts as finished -- it makes no further calls and yields no result", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-decomposed-finished"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.decomposed", {
      sessionID,
      subtaskId: "s1",
      children: [{ id: "s1.1", description: "c", parentId: "s1", depth: 1 }],
    })
    expect(ensure(sessionID).finishedOrder).toEqual(["s1"])
  })
})

describe("orchestrator-ui: step budget readout", () => {
  test("subtask.started records the announced budget, ceiling and whether it was estimated", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-budget"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.started", {
      sessionID,
      subtaskId: "s1",
      description: "d",
      budget: 14,
      hardCeiling: 24,
      estimated: true,
    })

    const sub = ensure(sessionID).subtasks.get("s1")
    expect(sub.budget).toBe(14)
    expect(sub.hardCeiling).toBe(24)
    expect(sub.estimated).toBe(true)
  })

  test("steps.extended raises the displayed budget and records why", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-extended"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.started", { sessionID, subtaskId: "s1", description: "d", budget: 8 })
    handle("session.next.orchestrator.worker.step", {
      sessionID,
      subtaskId: "s1",
      step: 8,
      contextPacket: "p",
      budget: 8,
    })
    handle("session.next.orchestrator.steps.extended", {
      sessionID,
      subtaskId: "s1",
      granted: 4,
      budget: 12,
      extensions: 1,
      reason: "trace the remaining two callers",
    })

    const sub = ensure(sessionID).subtasks.get("s1")
    expect(sub.budget).toBe(12)
    expect(sub.extensions).toBe(1)
    expect(sub.notes[0]).toContain("+4 steps granted (now 12)")
    expect(sub.notes[0]).toContain("trace the remaining two callers")
  })

  test("no-progress and checkpoint events are surfaced on the subtask", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-stalled"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.started", { sessionID, subtaskId: "s1", description: "d", budget: 20 })
    handle("session.next.orchestrator.no-progress", { sessionID, subtaskId: "s1", stalledSteps: 3, step: 4 })
    handle("session.next.orchestrator.checkpoint", {
      sessionID,
      subtaskId: "s1",
      reason: "no-progress",
      step: 4,
      budget: 20,
      extendable: false,
    })

    const sub = ensure(sessionID).subtasks.get("s1")
    expect(sub.stalled).toEqual({ stalledSteps: 3, step: 4 })
    // `extendable: false` is the visible evidence that a looping worker was refused more
    // steps rather than allowed to grind on to its ceiling.
    expect(sub.checkpoint).toEqual({ reason: "no-progress", extendable: false, step: 4 })
  })

  test("a partial finish is kept distinct from failed and carries its step accounting", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-partial"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.started", { sessionID, subtaskId: "s1", description: "d", budget: 8 })
    handle("session.next.orchestrator.subtask.finished", {
      sessionID,
      subtaskId: "s1",
      status: "partial",
      result: "found the entry point; ran out of steps before tracing callers",
      steps: { used: 8, budget: 8, extensions: 0 },
    })

    const sub = ensure(sessionID).subtasks.get("s1")
    expect(sub.status).toBe("partial")
    expect(sub.stepsUsed).toBe(8)
    expect(sub.result).toContain("found the entry point")
    expect(ensure(sessionID).finishedOrder).toEqual(["s1"])
  })

  test("reusing a terminal subtask id also resets its budget bookkeeping", async () => {
    const { handle, ensure } = await loadOrchestratorSandbox()
    const sessionID = "sess-reuse-budget"

    handle("session.next.orchestrator.plan.started", { sessionID, task: "t" })
    handle("session.next.orchestrator.subtask.started", { sessionID, subtaskId: "s1", description: "first", budget: 8 })
    handle("session.next.orchestrator.worker.step", { sessionID, subtaskId: "s1", step: 5, contextPacket: "p", budget: 8 })
    handle("session.next.orchestrator.no-progress", { sessionID, subtaskId: "s1", stalledSteps: 3, step: 5 })
    handle("session.next.orchestrator.subtask.finished", {
      sessionID,
      subtaskId: "s1",
      status: "partial",
      result: "r",
      steps: { used: 5, budget: 8, extensions: 1 },
    })

    // A later iteration's verifier reuses "s1" for an unrelated subtask.
    handle("session.next.orchestrator.subtask.started", { sessionID, subtaskId: "s1", description: "second", budget: 3 })

    const sub = ensure(sessionID).subtasks.get("s1")
    expect(sub.description).toBe("second")
    expect(sub.budget).toBe(3)
    expect(sub.lastStep).toBe(0)
    expect(sub.extensions).toBe(0)
    expect(sub.stepsUsed).toBeNull()
    expect(sub.stalled).toBeNull()
    expect(sub.notes).toEqual([])
  })
})
