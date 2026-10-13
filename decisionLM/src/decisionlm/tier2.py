"""
Tier 2 of /api/decide: the same local model, run with thinking enabled.

The model is shared with tier 1 (one resident copy, one lock). Thinking is bounded two ways:
max_think_tokens and max_think_seconds (the server handles one request at a time, so one runaway
generation would stall every request behind it). If the reasoner doesn't finish inside either bound, or
doesn't emit a parseable "Answer: X" line, the answer is forced by reading label logits after closing the
think block.
"""
from __future__ import annotations

import random
import re
import time
import zlib
from typing import Sequence

import numpy as np
from llama_cpp import StoppingCriteriaList

from decisionlm import model, prompts, settings
from decisionlm.errors import PromptTooLongError

_ANSWER_RE = re.compile(r"Answer:\s*\**\(?([A-Z])\b")


def parse_answer(text: str, labels: Sequence[str]) -> int | None:
    """Index of the last 'Answer: X' line written after the think block, or None if there is none (or X is not
    one of the labels)."""
    after = text.split("</think>")[-1] if "</think>" in text else ""
    found = _ANSWER_RE.findall(after)
    return labels.index(found[-1]) if found and found[-1] in labels else None


def _forced_answer(llm, prompt: str, thinking: str, labels: Sequence[str]) -> int:
    """Close the think block and read label logits for the answer letter."""
    text = prompt + thinking + "\n\nI must answer now.\n</think>\n\nAnswer: "
    llm.reset()
    llm.eval(llm.tokenize(text.encode("utf-8"), special=True))
    row = model.last_logits(llm)
    return int(np.argmax([row[i] for i in model.token_ids_for(llm, labels)]))


def reason(context: str, question: str, options: Sequence[str], max_think_tokens: int | None = None,
           max_think_seconds: float | None = None) -> dict:
    """
    options: option strings (already validated). Option order is shuffled (deterministically per question) so
    the reasoner doesn't inherit position bias.
    Returns {"index": int (into `options`), "forced": bool, "timed_out": bool, "tokens": int, "latency_s": float}.
    """
    cfg = settings.get()
    budget = max_think_tokens or cfg.max_think_tokens
    seconds = max_think_seconds or cfg.max_think_seconds
    labels = list(settings.LABELS[:len(options)])
    perm = list(range(len(options)))
    random.Random(zlib.crc32(question.encode("utf-8"))).shuffle(perm)  # shuffled position -> original index
    choices = [{"label": label, "text": options[i]} for label, i in zip(labels, perm, strict=True)]

    llm = model.get_llm()
    model.token_ids_for(llm, labels)  # fail early if a label isn't a single token
    prompt = prompts.tier2_prompt(context, question, choices)
    with model.lock:
        t0 = time.time()
        try:
            room = cfg.n_ctx - len(llm.tokenize(prompt.encode("utf-8"), special=True)) - 64  # window left for thinking
            if room < 256:
                raise PromptTooLongError("Prompt too long for the reasoner's context window.")
            deadline = t0 + seconds
            llm.reset()
            out = llm.create_completion(
                prompt=prompt, max_tokens=min(budget, room), temperature=0.6, top_p=0.95, top_k=20, seed=0,
                stop=["<|im_end|>"],
                stopping_criteria=StoppingCriteriaList([lambda ids, logits: time.time() > deadline]))
            text = out["choices"][0]["text"]
            timed_out = time.time() > deadline
            pos = parse_answer(text, labels)
            forced = pos is None
            if forced:
                pos = _forced_answer(llm, prompt, text.split("</think>")[0], labels)
        except BaseException:
            llm.reset()  # never leave a half-finished generation in the KV cache for the next request
            raise
        latency = time.time() - t0
    return {"index": perm[pos], "forced": forced, "timed_out": timed_out,
            "tokens": out["usage"]["completion_tokens"], "latency_s": latency}
