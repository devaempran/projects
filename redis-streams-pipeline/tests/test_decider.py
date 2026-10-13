"""Decider behaviour against a mock decisionLM and a real Redis."""
import threading
import time

import pytest
from helpers import add_event, decided_count, decisions, deliver, last_dlq, pending

from pipeline import config, runtime
from pipeline.decider import Decider


@pytest.fixture
def d(r, cfg, mock):
    return Decider(r, cfg, "t1")


# ---- bad events go straight to the dead-letter stream -----------------------------------------------
@pytest.mark.parametrize("fields", [{"other": "x"}, {"payload": ""}, {"payload": "   \n"}], ids=["missing", "empty", "blank"])
def test_event_without_usable_context_is_dead_lettered_immediately(r, cfg, d, mock, fields):
    r.xadd(cfg.ingestion, {"id": "x", **fields})
    deliver(r, cfg, d)
    assert pending(r, cfg) == 0 and "no usable" in last_dlq(r, cfg)["reason"]
    assert mock.stats()["requests"] == 0  # never reached decisionLM


def test_decisionlm_4xx_is_dead_lettered_not_retried(r, cfg, d):
    add_event(r, cfg, payload="BAD400 please")
    deliver(r, cfg, d)
    assert pending(r, cfg) == 0 and "HTTP 400" in last_dlq(r, cfg)["reason"]


def test_an_answer_that_is_not_a_configured_option_is_dead_lettered(r, cfg, d):
    add_event(r, cfg, payload="UNKNOWN option")
    deliver(r, cfg, d)
    assert pending(r, cfg) == 0 and "not a configured option" in last_dlq(r, cfg)["reason"]


# ---- transient failures are retried, then left pending -----------------------------------------------
def test_http_500_is_retried_then_left_pending(r, cfg, d, mock):
    add_event(r, cfg, payload="SERVER500")
    deliver(r, cfg, d)
    assert pending(r, cfg) == 1 and r.xlen(cfg.dlq) == 0
    assert mock.stats()["requests"] == cfg.retries + 1


def test_malformed_reply_is_left_pending(r, cfg, d):
    add_event(r, cfg, payload="GARBAGE reply")
    deliver(r, cfg, d)
    assert pending(r, cfg) == 1


def test_a_flaky_failure_is_recovered_by_the_in_process_retry(r, cfg, d):
    add_event(r, cfg, payload="FLAKY once")
    deliver(r, cfg, d)
    assert pending(r, cfg) == 0 and decided_count(r, cfg) == 1


def test_a_message_that_keeps_failing_is_dead_lettered_after_max_deliveries(r, cfg, d):
    add_event(r, cfg, payload="SERVER500 again")
    mid = deliver(r, cfg, d)
    for _ in range(cfg.max_deliveries):  # each XCLAIM counts as another delivery
        r.xclaim(cfg.ingestion, cfg.ingestion_group, "ghost", 0, [mid])
    rec = r.xrange(cfg.ingestion, mid, mid)[0][1]
    d.handle(mid, rec, reclaimed=True)
    assert pending(r, cfg) == 0 and "gave up" in last_dlq(r, cfg)["reason"]


def test_reclaiming_a_message_someone_else_acknowledged_does_nothing(r, cfg, d):
    add_event(r, cfg, payload="SERVER500 again")
    mid = deliver(r, cfg, d)
    rec = r.xrange(cfg.ingestion, mid, mid)[0][1]
    r.xack(cfg.ingestion, cfg.ingestion_group, mid)
    d.handle(mid, rec, reclaimed=True)
    assert r.xlen(cfg.dlq) == 0 and decided_count(r, cfg) == 0


# ---- success path --------------------------------------------------------------------------------------
def test_event_is_routed_to_its_option_stream_with_decision_fields(r, cfg, d):
    add_event(r, cfg, payload="a normal event")
    deliver(r, cfg, d)
    routed = {s: v for s, v in decisions(r, cfg).items() if v}
    assert len(routed) == 1 and pending(r, cfg) == 0
    (stream, [rec]) = next(iter(routed.items()))
    assert stream == cfg.option_streams[rec["label"]]
    assert rec["payload"] == "a normal event" and rec["low_confidence"] == "0" and rec["source"] in ("fast", "reasoner")
    assert {"confidence", "tier1_label", "decision_latency_s", "model_version", "decided_at"} <= set(rec)


def test_reasoner_failure_goes_to_the_review_stream(r, cfg, d):
    add_event(r, cfg, payload="LOWCONF event")
    deliver(r, cfg, d)
    (rec,) = decisions(r, cfg)[cfg.review_stream]
    assert rec["low_confidence"] == "1" and "mock failure" in rec["reasoner_error"]


def test_reasoner_failure_without_a_review_stream_uses_the_option_stream(make_cfg, mock):
    cfg = make_cfg(review_stream="")
    r = config.connect(cfg)
    d = Decider(r, cfg, "t1")
    add_event(r, cfg, payload="LOWCONF event")
    deliver(r, cfg, d)
    assert [len(v) for s, v in decisions(r, cfg).items() if s == cfg.options[0].stream] == [1]


def test_oversized_context_is_truncated_and_flagged(r, cfg, d, mock):
    add_event(r, cfg, payload="x" * 500)
    deliver(r, cfg, d)
    (rec,) = next(v for v in decisions(r, cfg).values() if v)
    assert mock.stats()["max_context_len"] <= cfg.max_context_chars and rec["context_truncated"] == "1"


def test_the_configured_limits_are_sent_to_decisionlm(r, cfg, d, mock):
    add_event(r, cfg)
    deliver(r, cfg, d)
    assert mock.stats()["max_think_seconds"] == cfg.max_think_seconds


# ---- de-duplication -------------------------------------------------------------------------------------
def test_the_same_event_id_is_forwarded_once(r, cfg, d):
    for _ in range(2):
        add_event(r, cfg, id="dup-1", payload="same event")
        deliver(r, cfg, d)
    assert decided_count(r, cfg) == 1 and pending(r, cfg) == 0


def test_events_without_an_id_are_not_deduplicated(r, cfg, d):
    for _ in range(2):
        add_event(r, cfg, id="", payload="no id here")
        deliver(r, cfg, d)
    assert decided_count(r, cfg) == 2


# ---- decisionLM outages --------------------------------------------------------------------------------
def test_a_decisionlm_outage_makes_messages_wait_not_fail(r, cfg, mock):
    mock.stop()
    add_event(r, cfg, payload="while down")
    t = threading.Thread(target=lambda: deliver(r, cfg, Decider(r, cfg, "t2")))
    t.start()
    time.sleep(2)
    entry = r.xpending_range(cfg.ingestion, cfg.ingestion_group, "-", "+", 5)[0]
    assert t.is_alive() and entry["times_delivered"] == 1 and r.xlen(cfg.dlq) == 0  # waiting, no attempt burned
    mock.start()
    t.join(15)
    assert not t.is_alive() and pending(r, cfg) == 0 and decided_count(r, cfg) == 1


def test_stop_requested_while_waiting_leaves_the_message_pending(r, cfg, mock):
    mock.stop()
    add_event(r, cfg, payload="stop while down")
    t = threading.Thread(target=lambda: deliver(r, cfg, Decider(r, cfg, "t3")))
    t.start()
    time.sleep(1)
    runtime.stop.set()
    t.join(5)
    assert not t.is_alive() and pending(r, cfg) == 1


def test_theta_is_sent_to_decisionlm(r, cfg, mock):
    d = Decider(r, cfg, "t5", theta=0.5)
    add_event(r, cfg)
    deliver(r, cfg, d)
    assert mock.stats()["last_theta"] == 0.5
