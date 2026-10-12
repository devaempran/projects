"""
Edge-case tests for the pipeline: bad events, decisionLM failures, outages, duplicates, Redis problems,
config mistakes. Fast (about a minute); uses throwaway streams (prefix `et:`) and a mock decisionLM.
Needs the Redis container from the README.   usage: python tests/test_edge_cases.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
os.environ["PYTHONPATH"] = SRC  # inherited by the subprocesses below
RUN = os.path.join(ROOT, "run.py")
PORT = 5098
OPTIONS = ["alpha", "beta", "gamma"]
QUESTION = "Which one?"
BASE = f'''[redis]
host = "127.0.0.1"
port = 6380
[ingestion]
stream = "et:in"
group = "et-deciders"
dlq_stream = "et:dlq"
maxlen = 1000
[decision]
url = "http://127.0.0.1:{PORT}/api/decide"
question = "{QUESTION}"
context_field = "payload"
max_context_chars = 100
theta = 0.85
max_think_tokens = 100
max_think_seconds = 9
timeout_s = 5
model_version = "mock"
review_stream = "et:review"
[reliability]
batch_size = 1
block_ms = 500
reclaim_idle_s = 60
reclaim_every_s = 30
max_deliveries = 3
retries = 1
retry_backoff_s = 0.1
health_poll_s = 0.3
dedupe_ttl_s = 3600
''' + "".join(f'[[options]]\nname = "{n}"\nstream = "et:{n}"\n' for n in OPTIONS)

cfg_path = os.path.join(tempfile.mkdtemp(), "cfg.toml")
open(cfg_path, "w").write(BASE)
os.environ["PIPELINE_CONFIG"] = cfg_path
from pipeline import config, decider, runtime, worker  # noqa: E402

r = config.connect()
ALL = [config.INGESTION, config.DLQ] + config.DECISION_STREAMS
passed = failed = 0


def check(cond, name):
    global passed, failed
    passed += bool(cond)
    failed += not cond
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def reset():
    r.delete(*ALL, *r.keys("dedupe:et:*"))
    config.ensure_groups(r)


def add(**fields):
    fields.setdefault("id", f"e{time.time_ns()}")
    fields.setdefault("payload", "hello")
    return r.xadd(config.INGESTION, fields), fields


def deliver(d):
    """Read one message as the decider would and handle it."""
    resp = r.xreadgroup(config.INGESTION_GROUP, d.name, {config.INGESTION: ">"}, count=1, block=1000)
    (_s, [(mid, rec)]) = resp[0][0], resp[0][1]
    d.handle(mid, rec)
    return mid


def pending():
    return r.xpending(config.INGESTION, config.INGESTION_GROUP)["pending"]


def decisions():
    return {s: [x[1] for x in r.xrange(s)] for s in config.DECISION_STREAMS}


def start_mock():
    env = dict(os.environ, MOCK_QUESTION=QUESTION, MOCK_OPTIONS=json.dumps(OPTIONS))
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_decisionlm.py"), str(PORT)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1)
            return p
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("mock did not start")


def mock_stats():
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/"))


def run_config(text, expect_in_error):
    p = os.path.join(os.path.dirname(cfg_path), "bad.toml")
    open(p, "w").write(text)
    out = subprocess.run([sys.executable, "-c", "import pipeline.config"], cwd=HERE, capture_output=True, text=True,
                         env=dict(os.environ, PIPELINE_CONFIG=p))
    return out.returncode != 0 and "ConfigError" in out.stderr and expect_in_error in out.stderr \
        and "KeyError" not in out.stderr


# =========================== config validation ===========================
check(run_config(BASE.replace('question = "Which one?"', ""), "[decision] question"), "config: missing question named in error")
check(run_config(BASE.replace("[reliability]", "[reliabilty]"), "[reliability]"), "config: misspelt section named in error")
check(run_config(BASE.replace("theta = 0.85", "theta = 1.5"), "theta"), "config: theta out of range rejected")
check(run_config(BASE.replace("theta = 0.85", 'theta = "high"'), "theta"), "config: non-numeric theta rejected")
check(run_config(BASE.replace('name = "beta"', 'name = "alpha"'), "unique"), "config: duplicate option names rejected")
check(run_config(BASE.replace('stream = "et:beta"', 'stream = "et:alpha"'), "unique"), "config: duplicate option streams rejected")
check(run_config(BASE.replace('stream = "et:gamma"', 'stream = "et:in"'), "differ"), "config: option stream colliding with ingestion rejected")
NL = chr(10)
def drop(name):
    return "[[options]]" + NL + f'name = "{name}"' + NL + f'stream = "et:{name}"' + NL
check(run_config(BASE.replace(drop("gamma"), "").replace(drop("beta"), ""), "2 and 26"), "config: fewer than 2 options rejected")
check(run_config(BASE.replace('name = "gamma"' + NL, ""), "text `name`"), "config: option without a name rejected")
check(run_config(BASE.replace("reclaim_idle_s = 60", "reclaim_idle_s = 3"), "reclaim_idle_s"), "config: reclaim_idle_s <= timeout_s rejected")
check(run_config(BASE.replace('url = "http', 'url = "ftp'), "http(s)"), "config: non-http url rejected")
check(run_config(BASE.replace("[redis]", "[redis"), "TOML"), "config: invalid TOML reported clearly")
check(run_config(BASE.replace("port = 6380", 'port = "abc"'), "port"), "config: non-numeric port rejected")
env_bad = subprocess.run([sys.executable, "-c", "import pipeline.config"], cwd=HERE, capture_output=True, text=True,
                         env=dict(os.environ, REDIS_PORT="abc"))
check("REDIS_PORT" in env_bad.stderr, "config: non-integer REDIS_PORT env var rejected")
missing = subprocess.run([sys.executable, "-c", "import pipeline.config"], cwd=HERE, capture_output=True, text=True,
                         env=dict(os.environ, PIPELINE_CONFIG="nope.toml"))
check("not found" in missing.stderr, "config: missing file reported clearly")
check(config.HEALTH_URL == f"http://127.0.0.1:{PORT}/api/health", "config: health URL derived from the decision URL")

# =========================== pipeline behaviour ===========================
mock = start_mock()
reset()
d = decider.Decider(r, "t1", config.THETA)

# --- bad events go straight to the DLQ (no retries, nothing left pending)
for label, fields in [("missing context field", {"other": "x"}), ("empty context", {"payload": ""}),
                      ("whitespace-only context", {"payload": "   \n"})]:
    add(**{k: v for k, v in fields.items()}) if "payload" in fields else r.xadd(config.INGESTION, {"id": "x", **fields})
    deliver(d)
    last = r.xrevrange(config.DLQ, count=1)[0][1]
    check(pending() == 0 and "no usable" in last["reason"], f"bad event ({label}) -> DLQ immediately with a reason")
check(mock_stats()["requests"] == 0, "bad events never reach decisionLM")

reset()
add(payload="BAD400 please")
deliver(d)
check(pending() == 0 and "HTTP 400" in r.xrevrange(config.DLQ, count=1)[0][1]["reason"], "decisionLM 4xx -> DLQ, not retried")
add(payload="UNKNOWN option")
deliver(d)
check(pending() == 0 and "not a configured option" in r.xrevrange(config.DLQ, count=1)[0][1]["reason"], "unknown option from decisionLM -> DLQ")

# --- transient failures: retried in-process, then left pending (never lost, never wrongly dead-lettered)
reset()
before = mock_stats()["requests"]
add(payload="SERVER500")
mid = deliver(d)
check(pending() == 1 and r.xlen(config.DLQ) == 0, "HTTP 500 -> left pending, not dead-lettered")
check(mock_stats()["requests"] - before == config.RETRIES + 1, f"HTTP 500 retried in-process ({config.RETRIES} retry)")
add(payload="GARBAGE reply")
deliver(d)
check(pending() == 2, "malformed decisionLM reply -> left pending")
add(payload="FLAKY once")
deliver(d)
check(pending() == 2 and sum(len(v) for v in decisions().values()) == 1, "flaky failure recovered by the in-process retry")

# --- redelivery after idle: attempts counted, then DLQ
reset()
add(payload="SERVER500 again")
mid = deliver(d)
for n in range(config.MAX_DELIVERIES):  # simulate max_deliveries failed deliveries (each claim counts)
    r.xclaim(config.INGESTION, config.INGESTION_GROUP, "ghost", 0, [mid])
rec = r.xrange(config.INGESTION, mid, mid)[0][1]
d.handle(mid, rec, reclaimed=True)
check(pending() == 0 and "gave up" in r.xrevrange(config.DLQ, count=1)[0][1]["reason"], "too many failed deliveries -> DLQ")
d.handle(mid, rec, reclaimed=True)
check(r.xlen(config.DLQ) == 1 and sum(len(v) for v in decisions().values()) == 0,
      "reclaiming a message someone else already acked does nothing")

# --- success path, routing, review stream, truncation
reset()
add(payload="a normal event")
deliver(d)
routed = {s: v for s, v in decisions().items() if v}
rec = next(iter(routed.values()))[0]
check(len(routed) == 1 and config.OPTION_STREAMS[rec["label"]] in routed and pending() == 0, "normal event routed to its option's stream and acked")
check(rec["payload"] == "a normal event" and rec["source"] in ("fast", "reasoner") and rec["low_confidence"] == "0",
      "original fields carried through, plus decision fields")
add(payload="LOWCONF event")
deliver(d)
rev = decisions()["et:review"]
check(len(rev) == 1 and rev[0]["low_confidence"] == "1" and "mock failure" in rev[0]["reasoner_error"],
      "reasoner failure -> review stream with low_confidence=1 and reasoner_error")
add(payload="x" * 500)
deliver(d)
check(mock_stats()["max_context_len"] <= 100, "oversized context truncated to max_context_chars before sending")
newest = max((rec for v in decisions().values() for rec in v if rec["payload"].startswith("xxx")), key=lambda x: x["decided_at"])
check(newest["context_truncated"] == "1", "truncation is flagged on the event")
check(mock_stats()["max_think_seconds"] == config.MAX_THINK_SECONDS, "max_think_seconds is sent to decisionLM")

# --- de-duplication
reset()
add(id="dup-1", payload="same event")
deliver(d)
add(id="dup-1", payload="same event")
deliver(d)
check(sum(len(v) for v in decisions().values()) == 1 and pending() == 0, "same event id twice -> forwarded once, both acked")
add(payload="no id here", id="")
add(payload="no id here", id="")
deliver(d); deliver(d)
check(sum(len(v) for v in decisions().values()) == 3, "events without an id are not de-duplicated against each other")

# --- decisionLM down: messages wait (not fail), then flow when it returns
mock.terminate(); mock.wait()
reset()
mid, _ = add(payload="while down")
result = {}
t = threading.Thread(target=lambda: result.setdefault("mid", deliver(decider.Decider(r, "t2", config.THETA))))
t.start()
time.sleep(2)
idle_before = r.xpending_range(config.INGESTION, config.INGESTION_GROUP, "-", "+", 5)[0]
check(t.is_alive() and pending() == 1 and r.xlen(config.DLQ) == 0, "decisionLM down -> message waits, not dead-lettered")
check(idle_before["times_delivered"] == 1, "waiting for decisionLM burns no delivery attempts")
mock = start_mock()
t.join(10)
check(not t.is_alive() and pending() == 0 and sum(len(v) for v in decisions().values()) == 1,
      "decisionLM back -> the waiting message is decided and acked")

# --- shutdown while waiting leaves the message pending
mock.terminate(); mock.wait()
runtime.stop.clear()
add(payload="stop while down")
t = threading.Thread(target=lambda: deliver(decider.Decider(r, "t3", config.THETA)))
t.start()
time.sleep(1)
runtime.stop.set()
t.join(5)
check(not t.is_alive() and pending() == 1, "stop requested while waiting -> returns promptly, message stays pending")
runtime.stop.clear()
mock = start_mock()

# --- Redis-side problems
reset()
r.delete(config.INGESTION)  # stream and group vanish (flush, failover without persistence, ...)
d = decider.Decider(r, "t4", config.THETA)
calls = []
def step_once():
    calls.append(1)
    if len(calls) >= 2:
        runtime.stop.set()
    d.step()
runtime.stop.clear()
runtime.run_loop(r, step_once, "t4")
runtime.stop.clear()
check(r.exists(config.INGESTION) and len(calls) >= 2, "missing stream/group (NOGROUP) is recreated and the loop continues")

reset()
n = {"c": 0}
def flaky_step():
    n["c"] += 1
    if n["c"] == 1:
        import redis as _r
        raise _r.ConnectionError("simulated outage")
    runtime.stop.set()
runtime.run_loop(r, flaky_step, "t5")
runtime.stop.clear()
check(n["c"] == 2, "Redis connection error -> back off and resume instead of crashing")

# --- trimmed entry reclaimed
reset()
mid, _ = add(payload="will be trimmed")
r.xreadgroup(config.INGESTION_GROUP, "ghost", {config.INGESTION: ">"}, count=1)
r.xdel(config.INGESTION, mid)
handled = runtime.reclaim(r, config.INGESTION, config.INGESTION_GROUP, "t6", lambda *a: None, 0)
check(pending() == 0, "trimmed/deleted pending entry is cleaned up when reclaimed")

# --- worker robustness
line = worker.format_record("s", "1-0", {"id": "x", "label": "a", "confidence": "not-a-number"})
check("conf=not-a-number" in line, "worker survives a non-numeric confidence")
check("conf=?" in worker.format_record("s", "1-0", {}), "worker survives a record with no fields")
reset()
for s in config.DECISION_STREAMS[:1]:
    r.xadd(s, {"id": "w1", "label": "a", "confidence": "oops"})
    w = worker.Worker(r, s, "w1")
    resp = r.xreadgroup(w.group, "w1", {s: ">"}, count=1)
    mid = resp[0][1][0][0]
    orig = worker.process
    worker.process = lambda *a: (_ for _ in ()).throw(RuntimeError("downstream down"))
    w.handle(mid, resp[0][1][0][1])
    worker.process = orig
    check(r.xpending(s, w.group)["pending"] == 1, "worker failure leaves the message pending instead of acking it")
    time.sleep(0.2)
    n = runtime.reclaim(r, s, w.group, "w2", w.handle, 100)
    check(n == 1 and r.xpending(s, w.group)["pending"] == 0, "a second worker reclaims and finishes the abandoned message")

# --- tools
reset()
r.xadd(config.DLQ, {"id": "d1", "payload": "p", "ts": "1", "source_id": "1-0", "reason": "decisionLM rejected", "attempts": 1})
r.xadd(config.DLQ, {"id": "d2", "payload": "p", "ts": "1", "source_id": "1-1", "reason": "other", "attempts": 1})
out = subprocess.run([sys.executable, RUN, "requeue", "--reason", "rejected"], cwd=ROOT, capture_output=True, text=True,
                     env=dict(os.environ))
back = [x[1] for x in r.xrange(config.INGESTION)]
check(len(back) == 1 and back[0]["id"] == "d1" and "reason" not in back[0] and r.xlen(config.DLQ) == 1,
      "requeue_dlq moves matching events back with the dead-letter fields stripped")
r.delete(config.INGESTION)
out = subprocess.run([sys.executable, RUN, "inspect"], cwd=ROOT, capture_output=True, text=True, env=dict(os.environ))
check(out.returncode == 0 and "missing" in out.stdout and "reason=other" in out.stdout,
      "inspect_streams copes with a missing stream and shows dead-letter reasons")

# --- producer argument validation
out = subprocess.run([sys.executable, RUN, "producer", "--count", "-5"], cwd=ROOT, capture_output=True, text=True,
                     env=dict(os.environ))
check(out.returncode != 0 and "must be >= 0" in out.stderr, "producer rejects a negative --count")

mock.terminate()
reset()
r.delete(*ALL)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
