"""
End-to-end check against a mock decisionLM, using a throwaway config (different stream and option names
from config.toml, which also proves everything is driven by the config file):
  1. streams/groups are created from the config
  2. 1,000 events go through 2 deciders; one is killed mid-run while holding un-acked messages
  3. sum(decision streams) + DLQ == 1,000, deciders pending == 0, the survivor logged an XAUTOCLAIM
  4. every request matched the contract (Context = event payload, fixed Question, fixed Options in order)
  5. every event landed on the stream of the option it was labelled with
  6. a message already delivered max_deliveries times goes to the DLQ
Uses a 60s reclaim idle time, so it takes a couple of minutes.   usage: python tests/verify.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
os.environ["PYTHONPATH"] = SRC  # inherited by the subprocesses below
CFG = os.path.join(HERE, ".verify_config.toml")
PORT = 5099
QUESTION = "Which queue should this ticket go to?"
OPTIONS = [("refund request", "vt:refund"), ("bug report", "vt:bug"), ("pricing question", "vt:pricing"),
           ("something else", "vt:other")]
N = 1000

with open(CFG, "w") as f:
    f.write(f'''[redis]
host = "127.0.0.1"
port = 6380
[ingestion]
stream = "vt:in"
group = "vt-deciders"
dlq_stream = "vt:in:dlq"
maxlen = 100000
[decision]
url = "http://127.0.0.1:{PORT}/api/decide"
question = "{QUESTION}"
context_field = "payload"
max_context_chars = 8000
theta = 0.85
max_think_tokens = 6000
timeout_s = 30
model_version = "mock"
review_stream = "vt:review"
[reliability]
batch_size = 50
block_ms = 2000
reclaim_idle_s = 60
reclaim_every_s = 30
max_deliveries = 5
''' + "".join(f'[[options]]\nname = "{n}"\nstream = "{s}"\n' for n, s in OPTIONS))
os.environ["PIPELINE_CONFIG"] = CFG
from pipeline import config  # noqa: E402  (must load after PIPELINE_CONFIG is set)


def spawn(script, *args, log, extra_env=None):
    env = dict(os.environ, PYTHONUNBUFFERED="1", **(extra_env or {}))
    # "mock_decisionlm.py" lives in tests/; anything else is a `run.py` command (decider, producer, ...)
    cmd = [sys.executable, os.path.join(HERE, script)] if script.endswith(".py") else [sys.executable, os.path.join(ROOT, "run.py"), script]
    return subprocess.Popen([*cmd, *args], env=env,
                            stdout=open(os.path.join(HERE, log), "w"), stderr=subprocess.STDOUT)


def pending(r):
    return r.xpending(config.INGESTION, config.INGESTION_GROUP)


def totals(r):
    return sum(r.xlen(s) for s in config.DECISION_STREAMS), r.xlen(config.DLQ)


def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    return ok


def main():
    r = config.connect()
    r.delete(config.INGESTION, config.DLQ, *config.DECISION_STREAMS)
    config.ensure_groups(r)
    ok = check(all(r.exists(s) for s in config.DECISION_STREAMS) and
               sorted(config.DECISION_STREAMS) == sorted(s for _, s in OPTIONS) + ["vt:review"],
               f"streams created from config: {config.DECISION_STREAMS}")

    mock = spawn("mock_decisionlm.py", str(PORT), log="mock.log",
                 extra_env={"MOCK_DELAY_MS": "50", "MOCK_QUESTION": QUESTION,
                            "MOCK_OPTIONS": json.dumps([n for n, _ in OPTIONS])})
    d1 = spawn("decider", "decider-1", log="decider-1.log")
    d2 = spawn("decider", "decider-2", log="decider-2.log")
    time.sleep(3)
    spawn("producer", "--count", str(N), log="producer.log").wait()
    time.sleep(3)
    held = {c["name"]: c["pending"] for c in (pending(r)["consumers"] or [])}
    print("pending per consumer at kill time:", held, flush=True)
    d2.kill()
    killed_pending = held.get("decider-2", 0)
    ok &= check(killed_pending > 0, f"decider-2 killed while holding {killed_pending} un-acked messages")

    t0 = time.time()
    while time.time() - t0 < 240:
        if pending(r)["pending"] == 0 and r.xinfo_groups(config.INGESTION)[0]["lag"] == 0:
            break
        time.sleep(2)
    print(f"drained after {time.time() - t0:.0f}s", flush=True)

    decisions, dlq = totals(r)
    ok &= check(decisions + dlq == N, f"decision streams ({decisions}) + DLQ ({dlq}) == {N}")
    ok &= check(pending(r)["pending"] == 0, f"no pending entries in group {config.INGESTION_GROUP}")
    ok &= check("reclaimed" in open(os.path.join(HERE, "decider-1.log")).read(),
                "decider-1 log shows XAUTOCLAIM reclaimed decider-2's messages")
    d1.terminate()
    d1.wait(timeout=10)

    stats = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/"))
    ok &= check(stats["bad"] == 0 and stats["requests"] >= N, f"all {stats['requests']} requests matched the contract")
    by_stream = {s: [rec for _, rec in r.xrange(s)] for s in config.DECISION_STREAMS}
    ok &= check(all(config.OPTION_STREAMS[rec["label"]] == s for s, recs in by_stream.items() for rec in recs),
                "every event is on the stream of its labelled option")
    ids = [rec["id"] for recs in by_stream.values() for rec in recs]
    ok &= check(len(ids) == len(set(ids)), "no event was forwarded twice (de-duplication held through the kill/reclaim)")
    ok &= check(len(by_stream["vt:review"]) == 0, "no events in the review stream (reasoner never failed)")
    sample = next(rec for recs in by_stream.values() for rec in recs)
    ok &= check({"id", "payload", "ts", "label", "confidence", "source", "model_version", "decided_at"} <= set(sample),
                "original fields carried through plus label/confidence/source/model_version/decided_at")

    # dead-letter path: a message that has already failed max_deliveries times
    r.delete(config.DLQ)
    before = totals(r)[0]
    r.xadd(config.INGESTION, {"id": "poison", "payload": "x", "ts": time.time()})
    r.xreadgroup(config.INGESTION_GROUP, "ghost", {config.INGESTION: ">"}, count=1)
    mid = r.xpending_range(config.INGESTION, config.INGESTION_GROUP, "-", "+", 10)[0]["message_id"]
    for _ in range(config.MAX_DELIVERIES - 1):  # each XCLAIM counts as another delivery
        r.xclaim(config.INGESTION, config.INGESTION_GROUP, "ghost", 0, [mid])
    print(f"poison message delivered {config.MAX_DELIVERIES}x; waiting {config.RECLAIM_IDLE_MS // 1000}s idle...",
          flush=True)
    time.sleep(config.RECLAIM_IDLE_MS / 1000 + 2)
    d3 = spawn("decider", "decider-3", log="decider-3.log")
    time.sleep(6)
    d3.terminate()
    d3.wait(timeout=10)
    mock.terminate()
    ok &= check(r.xlen(config.DLQ) == 1, "poison message moved to the DLQ")
    ok &= check(totals(r)[0] == before, "poison message was not forwarded to a decision stream")
    ok &= check(pending(r)["pending"] == 0, "poison message acked after dead-lettering")

    r.delete(config.INGESTION, config.DLQ, *config.DECISION_STREAMS)  # leave nothing behind
    for f in ("mock.log", "decider-1.log", "decider-2.log", "decider-3.log", "producer.log"):
        os.remove(os.path.join(HERE, f))
    os.remove(CFG)
    print("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED (logs kept)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
