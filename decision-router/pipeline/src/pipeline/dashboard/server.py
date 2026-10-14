"""
Live dashboard for the pipeline: which events ended up in which topic, how confident and how fast, and what is waiting.

    python run.py dashboard [--host H --port P]       # http://127.0.0.1:5055 (`start` runs it when [dashboard] enabled)

It works from the config alone: the topics, the options and the text field all come from it. Accuracy panels appear only when
events carry a ground-truth label in the field named by [dashboard] label_field; without one the dashboard shows volume,
confidence, tier and timing only.

Read-only: it reads streams (XREVRANGE, XINFO) and never touches consumer groups, so it cannot disturb the pipeline.
Statistics cover the newest SCAN_LIMIT events per topic, so it stays cheap on a long-running pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import redis

from pipeline import config, runtime

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_LIMIT = 5000   # newest entries read per stream for statistics
SHOW_LIMIT = 80     # newest events sent to the browser per topic
SNIPPET = 240
RATE_WINDOW_S = 60


def fnum(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def event(cfg: config.Config, entry_id: str, f: dict, kind: str) -> dict:
    """One stream entry in the shape the page uses. `expected` is the ground-truth label when the config names a field for it."""
    text = f.get(cfg.context_field) or ""
    expected = f.get(cfg.dashboard_label_field, "") if cfg.dashboard_label_field else ""
    label = f.get("label")
    return {
        "entry": entry_id, "id": f.get("id", "?"),
        "text": text[:SNIPPET], "text_len": len(text),
        "expected": expected, "label": label, "tier1": f.get("tier1_label"),
        "confidence": fnum(f.get("confidence")), "source": f.get("source"),
        "latency": fnum(f.get("decision_latency_s")), "decided_at": fnum(f.get("decided_at")),
        "truncated": f.get("context_truncated") == "1", "low_confidence": f.get("low_confidence") == "1",
        "reasoner_error": f.get("reasoner_error"), "reason": f.get("reason"), "attempts": fnum(f.get("attempts")),
        "correct": (label == expected) if (expected and label and kind != "dlq") else None,
        "tier1_correct": (f.get("tier1_label") == expected) if (expected and f.get("tier1_label")) else None,
    }


def group_info(cfg: config.Config, r: redis.Redis, stream: str) -> dict:
    empty = {"pending": 0, "lag": 0, "consumers": 0}
    try:
        groups = r.xinfo_groups(stream)
    except redis.ResponseError:
        return empty
    g = next((x for x in groups if x["name"] == cfg.group_for(stream)), None)
    return empty if not g else {"pending": g["pending"], "lag": g.get("lag") or 0, "consumers": g["consumers"]}


def acc(events: list[dict], key: str = "correct") -> dict:
    scored = [e[key] for e in events if e[key] is not None]
    return {"n": len(scored), "right": sum(scored), "acc": (sum(scored) / len(scored)) if scored else None}


def median(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def build_state(cfg: config.Config, r: redis.Redis, health: dict) -> dict:
    topics = [{"stream": stream, "name": name, "kind": "option"} for name, stream in cfg.option_streams.items()]
    if cfg.review_stream:
        topics.append({"stream": cfg.review_stream, "name": "review (reasoner failed)", "kind": "review"})
    topics.append({"stream": cfg.dlq, "name": "dead letters (cannot be decided)", "kind": "dlq"})

    now = time.time()
    decided_all, matrix = [], {}
    for t in topics:
        evs = [event(cfg, i, f, t["kind"]) for i, f in r.xrevrange(t["stream"], count=SCAN_LIMIT)]  # newest first
        t["len"] = r.xlen(t["stream"])
        t["events"] = evs[:SHOW_LIMIT]
        t["group"] = group_info(cfg, r, t["stream"]) if t["kind"] != "dlq" else None
        if t["kind"] != "dlq":
            decided_all += evs
        for e in evs:  # where did each labelled event end up?
            if e["expected"]:
                matrix.setdefault(e["expected"], {}).setdefault(t["stream"], 0)
                matrix[e["expected"]][t["stream"]] += 1
        t["accuracy"] = acc(evs) if t["kind"] != "dlq" else None

    fast = [e for e in decided_all if e["source"] == "fast"]
    reasoned = [e for e in decided_all if e["source"] == "reasoner"]
    tiers = [
        {"name": name, "n": len(evs), "acc": acc(evs), "tier1": acc(evs, "tier1_correct"),
         "latency": median([e["latency"] for e in evs]),
         "confidence": median([e["confidence"] for e in evs])}
        for name, evs in (("all decided", decided_all), ("answered by tier 1 (fast)", fast),
                          ("escalated to the reasoner", reasoned))
    ]
    ing = group_info(cfg, r, cfg.ingestion)
    labelled = acc(decided_all)["n"]
    # Rows of the label grid: the configured options, then any other label value seen in the data.
    label_rows = list(cfg.option_names) + sorted(set(matrix) - set(cfg.option_names))
    return {
        "now": now,
        "config": {"ingestion": cfg.ingestion, "question": cfg.question, "theta": cfg.theta,
                   "options": list(cfg.option_names), "streams": dict(cfg.option_streams),
                   "review": cfg.review_stream, "dlq": cfg.dlq, "url": cfg.url, "label_field": cfg.dashboard_label_field},
        "ingestion": {"len": r.xlen(cfg.ingestion), **ing},
        "decisionlm": {"ok": health["ok"], "checked": health["checked"]},
        "totals": {"decided": len(decided_all), "dead": next(t["len"] for t in topics if t["kind"] == "dlq"),
                   "in_flight": ing["pending"] + ing["lag"], "escalated": len(reasoned), "labelled": labelled,
                   "per_minute": sum(1 for e in decided_all if e["decided_at"] and now - e["decided_at"] <= RATE_WINDOW_S),
                   "window_limited": any(t["len"] > SCAN_LIMIT for t in topics)},
        "tiers": tiers, "topics": topics, "matrix": matrix, "label_rows": label_rows,
    }


class Dashboard:
    """Serves the page and /api/state for one config."""

    def __init__(self, cfg: config.Config, r: redis.Redis | None = None):
        self.cfg, self.r = cfg, r or config.connect(cfg)
        self.health = {"ok": None, "checked": 0.0}
        self._cache = {"t": 0.0, "body": b"{}"}
        self._lock = threading.Lock()

    def poll_health(self) -> None:
        """decisionLM serves one request at a time, so a busy server may not answer in time: reported as 'busy or down'."""
        while not runtime.stop.is_set():
            try:
                with urllib.request.urlopen(self.cfg.health_url, timeout=1.5) as resp:
                    self.health["ok"] = resp.status == 200
            except Exception:
                self.health["ok"] = False
            self.health["checked"] = time.time()
            runtime.stop.wait(3)

    def state_json(self) -> bytes:
        with self._lock:  # many browser tabs share one computation per second
            if time.time() - self._cache["t"] > 1.0:
                try:
                    self._cache["body"] = json.dumps(build_state(self.cfg, self.r, self.health)).encode()
                except redis.RedisError as e:
                    self._cache["body"] = json.dumps({"error": f"redis unavailable: {e}"}).encode()
                self._cache["t"] = time.time()
            return self._cache["body"]

    def handler(self):
        dash = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path  # ignore ?query, so /?theme=light works
                if path == "/api/state":
                    return self._send(200, dash.state_json(), "application/json")
                if path in ("/", "/index.html"):
                    with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                self._send(404, b'{"error": "not found"}', "application/json")

            def log_message(self, *a):
                pass

        return Handler


def main(argv: list[str] | None = None) -> None:
    cfg = config.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=cfg.dashboard_port)
    ap.add_argument("--host", default=cfg.dashboard_host)
    args = ap.parse_args(argv)

    dash = Dashboard(cfg)
    try:
        dash.r.ping()
    except redis.RedisError as e:
        raise SystemExit(f"cannot reach Redis at {cfg.host}:{cfg.port}: {e}") from None
    runtime.install_signal_handlers("dashboard")
    threading.Thread(target=dash.poll_health, daemon=True).start()
    try:
        server = ThreadingHTTPServer((args.host, args.port), dash.handler())
    except OSError as e:
        raise SystemExit(f"cannot serve the dashboard on {args.host}:{args.port}: {e}") from None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"dashboard for {cfg.ingestion!r} at http://{args.host}:{args.port}  (config: {cfg.path})", flush=True)
    try:
        runtime.stop.wait()
    except KeyboardInterrupt:
        pass
    server.shutdown()


if __name__ == "__main__":
    main()
