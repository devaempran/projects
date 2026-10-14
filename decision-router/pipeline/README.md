# Redis Streams pipeline for decisionLM

Takes events from a Redis stream, asks [decisionLM](../service/README.md) to choose one of a fixed set of options for each, and routes the
event to a stream named for the option it chose. Everything (the question, the options, their streams, thresholds) is set in one config file.

```
 your data ──XADD──> ingestion ──> decider(s) ──POST /api/decide──> decisionLM
                                      │
                                      ├──> decision:billing    ┐
                                      ├──> decision:technical  ├─ one stream per [[options]] entry ──> workers
                                      ├──> decision:sales      ┘
                                      ├──> decision:review     reasoner failed, so tier 1's answer is flagged
                                      └──> ingestion:dlq       events that cannot be decided
```

---

## 1. Structure

```
pipeline/
├── run.py                       entry point:  python run.py <command>
├── config/pipeline.toml         a template to fill in: the question, the options and (if needed) the Redis address
├── config/examples/             a worked example config
├── src/pipeline/
│   ├── config.py                an immutable Config object from config.load(); validation; creates streams and groups
│   ├── dashboard/               live dashboard (server.py + static/index.html), served by `start` or `python run.py dashboard`
│   ├── supervisor.py            `start`: checks, creates streams, launches decisionLM, runs and restarts the deciders, graceful stop
│   ├── decider.py               reads ingestion, calls decisionLM, routes, retries, dead-letters, reclaims
│   ├── decisionlm_client.py     HTTP client for /api/decide and its failure classes
│   ├── runtime.py               graceful shutdown, recovery from Redis problems, XAUTOCLAIM
│   ├── worker.py                one consumer per decision stream (replace `process` with real work)
│   ├── producer.py              test event generator
│   ├── setup_groups.py          create streams and groups
│   ├── inspect_streams.py       show lengths, groups, pending counts, latest dead letters
│   └── requeue_dlq.py           move dead-lettered events back to ingestion
├── demo/                        end-to-end demo: labelled support tickets and a publisher, run with the dashboard's accuracy panels on (demo/README.md)
├── tests/                       pytest suite; every test gets its own uniquely named streams and mock port
│   ├── test_config.py, test_decider.py, test_runtime.py, test_worker.py, test_tools.py, test_supervisor.py, test_dashboard.py
│   ├── test_end_to_end.py       slow: 1,000 events, kill a decider mid-run, poison message (about 3 minutes)
│   └── mock_decisionlm.py       stand-in for decisionLM used by the tests
├── requirements.txt             redis>=5.0
└── pyproject.toml               packaging (src layout, `pipeline` command), pytest and ruff settings
```

Commands: `python run.py start | setup | producer | decider NAME | worker STREAM [NAME] | inspect | requeue` (`--help` lists them).

### Configuration (`config/pipeline.toml`)

| setting | meaning |
|---|---|
| `[redis] host, port, db, password` | Redis address (env `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` override). Default `127.0.0.1:6379`. |
| `[run] deciders, workers, status_every_s, shutdown_grace_s` | what `start` runs: decider count (1), a logging worker per output stream (false), status line interval (30), how long a decider may take to finish its message on shutdown (150) |
| `[dashboard] enabled, host, port, label_field` | the live dashboard: on or off (true), address (`127.0.0.1:5055`), and an optional event field holding a ground-truth label, which turns on the accuracy panels |
| `[service] start, dir, python` | whether `start` launches decisionLM (true, only when the URL is local and nothing answers), its folder (`../../service` relative to the config) and the interpreter (its `.venv`, else the current one) |
| `[ingestion] stream, group, dlq_stream, maxlen` | main topic name, its consumer group, dead-letter stream, approximate cap on every stream |
| `[decision] url, health_url` | decisionLM's `/api/decide`; its health endpoint (default derived from `url`) |
| `[decision] question` | sent as `Question`, identical for every event |
| `[decision] context_field, max_context_chars` | which event field is sent as `Context`; longer values are truncated |
| `[[options]] name, stream` | one table per option. `name` is the text the model reads (make it descriptive; order is preserved). `stream` is its downstream topic (default `decision:<name>`). |
| `[decision] theta` | decisionLM escalates to its thinking reasoner below this confidence |
| `[decision] max_think_tokens, max_think_seconds, timeout_s` | reasoner budget, and the HTTP timeout (must cover queueing plus a full escalation) |
| `[decision] review_stream, model_version` | where reasoner-failed results go (`""` disables); tag written on each event |
| `[reliability] batch_size, block_ms` | messages per read (keep 1, see below); read block time |
| `[reliability] reclaim_idle_s, reclaim_every_s, max_deliveries` | when abandoned messages are reclaimed (must exceed `timeout_s`, enforced); how often; deliveries before the DLQ |
| `[reliability] retries, retry_backoff_s, health_poll_s, dedupe_ttl_s` | in-process retries and backoff; outage polling; how long an event `id` is remembered |

Only `[decision] question` and `[[options]]` are required; every other setting has a default. A bad setting stops start-up with an error naming it. The streams and groups for ingestion, every option and the review stream are created from this
file by `python run.py setup` and at the start of every command. Removing an option does not delete its stream. `config/pipeline.toml` ships as a **placeholder**: replace the question and options before running real data.
`config/examples/support_tickets.toml` is a filled-in example (point at it with `PIPELINE_CONFIG`).

---

## 2. How to run

### Prerequisites

- Python 3.11+ and `pip install -r requirements.txt`.
- Redis, anywhere you can reach (a Docker one-liner: `docker run -d --name redis -p 6379:6379 redis:latest --appendonly yes`).
- The decisionLM service set up once (`service/README.md`). It needs a GPU; `start` launches it for you.

### Start everything: one command

1. Copy `config/pipeline.toml`, set `[decision] question` and the `[[options]]` (and `[redis]` if Redis is not on `127.0.0.1:6379`).
2. Run, from this folder (or from the project root, where `python run.py start` does the same):

```powershell
python run.py start --config my.toml
```

`start` checks the config and Redis, creates every stream and consumer group, launches decisionLM if `[decision] url` is local and nothing answers
there, runs `[run] deciders` decider processes, restarts anything that dies, and prints a status line every 30 seconds
(`in-queue=… deciding=… routed=… dead-lettered=…`). Ctrl+C lets each decider finish the message in flight, then stops everything.

You do not need to start it before your data source: groups begin at the start of the stream, so events written earlier are decided too.
If decisionLM is down or still loading, events wait (nothing is lost, no attempt is used up) until it answers.

Running the parts yourself (`setup`, `decider NAME`, `worker STREAM`) is still possible and is what `start` does for you. Each needs a unique name.

### Feed it your data

Write events to the ingestion stream with `XADD`, as **flat string fields**, including the field named by `context_field` (default `payload`):

```
XADD ingestion MAXLEN ~ 100000 * id 7f3a... payload "customer says the invoice total is wrong" ts 1790000000.5
```

- `payload` (or your `context_field`) becomes decisionLM's Context. It must be non-empty text.
- `id` is optional but recommended: an event `id` is forwarded at most once (de-duplication).
- Other fields are carried through unchanged.

### What comes out

Each decided event lands on the option's stream with its original fields plus: `label`, `confidence`, `source` (`fast` or `reasoner`), `tier1_label`,
`low_confidence`, `context_truncated`, `decision_latency_s`, `model_version`, `decided_at` (and `reasoner_error` when the reasoner failed).
Replace `process` in `worker.py` (or run your own consumer group on those streams) to act on them.

### See it working

`demo/` publishes labelled support tickets through the pipeline and the real decisionLM and shows on the dashboard which topic each lands in, and whether that matched the label.
See [`demo/README.md`](demo/README.md).

### The dashboard

`start` serves a live, read-only dashboard at `http://127.0.0.1:5055` (`[dashboard] enabled = false` turns it off; `python run.py dashboard` runs it alone).
It is built from your config, so it works with any question and options: events per topic, what is waiting, throughput, tier 1 against the reasoner,
confidence and timing, dead letters, and the newest events in every topic with search. Statistics cover the newest 5,000 events per topic.

If your events carry a ground-truth label (say, from a test set), name that field in `[dashboard] label_field` and it also shows accuracy against the label,
tier 1's accuracy alone, a grid of label against landing topic, and an "only mismatches" filter. Without it those panels are hidden. The dashboard has no
authentication and binds to localhost by default: set `host = "0.0.0.0"` only on a network you trust.

### Operate

```powershell
python run.py inspect                       # stream lengths, groups, pending, lag, latest dead-letter reasons
python run.py requeue --reason "decisionLM" # move dead-lettered events back to ingestion (--limit N, --dry-run)
pip install pytest ruff                     # once
pytest -m "not slow"                        # 80 tests, about a minute, needs Redis (127.0.0.1:6380 by default; REDIS_PORT overrides)
pytest                                      # everything, including the 3-minute end-to-end run
ruff check .                                # lint
```

---

## 3. How it works

### Decider (one message at a time)

1. **Read** one event from `ingestion` as consumer group `deciders` (XREADGROUP). Several deciders with different names can run in parallel; Redis gives each message to one of them.
2. **Build the request** for decisionLM: Context = the event's `context_field`, Question and Options = the fixed values from the config, plus `theta` and the thinking limits.
3. **Call `/api/decide`.** decisionLM answers with an option, a calibrated confidence, and whether its slow reasoner was needed. (decisionLM itself serves one request at a time, so extra deciders queue behind it rather than speed things up.)
4. **Route** to the stream configured for the chosen option, or to `review_stream` if the reasoner failed (the reply is flagged `low_confidence`).
5. **Forward and acknowledge in one step.** A single Lua script adds the event to the target stream and XACKs the source message, and skips the add if this event `id` was already forwarded.

### When something goes wrong

| situation | what happens |
|---|---|
| event has no usable `context_field` (missing, empty, whitespace) | dead-lettered immediately with a `reason` |
| decisionLM rejects the request (HTTP 4xx) or answers with an option that is not configured | dead-lettered immediately (retrying cannot help) |
| decisionLM down or loading (connection refused, 502/503/504) | the message waits, kept alive by a heartbeat, until `/api/health` answers, then is retried. No delivery attempts are used up. |
| timeout, HTTP 5xx, malformed reply | retried `retries` times with backoff, then left pending; redelivered after `reclaim_idle_s`; dead-lettered after `max_deliveries` deliveries |
| reasoner failed (reply is `low_confidence`) | routed to `review_stream` (or the option's stream if none) with `low_confidence=1` and `reasoner_error` |
| same event `id` sent or redelivered twice | forwarded once (within `dedupe_ttl_s`); both copies acknowledged |
| context longer than `max_context_chars` | truncated; `context_truncated=1` on the output |
| Redis down, restarted, or a stream or group deleted | the process backs off and resumes; groups are recreated |
| Ctrl+C or SIGTERM | finishes the message in flight, then exits; a second press aborts and the message is reclaimed later |
| a decider dies mid-message | another decider reclaims it with XAUTOCLAIM once it has been idle longer than `reclaim_idle_s` |
| a worker crashes or its handler raises | the message stays pending; another worker reclaims it after 60 s |
| ingestion backlog nears `maxlen` | a warning is logged (XADD MAXLEN trims the oldest entries, read or not) |

Dead-lettered events keep all their fields plus `source_id`, `reason` and `attempts`; `python run.py requeue` puts them back.

### Reclaim versus latency (the setting most likely to bite)

A message's idle time starts when it is delivered. A slow decision (an escalation can take up to about two minutes, and a decider may queue behind another) must finish
before `reclaim_idle_s`, or a second decider will reclaim and decide it again. So the defaults are `batch_size = 1`, `timeout_s = 400` and `reclaim_idle_s = 600`, and the
config refuses `reclaim_idle_s <= timeout_s`. Keep `reclaim_idle_s` above (number of deciders x worst-case decision time). The cost is that a dead decider's messages are recovered only after that long.

### Delivery guarantees

- A message is **never acknowledged without being forwarded** (one atomic script).
- Calls to decisionLM are **at-least-once** (a decider can die after the call but before forwarding), but output is **once per event `id`**.
- Events without an `id` cannot be de-duplicated against each other.
- Consumer names must be unique per running process; two with the same name would share a pending list.
- Not supported: Redis Cluster (the script touches keys in different slots).

### What has been tested

- A pytest suite: 80 fast tests (bad events, decisionLM failures and outages, duplicates, Redis problems, config mistakes, the command-line tools, the supervisor, the dashboard) and 4 slow end-to-end tests (1,000 events through two real decider processes, one killed while it holds un-acked messages: all 1,000 accounted for, none duplicated, nothing pending; a poison message dead-lettered; and `start` run as a user would, deciding events written before and after it began). They use a mock decisionLM and a real Redis, with unique stream names per test.
- A live check sent 5 real events through the pipeline and the real decisionLM: all were routed and acknowledged, with nothing dead-lettered. The payloads were meaningless placeholders (all were escalated), so this shows the plumbing, not decision quality.
- Not validated: accuracy on your real decisions (decisionLM's calibration comes from ARC, a science exam), long-running behaviour, and Redis Cluster.
