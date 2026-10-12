"""The one resident llama.cpp model, shared by both tiers: loading, GPU status, the lock that serialises
access (a llama.cpp context is not thread-safe) and the option-letter token ids."""
import os
import subprocess
import sys
import tempfile
import threading
import time

from llama_cpp import Llama, llama_supports_gpu_offload

from decisionlm import settings
from decisionlm.errors import ModelLoadError, ScoringError

_state = {"llm": None, "load_report": None}
label_ids = {}            # label -> token id, computed once
lock = threading.Lock()   # hold while evaluating or generating


def _load_with_captured_output(fn):
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


def _nvidia_smi_memory():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            timeout=5,
        ).decode().strip()
        used, total = (int(x.strip()) for x in out.split(","))
        return {"used_mb": used, "total_mb": total}
    except Exception:
        return None


def get_llm():
    """Load the model once and keep it resident for the life of the process."""
    if _state["llm"] is None:
        if not os.path.exists(settings.MODEL_PATH):
            raise ModelLoadError(
                f"Model file not found at {settings.MODEL_PATH}. See README for how to fetch it."
            )
        t0 = time.time()

        def _do_load():
            return Llama(
                model_path=str(settings.MODEL_PATH),
                n_gpu_layers=settings.N_GPU_LAYERS,
                n_ctx=settings.N_CTX,
                logits_all=False,  # we read the last-position logits directly, not via logprobs
                verbose=True,
            )

        try:
            llm, log = _load_with_captured_output(_do_load)
        except Exception as e:
            raise ModelLoadError(f"Model failed to load: {e}") from e
        load_s = time.time() - t0

        offload_lines = [l for l in log.splitlines() if "offload" in l.lower()]
        _state["llm"] = llm
        _state["load_report"] = {
            "load_duration_s": load_s,
            "supports_gpu_offload": llama_supports_gpu_offload(),
            "n_gpu_layers_requested": settings.N_GPU_LAYERS,
            "offload_log_lines": offload_lines,
            "gpu_memory": _nvidia_smi_memory(),
        }
    return _state["llm"]


def warmup():
    """Explicitly trigger (or confirm) the model load and report timing/GPU info."""
    get_llm()
    return dict(_state["load_report"])


def gpu_status():
    if _state["llm"] is None:
        return {"loaded": False}
    report = dict(_state["load_report"])
    report["loaded"] = True
    return report


def token_ids_for(llm, labels):
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
