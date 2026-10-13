"""Settings: config/decisionlm.toml (path overridable with DECISIONLM_CONFIG) as an immutable object.

Nothing is read at import time. `load()` builds a `Settings` from a file and an environment; `get()` loads the default
once on first use; `use()` swaps in a specific object (tests, embedding). A bad or missing setting raises
SettingsError naming it, never a bare KeyError."""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "config" / "decisionlm.toml"
STATIC_DIR = ROOT / "static"
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    model_path: Path
    model_label: str
    n_ctx: int
    n_gpu_layers: int
    temperature: float          # softmax temperature T from the calibration file (1.0 if unavailable)
    system_prompt: str
    min_prefix_reuse: int
    theta: float                # /api/decide: escalate below this confidence
    min_label_mass: float
    max_think_tokens: int
    max_think_seconds: float
    host: str
    port: int
    threads: int
    max_body_bytes: int


def _path(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def _temperature(path: Path) -> float:
    try:
        with open(path) as f:
            return float(json.load(f)["temperature"])
    except (OSError, KeyError, ValueError):
        return 1.0


def load(path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
    """Read a settings file. `environ` (default: os.environ) overrides PORT, DECIDE_THETA, MAX_THINK_TOKENS,
    MAX_THINK_SECONDS and MAX_BODY_BYTES."""
    environ = os.environ if environ is None else environ
    path = Path(path or environ.get("DECISIONLM_CONFIG") or DEFAULT_PATH)
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        raise SettingsError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path} is not valid TOML: {e}") from None

    def get(section: str, key: str, cast: Callable = str, env: str | None = None):
        raw = environ.get(env) if env else None
        from_env = raw is not None
        if not from_env:
            sec = cfg.get(section)
            if not isinstance(sec, dict) or key not in sec:
                raise SettingsError(f"missing setting [{section}] {key} in {path}")
            raw = sec[key]
            if isinstance(raw, bool) or (cast in (int, float) and not isinstance(raw, (int, float))):
                raise SettingsError(f"[{section}] {key} must be {'a number' if cast in (int, float) else 'text'}, got {raw!r}")
        try:
            return cast(raw)
        except (TypeError, ValueError):
            where = f"environment variable {env}" if from_env else f"[{section}] {key}"
            raise SettingsError(f"{where} has an invalid value: {raw!r}") from None

    s = Settings(
        model_path=_path(get("model", "path")),
        model_label=get("model", "label"),
        n_ctx=get("model", "n_ctx", int),
        n_gpu_layers=get("model", "n_gpu_layers", int),
        temperature=_temperature(_path(get("scoring", "calibration_file"))),
        system_prompt=get("scoring", "system_prompt"),
        min_prefix_reuse=get("scoring", "min_prefix_reuse", int),
        theta=get("decide", "theta", float, env="DECIDE_THETA"),
        min_label_mass=get("decide", "min_label_mass", float),
        max_think_tokens=get("decide", "max_think_tokens", int, env="MAX_THINK_TOKENS"),
        max_think_seconds=get("decide", "max_think_seconds", float, env="MAX_THINK_SECONDS"),
        host=get("server", "host"),
        port=get("server", "port", int, env="PORT"),
        threads=get("server", "threads", int),
        max_body_bytes=get("server", "max_body_bytes", int, env="MAX_BODY_BYTES"),
    )
    if s.n_ctx < 512 or s.max_think_tokens < 1 or s.max_think_seconds <= 0 or s.max_body_bytes < 1 or s.threads < 1:
        raise SettingsError("n_ctx must be >= 512; max_think_tokens, max_think_seconds, max_body_bytes, threads must be positive")
    if not 0 <= s.theta <= 1000:
        raise SettingsError("[decide] theta is out of range")
    return s


_current: Settings | None = None


def get() -> Settings:
    """The active settings (loaded from the default location on first use)."""
    global _current
    if _current is None:
        _current = load()
    return _current


def use(settings: Settings | None) -> None:
    """Make `settings` the active settings (None resets, so the next get() reloads)."""
    global _current
    _current = settings
