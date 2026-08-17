export * as OrchestratorRunner from "./runner"

import { Effect } from "effect"
import { LLMError, type LLMClientService, type Model } from "@opencode-ai/llm"
import { SessionSchema } from "../schema"
import { OrchestratorState } from "./state"
import { OrchestratorSchema } from "./schema"
import { TaskIntake } from "./intake"
import { Planner } from "./planner"
import { TaskGraph } from "./task-graph"
import { WorkerExecutor } from "./worker"
import { Reducer } from "./reducer"
import { Verifier } from "./verifier"
import { OrchestratorObserver } from "./observer"

export interface RunInput {
  readonly sessionID: SessionSchema.ID
  readonly model: Model
  readonly prompt: string
  readonly tools: WorkerExecutor.ToolRunner
  readonly toolCatalog?: WorkerExecutor.RunInput["toolCatalog"]
  readonly maxIterations?: number
  readonly maxStepsPerWorker?: number
  readonly maxDecomposeDepth?: number
  readonly observer?: OrchestratorObserver.Interface
}

export interface RunResult {
  readonly status: OrchestratorSchema.Status
  readonly iterations: number
  readonly summary: string
  readonly gaps: ReadonlyArray<string>
}

// Enforced twice: in `decompose`'s JSON Schema (`worker.ts`'s `maxItems`) and again here,
// defensively, since a local model is not guaranteed to respect the schema.
export const MAX_CHILDREN_PER_DECOMPOSE = 4

/**
 * Clamp a possibly-misconfigured `maxDecomposeDepth` to a non-negative integer. A bad
 * config value (e.g. `-1`) degrades to depth `0` ("decomposition off" -- `decompose` never
 * enters any node's tool catalog, see `worker.ts`'s `canDecompose`) rather than producing a
 * negative subtree budget that would fail every node before its worker is ever called.
 */
export const resolveMaxDecomposeDepth = (maxDecomposeDepth: number | undefined): number =>
  Math.max(0, Math.floor(maxDecomposeDepth ?? 1))

/**
 * The maximum number of nodes (a subtree's root plus every descendant) a single top-level
 * subtask may consume, given a (clamped, non-negative) `maxDecomposeDepth`.
 *
 * This is the geometric sum `Σ(i=0..d) MAX_CHILDREN_PER_DECOMPOSE^i`, not the linear
 * `1 + MAX_CHILDREN_PER_DECOMPOSE * d`: the depth gate (`worker.ts`'s `canDecompose`) lets
 * *every* node at depth `< d` decompose into up to `MAX_CHILDREN_PER_DECOMPOSE` children, so
 * the legal tree branches at every level down to `d`, not just once off the root. The linear
 * formula under-counts as soon as `d >= 2`, spuriously marking legal siblings "subtree node
 * budget exhausted" even though the depth gate would have allowed them.
 *
 * `d=0 -> 1`, `d=1 -> 5` (unchanged from the old linear formula -- no default-behavior
 * change at the shipped default of 1), `d=2 -> 21`.
 *
 * This also bounds the transitive LLM-call ceiling per top-level subtask at
 * `subtreeNodeBudget(d) * maxStepsPerWorker`.
 */
export const subtreeNodeBudget = (maxDecomposeDepth: number): number => {
  let total = 0
  let levelSize = 1 // MAX_CHILDREN_PER_DECOMPOSE^0
  for (let depth = 0; depth <= maxDecomposeDepth; depth++) {
    total += levelSize
    levelSize *= MAX_CHILDREN_PER_DECOMPOSE
  }
  return total
}

/** A node as seen by the stack-DFS traversal, before/regardless of execution. */
export interface DecomposeNode {
  readonly id: string
  readonly description: string
  readonly dependsOn: ReadonlyArray<string>
}

/**
 * One entry on the explicit DFS stack. `root` is the top-level (planner- or
 * verifier-produced) subtask id this node's subtree budget is counted against; `parentId`/
 * `parentDescription` are set only for nodes minted by a `decompose` call.
 */
export interface StackFrame {
  readonly node: DecomposeNode
  readonly depth: number
  readonly root: string
  readonly parentId?: string
  readonly parentDescription?: string
}

// TaskGraph.order() returns dependency order (deps before dependents). A plain array used
// as a LIFO stack pops from the end, so pushing that order onto the stack AS-IS would pop
// it back to front (dependents before deps) -- reversing it first means pop() yields the
// dependency order back, one node at a time, which is what the DFS needs.
export const seedStack = (nodes: ReadonlyArray<DecomposeNode>): StackFrame[] =>
  [...TaskGraph.order(nodes)].reverse().map((node) => ({ node, depth: 0, root: node.id }))

/** Mint child stack frames for a decomposed node: `${parent.id}.${n}` (1-based) ids, a
 * fresh `dependsOn: []` (children are not part of the planner's DAG), and truncated to
 * `MAX_CHILDREN_PER_DECOMPOSE` regardless of how many the model asked for. */
export const mintChildFrames = (
  frame: StackFrame,
  children: ReadonlyArray<{ readonly description: string }>,
): StackFrame[] =>
  children.slice(0, MAX_CHILDREN_PER_DECOMPOSE).map((child, i) => ({
    node: { id: `${frame.node.id}.${i + 1}`, description: child.description, dependsOn: [] },
    depth: frame.depth + 1,
    root: frame.root,
    parentId: frame.node.id,
    parentDescription: frame.node.description,
  }))

/** Push items onto a LIFO stack in reverse, so the first item of `items` is the first one
 * `pop()` returns (i.e. `items` is consumed front-to-back). */
export const pushReversed = <T>(stack: T[], items: ReadonlyArray<T>): void => {
  for (let i = items.length - 1; i >= 0; i--) stack.push(items[i]!)
}

export type DecomposeVisitStatus = "visited" | "decomposed" | "failed"

export interface DecomposeVisitRecord {
  readonly id: string
  readonly description: string
  readonly status: DecomposeVisitStatus
  readonly dependsOn: ReadonlyArray<string>
  readonly result?: string
  readonly parentId?: string
  readonly depth: number
}

export interface OrderWithDecompositionResult {
  readonly order: ReadonlyArray<string>
  readonly nodes: ReadonlyArray<DecomposeVisitRecord>
}

/**
 * Pure, LLM-free mirror of the stack-DFS traversal `run` performs below -- extracted so the
 * ordering/id-minting/budget invariants have a unit test with no model or Effect layer in
 * the loop. `decide` stands in for a worker's decision: return the child descriptions to
 * decompose into, or `undefined` to treat the node as a finished leaf.
 */
export const orderWithDecomposition = (
  nodes: ReadonlyArray<DecomposeNode>,
  decide: (node: {
    readonly id: string
    readonly description: string
    readonly depth: number
  }) => ReadonlyArray<{ readonly description: string }> | undefined,
  options: { readonly maxDecomposeDepth?: number } = {},
): OrderWithDecompositionResult => {
  const maxDecomposeDepth = resolveMaxDecomposeDepth(options.maxDecomposeDepth)
  const maxNodesPerSubtree = subtreeNodeBudget(maxDecomposeDepth)
  const stack = seedStack(nodes)
  const nodesUsed = new Map<string, number>()
  const order: string[] = []
  const records: DecomposeVisitRecord[] = []

  while (stack.length > 0) {
    const frame = stack.pop()!
    const used = nodesUsed.get(frame.root) ?? 0
    if (used >= maxNodesPerSubtree) {
      records.push({
        id: frame.node.id,
        description: frame.node.description,
        status: "failed",
        dependsOn: frame.node.dependsOn,
        result: `Subtree node budget exhausted (max ${maxNodesPerSubtree} nodes per top-level subtask)`,
        parentId: frame.parentId,
        depth: frame.depth,
      })
      continue
    }
    nodesUsed.set(frame.root, used + 1)
    order.push(frame.node.id)

    const children = decide({ id: frame.node.id, description: frame.node.description, depth: frame.depth })
    if (children === undefined) {
      records.push({
        id: frame.node.id,
        description: frame.node.description,
        status: "visited",
        dependsOn: frame.node.dependsOn,
        parentId: frame.parentId,
        depth: frame.depth,
      })
      continue
    }

    records.push({
      id: frame.node.id,
      description: frame.node.description,
      status: "decomposed",
      dependsOn: frame.node.dependsOn,
      parentId: frame.parentId,
      depth: frame.depth,
    })
    pushReversed(stack, mintChildFrames(frame, children))
  }

  return { order, nodes: records }
}

export const run = (
  input: RunInput,
): Effect.Effect<RunResult, LLMError, LLMClientService | OrchestratorState.Service> =>
  Effect.gen(function* () {
    const state = yield* OrchestratorState.Service
    const observer = input.observer ?? OrchestratorObserver.noop
    const maxIterations = input.maxIterations ?? 3
    const maxDecomposeDepth = resolveMaxDecomposeDepth(input.maxDecomposeDepth)
    const maxNodesPerSubtree = subtreeNodeBudget(maxDecomposeDepth)
    const spec = TaskIntake.fromPrompt(input.prompt)

    const persist = (data: {
      status: OrchestratorSchema.Status
      iteration: number
      subtasks: OrchestratorSchema.Subtask[]
      reduced?: string
      gaps: string[]
    }) =>
      state.set({
        sessionID: input.sessionID,
        status: data.status,
        iteration: data.iteration,
        maxIterations,
        data: { task: spec.task, subtasks: data.subtasks, reduced: data.reduced, gaps: data.gaps },
      })

    yield* persist({ status: "planning", iteration: 0, subtasks: [], gaps: [] })
    yield* observer.planStarted({ task: spec.task })
    const plan = yield* Planner.plan({ model: input.model, task: spec.task, observer })
    yield* observer.planned({
      subtasks: plan.subtasks.map((s) => ({ id: s.id, description: s.description, dependsOn: s.dependsOn })),
    })
    let pending = TaskGraph.order(plan.subtasks)
    let iteration = 0
    let summary = ""
    let gaps: string[] = []

    while (true) {
      iteration++

      // Accumulated node records for this iteration, keyed by id but tracked in traversal
      // (insertion) order -- built from every node encountered this iteration (roots AND
      // any children minted by a `decompose` call), not just `pending`. This is what makes
      // `data.subtasks` reflect the real tree instead of only the flat planner/verifier
      // output.
      const nodeOrder: string[] = []
      const nodesById = new Map<string, OrchestratorSchema.Subtask>()
      const setNode = (node: OrchestratorSchema.Subtask) => {
        if (!nodesById.has(node.id)) nodeOrder.push(node.id)
        nodesById.set(node.id, node)
      }
      const snapshot = () => nodeOrder.map((id) => nodesById.get(id)!)

      for (const s of pending) {
        setNode({ id: s.id, description: s.description, status: "pending", dependsOn: s.dependsOn })
      }

      yield* persist({ status: "working", iteration, subtasks: snapshot(), gaps })
      yield* observer.iterationStarted({ iteration, maxIterations })

      const results: WorkerExecutor.WorkerResult[] = []
      // These `pending` nodes are new roots this iteration (fresh from the planner or from
      // the verifier's `nextSubtasks`): depth 0, no parentId, their own subtree budget.
      // Reseeding the stack and the node-count map inside the iteration loop is what gives
      // each iteration's roots a fresh budget.
      const stack = seedStack(pending)
      const nodesUsed = new Map<string, number>()

      while (stack.length > 0) {
        const frame = stack.pop()!
        const used = nodesUsed.get(frame.root) ?? 0
        if (used >= maxNodesPerSubtree) {
          setNode({
            id: frame.node.id,
            description: frame.node.description,
            status: "failed",
            dependsOn: frame.node.dependsOn,
            result: `Subtree node budget exhausted (max ${maxNodesPerSubtree} nodes per top-level subtask)`,
            parentId: frame.parentId,
            depth: frame.depth,
          })
          continue
        }
        nodesUsed.set(frame.root, used + 1)

        const r = yield* WorkerExecutor.run({
          model: input.model,
          task: spec.task,
          subtask: { id: frame.node.id, description: frame.node.description },
          tools: input.tools,
          toolCatalog: input.toolCatalog,
          maxSteps: input.maxStepsPerWorker,
          depth: frame.depth,
          maxDecomposeDepth,
          parentId: frame.parentId,
          parentContext: frame.parentDescription,
          observer,
        })

        if (r.status === "decomposed") {
          setNode({
            id: frame.node.id,
            description: frame.node.description,
            status: "decomposed",
            dependsOn: frame.node.dependsOn,
            result: r.result,
            parentId: frame.parentId,
            depth: frame.depth,
          })
          const children = mintChildFrames(frame, r.children ?? [])
          for (const child of children) {
            setNode({
              id: child.node.id,
              description: child.node.description,
              status: "pending",
              dependsOn: child.node.dependsOn,
              parentId: child.parentId,
              depth: child.depth,
            })
          }
          // Persist immediately, not just at the end of the iteration -- otherwise a UI
          // client attaching mid-run, or a crash, would see a graph missing the children
          // that were just minted.
          yield* persist({ status: "working", iteration, subtasks: snapshot(), gaps })
          yield* observer.subtaskDecomposed({
            subtaskId: frame.node.id,
            children: children.map((c) => ({ id: c.node.id, description: c.node.description, depth: c.depth })),
          })
          // Push in reverse so child `.1` is the next frame popped (DFS: descend into the
          // first child before returning to this node's siblings).
          pushReversed(stack, children)
        } else {
          setNode({
            id: frame.node.id,
            description: frame.node.description,
            status: r.status,
            dependsOn: frame.node.dependsOn,
            result: r.result,
            parentId: frame.parentId,
            depth: frame.depth,
          })
          // The parent of a decomposed subtree contributes no result of its own -- only
          // leaves (done/failed) feed the Reducer.
          results.push(r)
        }
      }

      const subtaskState = snapshot()
      yield* persist({ status: "reducing", iteration, subtasks: subtaskState, gaps })
      const reduction = yield* Reducer.reduce({ model: input.model, task: spec.task, results, iteration, observer })
      summary = reduction.summary
      yield* observer.reduced({ iteration, summary })
      yield* persist({ status: "verifying", iteration, subtasks: subtaskState, reduced: summary, gaps })
      const verdict = yield* Verifier.verify({ model: input.model, task: spec.task, summary, iteration, observer })
      gaps = [...verdict.gaps]
      yield* observer.verified({ iteration, complete: verdict.complete, gaps })
      if (verdict.complete) {
        yield* persist({ status: "complete", iteration, subtasks: subtaskState, reduced: summary, gaps })
        yield* observer.finished({ status: "complete", iterations: iteration })
        return { status: "complete", iterations: iteration, summary, gaps }
      }
      if (iteration >= maxIterations) {
        yield* persist({ status: "failed", iteration, subtasks: subtaskState, reduced: summary, gaps })
        yield* observer.finished({ status: "failed", iterations: iteration })
        return { status: "failed", iterations: iteration, summary, gaps }
      }
      // Verifier re-entry: these are new roots (depth 0, no parentId), handled by
      // re-seeding `pending` -- the top of the next iteration reseeds the stack and the
      // node-count map from it, giving them a fresh subtree budget.
      pending = TaskGraph.order(verdict.nextSubtasks)
    }
  })
