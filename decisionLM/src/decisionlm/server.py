"""HTTP layer: request validation, routes, error mapping, and the decide cascade (tier 1 -> tier 2)."""
import logging
import math
import time

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from decisionlm import model, settings, tier1, tier2
from decisionlm.errors import ModelLoadError, PromptTooLongError, ScoringError

log = logging.getLogger("decisionLM")

app = Flask(__name__, static_folder=str(settings.STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = settings.MAX_BODY_BYTES


class BadRequest(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# ---- errors: every failure comes back as JSON, never an HTML stack trace --------------------------
@app.errorhandler(BadRequest)
def _bad_request(e):
    return jsonify({"error": str(e)}), e.status


@app.errorhandler(ScoringError)
def _scoring_error(e):
    status = 503 if isinstance(e, ModelLoadError) else 413 if isinstance(e, PromptTooLongError) else 400
    return jsonify({"error": str(e)}), status


@app.errorhandler(Exception)
def _unexpected(e):
    if isinstance(e, HTTPException):  # 404, 405, 413 (body too large), ...
        return jsonify({"error": e.description}), e.code
    log.exception("unhandled error")
    return jsonify({"error": "internal error"}), 500


# ---- request parsing ------------------------------------------------------------------------------
def _parse_body():
    """Validate {Context, Question, Options}; returns the JSON body dict."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise BadRequest("Body must be a JSON object.")
    if not isinstance(body.get("Context") or "", str):
        raise BadRequest("Context must be a string.")
    question = body.get("Question")
    if not isinstance(question, str) or not question.strip():
        raise BadRequest("Question is required.")
    return body


def _number(body, key, default, lo, hi, integer=False):
    """Optional numeric field: finite, not a bool, within [lo, hi]."""
    v = body.get(key, default)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int if integer else (int, float)) or not math.isfinite(v):
        raise BadRequest(f"{key} must be {'an integer' if integer else 'a number'}.")
    if not lo <= v <= hi:
        raise BadRequest(f"{key} must be between {lo} and {hi}.")
    return v


def _run_score(body):
    return tier1.score_options(body.get("Context") or "", body["Question"], body.get("Options"))


# ---- routes ---------------------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"ok": True, "model": settings.MODEL_LABEL, "gpu": model.gpu_status()})


@app.route("/api/warmup", methods=["POST"])
def api_warmup():
    return jsonify(model.warmup())


@app.route("/api/score", methods=["POST"])
def api_score():
    """Input: {Context, Question, Options:[val1, ...]}  Output: {val1: prob, ...}"""
    result = _run_score(_parse_body())
    return jsonify({c["text"]: c["probability"] for c in result["choices"]})


@app.route("/api/score/detailed", methods=["POST"])
def api_score_detailed():
    """Same input as /api/score; returns logprobs, prompt, timing, etc. (used by the UI)."""
    return jsonify(_run_score(_parse_body()))


@app.route("/api/decide", methods=["POST"])
def api_decide():
    """
    Input: {Context, Question, Options:[...], theta?, max_think_tokens?, max_think_seconds?}
    Tier 1 scores the options; if its confidence < theta (or label mass is low) the reasoner decides.
    theta > 1 forces escalation. If the reasoner fails, tier 1's answer is returned with
    low_confidence=true (and reasoner_error says why).
    """
    t0 = time.time()
    body = _parse_body()
    theta = _number(body, "theta", settings.DECIDE_THETA, 0, 1000)
    budget = _number(body, "max_think_tokens", None, 1, settings.N_CTX, integer=True)
    seconds = _number(body, "max_think_seconds", None, 1, 3600)

    result = _run_score(body)
    choices = result["choices"]
    options = [c["text"] for c in choices]
    probs = {c["text"]: c["probability"] for c in choices}
    top = max(choices, key=lambda c: c["probability"])
    conf = top["probability"]
    out = {"answer": top["text"], "source": "fast", "confidence": conf, "probabilities": probs,
           "tier1_answer": top["text"], "low_confidence": False}

    if conf < theta or result["total_label_probability_mass"] < settings.MIN_LABEL_MASS:
        try:
            r = tier2.reason(body.get("Context") or "", body["Question"], options, budget, seconds)
            out.update(answer=options[r["index"]], source="reasoner", forced=r["forced"],
                       timed_out=r["timed_out"], reasoner_tokens=r["tokens"], reasoner_latency_s=r["latency_s"])
        except Exception as e:  # tier 2 must never take the request down
            log.warning("reasoner failed: %r", e)
            out.update(source="fast", low_confidence=True, reasoner_error=str(e))
    out["latency_s"] = time.time() - t0
    return jsonify(out)


def main():
    print(f"Loading {settings.MODEL_LABEL}...")
    report = model.warmup()
    print(f"Loaded in {report['load_duration_s']:.2f}s. "
          f"GPU offload support: {report['supports_gpu_offload']}. "
          f"GPU memory: {report['gpu_memory']}")
    for line in report["offload_log_lines"]:
        print(" ", line.strip())
    print(f"decisionLM running at http://{settings.HOST}:{settings.PORT}")
    # One worker thread: a llama.cpp context is not thread-safe (the Flask dev server's concurrent
    # threads crash it). Requests queue and are served one at a time.
    from waitress import serve
    serve(app, host=settings.HOST, port=settings.PORT, threads=settings.THREADS)
