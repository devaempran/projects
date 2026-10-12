"""Loads and validates config.toml (path overridable with PIPELINE_CONFIG) and exposes it as constants.
Any problem with the file raises ConfigError with a message naming the setting, never a bare KeyError."""
import os
import tomllib
from urllib.parse import urlsplit

import redis

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.environ.get("PIPELINE_CONFIG", os.path.join(ROOT, "config", "pipeline.toml"))


class ConfigError(Exception):
    pass


try:
    with open(PATH, "rb") as _f:
        _cfg = tomllib.load(_f)
except FileNotFoundError:
    raise ConfigError(f"config file not found: {PATH}") from None
except tomllib.TOMLDecodeError as _e:
    raise ConfigError(f"{PATH} is not valid TOML: {_e}") from None

_MISSING = object()


def _get(section, key, cast=str, default=_MISSING):
    sec = _cfg.get(section)
    if not isinstance(sec, dict):
        raise ConfigError(f"missing [{section}] section in {PATH}")
    if key not in sec:
        if default is _MISSING:
            raise ConfigError(f"missing setting [{section}] {key}")
        return default
    value = sec[key]
    if isinstance(value, bool) or (cast in (int, float) and not isinstance(value, (int, float))):
        raise ConfigError(f"[{section}] {key} must be {'a number' if cast in (int, float) else 'text'}, got {value!r}")
    try:
        return cast(value)
    except (TypeError, ValueError):
        raise ConfigError(f"[{section}] {key} has an invalid value: {value!r}") from None


def _env_int(name, fallback):
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"environment variable {name} must be an integer, got {raw!r}") from None


HOST = os.environ.get("REDIS_HOST", _get("redis", "host"))
PORT = _env_int("REDIS_PORT", _get("redis", "port", int))

INGESTION = _get("ingestion", "stream")
INGESTION_GROUP = _get("ingestion", "group")
DLQ = _get("ingestion", "dlq_stream")
MAXLEN = _get("ingestion", "maxlen", int)

URL = _get("decision", "url")
HEALTH_URL = _get("decision", "health_url", str, "") or (
    "{0.scheme}://{0.netloc}/api/health".format(urlsplit(URL)))
QUESTION = _get("decision", "question").strip()
CONTEXT_FIELD = _get("decision", "context_field")
MAX_CONTEXT_CHARS = _get("decision", "max_context_chars", int)
THETA = _get("decision", "theta", float)
MAX_THINK_TOKENS = _get("decision", "max_think_tokens", int)
MAX_THINK_SECONDS = _get("decision", "max_think_seconds", float, 150)
TIMEOUT_S = _get("decision", "timeout_s", float)
MODEL_VERSION = _get("decision", "model_version")
REVIEW_STREAM = _get("decision", "review_stream", str, "")

_options = _cfg.get("options")
if not isinstance(_options, list) or not all(isinstance(o, dict) and isinstance(o.get("name"), str) for o in _options):
    raise ConfigError("define at least two [[options]] tables, each with a text `name`")
OPTIONS = [o["name"] for o in _options]                       # order is sent to the model as-is
OPTION_STREAMS = {o["name"]: o.get("stream") or f"decision:{o['name']}" for o in _options}
DECISION_STREAMS = list(OPTION_STREAMS.values()) + ([REVIEW_STREAM] if REVIEW_STREAM else [])

BATCH = _get("reliability", "batch_size", int)
BLOCK_MS = _get("reliability", "block_ms", int)
RECLAIM_IDLE_S = _get("reliability", "reclaim_idle_s", float)
RECLAIM_IDLE_MS = int(RECLAIM_IDLE_S * 1000)
RECLAIM_EVERY_S = _get("reliability", "reclaim_every_s", float)
MAX_DELIVERIES = _get("reliability", "max_deliveries", int)
RETRIES = _get("reliability", "retries", int, 2)                # in-process retries of a transient failure
RETRY_BACKOFF_S = _get("reliability", "retry_backoff_s", float, 5)
HEALTH_POLL_S = _get("reliability", "health_poll_s", float, 5)
DEDUPE_TTL_S = _get("reliability", "dedupe_ttl_s", int, 86400)  # 0 disables event-id de-duplication


def _validate():
    if not QUESTION:
        raise ConfigError("[decision] question must not be empty")
    if urlsplit(URL).scheme not in ("http", "https") or not urlsplit(URL).netloc:
        raise ConfigError(f"[decision] url must be an http(s) URL, got {URL!r}")
    if not CONTEXT_FIELD.strip():
        raise ConfigError("[decision] context_field must not be empty")
    if not 0 < THETA <= 1:
        raise ConfigError("[decision] theta must be in (0, 1]")
    if MAX_CONTEXT_CHARS < 1 or MAX_THINK_TOKENS < 1 or MAX_THINK_SECONDS <= 0 or TIMEOUT_S <= 0:
        raise ConfigError("[decision] max_context_chars, max_think_tokens, max_think_seconds and timeout_s must be > 0")
    if not 2 <= len(OPTIONS) <= 26:
        raise ConfigError("define between 2 and 26 [[options]]")
    if any(not o.strip() for o in OPTIONS) or len(set(OPTIONS)) != len(OPTIONS):
        raise ConfigError("option names must be unique, non-empty strings")
    streams = list(OPTION_STREAMS.values()) + ([REVIEW_STREAM] if REVIEW_STREAM else [])
    if any(not s.strip() for s in streams + [INGESTION, DLQ, INGESTION_GROUP]):
        raise ConfigError("stream and group names must not be empty")
    if len(set(streams)) != len(streams):
        raise ConfigError("option streams (and review_stream) must be unique")
    if {INGESTION, DLQ} & set(streams) or INGESTION == DLQ:
        raise ConfigError("ingestion/dlq streams must differ from the decision streams")
    if BATCH < 1 or MAX_DELIVERIES < 1 or RETRIES < 0 or MAXLEN < 1 or BLOCK_MS < 0 or DEDUPE_TTL_S < 0:
        raise ConfigError("batch_size, max_deliveries, maxlen must be >= 1 and retries, block_ms, dedupe_ttl_s >= 0")
    if RECLAIM_IDLE_S <= TIMEOUT_S:
        raise ConfigError(
            f"[reliability] reclaim_idle_s ({RECLAIM_IDLE_S}) must exceed [decision] timeout_s ({TIMEOUT_S}); "
            "otherwise a message still being decided is reclaimed and decided twice")


_validate()


def group_for(stream):
    return INGESTION_GROUP if stream == INGESTION else f"{stream}-workers"


def connect():
    # socket_timeout exceeds the XREADGROUP block time, so a dead connection errors instead of hanging forever.
    return redis.Redis(host=HOST, port=PORT, decode_responses=True, socket_connect_timeout=5,
                       socket_timeout=BLOCK_MS / 1000 + 30, health_check_interval=30)


def ensure_groups(r):
    """Idempotent: XGROUP CREATE ... $ MKSTREAM for the ingestion stream and every configured decision
    stream; BUSYGROUP means it already exists."""
    for stream in [INGESTION] + DECISION_STREAMS:
        try:
            r.xgroup_create(stream, group_for(stream), id="$", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
