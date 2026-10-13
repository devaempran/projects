"""
Live dashboard for the pipeline: shows which events ended up in which downstream topic.

    python demo/dashboard/server.py            # http://127.0.0.1:5055

Read-only: it only reads streams (XRANGE, XINFO) and never touches consumer groups, so it cannot disturb the pipeline.
Uses demo/pipeline.demo.toml unless PIPELINE_CONFIG is set. Standard library plus the pipeline's own redis dependency.
"""
import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.dirname(HERE)
ROOT = os.path.dirname(DEMO)
os.environ.setdefault("PIPELINE_CONFIG", os.path.join(DEMO, "pipeline.demo.toml"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import redis  # noqa: E402

from pipeline import config  # noqa: E402

SCAN_LIMIT = 5000   # entries read per stream for statistics
SHOW_LIMIT = 80     # newest events sent to the browser per topic
SNIPPET = 240

r = config.connect()
health = {"ok": None, "checked": 0.0}


def poll_health():
    """decisionLM serves one request at a time, so a busy server may not answer in time: report that as 'busy or down'."""
    while True:
        try:
            with urllib.request.urlopen(config.HEALTH_URL, timeout=1.5) as resp:
                health["ok"] = resp.status == 200
        except Exception:
            health["ok"] = False
        health["checked"] = time.time()
        time.sleep(3)


def fnum(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def event(entry_id, f, stream_kind):
    expected = f.get("expected", "")
    label = f.get("label")
    return {
        "entry": entry_id, "id": f.get("id", "?"), "kind": f.get("kind", ""),
        "text": (f.get("payload") or "")[:SNIPPET], "text_len": len(f.get("payload") or ""),
        "expected": expected, "label": label, "tier1": f.get("tier1_label"),
        "confidence": fnum(f.get("confidence")), "source": f.get("source"),
        "latency": fnum(f.get("decision_latency_s")), "decided_at": fnum(f.get("decided_at")),
        "truncated": f.get("context_truncated") == "1", "low_confidence": f.get("low_confidence") == "1",
        "reasoner_error": f.get("reasoner_error"), "reason": f.get("reason"), "attempts": fnum(f.get("attempts")),
        "correct": (label == expected) if (expected and label and stream_kind != "dlq") else None,
        "tier1_correct": (f.get("tier1_label") == expected) if (expected and f.get("tier1_label")) else None,
    }


def group_info(stream):
    try:
        groups = r.xinfo_groups(stream)
    except redis.ResponseError:
        return {"pending": 0, "lag": 0, "consumers": 0}
    g = next((x for x in groups if x["name"] == config.group_for(stream)), None)
    if not g:
        return {"pending": 0, "lag": 0, "consumers": 0}
    return {"pending": g["pending"], "lag": g.get("lag") or 0, "consumers": g["consumers"]}


def read_stream(stream, kind):
    entries = r.xrange(stream, count=SCAN_LIMIT)
    return [event(i, f, kind) for i, f in entries]


def acc(events, key="correct"):
    scored = [e[key] for e in events if e[key] is not None]
    return {"n": len(scored), "right": sum(scored), "acc": (sum(scored) / len(scored)) if scored else None}


def median(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def build_state():
    option_names = {s: n for n, s in config.OPTION_STREAMS.items()}
    topics = []
    for name, stream in config.OPTION_STREAMS.items():
        topics.append({"stream": stream, "name": name, "kind": "option"})
    if config.REVIEW_STREAM:
        topics.append({"stream": config.REVIEW_STREAM, "name": "review (reasoner failed)", "kind": "review"})
    topics.append({"stream": config.DLQ, "name": "dead letters (cannot be decided)", "kind": "dlq"})

    decided_all, matrix = [], {}
    for t in topics:
        evs = read_stream(t["stream"], t["kind"])
        t["len"] = r.xlen(t["stream"])
        t["events"] = list(reversed(evs))[:SHOW_LIMIT]
        t["group"] = group_info(t["stream"]) if t["kind"] != "dlq" else None
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
        {"name": "all decided", "n": len(decided_all), "acc": acc(decided_all), "tier1": acc(decided_all, "tier1_correct"),
         "latency": median([e["latency"] for e in decided_all])},
        {"name": "answered by tier 1 (fast)", "n": len(fast), "acc": acc(fast), "tier1": acc(fast, "tier1_correct"),
         "latency": median([e["latency"] for e in fast])},
        {"name": "escalated to the reasoner", "n": len(reasoned), "acc": acc(reasoned), "tier1": acc(reasoned, "tier1_correct"),
         "latency": median([e["latency"] for e in reasoned])},
    ]
    ing = group_info(config.INGESTION)
    return {
        "now": time.time(),
        "config": {"ingestion": config.INGESTION, "question": config.QUESTION, "theta": config.THETA,
                   "options": list(config.OPTIONS), "streams": {n: s for n, s in config.OPTION_STREAMS.items()},
                   "review": config.REVIEW_STREAM, "dlq": config.DLQ, "url": config.URL},
        "ingestion": {"len": r.xlen(config.INGESTION), **ing},
        "decisionlm": {"ok": health["ok"], "checked": health["checked"]},
        "totals": {"decided": len(decided_all), "dead": next(t["len"] for t in topics if t["kind"] == "dlq"),
                   "in_flight": ing["pending"] + ing["lag"],
                   "escalated": len(reasoned), "labelled": acc(decided_all)["n"]},
        "tiers": tiers, "topics": topics, "matrix": matrix,
        "option_names": option_names,
    }


_cache = {"t": 0.0, "body": b"{}"}
_lock = threading.Lock()


def state_json():
    with _lock:  # many browser tabs share one computation per second
        if time.time() - _cache["t"] > 1.0:
            try:
                _cache["body"] = json.dumps(build_state()).encode()
            except redis.RedisError as e:
                _cache["body"] = json.dumps({"error": f"redis unavailable: {e}"}).encode()
            _cache["t"] = time.time()
        return _cache["body"]


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
            return self._send(200, state_json(), "application/json")
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        self._send(404, b'{"error": "not found"}', "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5055)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    r.ping()
    threading.Thread(target=poll_health, daemon=True).start()
    print(f"dashboard for {config.INGESTION!r} at http://{args.host}:{args.port}  (config: {config.PATH})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
