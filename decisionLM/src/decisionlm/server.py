"""HTTP layer: request validation, routes and error mapping. The decisions themselves live in tier1, tier2 and decide."""
from __future__ import annotations

import logging
import math

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from decisionlm import decide, model, settings, tier1
from decisionlm.errors import ModelLoadError, PromptTooLongError, ScoringError

log = logging.getLogger("decisionLM")


class BadRequest(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def parse_body() -> dict:
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


def number(body: dict, key: str, default, lo: float, hi: float, integer: bool = False):
    """Optional numeric field: finite, not a bool, within [lo, hi]."""
    v = body.get(key, default)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int if integer else (int, float)) or not math.isfinite(v):
        raise BadRequest(f"{key} must be {'an integer' if integer else 'a number'}.")
    if not lo <= v <= hi:
        raise BadRequest(f"{key} must be between {lo} and {hi}.")
    return v


def create_app(cfg: settings.Settings | None = None) -> Flask:
    """Build the Flask app. `cfg` (default: the active settings) makes it the active settings."""
    if cfg is not None:
        settings.use(cfg)
    cfg = settings.get()
    app = Flask(__name__, static_folder=str(settings.STATIC_DIR), static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_body_bytes

    # ---- errors: every failure comes back as JSON, never an HTML stack trace ----------------------
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

    # ---- routes -------------------------------------------------------------------------------------
    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/api/health", methods=["GET"])
    def api_health():
        return jsonify({"ok": True, "model": settings.get().model_label, "gpu": model.gpu_status()})

    @app.route("/api/warmup", methods=["POST"])
    def api_warmup():
        return jsonify(model.warmup())

    def run_score(body: dict) -> dict:
        return tier1.score_options(body.get("Context") or "", body["Question"], body.get("Options"))

    @app.route("/api/score", methods=["POST"])
    def api_score():
        """Input: {Context, Question, Options:[val1, ...]}  Output: {val1: prob, ...}"""
        result = run_score(parse_body())
        return jsonify({c["text"]: c["probability"] for c in result["choices"]})

    @app.route("/api/score/detailed", methods=["POST"])
    def api_score_detailed():
        """Same input as /api/score; returns logprobs, prompt, timing, etc. (used by the UI)."""
        return jsonify(run_score(parse_body()))

    @app.route("/api/decide", methods=["POST"])
    def api_decide():
        """Input: {Context, Question, Options, theta?, max_think_tokens?, max_think_seconds?}. See decide.decide."""
        body = parse_body()
        current = settings.get()
        theta = number(body, "theta", current.theta, 0, 1000)
        budget = number(body, "max_think_tokens", None, 1, current.n_ctx, integer=True)
        seconds = number(body, "max_think_seconds", None, 1, 3600)
        return jsonify(decide.decide(body.get("Context") or "", body["Question"], body.get("Options"),
                                     theta, budget, seconds))

    return app


def main() -> None:
    cfg = settings.get()
    print(f"Loading {cfg.model_label}...")
    report = model.warmup()
    print(f"Loaded in {report['load_duration_s']:.2f}s. "
          f"GPU offload support: {report['supports_gpu_offload']}. "
          f"GPU memory: {report['gpu_memory']}")
    for line in report["offload_log_lines"]:
        print(" ", line.strip())
    print(f"decisionLM running at http://{cfg.host}:{cfg.port}")
    # One worker thread: a llama.cpp context is not thread-safe (the Flask dev server's concurrent
    # threads crash it). Requests queue and are served one at a time.
    from waitress import serve
    serve(create_app(), host=cfg.host, port=cfg.port, threads=cfg.threads)
