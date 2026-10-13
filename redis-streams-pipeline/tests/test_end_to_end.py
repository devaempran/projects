"""End to end with real decider processes: 1,000 events through two deciders, one killed mid-run while it holds
un-acked messages, and a poison message. Takes about three minutes (the reclaim idle time is 60 s)."""
import os
import subprocess
import sys
import time

import pytest
from helpers import OPTIONS, QUESTION, RUN, Mock, add_event, decisions, pending

from pipeline import config, decisionlm_client

pytestmark = pytest.mark.slow
N = 1000


def spawn(cfg, tmp_path, name, *args):
    env = dict(os.environ, PIPELINE_CONFIG=cfg.path, REDIS_PORT=str(cfg.port), PYTHONUNBUFFERED="1")
    log = open(tmp_path / f"{name}.log", "w")
    return subprocess.Popen([sys.executable, RUN, *args], env=env, stdout=log, stderr=subprocess.STDOUT), tmp_path / f"{name}.log"


@pytest.fixture
def busy_mock(mock_port):
    """A mock decisionLM with 50 ms of simulated latency, so deciders hold batches when one is killed."""
    m = Mock(mock_port, delay_ms=50)
    m.start()
    yield m
    m.stop()


def test_nothing_is_lost_or_duplicated_when_a_decider_dies(make_cfg, busy_mock, tmp_path):
    cfg = make_cfg(batch_size=50, reclaim_idle_s=60, timeout_s=30, block_ms=2000)
    r = config.connect(cfg)
    d1, log1 = spawn(cfg, tmp_path, "decider-1", "decider", "decider-1")
    d2, _ = spawn(cfg, tmp_path, "decider-2", "decider", "decider-2")
    time.sleep(3)
    subprocess.run([sys.executable, RUN, "producer", "--count", str(N)], check=True, capture_output=True,
                   env=dict(os.environ, PIPELINE_CONFIG=cfg.path, REDIS_PORT=str(cfg.port)))
    time.sleep(3)
    held = {c["name"]: c["pending"] for c in (r.xpending(cfg.ingestion, cfg.ingestion_group)["consumers"] or [])}
    d2.kill()
    assert held.get("decider-2", 0) > 0, f"the killed decider held nothing: {held}"

    deadline = time.time() + 240
    while time.time() < deadline:
        group = next(g for g in r.xinfo_groups(cfg.ingestion) if g["name"] == cfg.ingestion_group)
        if group["pending"] == 0 and group["lag"] == 0:
            break
        time.sleep(2)

    streams = decisions(r, cfg)
    ids = [rec["id"] for recs in streams.values() for rec in recs]
    assert len(ids) + r.xlen(cfg.dlq) == N          # nothing lost
    assert len(ids) == len(set(ids))                # nothing forwarded twice
    assert pending(r, cfg) == 0
    assert all(cfg.option_streams[rec["label"]] == s for s, recs in streams.items() if s != cfg.review_stream for rec in recs)
    assert busy_mock.stats()["bad"] == 0            # every request matched the Context/Question/Options contract
    d1.terminate()
    d1.wait(timeout=15)
    assert "reclaimed" in open(log1).read()         # the survivor recovered the killed decider's messages


def test_a_message_delivered_too_often_goes_to_the_dead_letter_stream(make_cfg, mock, tmp_path):
    cfg = make_cfg(reclaim_idle_s=5, timeout_s=3, max_deliveries=5)
    r = config.connect(cfg)
    add_event(r, cfg, id="poison", payload="x")
    r.xreadgroup(cfg.ingestion_group, "ghost", {cfg.ingestion: ">"}, count=1)
    mid = r.xpending_range(cfg.ingestion, cfg.ingestion_group, "-", "+", 10)[0]["message_id"]
    for _ in range(cfg.max_deliveries - 1):
        r.xclaim(cfg.ingestion, cfg.ingestion_group, "ghost", 0, [mid])
    time.sleep(cfg.reclaim_idle_s + 1)
    d, _ = spawn(cfg, tmp_path, "decider-3", "decider", "decider-3")
    deadline = time.time() + 20
    while time.time() < deadline and not r.xlen(cfg.dlq):
        time.sleep(0.5)
    d.terminate()
    d.wait(timeout=15)
    assert r.xlen(cfg.dlq) == 1 and pending(r, cfg) == 0 and sum(len(v) for v in decisions(r, cfg).values()) == 0


def test_the_decide_request_matches_the_config(make_cfg, mock):
    """The three parts of the decisionLM request come straight from the config."""
    cfg = make_cfg()
    body = decisionlm_client.build_request(cfg, "some ticket", 0.7)
    assert (body["Context"], body["Question"], body["Options"], body["theta"]) == ("some ticket", QUESTION, OPTIONS, 0.7)
