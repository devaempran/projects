"""
Stand-in for decisionLM's POST /api/decide, used by verify.py. It enforces the request contract
(Context/Question/Options exactly as configured) and counts violations at GET /stats.
usage: python mock_decisionlm.py PORT   (env MOCK_DELAY_MS, MOCK_QUESTION, MOCK_OPTIONS as JSON)
"""
import hashlib
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = float(os.environ.get("MOCK_DELAY_MS", 0)) / 1000
QUESTION = os.environ["MOCK_QUESTION"]
OPTIONS = json.loads(os.environ["MOCK_OPTIONS"])
stats = {"requests": 0, "bad": 0, "escalated": 0, "max_context_len": 0, "max_think_seconds": None, "last_theta": None}
once = set()  # contexts that already failed once (FLAKY)


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True} if self.path == "/api/health" else stats)

    def do_POST(self):
        t0 = time.time()
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        stats["requests"] += 1
        if (body.get("Question") != QUESTION or body.get("Options") != OPTIONS
                or not isinstance(body.get("Context"), str) or not body["Context"]):
            stats["bad"] += 1
            return self._send(400, {"error": "contract violation"})
        stats["max_context_len"] = max(stats["max_context_len"], len(body["Context"]))
        stats["max_think_seconds"] = body.get("max_think_seconds")
        stats["last_theta"] = body.get("theta")
        ctx = body["Context"]
        # Context text can trigger failure modes (used by test_edge_cases.py)
        if "BAD400" in ctx:
            return self._send(400, {"error": "rejected"})
        if "SERVER500" in ctx:
            return self._send(500, {"error": "boom"})
        if "UNAVAILABLE503" in ctx:
            return self._send(503, {"error": "model loading"})
        if "FLAKY" in ctx and ctx not in once:
            once.add(ctx)
            return self._send(500, {"error": "first try fails"})
        if "GARBAGE" in ctx:
            return self._send(200, {"unexpected": True})
        if "UNKNOWN" in ctx:
            return self._send(200, {"answer": "nope", "tier1_answer": "nope", "confidence": 0.9, "source": "fast"})
        if "LOWCONF" in ctx:
            return self._send(200, {"answer": OPTIONS[0], "tier1_answer": OPTIONS[0], "confidence": 0.4,
                                    "source": "fast", "low_confidence": True, "reasoner_error": "mock failure"})
        time.sleep(DELAY)
        h = int(hashlib.sha256(body["Context"].encode()).hexdigest(), 16)
        conf = ((h >> 8) % 1000) / 1000
        top = OPTIONS[h % len(OPTIONS)]
        escalated = conf < body.get("theta", 0.85)
        stats["escalated"] += escalated
        self._send(200, {"answer": top, "tier1_answer": top, "confidence": conf, "probabilities": {},
                         "source": "reasoner" if escalated else "fast", "low_confidence": False,
                         "latency_s": time.time() - t0})

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
