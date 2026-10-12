"""HTTP client for decisionLM's /api/decide, and the three ways a call can fail:
  Permanent    retrying cannot help (bad event, request rejected with HTTP 4xx, unknown option)
  Transient    might work next time (timeout, HTTP 5xx, malformed reply)
  Unreachable  decisionLM is not accepting requests (connection refused, 502/503/504)"""
import json
import urllib.error
import urllib.request

from pipeline import config


class Permanent(Exception):
    """Retrying cannot help."""


class Transient(Exception):
    """Might work next time."""


class Unreachable(Transient):
    """decisionLM is not accepting requests."""


def call_decisionlm(record, theta):
    """POST the event to /api/decide. Returns the parsed reply (+ context_truncated)."""
    context = record.get(config.CONTEXT_FIELD)
    if not isinstance(context, str) or not context.strip():
        raise Permanent(f"event has no usable {config.CONTEXT_FIELD!r} field")
    truncated = len(context) > config.MAX_CONTEXT_CHARS
    body = json.dumps({"Context": context[:config.MAX_CONTEXT_CHARS], "Question": config.QUESTION,
                       "Options": config.OPTIONS, "theta": theta,
                       "max_think_tokens": config.MAX_THINK_TOKENS,
                       "max_think_seconds": config.MAX_THINK_SECONDS}).encode()
    req = urllib.request.Request(config.URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=config.TIMEOUT_S) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode(errors="replace")
        if e.code in (502, 503, 504):
            raise Unreachable(f"decisionLM unavailable (HTTP {e.code})") from None
        if e.code in (408, 429) or e.code >= 500:
            raise Transient(f"decisionLM error (HTTP {e.code}): {detail}") from None
        raise Permanent(f"decisionLM rejected the request (HTTP {e.code}): {detail}") from None
    except TimeoutError:
        raise Transient(f"decisionLM did not answer within {config.TIMEOUT_S:.0f}s") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise Transient(f"decisionLM did not answer within {config.TIMEOUT_S:.0f}s") from None
        raise Unreachable(f"cannot reach decisionLM: {e.reason}") from None
    except OSError as e:  # connection reset mid-reply, etc.
        raise Unreachable(f"cannot reach decisionLM: {e}") from None
    try:
        out = json.loads(raw)
        answer, conf, src, t1 = out["answer"], out["confidence"], out["source"], out["tier1_answer"]
        float(conf)
    except (ValueError, KeyError, TypeError):
        raise Transient("decisionLM returned a malformed reply") from None
    if answer not in config.OPTION_STREAMS:
        raise Permanent(f"decisionLM answered {answer!r}, which is not a configured option")
    out["context_truncated"] = truncated
    return out


def decisionlm_healthy():
    try:
        with urllib.request.urlopen(config.HEALTH_URL, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
