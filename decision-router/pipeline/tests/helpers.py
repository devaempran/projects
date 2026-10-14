"""Shared test helpers: building throwaway configs and driving a Decider by hand."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
SRC = os.path.join(ROOT, "src")
RUN = os.path.join(ROOT, "run.py")
OPTIONS = ["alpha", "beta", "gamma"]
QUESTION = "Which one?"


def config_text(prefix: str, redis_port: int, mock_port: int, **kw) -> str:
    """A complete config whose streams all start with `prefix`. Keyword arguments override reliability and decision
    settings (defaults are tuned for fast tests)."""
    s = dict(retries=1, retry_backoff_s=0.1, health_poll_s=0.3, timeout_s=5, reclaim_idle_s=60, reclaim_every_s=30,
             max_deliveries=3, maxlen=1000, max_context_chars=100, batch_size=1, block_ms=500, dedupe_ttl_s=3600,
             theta=0.85, review_stream=f"{prefix}:review", question=QUESTION)
    s.update(kw)
    options = "".join(f'[[options]]\nname = "{n}"\nstream = "{prefix}:{n}"\n' for n in OPTIONS)
    return f'''[redis]
host = "127.0.0.1"
port = {redis_port}
[ingestion]
stream = "{prefix}:in"
group = "{prefix}-deciders"
dlq_stream = "{prefix}:dlq"
maxlen = {s["maxlen"]}
[decision]
url = "http://127.0.0.1:{mock_port}/api/decide"
question = "{s["question"]}"
context_field = "payload"
max_context_chars = {s["max_context_chars"]}
theta = {s["theta"]}
max_think_tokens = 100
max_think_seconds = 9
timeout_s = {s["timeout_s"]}
model_version = "mock"
review_stream = "{s["review_stream"]}"
[reliability]
batch_size = {s["batch_size"]}
block_ms = {s["block_ms"]}
reclaim_idle_s = {s["reclaim_idle_s"]}
reclaim_every_s = {s["reclaim_every_s"]}
max_deliveries = {s["max_deliveries"]}
retries = {s["retries"]}
retry_backoff_s = {s["retry_backoff_s"]}
health_poll_s = {s["health_poll_s"]}
dedupe_ttl_s = {s["dedupe_ttl_s"]}
{options}'''


def add_event(r, cfg, **fields) -> str:
    """XADD an event to the ingestion stream (defaults: a unique id and a short payload)."""
    fields.setdefault("id", f"e{time.time_ns()}")
    fields.setdefault("payload", "hello")
    return r.xadd(cfg.ingestion, fields)


def deliver(r, cfg, decider) -> str:
    """Read one message the way the decider loop would, and handle it. Returns its stream id."""
    resp = r.xreadgroup(cfg.ingestion_group, decider.name, {cfg.ingestion: ">"}, count=1, block=1000)
    msg_id, rec = resp[0][1][0]
    decider.handle(msg_id, rec)
    return msg_id


def pending(r, cfg) -> int:
    return r.xpending(cfg.ingestion, cfg.ingestion_group)["pending"]


def decisions(r, cfg) -> dict[str, list[dict]]:
    return {s: [rec for _id, rec in r.xrange(s)] for s in cfg.decision_streams}


def decided_count(r, cfg) -> int:
    return sum(len(v) for v in decisions(r, cfg).values())


def last_dlq(r, cfg) -> dict:
    return r.xrevrange(cfg.dlq, count=1)[0][1]


def run_cli(cfg, *args, timeout=60, extra_env=None):
    """Run `run.py <args>` against the test config in a subprocess."""
    env = dict(os.environ, PIPELINE_CONFIG=cfg.path, REDIS_PORT=str(cfg.port), PYTHONUNBUFFERED="1", **(extra_env or {}))
    return subprocess.run([sys.executable, RUN, *args], capture_output=True, text=True, env=env, timeout=timeout)


class Mock:
    """A mock decisionLM in a subprocess (tests/mock_decisionlm.py); can be stopped and restarted on the same port."""

    def __init__(self, port: int, delay_ms: int = 0):
        self.port, self.delay_ms, self.proc = port, delay_ms, None

    def start(self) -> None:
        env = dict(os.environ, MOCK_QUESTION=QUESTION, MOCK_OPTIONS=json.dumps(OPTIONS), MOCK_DELAY_MS=str(self.delay_ms))
        self.proc = subprocess.Popen([sys.executable, os.path.join(TESTS, "mock_decisionlm.py"), str(self.port)], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=1)
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("mock decisionLM did not start")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            self.proc = None

    def stats(self) -> dict:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5))
