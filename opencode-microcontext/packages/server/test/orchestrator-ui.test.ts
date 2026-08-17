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
