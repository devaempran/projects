export * as TaskGraph from "./task-graph"

import { Planner } from "./planner"

export type Node = Planner.PlanSubtask

/**
 * Return subtasks in dependency order (deps before dependents) via DFS topological sort.
 * Unknown dependency ids are ignored. Cycles are broken (a node already being visited is
 * skipped), so the function always terminates and returns every input node exactly once.
 */
export const order = (subtasks: ReadonlyArray<Node>): Node[] => {
  const byId = new Map(subtasks.map((s) => [s.id, s]))
  const visited = new Set<string>()
  const visiting = new Set<string>()
  const result: Node[] = []
  const visit = (node: Node) => {
    if (visited.has(node.id) || visiting.has(node.id)) return
    visiting.add(node.id)
    for (const dep of node.dependsOn) {
      const d = byId.get(dep)
      if (d) visit(d)
    }
    visiting.delete(node.id)
    visited.add(node.id)
    result.push(node)
  }
  for (const s of subtasks) visit(s)
  return result
}
