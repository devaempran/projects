"""Pipeline configuration as an immutable object, loaded explicitly.

`load()` reads config/pipeline.toml (or the path in PIPELINE_CONFIG, or an explicit path) and validates it; nothing is
read at import time, so tests and tools can build as many configs as they like. Any problem raises ConfigError with a
message naming the setting, never a bare KeyError."""
from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit

import redis

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_PATH = os.path.join(ROOT, "config", "pipeline.toml")
DEFAULT_SERVICE_DIR = os.path.join(os.path.dirname(ROOT), "service")
PLACEHOLDER = "<REPLACE"
_MISSING = object()


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Option:
    name: str      # the text the model reads
    stream: str    # the downstream topic for events decided as this option


@dataclass(frozen=True)
class Config:
    path: str
    host: str
    port: int
    db: int
    password: str
    # ingestion
    ingestion: str
    ingestion_group: str
    dlq: str
    maxlen: int
    # decision (the decisionLM request)
    url: str
    health_url: str
    question: str
    context_field: str
    max_context_chars: int
    theta: float
    max_think_tokens: int
    max_think_seconds: float
    timeout_s: float
    model_version: str
    review_stream: str
    options: tuple[Option, ...]
    # reliability
    batch_size: int
    block_ms: int
    reclaim_idle_s: float
    reclaim_every_s: float
    max_deliveries: int
    retries: int
    retry_backoff_s: float
    health_poll_s: float
    dedupe_ttl_s: int
    # run (what `start` supervises)
    deciders: int
    node_name: str
    workers: bool
    status_every_s: float
    shutdown_grace_s: float
    # dashboard
    dashboard_enabled: bool
    dashboard_host: str
    dashboard_port: int
    dashboard_label_field: str
    # service (the decisionLM server that `start` can launch)
    service_start: bool
    service_dir: str
    service_python: str

    @property
    def option_names(self) -> list[str]:
        return [o.name for o in self.options]

    @property
    def option_streams(self) -> dict[str, str]:
        return {o.name: o.stream for o in self.options}

    @property
    def decision_streams(self) -> list[str]:
        return [o.stream for o in self.options] + ([self.review_stream] if self.review_stream else [])

    @property
    def has_placeholders(self) -> bool:
        return PLACEHOLDER in self.question or any(PLACEHOLDER in n for n in self.option_names)

    @property
    def reclaim_idle_ms(self) -> int:
        return int(self.reclaim_idle_s * 1000)

    def group_for(self, stream: str) -> str:
        return self.ingestion_group if stream == self.ingestion else f"{stream}-workers"


def load(path: str | None = None, environ: Mapping[str, str] | None = None) -> Config:
    """Read and validate a config file. `environ` (default: os.environ) supplies PIPELINE_CONFIG (when `path` is not
    given) and the REDIS_HOST / REDIS_PORT overrides."""
    environ = os.environ if environ is None else environ
    path = path or environ.get("PIPELINE_CONFIG") or DEFAULT_PATH
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None

    def get(section: str, key: str, cast: Callable = str, default=_MISSING):
        sec = raw.get(section, {})
        if not isinstance(sec, dict):
            raise ConfigError(f"[{section}] must be a table in {path}")
        if key not in sec:
            if default is _MISSING:
                raise ConfigError(f"missing setting [{section}] {key}")
            return default
        value = sec[key]
        if cast is bool:
            if not isinstance(value, bool):
                raise ConfigError(f"[{section}] {key} must be true or false, got {value!r}")
            return value
        if isinstance(value, bool) or (cast in (int, float) and not isinstance(value, (int, float))):
            raise ConfigError(f"[{section}] {key} must be {'a number' if cast in (int, float) else 'text'}, got {value!r}")
        try:
            return cast(value)
        except (TypeError, ValueError):
            raise ConfigError(f"[{section}] {key} has an invalid value: {value!r}") from None

    file_port = get("redis", "port", int, 6379)  # validated even when the environment overrides it
    port_env = environ.get("REDIS_PORT")
    try:
        port = int(port_env) if port_env is not None else file_port
    except ValueError:
        raise ConfigError(f"environment variable REDIS_PORT must be an integer, got {port_env!r}") from None

    tables = raw.get("options")
    if not isinstance(tables, list) or not all(isinstance(o, dict) and isinstance(o.get("name"), str) for o in tables):
        raise ConfigError("define at least two [[options]] tables, each with a text `name`")
    url = get("decision", "url", str, "http://127.0.0.1:5001/api/decide")
    ingestion = get("ingestion", "stream", str, "ingestion")
    cfg = Config(
        path=path,
        host=environ.get("REDIS_HOST") or get("redis", "host", str, "127.0.0.1"),
        db=get("redis", "db", int, 0),
        password=environ.get("REDIS_PASSWORD") or get("redis", "password", str, ""),
        port=port,
        ingestion=ingestion,
        ingestion_group=get("ingestion", "group", str, "deciders"),
        dlq=get("ingestion", "dlq_stream", str, f"{ingestion}:dlq"),
        maxlen=get("ingestion", "maxlen", int, 100000),
        url=url,
        health_url=get("decision", "health_url", str, "") or "{0.scheme}://{0.netloc}/api/health".format(urlsplit(url)),
        question=get("decision", "question").strip(),
        context_field=get("decision", "context_field", str, "payload"),
        max_context_chars=get("decision", "max_context_chars", int, 8000),
        theta=get("decision", "theta", float, 0.85),
        max_think_tokens=get("decision", "max_think_tokens", int, 6000),
        max_think_seconds=get("decision", "max_think_seconds", float, 150),
        timeout_s=get("decision", "timeout_s", float, 400),
        model_version=get("decision", "model_version", str, "decisionLM"),
        review_stream=get("decision", "review_stream", str, "decision:review"),
        options=tuple(Option(o["name"], o.get("stream") or f"decision:{o['name']}") for o in tables),
        batch_size=get("reliability", "batch_size", int, 1),
        block_ms=get("reliability", "block_ms", int, 5000),
        reclaim_idle_s=get("reliability", "reclaim_idle_s", float, 600),
        reclaim_every_s=get("reliability", "reclaim_every_s", float, 30),
        max_deliveries=get("reliability", "max_deliveries", int, 5),
        retries=get("reliability", "retries", int, 2),
        retry_backoff_s=get("reliability", "retry_backoff_s", float, 5),
        health_poll_s=get("reliability", "health_poll_s", float, 5),
        dedupe_ttl_s=get("reliability", "dedupe_ttl_s", int, 86400),
        deciders=get("run", "deciders", int, 1),
        node_name=get("run", "node_name", str, "") or socket.gethostname(),
        workers=get("run", "workers", bool, False),
        status_every_s=get("run", "status_every_s", float, 30),
        shutdown_grace_s=get("run", "shutdown_grace_s", float, 150),
        dashboard_enabled=get("dashboard", "enabled", bool, True),
        dashboard_host=get("dashboard", "host", str, "127.0.0.1"),
        dashboard_port=get("dashboard", "port", int, 5055),
        dashboard_label_field=get("dashboard", "label_field", str, ""),
        service_start=get("service", "start", bool, True),
        service_dir=_resolve(path, get("service", "dir", str, DEFAULT_SERVICE_DIR)),
        service_python=get("service", "python", str, ""),
    )
    validate(cfg)
    return cfg


def _resolve(config_path: str, value: str) -> str:
    """A relative path in the config is relative to the config file."""
    return value if os.path.isabs(value) else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(config_path)), value))


def validate(c: Config) -> None:
    if not c.question:
        raise ConfigError("[decision] question must not be empty")
    parts = urlsplit(c.url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(f"[decision] url must be an http(s) URL, got {c.url!r}")
    if not c.context_field.strip():
        raise ConfigError("[decision] context_field must not be empty")
    if not 0 < c.theta <= 1:
        raise ConfigError("[decision] theta must be in (0, 1]")
    if c.max_context_chars < 1 or c.max_think_tokens < 1 or c.max_think_seconds <= 0 or c.timeout_s <= 0:
        raise ConfigError("[decision] max_context_chars, max_think_tokens, max_think_seconds and timeout_s must be > 0")
    names = c.option_names
    if not 2 <= len(names) <= 26:
        raise ConfigError("define between 2 and 26 [[options]]")
    if any(not n.strip() for n in names) or len(set(names)) != len(names):
        raise ConfigError("option names must be unique, non-empty strings")
    streams = c.decision_streams
    if any(not s.strip() for s in streams + [c.ingestion, c.dlq, c.ingestion_group]):
        raise ConfigError("stream and group names must not be empty")
    if len(set(streams)) != len(streams):
        raise ConfigError("option streams (and review_stream) must be unique")
    if {c.ingestion, c.dlq} & set(streams) or c.ingestion == c.dlq:
        raise ConfigError("ingestion/dlq streams must differ from the decision streams")
    if c.batch_size < 1 or c.max_deliveries < 1 or c.retries < 0 or c.maxlen < 1 or c.block_ms < 0 or c.dedupe_ttl_s < 0:
        raise ConfigError("batch_size, max_deliveries, maxlen must be >= 1 and retries, block_ms, dedupe_ttl_s >= 0")
    if not c.node_name.strip() or any(ch.isspace() for ch in c.node_name):
        raise ConfigError("[run] node_name must be non-empty text without spaces")
    if c.deciders < 1 or c.status_every_s < 0 or c.shutdown_grace_s < 0 or c.db < 0:
        raise ConfigError("[run] deciders must be >= 1, and status_every_s, shutdown_grace_s and [redis] db must be >= 0")
    if not 0 < c.dashboard_port < 65536 or not c.dashboard_host.strip():
        raise ConfigError("[dashboard] port must be 1-65535 and host must not be empty")
    if c.reclaim_idle_s <= c.timeout_s:
        raise ConfigError(
            f"[reliability] reclaim_idle_s ({c.reclaim_idle_s}) must exceed [decision] timeout_s ({c.timeout_s}); "
            "otherwise a message still being decided is reclaimed and decided twice")


def connect(cfg: Config) -> redis.Redis:
    # socket_timeout exceeds the XREADGROUP block time, so a dead connection errors instead of hanging forever.
    return redis.Redis(host=cfg.host, port=cfg.port, db=cfg.db, password=cfg.password or None, decode_responses=True,
                       socket_connect_timeout=5, socket_timeout=cfg.block_ms / 1000 + 30, health_check_interval=30)


def ensure_groups(r: redis.Redis, cfg: Config) -> None:
    """Idempotent: XGROUP CREATE ... 0 MKSTREAM for the ingestion stream and every configured decision stream;
    BUSYGROUP means it already exists. Groups start at 0, so events written before the first decider starts are
    still decided."""
    for stream in [cfg.ingestion] + cfg.decision_streams:
        try:
            r.xgroup_create(stream, cfg.group_for(stream), id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
