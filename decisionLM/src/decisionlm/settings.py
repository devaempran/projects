"""Loads config/decisionlm.toml (path overridable with DECISIONLM_CONFIG) and exposes it as constants.
A bad or missing setting raises SettingsError naming it, never a bare KeyError."""
import json
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("DECISIONLM_CONFIG", ROOT / "config" / "decisionlm.toml"))
STATIC_DIR = ROOT / "static"
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class SettingsError(Exception):
    pass


try:
    with open(CONFIG_PATH, "rb") as _f:
        _cfg = tomllib.load(_f)
except FileNotFoundError:
    raise SettingsError(f"config file not found: {CONFIG_PATH}") from None
except tomllib.TOMLDecodeError as _e:
    raise SettingsError(f"{CONFIG_PATH} is not valid TOML: {_e}") from None


def _get(section, key, cast=str, env=None):
    """Setting [section] key, optionally overridden by environment variable `env`."""
    raw = os.environ.get(env) if env else None
    if raw is None:
        sec = _cfg.get(section)
        if not isinstance(sec, dict) or key not in sec:
            raise SettingsError(f"missing setting [{section}] {key} in {CONFIG_PATH}")
        raw = sec[key]
    if isinstance(raw, bool) or (cast in (int, float) and isinstance(raw, str) and env is None):
        raise SettingsError(f"[{section}] {key} must be {'a number' if cast in (int, float) else 'text'}, got {raw!r}")
    try:
        return cast(raw)
    except (TypeError, ValueError):
        source = f"environment variable {env}" if os.environ.get(env or "") is not None else f"[{section}] {key}"
        raise SettingsError(f"{source} has an invalid value: {raw!r}") from None


def _path(value):
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


MODEL_PATH = _path(_get("model", "path"))
MODEL_LABEL = _get("model", "label")
N_CTX = _get("model", "n_ctx", int)
N_GPU_LAYERS = _get("model", "n_gpu_layers", int)

CALIBRATION_PATH = _path(_get("scoring", "calibration_file"))
SYSTEM_PROMPT = _get("scoring", "system_prompt")
MIN_PREFIX_REUSE = _get("scoring", "min_prefix_reuse", int)

DECIDE_THETA = _get("decide", "theta", float, env="DECIDE_THETA")
MIN_LABEL_MASS = _get("decide", "min_label_mass", float)
MAX_THINK_TOKENS = _get("decide", "max_think_tokens", int, env="MAX_THINK_TOKENS")
MAX_THINK_SECONDS = _get("decide", "max_think_seconds", float, env="MAX_THINK_SECONDS")

HOST = _get("server", "host")
PORT = _get("server", "port", int, env="PORT")
THREADS = _get("server", "threads", int)
MAX_BODY_BYTES = _get("server", "max_body_bytes", int, env="MAX_BODY_BYTES")

if N_CTX < 512 or MAX_THINK_TOKENS < 1 or MAX_THINK_SECONDS <= 0 or MAX_BODY_BYTES < 1 or THREADS < 1:
    raise SettingsError("n_ctx must be >= 512; max_think_tokens, max_think_seconds, max_body_bytes, threads must be positive")
if not 0 <= DECIDE_THETA <= 1000:
    raise SettingsError("[decide] theta is out of range")


def load_temperature():
    """Softmax temperature T (>1 softens confidence), fit by scripts/fit_temperature.py. 1.0 if unavailable."""
    try:
        with open(CALIBRATION_PATH) as f:
            return float(json.load(f)["temperature"])
    except (OSError, KeyError, ValueError):
        return 1.0
