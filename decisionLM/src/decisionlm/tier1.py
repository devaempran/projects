"""
Tier 1: one forward pass through the local model, reading the next-token logits at the answer position
instead of generating text. No Ollama, no network calls.
"""
import math
import time

import numpy as np

from decisionlm import model, prompts, settings
from decisionlm.errors import PromptTooLongError, ScoringError

LABELS = settings.LABELS
TEMPERATURE = settings.load_temperature()


def score_options(context, question, options):
    """
    Public entry point. options: list of plain option strings. Each is assigned a letter (A, B, C, ...)
    for the LLM prompt; the letters are an internal detail. Returns the full score() result, with
    choices in the same order as `options`.
    """
    if not isinstance(options, list) or len(options) < 2:
        raise ScoringError("Provide at least two options.")
    if len(options) > len(LABELS):
        raise ScoringError(f"At most {len(LABELS)} options are supported.")
    if not all(isinstance(o, str) and o.strip() for o in options):
        raise ScoringError("Every option must be a non-empty string.")
    if len(set(options)) != len(options):
        raise ScoringError("Options must be unique (they are used as output keys).")
    choices = [{"label": l, "text": o} for l, o in zip(LABELS, options)]
    return score(context, question, choices)


def score(context, question, choices):
    """
    choices: list of {"label": str, "text": str}
    Returns a dict with per-choice probabilities, the winner, diagnostic info, and timing.
    Raises ScoringError if any label can't be read as a single token at the answer position.
    """
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
        if len(tokens) >= settings.N_CTX:
            raise PromptTooLongError(
                f"Prompt is {len(tokens)} tokens; the context window is {settings.N_CTX}. Shorten the Context."
            )
        # Reuse the cached KV prefix only for a genuinely shared context (see min_prefix_reuse).
        prev = llm.input_ids[: llm.n_tokens]
        m, lim = 0, min(len(prev), len(tokens) - 1)
        while m < lim and prev[m] == tokens[m]:
            m += 1
        if m >= settings.MIN_PREFIX_REUSE:
            llm.n_tokens = m
            llm.eval(tokens[m:])
        else:
            llm.reset()
            llm.eval(tokens)
        row = np.ctypeslib.as_array(llm._ctx.get_logits_ith(-1), shape=(llm.n_vocab(),)).astype(np.float64)
        eval_s = time.time() - t0
        top_id = int(row.argmax())
        unconstrained_top = llm.detokenize([top_id]).decode("utf-8", errors="replace")
    lse = float(row.max() + np.log(np.exp(row - row.max()).sum()))  # full-vocabulary log-softmax
    logprobs = {label: float(row[i] - lse) for label, i in zip(labels, ids)}

    # Softmax restricted to just the permitted labels (conditional probability given that the answer is
    # one of these choices), with temperature scaling to correct the model's overconfidence. T doesn't
    # change the winner.
    max_lp = max(logprobs.values())
    exps = {l: math.exp((lp - max_lp) / TEMPERATURE) for l, lp in logprobs.items()}
    denom = sum(exps.values())
    cond_probs = {l: exps[l] / denom for l in labels}

    # Unconditional probability mass the FULL vocabulary distribution assigns to the permitted labels
    # (no renormalization): shows when the model would rather say something else entirely.
    total_label_mass = sum(math.exp(lp) for lp in logprobs.values())

    winner = max(cond_probs, key=cond_probs.get)

    return {
        "model": settings.MODEL_LABEL,
        "prompt": prompt,
        "choices": [
            {
                "label": l,
                "text": next(c["text"] for c in choices if c["label"] == l),
                "logprob": logprobs[l],
                "probability": cond_probs[l],
            }
            for l in labels
        ],
        "winner": winner,
        "temperature": TEMPERATURE,
        "total_label_probability_mass": total_label_mass,
        "unconstrained_top_token": unconstrained_top,
        "unconstrained_matches_a_label": unconstrained_top.strip() in labels,
        "timing": {
            "eval_s": eval_s,
            "prompt_tokens": len(tokens),
        },
        "gpu": model.gpu_status(),
    }
