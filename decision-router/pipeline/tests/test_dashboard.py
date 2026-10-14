"""The dashboard: generic over any config, accuracy only when a label field is configured, and switchable."""
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from helpers import OPTIONS, add_event, config_text

from pipeline import config, supervisor
from pipeline.dashboard import server
from pipeline.dashboard.server import Dashboard, build_state

MINIMAL = """[decision]
question = "q"
[[options]]
name = "a"
[[options]]
name = "b"
"""
HEALTH = {"ok": True, "checked": 0.0}


def decided(r, cfg, option, **extra):
    fields = {"id": f"e{time.time_ns()}", "payload": "some text", "label": option, "confidence": "0.9", "source": "fast",
              "tier1_label": option, "low_confidence": "0", "context_truncated": "0", "decision_latency_s": "0.05",
              "decided_at": str(time.time()), **extra}
    r.xadd(cfg.option_streams[option], fields)


def test_it_reports_every_configured_topic_without_any_labels(r, cfg):
    add_event(r, cfg)
    decided(r, cfg, OPTIONS[0])
    decided(r, cfg, OPTIONS[0])
    decided(r, cfg, OPTIONS[1])
    s = build_state(cfg, r, HEALTH)
    by_stream = {t["stream"]: t for t in s["topics"]}
    assert set(by_stream) == set(cfg.decision_streams) | {cfg.dlq}
    assert by_stream[cfg.option_streams[OPTIONS[0]]]["len"] == 2
    assert s["totals"]["decided"] == 3 and s["totals"]["per_minute"] == 3 and s["ingestion"]["len"] == 1
    assert s["config"]["label_field"] == "" and s["totals"]["labelled"] == 0 and s["matrix"] == {}
    assert s["tiers"][0]["confidence"] == pytest.approx(0.9)
    assert by_stream[cfg.option_streams[OPTIONS[0]]]["events"][0]["text"] == "some text"  # the config's context_field


def test_the_text_comes_from_the_configured_context_field(tmp_path, r, cfg):
    text = config_text("dash", 6380, 5099).replace('context_field = "payload"', 'context_field = "body"')
    p = tmp_path / "c.toml"
    p.write_text(text)
    other = config.load(str(p), environ={})
    rr = config.connect(other)
    rr.xadd(other.option_streams[OPTIONS[0]], {"id": "x", "body": "hello body", "label": OPTIONS[0], "source": "fast"})
    try:
        s = build_state(other, rr, HEALTH)
        assert s["topics"][0]["events"][0]["text"] == "hello body"
    finally:
        for k in rr.scan_iter("dash*"):
            rr.delete(k)


def test_accuracy_appears_only_with_a_label_field(tmp_path, r, make_cfg):
    base = make_cfg()
    p = tmp_path / "labelled.toml"
    p.write_text(open(base.path).read() + '\n[dashboard]\nlabel_field = "truth"\n')
    cfg = config.load(str(p), environ={"REDIS_HOST": base.host})
    decided(r, cfg, OPTIONS[0], truth=OPTIONS[0])                    # right
    decided(r, cfg, OPTIONS[1], truth=OPTIONS[0], tier1_label=OPTIONS[0])   # wrong; tier 1 had it right
    decided(r, cfg, OPTIONS[1])                                      # unlabelled
    s = build_state(cfg, r, HEALTH)
    assert s["config"]["label_field"] == "truth" and s["totals"]["labelled"] == 2
    assert (s["tiers"][0]["acc"]["right"], s["tiers"][0]["acc"]["n"]) == (1, 2)
    assert (s["tiers"][0]["tier1"]["right"], s["tiers"][0]["tier1"]["n"]) == (2, 2)
    assert s["matrix"][OPTIONS[0]] == {cfg.option_streams[OPTIONS[0]]: 1, cfg.option_streams[OPTIONS[1]]: 1}
    assert s["label_rows"][: len(OPTIONS)] == OPTIONS


def test_labels_outside_the_options_get_their_own_row(tmp_path, r, make_cfg):
    base = make_cfg()
    p = tmp_path / "labelled.toml"
    p.write_text(open(base.path).read() + '\n[dashboard]\nlabel_field = "truth"\n')
    cfg = config.load(str(p), environ={"REDIS_HOST": base.host})
    decided(r, cfg, OPTIONS[0], truth="something else")
    assert build_state(cfg, r, HEALTH)["label_rows"][-1] == "something else"


def test_stats_cover_the_newest_events_of_a_long_stream(r, cfg, monkeypatch):
    monkeypatch.setattr(server, "SCAN_LIMIT", 3)
    monkeypatch.setattr(server, "SHOW_LIMIT", 2)
    for i in range(5):
        decided(r, cfg, OPTIONS[0], id=f"n{i}")
    s = build_state(cfg, r, HEALTH)
    topic = next(t for t in s["topics"] if t["stream"] == cfg.option_streams[OPTIONS[0]])
    assert [e["id"] for e in topic["events"]] == ["n4", "n3"] and topic["len"] == 5 and s["totals"]["decided"] == 3
    assert s["totals"]["window_limited"]


def test_dead_letters_and_a_missing_review_stream(r, make_cfg):
    cfg = make_cfg(review_stream="")
    r.xadd(cfg.dlq, {"id": "bad", "payload": "", "reason": "no usable payload", "attempts": "0"})
    s = build_state(cfg, r, HEALTH)
    assert [t["kind"] for t in s["topics"]].count("review") == 0
    assert s["totals"]["dead"] == 1 and s["topics"][-1]["events"][0]["reason"] == "no usable payload"


def test_it_serves_the_page_and_the_state(r, cfg):
    dash = Dashboard(cfg, r)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), dash.handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert b"decision-router" in urllib.request.urlopen(base + "/?theme=light").read()
        state = json.load(urllib.request.urlopen(base + "/api/state"))
        assert state["config"]["ingestion"] == cfg.ingestion and "error" not in state
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/nope")
    finally:
        httpd.shutdown()


def test_the_dashboard_setting_controls_whether_start_runs_it(tmp_path):
    def names(extra):
        p = tmp_path / "c.toml"
        p.write_text(MINIMAL + "[service]\nstart = false\n" + extra)
        return [c.name for c in supervisor.plan(config.load(str(p), environ={}), True)]

    assert "dashboard" in names("")
    assert "dashboard" not in names("[dashboard]\nenabled = false\n")


@pytest.mark.parametrize("extra,message", [("port = 0", "port"), ("port = 70000", "port"), ('host = ""', "host"),
                                           ('enabled = "no"', "enabled")])
def test_bad_dashboard_settings_are_rejected(tmp_path, extra, message):
    p = tmp_path / "c.toml"
    p.write_text('[decision]\nquestion = "q"\n[[options]]\nname = "a"\n[[options]]\nname = "b"\n[dashboard]\n' + extra + "\n")
    with pytest.raises(config.ConfigError, match=message):
        config.load(str(p), environ={})
