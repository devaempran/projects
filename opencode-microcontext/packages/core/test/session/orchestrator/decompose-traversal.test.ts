import { describe, expect, test } from "bun:test"
import { TaskGraph } from "@opencode-ai/core/session/orchestrator/task-graph"
import {
  MAX_CHILDREN_PER_DECOMPOSE,
  orderWithDecomposition,
  resolveMaxDecomposeDepth,
  subtreeNodeBudget,
  type DecomposeNode,
} from "@opencode-ai/core/session/orchestrator/runner"

const node = (id: string, dependsOn: string[] = []): DecomposeNode => ({ id, description: `desc ${id}`, dependsOn })

describe("orderWithDecomposition", () => {
  test("matches TaskGraph.order when nothing decomposes", () => {
    const nodes = [node("s2", ["s1"]), node("s1")]
    const { order } = orderWithDecomposition(nodes, () => undefined)
    expect(order).toEqual(TaskGraph.order(nodes).map((n) => n.id))
  })

  test("a decomposed node's children run immediately after it, without reordering siblings", () => {
    const nodes = [node("s1"), node("s2")]
    const { order, nodes: records } = orderWithDecomposition(nodes, (n) =>
      n.id === "s1" ? [{ description: "child a" }, { description: "child b" }] : undefined,
    )
    expect(order).toEqual(["s1", "s1.1", "s1.2", "s2"])
    expect(records.find((r) => r.id === "s1")?.status).toBe("decomposed")
    expect(records.find((r) => r.id === "s2")?.status).toBe("visited")
  })

  test("nested decomposition is visited depth-first before returning to siblings", () => {
    const nodes = [node("s1"), node("s2")]
    const { order } = orderWithDecomposition(nodes, (n) => {
      if (n.id === "s1") return [{ description: "a" }, { description: "b" }]
      if (n.id === "s1.1") return [{ description: "aa" }, { description: "ab" }]
      return undefined
    })
    expect(order).toEqual(["s1", "s1.1", "s1.1.1", "s1.1.2", "s1.2", "s2"])
  })

  test("mints 1-based parent.N child ids and sets parentId/depth", () => {
    const nodes = [node("s1")]
    const { nodes: records } = orderWithDecomposition(nodes, (n) =>
      n.id === "s1" ? [{ description: "a" }, { description: "b" }] : undefined,
    )
    const child1 = records.find((r) => r.id === "s1.1")
    const child2 = records.find((r) => r.id === "s1.2")
    expect(child1).toMatchObject({ parentId: "s1", depth: 1, status: "visited" })
    expect(child2).toMatchObject({ parentId: "s1", depth: 1, status: "visited" })
  })

  test("truncates a children list longer than 4 to 4", () => {
    const nodes = [node("s1")]
    const many = Array.from({ length: 6 }, (_, i) => ({ description: `child ${i + 1}` }))
    const { nodes: records } = orderWithDecomposition(nodes, (n) => (n.id === "s1" ? many : undefined))
    const children = records.filter((r) => r.parentId === "s1")
    expect(children.length).toBe(MAX_CHILDREN_PER_DECOMPOSE)
    expect(children.map((c) => c.id)).toEqual(["s1.1", "s1.2", "s1.3", "s1.4"])
  })

  test("a node that always decomposes terminates within the subtree node budget; overflow comes back failed", () => {
    const nodes = [node("s1")]
    const maxDecomposeDepth = 1
    const maxNodesPerSubtree = 1 + MAX_CHILDREN_PER_DECOMPOSE * maxDecomposeDepth
    const { nodes: records } = orderWithDecomposition(nodes, () => [{ description: "x" }, { description: "y" }], {
      maxDecomposeDepth,
    })

    const nonFailed = records.filter((r) => r.status !== "failed")
    expect(nonFailed.length).toBe(maxNodesPerSubtree)
    expect(nonFailed.every((r) => r.status === "decomposed")).toBe(true)

    const failed = records.filter((r) => r.status === "failed")
    expect(failed.length).toBeGreaterThan(0)
    for (const f of failed) {
      expect(f.result).toContain("Subtree node budget exhausted")
      expect(f.result).toContain(`max ${maxNodesPerSubtree} nodes per top-level subtask`)
    }
  })

  describe("subtreeNodeBudget", () => {
    test("is the geometric sum, not the old linear formula", () => {
      // d=0 -> 1 (just the root, decompose never allowed).
      expect(subtreeNodeBudget(0)).toBe(1)
      // d=1 -> 5, unchanged from the old linear formula (1 + 4*1) -- no default-behavior
      // change at the shipped maxDecomposeDepth default of 1.
      expect(subtreeNodeBudget(1)).toBe(5)
      // d=2 -> 21 (1 + 4 + 16), not the old linear formula's 9 (1 + 4*2). The linear
      // formula would spuriously cut off a legal full tree here.
      expect(subtreeNodeBudget(2)).toBe(21)
    })

    test("resolveMaxDecomposeDepth clamps negative/fractional config values, defaults to 1", () => {
      expect(resolveMaxDecomposeDepth(-1)).toBe(0)
      expect(resolveMaxDecomposeDepth(-100)).toBe(0)
      expect(resolveMaxDecomposeDepth(2.9)).toBe(2)
      expect(resolveMaxDecomposeDepth(undefined)).toBe(1)
      expect(resolveMaxDecomposeDepth(0)).toBe(0)
    })
  })

  test("a full legal tree at maxDecomposeDepth 2 completes with no node marked budget-exhausted", () => {
    // Every node below the depth gate decomposes into exactly MAX_CHILDREN_PER_DECOMPOSE
    // children -- the maximal tree the depth gate permits. Before the geometric-sum fix,
    // the old linear budget (9 at d=2) would have cut this off partway through and
    // spuriously marked legal siblings "subtree node budget exhausted".
    const nodes = [node("s1")]
    const maxDecomposeDepth = 2
    const fullChildren = Array.from({ length: MAX_CHILDREN_PER_DECOMPOSE }, (_, i) => ({
      description: `child ${i + 1}`,
    }))
    const { order, nodes: records } = orderWithDecomposition(
      nodes,
      (n) => (n.depth < maxDecomposeDepth ? fullChildren : undefined),
      { maxDecomposeDepth },
    )

    expect(records.some((r) => r.status === "failed")).toBe(false)
    expect(order.length).toBe(subtreeNodeBudget(maxDecomposeDepth))
    expect(order.length).toBe(21)
  })
})
