// Bookkeeping for one run-test-loop.sh cycle. Reads the captured CLI stdout + (optionally) the
// one llm-io log file run-test-loop.sh identified, folds in analyze.ts's findings, updates the
// streak/history state, and prints exactly one compact JSON object to stdout — that object is
// the only thing a human/Claude should need to read per cycle. See TEST_LOOP_RUNBOOK.md.
import fs from "fs"
import path from "path"
import { analyzeLog, type LogIssue, type LogStats } from "./analyze"

const STATE_DIR = path.join(import.meta.dir)
const STATE_FILE = path.join(STATE_DIR, "state.json")
const HISTORY_FILE = path.join(STATE_DIR, "history.jsonl")
const HISTORY_LIMIT = 5
const CLI_PREVIEW_MAX_CHARS = 300

interface State {
  runNumber: number
  streak: number
}

function loadState(): State {
  if (!fs.existsSync(STATE_FILE)) return { runNumber: 0, streak: 0 }
  try {
    const parsed = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
    return { runNumber: Number(parsed.runNumber) || 0, streak: Number(parsed.streak) || 0 }
  } catch {
    return { runNumber: 0, streak: 0 }
  }
}

function saveState(state: State) {
  fs.writeFileSync(STATE_FILE, JSON.stringify(state))
}

function loadHistory(): unknown[] {
  if (!fs.existsSync(HISTORY_FILE)) return []
  return fs
    .readFileSync(HISTORY_FILE, "utf8")
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => {
      try {
        return JSON.parse(line)
      } catch {
        return undefined
      }
    })
    .filter((entry) => entry !== undefined)
}

function saveHistory(entries: unknown[]) {
  const trimmed = entries.slice(-HISTORY_LIMIT)
  fs.writeFileSync(HISTORY_FILE, trimmed.map((entry) => JSON.stringify(entry)).join("\n") + "\n")
}

function parseArgs(argv: string[]) {
  const out: Record<string, string> = {}
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (!arg.startsWith("--")) continue
    const key = arg.slice(2)
    const value = argv[i + 1]
    out[key] = value ?? ""
    i++
  }
  return out
}

// --annotate-fix <runNumber> <description> patches the "fix" field onto an already-recorded
// history entry once a real code fix for that run's issues has been applied and committed,
// so the next cycle's context shows what was tried without re-deriving it from git log.
//
// --annotate-diagnosis <runNumber> <description> patches the "diagnosis" field instead, for
// issues traced to the model rather than the code (see TEST_LOOP_RUNBOOK.md's "Model-quality
// issue protocol") — conventionally a pointer into test-loop/model-issues.md rather than the
// full write-up, e.g. "see test-loop/model-issues.md#run-7". Kept as a separate field from
// `fix` so history.jsonl stays honest about which runs got an actual code change vs. just a
// diagnosis.
function annotate(field: "fix" | "diagnosis", runNumber: number, description: string) {
  const history = loadHistory() as Array<Record<string, unknown>>
  const entry = history.find((item) => item.run === runNumber)
  if (!entry) {
    console.error(`No history entry for run ${runNumber} (history only keeps the last ${HISTORY_LIMIT} runs)`)
    process.exit(1)
  }
  entry[field] = description
  saveHistory(history)
  console.log(JSON.stringify(entry, null, 2))
}

function extractCliPreview(cliOutput: string) {
  // Strip ANSI escapes and the "Running opencode on:" / "Model:" header lines run-terminal.sh
  // prints to stderr (captured here too since run-test-loop.sh redirects 2>&1), keep the rest.
  const clean = cliOutput.replace(/\x1b\[[0-9;]*m/g, "")
  const body = clean
    .split("\n")
    .filter((line) => !/^Running opencode on:/.test(line) && !/^Model:/.test(line))
    .join("\n")
    .trim()
  return body
}

function main() {
  const argv = process.argv.slice(2)

  if (argv[0] === "--annotate-fix" || argv[0] === "--annotate-diagnosis") {
    const field = argv[0] === "--annotate-fix" ? "fix" : "diagnosis"
    const runNumber = Number(argv[1])
    const description = argv.slice(2).join(" ")
    annotate(field, runNumber, description)
    return
  }

  const args = parseArgs(argv)
  const prompt = args.prompt ?? ""
  const exitCode = Number(args["exit-code"] ?? "1")
  const stdoutFile = args["stdout-file"]
  const logFile = args["log-file"] || undefined

  const cliOutput = stdoutFile && fs.existsSync(stdoutFile) ? fs.readFileSync(stdoutFile, "utf8") : ""
  const cliPreviewFull = extractCliPreview(cliOutput)

  const issues: LogIssue[] = []
  if (exitCode !== 0) {
    issues.push({ category: "nonzero-exit", detail: `exit code ${exitCode}` })
  }
  if (!logFile) {
    issues.push({ category: "no-log-file", detail: "no new file appeared under the llm-io log dir — check OPENCODE_LLM_IO_LOG" })
  }
  if (!cliPreviewFull) {
    issues.push({ category: "empty-cli-output", detail: "no final answer text printed to stdout" })
  }
  const crashMatch = cliOutput.match(/Orchestrator run failed:.{0,200}/)
  if (crashMatch) {
    issues.push({ category: "orchestrator-crash", detail: crashMatch[0] })
  }

  let stats: LogStats | undefined
  if (logFile && fs.existsSync(logFile)) {
    stats = analyzeLog(logFile)
    issues.push(...stats.issues)
  }

  const state = loadState()
  state.runNumber += 1
  state.streak = issues.length === 0 ? state.streak + 1 : 0
  saveState(state)

  const result = {
    run: state.runNumber,
    prompt,
    exitCode,
    logFile: logFile ? path.basename(logFile) : null,
    cliPreview: cliPreviewFull.slice(0, CLI_PREVIEW_MAX_CHARS),
    verdict: issues.length === 0 ? "clean" : "issues",
    issues,
    stats: stats ? { lines: stats.lines, byRole: stats.byRole, finishReasons: stats.finishReasons, totalUsage: stats.totalUsage } : undefined,
    streak: state.streak,
  }

  const history = loadHistory()
  history.push(result)
  saveHistory(history)

  console.log(JSON.stringify(result, null, 2))
}

main()
