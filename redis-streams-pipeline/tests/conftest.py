"""Fixtures: a Redis connection, a mock decisionLM on a free port, and a config with its own uniquely prefixed streams, so
tests never share state (or ports) and can run in any order. Needs Redis; tests are skipped if it is not reachable
(REDIS_HOST / REDIS_PORT, default 127.0.0.1:6380)."""
import os
import socket
import uuid

import pytest
import redis
from helpers import Mock, config_text

from pipeline import config, runtime

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6380))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session", autouse=True)
def _redis_available():
    try:
        redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=2).ping()
    except redis.RedisError:
        pytest.skip(f"Redis is not reachable at {REDIS_HOST}:{REDIS_PORT}", allow_module_level=False)


@pytest.fixture(autouse=True)
def _reset_stop_flag():
    runtime.stop.clear()
    yield
    runtime.stop.clear()


@pytest.fixture
def mock_port():
    return free_port()


@pytest.fixture
def make_cfg(tmp_path, mock_port):
    """Factory for configs with fresh stream names; cleans up everything it created."""
    prefixes = []

    def _make(**overrides):
        prefix = "t" + uuid.uuid4().hex[:8]
        prefixes.append(prefix)
        path = tmp_path / f"{prefix}.toml"
        path.write_text(config_text(prefix, REDIS_PORT, mock_port, **overrides))
        cfg = config.load(str(path), environ={"REDIS_HOST": REDIS_HOST})
        config.ensure_groups(config.connect(cfg), cfg)
        return cfg

    yield _make
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
    for prefix in prefixes:
        for key in list(r.scan_iter(f"{prefix}*")) + list(r.scan_iter(f"dedupe:{prefix}*")):
            r.delete(key)


@pytest.fixture
def cfg(make_cfg):
    return make_cfg()


@pytest.fixture
def r(cfg):
    return config.connect(cfg)


@pytest.fixture
def mock(mock_port):
    m = Mock(mock_port)
    m.start()
    yield m
    m.stop()
