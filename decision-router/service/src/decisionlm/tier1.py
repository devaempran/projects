"""
Tier 1: one forward pass through the local model, reading the next-token logits at the answer position
instead of generating text. No Ollama, no network calls.
"""
from __future__ import annotations

import math
import time
from typing import Sequence

import numpy as np

from decisionlm import model, prompts, settings
from decisionlm.errors import PromptTooLongError, ScoringError

LABELS = settings.LABELS


def score_options(context: str, question: str, options: Sequence[str]) -> dict:
    """
    Public entry point. options: plain option strings. Each is assigned a letter (A, B, C, ...) for the LLM
    prompt; the letters are an internal detail. Returns the full score() result, with choices in the same
    order as `options`.
    """
    if not isinstance(options, list) or len(options) < 2:
        raise ScoringError("Provide at least two options.")
    if len(options) > len(LABELS):
        raise ScoringError(f"At most {len(LABELS)} options are supported.")
    if not all(isinstance(o, str) and o.strip() for o in options):
        raise ScoringError("Every option must be a non-empty string.")
    if len(set(options)) != len(options):
        raise ScoringError("Options must be unique (they are used as output keys).")
    choices = [{"label": label, "text": o} for label, o in zip(LABELS, options, strict=False)]
    return score(context, question, choices)


def _evaluate(llm, tokens: list[int], min_prefix_reuse: int) -> np.ndarray:
    """Run the prompt through the model, reusing the cached KV prefix only for a genuinely shared context
    (a shorter prefix saves nothing and shifts the logits slightly). Returns the last-position logits."""
    prev = llm.input_ids[: llm.n_tokens]
    m, lim = 0, min(len(prev), len(tokens) - 1)
    while m < lim and prev[m] == tokens[m]:
        m += 1
    if m >= min_prefix_reuse:
        llm.n_tokens = m
        llm.eval(tokens[m:])
    else:
        llm.reset()
        llm.eval(tokens)
    return model.last_logits(llm)


def score(context: str, question: str, choices: Sequence[dict]) -> dict:
    """
    choices: list of {"label": str, "text": str}
    Returns a dict with per-choice probabilities, the winner, diagnostic info, and timing.
    Raises ScoringError if any label can't be read as a single token at the answer position.
    """
    cfg = settings.get()
    if not choices or len(choices) < 2:
        raise ScoringError("Provide at least two choices.")
    labels = [c["label"] for c in choices]
    if len(set(labels)) != len(labels):
        raise ScoringError(f"Choice labels must be unique, got: {labels}")

    llm = model.get_llm()
    prompt = prompts.tier1_prompt(context, question, choices)
    ids = model.token_ids_for(llm, labels)

    with model.lock:
        t0 = time.time()
        tokens = llm.tokenize(prompt.encode("utf-8"), special=True)
        if len(tokens) >= cfg.n_ctx:
            raise PromptTooLongError(
                f"Prompt is {len(tokens)} tokens; the context window is {cfg.n_ctx}. Shorten the Context."
            )
        row = _evaluate(llm, tokens, cfg.min_prefix_reuse)
        eval_s = time.time() - t0
        unconstrained_top = llm.detokenize([int(row.argmax())]).decode("utf-8", errors="replace")
    lse = float(row.max() + np.log(np.exp(row - row.max()).sum()))  # full-vocabulary log-softmax
    logprobs = {label: float(row[i] - lse) for label, i in zip(labels, ids, strict=True)}

    # Softmax restricted to just the permitted labels (conditional probability given that the answer is
    # one of these choices), with temperature scaling to correct the model's overconfidence. T doesn't
    # change the winner.
    max_lp = max(logprobs.values())
    exps = {label: math.exp((lp - max_lp) / cfg.temperature) for label, lp in logprobs.items()}
    denom = sum(exps.values())
    cond_probs = {label: exps[label] / denom for label in labels}

    # Unconditional probability mass the FULL vocabulary distribution assigns to the permitted labels
    # (no renormalization): shows when the model would rather say something else entirely.
    total_label_mass = sum(math.exp(lp) for lp in logprobs.values())

    return {
        "model": cfg.model_label,
        "prompt": prompt,
        "choices": [
            {
                "label": label,
                "text": next(c["text"] for c in choices if c["label"] == label),
                "logprob": logprobs[label],
                "probability": cond_probs[label],
            }
            for label in labels
        ],
        "winner": max(cond_probs, key=cond_probs.get),
        "temperature": cfg.temperature,
        "total_label_probability_mass": total_label_mass,
        "unconstrained_top_token": unconstrained_top,
        "unconstrained_matches_a_label": unconstrained_top.strip() in labels,
        "timing": {"eval_s": eval_s, "prompt_tokens": len(tokens)},
        "gpu": model.gpu_status(),
    }
