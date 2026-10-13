"""Workers: formatting, failure handling and reclaiming."""
import time

from pipeline import runtime, worker


def test_format_record_survives_a_non_numeric_confidence():
    assert "conf=not-a-number" in worker.format_record("s", "1-0", {"id": "x", "label": "a", "confidence": "not-a-number"})


def test_format_record_survives_an_empty_record():
    assert "conf=?" in worker.format_record("s", "1-0", {})


def test_a_failing_handler_leaves_the_message_pending_and_another_worker_finishes_it(r, cfg, monkeypatch):
    stream = cfg.decision_streams[0]
    r.xadd(stream, {"id": "w1", "label": "a", "confidence": "oops"})
    w1 = worker.Worker(r, cfg, stream, "w1")
    resp = r.xreadgroup(w1.group, "w1", {stream: ">"}, count=1)
    mid, rec = resp[0][1][0]

    monkeypatch.setattr(worker, "process", lambda *a: (_ for _ in ()).throw(RuntimeError("downstream down")))
    w1.handle(mid, rec)
    assert r.xpending(stream, w1.group)["pending"] == 1  # not acknowledged

    monkeypatch.undo()
    time.sleep(0.2)
    w2 = worker.Worker(r, cfg, stream, "w2")
    assert runtime.reclaim(r, stream, w2.group, "w2", w2.handle, 100) == 1
    assert r.xpending(stream, w1.group)["pending"] == 0


def test_a_successful_handler_acknowledges(r, cfg):
    stream = cfg.decision_streams[0]
    r.xadd(stream, {"id": "w2", "label": "a", "confidence": "0.9"})
    w = worker.Worker(r, cfg, stream, "w")
    mid, rec = r.xreadgroup(w.group, "w", {stream: ">"}, count=1)[0][1][0]
    w.handle(mid, rec)
    assert r.xpending(stream, w.group)["pending"] == 0 and w.done == 1
