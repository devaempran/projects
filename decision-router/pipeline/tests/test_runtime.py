"""Loop recovery and reclaiming, against a real Redis."""
import time

import pytest
import redis
from helpers import add_event, pending

from pipeline import config, runtime


def test_a_missing_stream_and_group_is_recreated_and_the_loop_continues(r, cfg):
    r.delete(cfg.ingestion)  # stream and group vanish (flush, failover without persistence, ...)
    calls = []

    def step():
        calls.append(1)
        if len(calls) >= 2:
            runtime.stop.set()
        r.xreadgroup(cfg.ingestion_group, "c", {cfg.ingestion: ">"}, count=1, block=10)

    runtime.run_loop(r, cfg, step, "t")
    assert r.exists(cfg.ingestion) and len(calls) >= 2


def test_a_redis_connection_error_backs_off_and_resumes(r, cfg):
    calls = []

    def step():
        calls.append(1)
        if len(calls) == 1:
            raise redis.ConnectionError("simulated outage")
        runtime.stop.set()

    runtime.run_loop(r, cfg, step, "t")
    assert len(calls) == 2


def test_an_unrelated_redis_error_is_not_swallowed(r, cfg):
    def step():
        raise redis.ResponseError("WRONGTYPE something else")

    with pytest.raises(redis.ResponseError):
        runtime.run_loop(r, cfg, step, "t")


def test_a_trimmed_pending_entry_is_cleaned_up_when_reclaimed(r, cfg):
    mid = add_event(r, cfg, payload="will be trimmed")
    r.xreadgroup(cfg.ingestion_group, "ghost", {cfg.ingestion: ">"}, count=1)
    r.xdel(cfg.ingestion, mid)
    runtime.reclaim(r, cfg.ingestion, cfg.ingestion_group, "t6", lambda *a: None, 0)
    assert pending(r, cfg) == 0


def test_reclaim_hands_idle_messages_to_the_handler(r, cfg):
    add_event(r, cfg, payload="abandoned")
    r.xreadgroup(cfg.ingestion_group, "dead-consumer", {cfg.ingestion: ">"}, count=1)
    time.sleep(0.2)
    seen = []
    n = runtime.reclaim(r, cfg.ingestion, cfg.ingestion_group, "alive", lambda mid, rec: seen.append(rec["payload"]), 100)
    assert n == 1 and seen == ["abandoned"]


def test_ensure_groups_is_idempotent(r, cfg):
    config.ensure_groups(r, cfg)
    config.ensure_groups(r, cfg)
    assert {g["name"] for g in r.xinfo_groups(cfg.ingestion)} == {cfg.ingestion_group}
