"""HTTP client for decisionLM's /api/decide, and the three ways a call can fail:
  Permanent    retrying cannot help (bad event, request rejected with HTTP 4xx, unknown option)
  Transient    might work next time (timeout, HTTP 5xx, malformed reply)
  Unreachable  decisionLM is not accepting requests (connection refused, 502/503/504)"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from pipeline.config import Config


class Permanent(Exception):
    """Retrying cannot help."""


class Transient(Exception):
    """Might work next time."""


class Unreachable(Transient):
    """decisionLM is not accepting requests."""


def build_request(cfg: Config, context: str, theta: float) -> dict:
    """The three parts of the decisionLM request (Context varies, Question and Options are fixed) plus the limits."""
    return {"Context": context[:cfg.max_context_chars], "Question": cfg.question, "Options": cfg.option_names,
            "theta": theta, "max_think_tokens": cfg.max_think_tokens, "max_think_seconds": cfg.max_think_seconds}


def call_decisionlm(cfg: Config, record: dict, theta: float) -> dict:
    """POST the event to /api/decide. Returns the parsed reply (+ context_truncated)."""
    context = record.get(cfg.context_field)
    if not isinstance(context, str) or not context.strip():
        raise Permanent(f"event has no usable {cfg.context_field!r} field")
    body = json.dumps(build_request(cfg, context, theta)).encode()
    req = urllib.request.Request(cfg.url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode(errors="replace")
        if e.code in (502, 503, 504):
            raise Unreachable(f"decisionLM unavailable (HTTP {e.code})") from None
        if e.code in (408, 429) or e.code >= 500:
            raise Transient(f"decisionLM error (HTTP {e.code}): {detail}") from None
        raise Permanent(f"decisionLM rejected the request (HTTP {e.code}): {detail}") from None
    except TimeoutError:
        raise Transient(f"decisionLM did not answer within {cfg.timeout_s:.0f}s") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise Transient(f"decisionLM did not answer within {cfg.timeout_s:.0f}s") from None
        raise Unreachable(f"cannot reach decisionLM: {e.reason}") from None
    except OSError as e:  # connection reset mid-reply, etc.
        raise Unreachable(f"cannot reach decisionLM: {e}") from None
    try:
        out = json.loads(raw)
        answer = out["answer"]
        float(out["confidence"])
        out["source"], out["tier1_answer"]  # required fields
    except (ValueError, KeyError, TypeError):
        raise Transient("decisionLM returned a malformed reply") from None
    if answer not in cfg.option_streams:
        raise Permanent(f"decisionLM answered {answer!r}, which is not a configured option")
    out["context_truncated"] = len(context) > cfg.max_context_chars
    return out


def decisionlm_healthy(cfg: Config) -> bool:
    try:
        with urllib.request.urlopen(cfg.health_url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
