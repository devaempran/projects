export * as WorkerBudget from "./budget"

/**
 * Step budgeting and no-progress auditing for one worker subtask.
 *
 * The original design gave every subtask a flat, hardcoded 8 steps and, on exhaustion,
 * returned `"Reached max steps (8) without finishing"` — discarding every observation the
 * worker had gathered. That failed in all three of the ways a step cap can fail at once:
 *
 * 1. The bound was blind to scope. "Produce a high-level code flow overview of the
 *    codebase" and "check whether a LICENSE file exists" got the same 8 steps.
 * 2. Exhaustion destroyed the work. The Reducer received a bare cap message, so a subtask
 *    that had read nine useful files contributed literally nothing.
 * 3. The loop guard only compared a call against the *immediately preceding* one, so an
 *    A→B→A→B oscillation burned the entire budget without ever tripping it.
 *
 * A step cap is still necessary — a model cannot be trusted to bound itself, and an
 * unbounded ReAct loop against a paid or local model is an unbounded bill. But the cap
 * should be the *backstop*, not the primary control. So the budget here has three layers:
 *
 * - a **soft budget**, which the planner (an LLM) estimates per subtask and which the
 *   worker (an LLM) can ask to extend — this is the "chosen by the model" part;
 * - **progress-gated extensions**, where a grant is earned by demonstrated novel progress,
 *   so a looping worker provably cannot buy its way past the soft budget;
 * - a **hard ceiling**, a flat absolute cap on total steps that no amount of model
 *   self-advocacy can raise.
 *
 * The audit below is what makes the middle layer safe, and it is a better loop detector
 * than a step cap on its own: it terminates a genuinely stuck worker *sooner* than the old
 * flat 8 (a 3-step stall aborts at step 3), which is precisely what buys the headroom to
 * let a productive worker run to 24.
 */

/** Soft budget used when neither config nor the planner supplied one. Matches the previous hardcoded cap. */
export const DEFAULT_SOFT_BUDGET = 8
/** Floor for a model-supplied estimate — below this a subtask cannot even read a file and report on it. */
export const DEFAULT_MIN_BUDGET = 3
/** Absolute per-subtask cap, extensions included. The backstop against an unbounded loop. */
export const DEFAULT_HARD_CEILING = 24
/** How many times one worker may successfully request more steps. */
export const DEFAULT_MAX_EXTENSIONS = 2
/** Consecutive no-progress steps that abort the subtask early. */
export const DEFAULT_NO_PROGRESS_LIMIT = 3
/** Largest single grant honoured from one `request_steps` call, however many the model asks for. */
export const MAX_EXTENSION_GRANT = 8

export interface LimitsInput {
  /** Default soft budget (config `maxStepsPerWorker`), used when there is no per-subtask estimate. */
  readonly softBudget?: number
  /** Floor a per-subtask estimate is clamped up to (config `minStepsPerWorker`). */
  readonly minBudget?: number
  /** Absolute cap on total steps including extensions (config `hardStepCeiling`). */
  readonly hardCeiling?: number
  /** Successful `request_steps` grants allowed (config `maxStepExtensions`). */
  readonly maxExtensions?: number
  /** Consecutive stalled steps before an early abort (config `noProgressLimit`). */
  readonly noProgressLimit?: number
  /** Per-subtask estimate authored by the planner, verifier, or a parent's `decompose`. */
  readonly estimatedSteps?: number
}

export interface Limits {
  readonly softBudget: number
  readonly hardCeiling: number
  readonly maxExtensions: number
  readonly noProgressLimit: number
  /** True when `softBudget` came from a model-authored estimate rather than config/default. */
  readonly estimated: boolean
}

const positiveInt = (value: number | undefined, fallback: number): number => {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback
  return Math.max(1, Math.floor(value))
}

const nonNegativeInt = (value: number | undefined, fallback: number): number => {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback
  return Math.max(0, Math.floor(value))
}

/**
 * Resolve the effective limits for one subtask.
 *
 * A model-authored `estimatedSteps` is clamped into `[minBudget, hardCeiling]` — it may
 * legitimately exceed the configured default (that is the entire point of letting the
 * planner size a subtask), but never the hard ceiling. The ceiling itself is raised to at
 * least the resolved soft budget so an inconsistent config (`hardStepCeiling` below
 * `maxStepsPerWorker`) can't silently truncate the very first step's budget.
 *
 * Setting `hardCeiling` equal to `softBudget` and `maxExtensions` to `0` reproduces the
 * original fixed-cap behaviour exactly.
 */
export const resolveLimits = (input: LimitsInput = {}): Limits => {
  const configured = positiveInt(input.softBudget, DEFAULT_SOFT_BUDGET)
  const minBudget = positiveInt(input.minBudget, DEFAULT_MIN_BUDGET)
  const ceilingInput = positiveInt(input.hardCeiling, Math.max(DEFAULT_HARD_CEILING, configured))
  const hasEstimate = typeof input.estimatedSteps === "number" && Number.isFinite(input.estimatedSteps)
  const requested = hasEstimate ? Math.max(1, Math.floor(input.estimatedSteps as number)) : configured
  const softBudget = Math.min(Math.max(requested, hasEstimate ? minBudget : 1), ceilingInput)
  return {
    softBudget,
    hardCeiling: Math.max(ceilingInput, softBudget),
    maxExtensions: nonNegativeInt(input.maxExtensions, DEFAULT_MAX_EXTENSIONS),
    noProgressLimit: positiveInt(input.noProgressLimit, DEFAULT_NO_PROGRESS_LIMIT),
    estimated: hasEstimate,
  }
}

/** Clamp a granted extension: at most `MAX_EXTENSION_GRANT`, and never past the hard ceiling. */
export const grantSize = (input: {
  readonly requested: number | undefined
  readonly budget: number
  readonly hardCeiling: number
}): number => {
  const headroom = input.hardCeiling - input.budget
  if (headroom <= 0) return 0
  const asked = positiveInt(input.requested, MAX_EXTENSION_GRANT)
  return Math.min(asked, MAX_EXTENSION_GRANT, headroom)
}

// ---------------------------------------------------------------------------
// Progress audit
// ---------------------------------------------------------------------------

export type Call = { readonly tool: string; readonly input: unknown }

/**
 * Sort object keys (recursively) so two structurally-identical calls compare equal
 * regardless of the JSON key order the model happened to emit.
 */
export const canonicalize = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (value !== null && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((acc, key) => {
        acc[key] = canonicalize((value as Record<string, unknown>)[key])
        return acc
      }, {})
  }
  return value
}

export const signatureOf = (call: Call): string => `${call.tool}:${JSON.stringify(canonicalize(call.input))}`

/**
 * Why a step failed to advance the subtask. `novel` is the only outcome that counts as
 * progress — and therefore the only one that earns a step extension.
 *
 * `repeat-output` matters independently of `repeat-call`: a model that keeps grepping for
 * slightly different patterns that all return "no matches" is emitting a *different* call
 * every step, so signature-equality alone would score it as progress forever.
 */
export type Outcome = "novel" | "repeat-call" | "repeat-output" | "empty"

export interface Verdict {
  readonly outcome: Outcome
  /** 1-based step at which this exact call was previously made, when `outcome` is `repeat-call`. */
  readonly firstSeenAtStep?: number
}

/** Trimmed output that carries no information — treated as a stalled step. */
const isEmptyOutput = (output: string): boolean => {
  const trimmed = output.trim()
  return trimmed.length === 0 || trimmed === "(none)" || trimmed === "[]" || trimmed === "{}"
}

export interface AuditState {
  /** Steps recorded so far (excludes synthetic checkpoint calls). */
  readonly steps: number
  /** Steps that produced genuinely new information. */
  readonly novel: number
  /** Consecutive non-novel steps ending at the most recent one. */
  readonly stalled: number
  /** Longest consecutive non-novel run seen. */
  readonly worstStall: number
  /** Novel steps recorded since the last `markExtensionGranted()`. */
  readonly novelSinceGrant: number
}

export interface Audit {
  /** Classify a step, updating the ledger. Call exactly once per executed step. */
  readonly record: (call: Call, output: string) => Verdict
  /** Classify a call *before* running it, without mutating the ledger. */
  readonly peek: (call: Call) => Verdict
  /** Reset the earned-progress counter that gates the next extension. */
  readonly markExtensionGranted: () => void
  readonly state: () => AuditState
}

/**
 * A per-subtask progress ledger.
 *
 * Unlike the previous single-slot `lastCall` guard, signatures accumulate for the whole
 * subtask, so a repeat is detected at any distance — the A→B→A→B oscillation that used to
 * consume a full budget in silence trips on its third step.
 */
export const makeAudit = (): Audit => {
  const callSteps = new Map<string, number>()
  const outputs = new Set<string>()
  let steps = 0
  let novel = 0
  let stalled = 0
  let worstStall = 0
  let novelSinceGrant = 0

  const peek = (call: Call): Verdict => {
    const firstSeenAtStep = callSteps.get(signatureOf(call))
    return firstSeenAtStep === undefined ? { outcome: "novel" } : { outcome: "repeat-call", firstSeenAtStep }
  }

  const record = (call: Call, output: string): Verdict => {
    steps++
    const signature = signatureOf(call)
    const seenAt = callSteps.get(signature)
    if (seenAt === undefined) callSteps.set(signature, steps)

    const verdict: Verdict =
      seenAt !== undefined
        ? { outcome: "repeat-call", firstSeenAtStep: seenAt }
        : isEmptyOutput(output)
          ? { outcome: "empty" }
          : outputs.has(output)
            ? { outcome: "repeat-output" }
            : { outcome: "novel" }

    outputs.add(output)
    if (verdict.outcome === "novel") {
      novel++
      novelSinceGrant++
      stalled = 0
    } else {
      stalled++
      if (stalled > worstStall) worstStall = stalled
    }
    return verdict
  }

  return {
    record,
    peek,
    markExtensionGranted: () => {
      novelSinceGrant = 0
    },
    state: () => ({ steps, novel, stalled, worstStall, novelSinceGrant }),
  }
}

/** An extension is grantable only when the worker earned it: new information since the last grant, grants left, and ceiling headroom. */
export const canExtend = (input: {
  readonly audit: AuditState
  readonly extensionsUsed: number
  readonly budget: number
  readonly limits: Limits
}): boolean =>
  input.extensionsUsed < input.limits.maxExtensions &&
  input.budget < input.limits.hardCeiling &&
  input.audit.novelSinceGrant > 0

/** Human-readable reason a worker is being asked to wrap up, used in the checkpoint prompt. */
export type ExitReason = "budget-exhausted" | "no-progress" | "ceiling-reached"

export const exitReasonText = (reason: ExitReason, input: { readonly budget: number; readonly stalled: number }): string => {
  switch (reason) {
    case "no-progress":
      return `Your last ${input.stalled} steps produced no new information (repeated calls or repeated/empty results).`
    case "ceiling-reached":
      return `You have used all ${input.budget} steps available for this subtask and no further extension is possible.`
    case "budget-exhausted":
      return `You have used all ${input.budget} steps budgeted for this subtask.`
  }
}
