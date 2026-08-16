export * as OrchestratorBridge from "./orchestrator-bridge"

import path from "path"

import { Context, DateTime, Effect, Layer, Semaphore } from "effect"
import { serviceUse } from "@opencode-ai/core/effect/service-use"
import { LayerNode } from "@opencode-ai/core/effect/layer-node"
import { AbsolutePath } from "@opencode-ai/core/schema"
import { Location } from "@opencode-ai/core/location"
import { LocationServiceMap, locationServiceMapLayer } from "@opencode-ai/core/location-services"
import { llmClient } from "@opencode-ai/core/effect/app-node-platform"
import { Config } from "@opencode-ai/core/config"
import { AgentV2 } from "@opencode-ai/core/agent"
import { ToolRegistry } from "@opencode-ai/core/tool/registry"
import { PermissionV2 } from "@opencode-ai/core/permission"
import { OrchestratorState } from "@opencode-ai/core/session/orchestrator/state"
import { SessionOrchestrator } from "@opencode-ai/core/session/orchestrator/entry"
import { SessionSchema } from "@opencode-ai/core/session/schema"
import { SessionMessage } from "@opencode-ai/core/session/message"
import { PermissionV1 } from "@opencode-ai/core/v1/permission"
import { FSUtil } from "@opencode-ai/core/fs-util"
import { Global } from "@opencode-ai/core/global"
import { LLMClient } from "@opencode-ai/llm"
import { OrchestratorEvent } from "@opencode-ai/schema/orchestrator-event"
import type { Provider } from "@/provider/provider"
import { EventV2Bridge } from "@/event-v2-bridge"
import { LLMIOLog } from "@/session/llm/io-log"
import { LLMNative } from "@/session/llm/native-request"

export interface RunInput {
  readonly directory: string
  readonly sessionID: string
  readonly agentName: string
  readonly prompt: string
  readonly permissions: PermissionV1.Ruleset
  readonly model: Provider.Model
  readonly baseURL?: string
  readonly apiKey?: string
}

export interface Interface {
  // Returns the rendered orchestrator result text when orchestration ran,
  // or undefined when this agent/config does not opt into orchestration
  // (caller should then fall through to the normal loop).
  readonly run: (input: RunInput) => Effect.Effect<string | undefined>
}

export class Service extends Context.Service<Service, Interface>()("@opencode/OrchestratorBridge") {}

export const use = serviceUse(Service)

const mapPermissions = (ruleset: PermissionV1.Ruleset): PermissionV2.Ruleset =>
  ruleset.map((rule) => ({ action: rule.permission, resource: rule.pattern, effect: rule.action }))

type LlmStartData = Parameters<SessionOrchestrator.OrchestratorObserver.Interface["llmCallStarted"]>[0]
type LlmFinishData = Parameters<SessionOrchestrator.OrchestratorObserver.Interface["llmCallFinished"]>[0]

// The observer interface doesn't thread a call id through llmCallStarted /
// llmCallFinished, so we correlate the two ourselves from the fields both
// receive (role, subtaskId, step, iteration, attempt — see core's
// llm-report.ts reporterFor). That tuple is NOT guaranteed unique on its own:
// a planner's outer retry loop re-runs with attempt reset to 1, and a
// worker's Correlation omits iteration, so two genuinely different calls can
// report the identical tuple. `llmCallTracker` disambiguates by keeping a
// per-tuple sequence counter, bumped every time a *new* start reuses a tuple
// that's already open. One tracker per orchestrator run, prefixed with
// sessionID, because ids land in a single process-wide JSONL file
// (io-log.ts) that concurrent orchestrator runs across different sessions
// all append to.
function llmCallTracker(sessionID: string) {
  const open = new Map<string, string>()
  const seq = new Map<string, number>()
  const key = (d: Pick<LlmStartData, "role" | "subtaskId" | "step" | "iteration" | "attempt">) =>
    `${sessionID}:${d.role}:${d.subtaskId ?? "-"}:${d.step ?? "-"}:${d.iteration ?? "-"}:${d.attempt}`
  return {
    start(d: Pick<LlmStartData, "role" | "subtaskId" | "step" | "iteration" | "attempt">) {
      const k = key(d)
      const n = (seq.get(k) ?? 0) + 1
      seq.set(k, n)
      const id = `${k}:${n}`
      open.set(k, id)
      return id
    },
    // Not deleted from `open`: llm-report.ts can fire finish *and* error for
    // the same attempt (decode failure after a successful provider call), and
    // both must resolve to the id from that attempt's start, not a fallback.
    finish(d: Pick<LlmFinishData, "role" | "subtaskId" | "step" | "iteration" | "attempt">) {
      const k = key(d)
      return open.get(k) ?? `${k}:0`
    },
  }
}

const DUMP_RULE = "=".repeat(80)
const DUMP_SUB = "-".repeat(80)

const renderLlmStart = (iso: string, d: LlmStartData) => {
  const meta = [
    `role=${d.role}`,
    d.subtaskId !== undefined ? `subtask=${d.subtaskId}` : undefined,
    d.step !== undefined ? `step=${d.step}` : undefined,
    d.iteration !== undefined ? `iteration=${d.iteration}` : undefined,
    `attempt=${d.attempt}`,
    `model=${d.model}`,
    d.contextWindow !== undefined ? `contextWindow=${d.contextWindow}` : undefined,
    `estimatedInputTokens=${d.estimatedInputTokens}`,
  ]
    .filter((v): v is string => v !== undefined)
    .join(" ")
  return [
    "",
    DUMP_RULE,
    `>>> LLM CALL STARTED @ ${iso}`,
    meta,
    DUMP_RULE,
    ...(d.system !== undefined ? ["", "----- SYSTEM -----", d.system] : []),
    "",
    "----- PROMPT -----",
    d.prompt,
    "",
  ].join("\n")
}

const renderLlmFinish = (iso: string, d: LlmFinishData) => {
  const meta = [
    `role=${d.role}`,
    d.subtaskId !== undefined ? `subtask=${d.subtaskId}` : undefined,
    d.step !== undefined ? `step=${d.step}` : undefined,
    d.iteration !== undefined ? `iteration=${d.iteration}` : undefined,
    `attempt=${d.attempt}`,
    `durationMs=${d.durationMs}`,
    d.finishReason !== undefined ? `finishReason=${d.finishReason}` : undefined,
  ]
    .filter((v): v is string => v !== undefined)
    .join(" ")
  const usage = d.usage
    ? `usage: input=${d.usage.input} output=${d.usage.output} reasoning=${d.usage.reasoning} cacheRead=${d.usage.cacheRead} cacheWrite=${d.usage.cacheWrite} total=${d.usage.total}`
    : undefined
  return [
    "",
    DUMP_SUB,
    `<<< LLM CALL FINISHED @ ${iso}`,
    meta,
    ...(usage !== undefined ? [usage] : []),
    DUMP_SUB,
    ...(d.output !== undefined ? ["", "----- OUTPUT -----", d.output] : []),
    ...(d.error !== undefined ? ["", "----- ERROR -----", d.error] : []),
    "",
  ].join("\n")
}

const layer = Layer.effect(
  Service,
  Effect.gen(function* () {
    const locations = yield* LocationServiceMap.Service
    const llm = yield* LLMClient.Service
    const events = yield* EventV2Bridge.Service
    const fsu = yield* FSUtil.Service
    // Serialize dump appends so parallel subtasks can't interleave writes.
    const dumpLock = yield* Semaphore.make(1)
    return Service.of({
      run: Effect.fn("OrchestratorBridge.run")(function* (input: RunInput) {
        return yield* Effect.gen(function* () {
          // Read the orchestration gate straight from core's config documents.
          // We deliberately do NOT read the per-agent `orchestrator` field from
          // core's AgentV2 service: that service is populated by async plugins that
          // are not reliably materialized in this freshly-built location bundle,
          // whereas Config loads its documents eagerly. The `orchestrator` field
          // originates from `agents.<name>.orchestrator` in config anyway, so read
          // it from the same source.
          const config = yield* Config.Service
          const entries = yield* config.entries()
          const orchestratorConfig = Config.latest(entries, "experimental")?.orchestrator
          const agentOrchestrator = Config.latest(entries, "agents")?.[input.agentName]?.orchestrator
          if (
            !SessionOrchestrator.shouldOrchestrate({
              enabled: orchestratorConfig?.enabled,
              agentOrchestrator,
            })
          ) {
            // Debug-only so the default (non-orchestrator) path stays silent at
            // info level, while `--log-level DEBUG` explains why the gate rejected.
            yield* Effect.logDebug("orchestrator.bridge.skip", {
              agentName: input.agentName,
              enabled: orchestratorConfig?.enabled,
              agentOrchestrator,
            })
            return undefined
          }
          // Only logs when orchestration actually engages, so the default
          // (non-orchestrator) path stays silent.
          yield* Effect.logInfo("orchestrator.bridge.run", {
            agentName: input.agentName,
            orchestrator: agentOrchestrator,
            maxIterations: orchestratorConfig?.maxIterations,
          })
          const tools = yield* ToolRegistry.Service
          const materialization = yield* tools.materialize(mapPermissions(input.permissions))
          const orchestratorState = yield* OrchestratorState.Service
          // Publish the orchestrator flow as live (non-durable) events so clients (e.g. the
          // /orchestrator live page) can render planning, stages, tasks, and per-step context.
          // Mirrors the observer wiring in core's runner seam (session/runner/llm.ts).
          const sessionID = SessionSchema.ID.make(input.sessionID)
          const emitOrchestrator = (
            publish: (base: { sessionID: SessionSchema.ID; timestamp: DateTime.Utc }) => Effect.Effect<unknown>,
          ) =>
            DateTime.now.pipe(
              Effect.flatMap((timestamp) => publish({ sessionID, timestamp })),
              Effect.asVoid,
            )
          // Untruncated on-disk record of every LLM input/output for this session,
          // mirroring the live /orchestrator page which caps text at 4000 chars.
          const dumpFile = path.join(Global.Path.data, "orchestrator", input.sessionID, "llm-io.log")
          const appendDump = (build: (iso: string) => string) =>
            DateTime.now.pipe(
              Effect.flatMap((now) => {
                const block = build(DateTime.toDate(now).toISOString())
                return fsu
                  .ensureDir(path.dirname(dumpFile))
                  .pipe(Effect.flatMap(() => fsu.writeFileString(dumpFile, block, { flag: "a" })))
              }),
              dumpLock.withPermits(1),
              Effect.ignore,
            )
          yield* Effect.logInfo("orchestrator.bridge.llm-io", { file: dumpFile })
          yield* appendDump((iso) =>
            [
              "",
              DUMP_RULE,
              `### ORCHESTRATOR RUN @ ${iso}`,
              `sessionID=${input.sessionID} agent=${input.agentName}`,
              DUMP_RULE,
              "",
              "----- USER PROMPT -----",
              input.prompt,
              "",
            ].join("\n"),
          )
          const llmCalls = llmCallTracker(input.sessionID)
          const ORCHESTRATOR_EVENT_TEXT_MAX_CHARS = 4000
          const truncateOrchestratorText = (value: string) =>
            value.length <= ORCHESTRATOR_EVENT_TEXT_MAX_CHARS
              ? value
              : `${value.slice(0, ORCHESTRATOR_EVENT_TEXT_MAX_CHARS)}\n[truncated]`
          const observer: SessionOrchestrator.OrchestratorObserver.Interface = {
            planStarted: (data) =>
              emitOrchestrator((base) => events.publish(OrchestratorEvent.PlanStarted, { ...base, ...data })),
            planned: (data) =>
              emitOrchestrator((base) =>
                events.publish(OrchestratorEvent.Planned, {
                  ...base,
                  subtasks: data.subtasks.map((s) => ({
                    id: s.id,
                    description: s.description,
                    dependsOn: [...s.dependsOn],
                  })),
                }),
              ),
            iterationStarted: (data) =>
              emitOrchestrator((base) => events.publish(OrchestratorEvent.IterationStarted, { ...base, ...data })),
            subtaskStarted: (data) =>
              emitOrchestrator((base) => events.publish(OrchestratorEvent.SubtaskStarted, { ...base, ...data })),
            workerStep: (data) =>
              emitOrchestrator((base) => events.publish(OrchestratorEvent.WorkerStep, { ...base, ...data })),
            observation: (data) =>
              emitOrchestrator((base) => events.publish(OrchestratorEvent.Observation, { ...base, ...data })),
            subtaskFinished: (data) =>
              emitOrchestrator((base) => events.publish(OrchestratorEvent.SubtaskFinished, { ...base, ...data })),
            reduced: (data) =>
              emitOrchestrator((base) => events.publish(OrchestratorEvent.Reduced, { ...base, ...data })),
            verified: (data) =>
              emitOrchestrator((base) =>
                events.publish(OrchestratorEvent.Verified, { ...base, ...data, gaps: [...data.gaps] }),
              ),
            finished: (data) =>
              emitOrchestrator((base) => events.publish(OrchestratorEvent.Finished, { ...base, ...data })),
            llmCallStarted: (data) =>
              emitOrchestrator((base) =>
                events.publish(OrchestratorEvent.LlmCallStarted, {
                  ...base,
                  ...data,
                  system: data.system === undefined ? undefined : truncateOrchestratorText(data.system),
                  prompt: truncateOrchestratorText(data.prompt),
                }),
              ).pipe(
                Effect.andThen(appendDump((iso) => renderLlmStart(iso, data))),
                Effect.andThen(
                  Effect.sync(() =>
                    LLMIOLog.requestText({
                      id: llmCalls.start(data),
                      sessionID: input.sessionID,
                      role: data.role,
                      subtaskId: data.subtaskId,
                      step: data.step,
                      iteration: data.iteration,
                      attempt: data.attempt,
                      modelID: data.model,
                      contextWindow: data.contextWindow,
                      estimatedInputTokens: data.estimatedInputTokens,
                      system: data.system,
                      prompt: data.prompt,
                    }),
                  ),
                ),
              ),
            llmCallFinished: (data) =>
              emitOrchestrator((base) =>
                events.publish(OrchestratorEvent.LlmCallFinished, {
                  ...base,
                  ...data,
                  output: data.output === undefined ? undefined : truncateOrchestratorText(data.output),
                }),
              ).pipe(
                Effect.andThen(appendDump((iso) => renderLlmFinish(iso, data))),
                Effect.andThen(
                  Effect.sync(() =>
                    LLMIOLog.responseText({
                      id: llmCalls.finish(data),
                      durationMs: data.durationMs,
                      output: data.output,
                      error: data.error,
                      finishReason: data.finishReason,
                      usage: data.usage,
                    }),
                  ),
                ),
              ),
          }
          const model = LLMNative.model({
            model: input.model,
            baseURL: input.baseURL,
            // The native @opencode-ai/llm route requires a non-empty apiKey
            // (Auth.apply), but local OpenAI-compatible servers like Ollama don't
            // use one. Fall back to a harmless placeholder so keyless local
            // providers work through the orchestrator path.
            apiKey: input.apiKey ?? "opencode",
            messages: [],
          })
          return yield* SessionOrchestrator.runLive({
            sessionID,
            agent: AgentV2.ID.make(input.agentName),
            model,
            prompt: input.prompt,
            materialization,
            assistantMessageID: SessionMessage.ID.create(),
            emit: () => Effect.void,
            maxIterations: orchestratorConfig?.maxIterations,
            observer,
          }).pipe(
            Effect.provideService(LLMClient.Service, llm),
            Effect.provideService(OrchestratorState.Service, orchestratorState),
            Effect.map((result) => SessionOrchestrator.render(result)),
            Effect.catchCause((cause) => Effect.succeed(`Orchestrator run failed: ${String(cause)}`)),
          )
        }).pipe(
          Effect.provide(locations.get(Location.Ref.make({ directory: AbsolutePath.make(input.directory) }))),
        )
      }),
    })
  }),
)

const locationServiceMapNode = LayerNode.make({
  service: LocationServiceMap.Service,
  layer: locationServiceMapLayer,
  deps: [],
})

export const node = LayerNode.make({
  service: Service,
  layer,
  deps: [locationServiceMapNode, llmClient, EventV2Bridge.node, FSUtil.node],
})
