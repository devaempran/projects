import { Effect } from "effect"
import { HttpRouter, HttpServerResponse } from "effect/unstable/http"

// A self-contained live view of the small-context ReAct orchestrator flow. It consumes
// the existing `/api/event` SSE stream (same-origin, so no CORS), filters the
// `session.next.orchestrator.*` events, and renders planning, stages, tasks, and the
// per-step context packet handed to each worker. Served as a plain UI route outside the
// declared API surface (see opencode httpapi AGENTS.md guidance for raw router routes).

const PAGE = /* html */ `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Orchestrator Live</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --border: #30363d; --muted: #8b949e;
    --text: #e6edf3; --accent: #58a6ff; --ok: #3fb950; --warn: #d29922; --bad: #f85149;
    --chip: #21262d;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: var(--bg); color: var(--text); }
  header { display: flex; align-items: center; gap: 12px; padding: 10px 16px;
    border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--bg); z-index: 5; }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  header .spacer { flex: 1; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .dot.live { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
  select, button { background: var(--chip); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 5px 8px; font: inherit; }
  main { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.4fr); gap: 14px; padding: 14px; }
  .col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .panel > h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted);
    margin: 0; padding: 10px 12px; border-bottom: 1px solid var(--border); }
  .panel > .body { padding: 12px; }
  .task { font-size: 13px; color: var(--muted); white-space: pre-wrap; word-break: break-word; }
  .stages { display: flex; flex-wrap: wrap; gap: 6px; }
  .stage { padding: 4px 9px; border-radius: 999px; background: var(--chip); border: 1px solid var(--border);
    color: var(--muted); font-size: 12px; }
  .stage.active { color: var(--bg); background: var(--accent); border-color: var(--accent); font-weight: 600; }
  .stage.done { color: var(--ok); border-color: var(--ok); }
  .subtask { border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; }
  .subtask:last-child { margin-bottom: 0; }
  .subtask > .head { display: flex; align-items: center; gap: 8px; padding: 8px 10px; cursor: pointer; }
  .subtask .id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--accent); font-size: 12px; }
  .subtask .desc { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { font-size: 11px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }
  .badge.running { color: var(--warn); border-color: var(--warn); }
  .badge.done { color: var(--ok); border-color: var(--ok); }
  .badge.failed, .badge.pending { }
  .badge.failed { color: var(--bad); border-color: var(--bad); }
  .badge.role-planner { color: var(--accent); border-color: var(--accent); }
  .badge.role-worker { color: var(--muted); border-color: var(--muted); }
  .badge.role-reducer { color: var(--warn); border-color: var(--warn); }
  .badge.role-verifier { color: var(--ok); border-color: var(--ok); }
  .badge.attempt { color: var(--warn); border-color: var(--warn); }
  .deps { color: var(--muted); font-size: 11px; }
  .call-tok { flex: 1; min-width: 0; text-align: right; color: var(--text); font-size: 12px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .call-dur { color: var(--muted); font-size: 11px; }
  .usage { color: var(--muted); font-size: 11px; margin-top: 6px; }
  .detail { border-top: 1px solid var(--border); padding: 8px 10px; display: none; }
  .subtask.open .detail { display: block; }
  .result { white-space: pre-wrap; word-break: break-word; font-size: 12px; color: var(--text); }
  .step { margin-top: 8px; }
  .step > .label { font-size: 11px; color: var(--muted); margin-bottom: 3px; }
  pre { margin: 0; background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px; overflow: auto; max-height: 260px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px; white-space: pre-wrap; word-break: break-word; }
  .obs { color: var(--muted); font-size: 12px; margin-top: 4px; }
  .obs b { color: var(--accent); font-weight: 600; }
  .empty { color: var(--muted); font-style: italic; }
  .gaps li { color: var(--warn); }
  @media (max-width: 820px) { main { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <span class="dot" id="conn"></span>
  <h1>Orchestrator Live</h1>
  <div class="spacer"></div>
  <label style="color:var(--muted);font-size:12px">Session</label>
  <select id="sessions"><option value="">(waiting…)</option></select>
</header>
<main>
  <div class="col">
    <section class="panel"><h2>Plan</h2><div class="body"><div id="task" class="task empty">No task yet.</div></div></section>
    <section class="panel"><h2>Stages</h2><div class="body"><div id="stages" class="stages"></div></div></section>
    <section class="panel"><h2>Summary</h2><div class="body"><div id="summary" class="result empty">—</div>
      <ul id="gaps" class="gaps"></ul></div></section>
  </div>
  <div class="col">
    <section class="panel"><h2>Tasks &amp; Context</h2><div class="body"><div id="tasks" class="empty">No subtasks yet.</div></div></section>
    <section class="panel"><h2>LLM Calls</h2><div class="body"><div id="calls" class="empty">No calls yet.</div></div></section>
  </div>
</main>
<script>
const STAGES = ["planning", "working", "reducing", "verifying", "complete"];
const sessions = new Map();
let active = null;

function params() { return new URLSearchParams(location.search); }
function ensure(id) {
  if (!sessions.has(id)) sessions.set(id, {
    task: "", status: "planning", iteration: 0, maxIterations: 0,
    order: [], subtasks: new Map(), summary: "", gaps: [], complete: false, open: new Set(),
    calls: new Map(), openCalls: new Set(),
  });
  if (!active) active = id;
  return sessions.get(id);
}
function ensureSub(s, id, description) {
  if (!s.subtasks.has(id)) s.subtasks.set(id, { description: description || "", dependsOn: [], status: "pending", result: "", steps: [], observations: [] });
  const sub = s.subtasks.get(id);
  if (description) sub.description = description;
  return sub;
}
function callKey(role, subtaskId, step, iteration, attempt) {
  return role + ":" + (subtaskId == null ? "" : subtaskId) + ":" + (step == null ? "" : step) +
    ":" + (iteration == null ? "" : iteration) + ":" + attempt;
}
function ensureCall(s, d) {
  const key = callKey(d.role, d.subtaskId, d.step, d.iteration, d.attempt);
  if (!s.calls.has(key)) s.calls.set(key, {
    key, role: d.role, subtaskId: d.subtaskId, step: d.step, iteration: d.iteration, attempt: d.attempt,
    model: "", system: "", prompt: "", contextWindow: undefined, estimatedInputTokens: undefined,
    output: "", error: "", finishReason: "", usage: null, durationMs: null, status: "running",
  });
  return s.calls.get(key);
}
function fmtNum(n) { return typeof n === "number" ? n.toLocaleString() : n; }
function callCorrelation(c) {
  const parts = [];
  if (c.subtaskId != null) parts.push(c.subtaskId);
  if (c.step != null) parts.push("step " + c.step);
  if (c.iteration != null) parts.push("iter " + c.iteration);
  return parts.join(" · ");
}
function callTokenText(c) {
  if (c.usage && typeof c.usage.total === "number") {
    return c.contextWindow ? fmtNum(c.usage.total) + " / " + fmtNum(c.contextWindow) + " tok" : fmtNum(c.usage.total) + " tok";
  }
  if (typeof c.estimatedInputTokens === "number") return "~" + fmtNum(c.estimatedInputTokens) + " tok (est)";
  return "";
}

function refreshSessionPicker() {
  const sel = document.getElementById("sessions");
  const ids = [...sessions.keys()];
  if (ids.length === 0) return;
  const current = sel.value;
  sel.innerHTML = "";
  for (const id of ids) {
    const o = document.createElement("option");
    o.value = id; o.textContent = id.slice(0, 16) + "…";
    sel.appendChild(o);
  }
  sel.value = current && sessions.has(current) ? current : active;
}

function render() {
  if (!active || !sessions.has(active)) return;
  const s = sessions.get(active);
  const task = document.getElementById("task");
  task.textContent = s.task || "No task yet.";
  task.className = "task" + (s.task ? "" : " empty");

  const stages = document.getElementById("stages");
  stages.innerHTML = "";
  const idx = STAGES.indexOf(s.status === "failed" ? "complete" : s.status);
  STAGES.forEach((name, i) => {
    const el = document.createElement("span");
    el.className = "stage" + (i < idx ? " done" : "") + (i === idx ? " active" : "");
    el.textContent = name === "complete" && s.status === "failed" ? "failed" : name;
    stages.appendChild(el);
  });
  if (s.maxIterations) {
    const it = document.createElement("span");
    it.className = "stage";
    it.textContent = "iter " + s.iteration + "/" + s.maxIterations;
    stages.appendChild(it);
  }

  document.getElementById("summary").textContent = s.summary || "—";
  document.getElementById("summary").className = "result" + (s.summary ? "" : " empty");
  const gaps = document.getElementById("gaps");
  gaps.innerHTML = "";
  s.gaps.forEach((g) => { const li = document.createElement("li"); li.textContent = g; gaps.appendChild(li); });

  const tasks = document.getElementById("tasks");
  const order = s.order.length ? s.order : [...s.subtasks.keys()];
  if (order.length === 0) { tasks.className = "empty"; tasks.textContent = "No subtasks yet."; return; }
  tasks.className = "";
  tasks.innerHTML = "";
  for (const id of order) {
    const sub = s.subtasks.get(id);
    if (!sub) continue;
    const wrap = document.createElement("div");
    wrap.className = "subtask" + (s.open.has(id) ? " open" : "");
    const head = document.createElement("div");
    head.className = "head";
    head.innerHTML =
      '<span class="id">' + esc(id) + '</span>' +
      '<span class="desc">' + esc(sub.description) + '</span>' +
      (sub.dependsOn.length ? '<span class="deps">⇢ ' + esc(sub.dependsOn.join(", ")) + '</span>' : '') +
      '<span class="badge ' + sub.status + '">' + sub.status + '</span>';
    head.onclick = () => { s.open.has(id) ? s.open.delete(id) : s.open.add(id); render(); };
    wrap.appendChild(head);

    const detail = document.createElement("div");
    detail.className = "detail";
    if (sub.result) detail.innerHTML += '<div class="result"><b>result:</b> ' + esc(sub.result) + '</div>';
    sub.steps.forEach((st) => {
      const d = document.createElement("div");
      d.className = "step";
      d.innerHTML = '<div class="label">context packet · step ' + st.step + '</div>';
      const pre = document.createElement("pre");
      pre.textContent = st.contextPacket;
      d.appendChild(pre);
      detail.appendChild(d);
    });
    sub.observations.forEach((o) => {
      const d = document.createElement("div");
      d.className = "obs";
      d.innerHTML = '<b>' + esc(o.tool) + '</b> ⇒ ' + esc(o.output);
      detail.appendChild(d);
    });
    if (!sub.result && sub.steps.length === 0 && sub.observations.length === 0)
      detail.innerHTML = '<span class="empty">No steps yet.</span>';
    wrap.appendChild(detail);
    tasks.appendChild(wrap);
  }
  renderCalls(s);
}

function renderCalls(s) {
  const calls = document.getElementById("calls");
  const list = [...s.calls.values()].reverse();
  if (list.length === 0) { calls.className = "empty"; calls.textContent = "No calls yet."; return; }
  calls.className = "";
  calls.innerHTML = "";
  for (const c of list) {
    const wrap = document.createElement("div");
    wrap.className = "subtask" + (s.openCalls.has(c.key) ? " open" : "");
    const head = document.createElement("div");
    head.className = "head";
    const corr = callCorrelation(c);
    const badgeClass = c.status === "error" ? "failed" : c.status;
    head.innerHTML =
      '<span class="badge role-' + esc(c.role) + '">' + esc(c.role) + '</span>' +
      (corr ? '<span class="id">' + esc(corr) + '</span>' : '') +
      (c.attempt > 1 ? '<span class="badge attempt">attempt ' + esc(c.attempt) + '</span>' : '') +
      '<span class="call-tok">' + esc(callTokenText(c)) + '</span>' +
      '<span class="badge ' + esc(badgeClass) + '">' + esc(c.status) + '</span>' +
      (c.durationMs != null ? '<span class="call-dur">' + esc(c.durationMs) + 'ms</span>' : '');
    head.onclick = () => { s.openCalls.has(c.key) ? s.openCalls.delete(c.key) : s.openCalls.add(c.key); render(); };
    wrap.appendChild(head);

    const detail = document.createElement("div");
    detail.className = "detail";

    const sysStep = document.createElement("div");
    sysStep.className = "step";
    sysStep.innerHTML = '<div class="label">system</div>';
    const sysPre = document.createElement("pre");
    sysPre.textContent = c.system || "(none)";
    sysStep.appendChild(sysPre);
    detail.appendChild(sysStep);

    const promptStep = document.createElement("div");
    promptStep.className = "step";
    promptStep.innerHTML = '<div class="label">prompt</div>';
    const promptPre = document.createElement("pre");
    promptPre.textContent = c.prompt || "";
    promptStep.appendChild(promptPre);
    detail.appendChild(promptStep);

    if (c.error) {
      const errStep = document.createElement("div");
      errStep.className = "step";
      errStep.innerHTML = '<div class="label">error</div>';
      const errPre = document.createElement("pre");
      errPre.textContent = c.error;
      errStep.appendChild(errPre);
      detail.appendChild(errStep);
    }
    if (c.output) {
      const outStep = document.createElement("div");
      outStep.className = "step";
      outStep.innerHTML = '<div class="label">' + (c.error ? "raw output" : "output") + '</div>';
      const outPre = document.createElement("pre");
      outPre.textContent = c.output;
      outStep.appendChild(outPre);
      detail.appendChild(outStep);
    }
    if (!c.error && !c.output && c.status === "running") {
      const pendingStep = document.createElement("div");
      pendingStep.className = "empty";
      pendingStep.textContent = "Waiting for response…";
      detail.appendChild(pendingStep);
    }

    if (c.usage) {
      const u = c.usage;
      const usageDiv = document.createElement("div");
      usageDiv.className = "usage";
      usageDiv.textContent = "usage — input " + fmtNum(u.input) + " · output " + fmtNum(u.output) +
        " · reasoning " + fmtNum(u.reasoning) + " · cacheRead " + fmtNum(u.cacheRead) +
        " · cacheWrite " + fmtNum(u.cacheWrite) + " · total " + fmtNum(u.total);
      detail.appendChild(usageDiv);
    }

    wrap.appendChild(detail);
    calls.appendChild(wrap);
  }
}

function esc(v) { return String(v == null ? "" : v).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

function handle(type, d) {
  const short = type.replace("session.next.orchestrator.", "");
  const s = ensure(d.sessionID);
  switch (short) {
    case "plan.started": s.task = d.task; s.status = "planning"; break;
    case "planned":
      s.order = d.subtasks.map((x) => x.id);
      d.subtasks.forEach((x) => { const sub = ensureSub(s, x.id, x.description); sub.dependsOn = x.dependsOn || []; });
      break;
    case "iteration.started": s.iteration = d.iteration; s.maxIterations = d.maxIterations; s.status = "working"; break;
    case "subtask.started": { const sub = ensureSub(s, d.subtaskId, d.description); sub.status = "running"; break; }
    case "worker.step": ensureSub(s, d.subtaskId).steps.push({ step: d.step, contextPacket: d.contextPacket }); break;
    case "observation": ensureSub(s, d.subtaskId).observations.push({ tool: d.tool, output: d.output }); break;
    case "subtask.finished": { const sub = ensureSub(s, d.subtaskId); sub.status = d.status; sub.result = d.result; break; }
    case "reduced": s.summary = d.summary; s.status = "reducing"; break;
    case "verified": s.gaps = d.gaps || []; s.complete = d.complete; s.status = "verifying"; break;
    case "finished": s.status = d.status; break;
    case "llm.call.started": {
      const c = ensureCall(s, d);
      c.model = d.model || "";
      c.system = d.system || "";
      c.prompt = d.prompt || "";
      c.contextWindow = d.contextWindow;
      c.estimatedInputTokens = d.estimatedInputTokens;
      c.status = "running";
      break;
    }
    case "llm.call.finished": {
      const c = ensureCall(s, d);
      c.durationMs = d.durationMs;
      c.output = d.output || "";
      c.error = d.error || "";
      c.finishReason = d.finishReason || "";
      c.usage = d.usage || null;
      c.status = d.error ? "error" : "done";
      break;
    }
    default: return;
  }
  refreshSessionPicker();
  render();
}

document.getElementById("sessions").onchange = (e) => { active = e.target.value; render(); };

function connect() {
  const conn = document.getElementById("conn");
  let url = "/api/event";
  const pw = params().get("password");
  if (pw) url += "?auth_token=" + encodeURIComponent(btoa("opencode:" + pw));
  const es = new EventSource(url);
  es.onopen = () => conn.classList.add("live");
  es.onerror = () => conn.classList.remove("live");
  es.onmessage = (ev) => {
    let payload;
    try { payload = JSON.parse(ev.data); } catch { return; }
    const type = payload && payload.type;
    if (typeof type !== "string" || !type.startsWith("session.next.orchestrator.")) return;
    // The v2 server's /api/event sends the payload under "data"; the opencode
    // instance server's /api/event (used when running the TUI) sends the same
    // payload under "properties" instead.
    handle(type, payload.data || payload.properties || {});
  };
}
connect();
</script>
</body>
</html>`

export const OrchestratorUiRoute = HttpRouter.use((router) =>
  router.add("GET", "/orchestrator", () =>
    Effect.succeed(HttpServerResponse.text(PAGE, { contentType: "text/html; charset=utf-8" })),
  ),
)
