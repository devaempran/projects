"""The one resident llama.cpp model, shared by both tiers: loading, GPU status, the lock that serialises
access (a llama.cpp context is not thread-safe), the option-letter token ids, and reading the answer logits.

It is a process-wide singleton on purpose: the model is 8 GB of VRAM, so there is exactly one copy."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Sequence

import numpy as np
from llama_cpp import Llama, llama_supports_gpu_offload

from decisionlm import settings
from decisionlm.errors import ModelLoadError, ScoringError

_state: dict = {"llm": None, "load_report": None}
label_ids: dict[str, int | None] = {}   # label -> token id, computed once
lock = threading.Lock()                 # hold while evaluating or generating


def _load_with_captured_output(fn: Callable):
    """Run fn() while redirecting the process's stdout/stderr file descriptors
    to a temp file, so we can capture llama.cpp's native (non-Python) log
    lines — e.g. "offloaded N/M layers to GPU" — which bypass sys.stdout."""
    out_fd, err_fd = sys.stdout.fileno(), sys.stderr.fileno()
    saved_out, saved_err = os.dup(out_fd), os.dup(err_fd)
    with tempfile.TemporaryFile(mode="w+b") as tmp:
        os.dup2(tmp.fileno(), out_fd)
        os.dup2(tmp.fileno(), err_fd)
        try:
            result = fn()
        finally:
            os.dup2(saved_out, out_fd)
            os.dup2(saved_err, err_fd)
            os.close(saved_out)
            os.close(saved_err)
        tmp.seek(0)
        log = tmp.read().decode(errors="replace")
    return result, log


def _nvidia_smi_memory() -> dict | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            timeout=5,
        ).decode().strip()
        used, total = (int(x.strip()) for x in out.split(","))
        return {"used_mb": used, "total_mb": total}
    except Exception:
        return None


def get_llm() -> Llama:
    """Load the model once and keep it resident for the life of the process."""
    if _state["llm"] is None:
        cfg = settings.get()
        if not os.path.exists(cfg.model_path):
            raise ModelLoadError(f"Model file not found at {cfg.model_path}. See README for how to fetch it.")
        t0 = time.time()

        def _do_load():
            return Llama(
                model_path=str(cfg.model_path),
                n_gpu_layers=cfg.n_gpu_layers,
                n_ctx=cfg.n_ctx,
                logits_all=False,  # we read the last-position logits directly, not via logprobs
                verbose=True,
            )

        try:
            llm, log = _load_with_captured_output(_do_load)
        except Exception as e:
            raise ModelLoadError(f"Model failed to load: {e}") from e
        offload_lines = [line for line in log.splitlines() if "offload" in line.lower()]
        _state["llm"] = llm
        _state["load_report"] = {
            "load_duration_s": time.time() - t0,
            "supports_gpu_offload": llama_supports_gpu_offload(),
            "n_gpu_layers_requested": cfg.n_gpu_layers,
            "offload_log_lines": offload_lines,
            "gpu_memory": _nvidia_smi_memory(),
        }
    return _state["llm"]


def warmup() -> dict:
    """Explicitly trigger (or confirm) the model load and report timing/GPU info."""
    get_llm()
    return dict(_state["load_report"])


def gpu_status() -> dict:
    if _state["llm"] is None:
        return {"loaded": False}
    report = dict(_state["load_report"])
    report["loaded"] = True
    return report


def token_ids_for(llm: Llama, labels: Sequence[str]) -> list[int]:
    """Token id of each option label (cached). Raises ScoringError if a label is not a single token."""
    ids = []
    for label in labels:
        if label not in label_ids:
            toks = llm.tokenize(label.encode("utf-8"), add_bos=False)
            label_ids[label] = toks[0] if len(toks) == 1 else None
        if label_ids[label] is None:
            raise ScoringError(
                f"Label {label!r} is not a single token, so no logit can be read for it. "
                f"Use short labels (e.g. single letters A, B, C)."
            )
        ids.append(label_ids[label])
    return ids


def last_logits(llm: Llama) -> np.ndarray:
    """Logits at the last evaluated position, as a float64 copy. The one place that touches llama-cpp's
    private context handle, so a library change only has to be fixed here."""
    return np.ctypeslib.as_array(llm._ctx.get_logits_ith(-1), shape=(llm.n_vocab(),)).astype(np.float64)
