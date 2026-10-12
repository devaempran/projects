"""
Tier 2 of /api/decide: the same local model, run with thinking enabled.

The model is shared with tier 1 (one resident copy, one lock). Thinking is bounded two ways:
max_think_tokens and max_think_seconds (the server handles one request at a time, so one runaway
generation would stall every request behind it). If the reasoner doesn't finish inside either bound, or
doesn't emit a parseable "Answer: X" line, the answer is forced by reading label logits after closing the
think block.
"""
import random
import re
import time
import zlib

import numpy as np
from llama_cpp import StoppingCriteriaList

from decisionlm import model, prompts, settings
from decisionlm.errors import PromptTooLongError

_ANSWER_RE = re.compile(r"Answer:\s*\**\(?([A-Z])\b")


def _forced_answer(llm, prompt, thinking, labels):
    """Close the think block and read label logits for the answer letter."""
    text = prompt + thinking + "\n\nI must answer now.\n</think>\n\nAnswer: "
    tokens = llm.tokenize(text.encode("utf-8"), special=True)
    llm.reset()
    llm.eval(tokens)
    row = np.ctypeslib.as_array(llm._ctx.get_logits_ith(-1), shape=(llm.n_vocab(),))
    ids = model.token_ids_for(llm, labels)
    return int(np.argmax([row[i] for i in ids]))


def reason(context, question, options, max_think_tokens=None, max_think_seconds=None):
    """
    options: list of option strings (already validated). Option order is shuffled
    (deterministically per question) so the reasoner doesn't inherit position bias.
    Returns {"index": int (into `options`), "forced": bool, "timed_out": bool, "tokens": int, "latency_s": float}.
    """
    budget = max_think_tokens or settings.MAX_THINK_TOKENS
    seconds = max_think_seconds or settings.MAX_THINK_SECONDS
    k = len(options)
    labels = list(settings.LABELS[:k])
    perm = list(range(k))
    random.Random(zlib.crc32(question.encode("utf-8"))).shuffle(perm)  # shuffled position -> original index
    choices = [{"label": l, "text": options[i]} for l, i in zip(labels, perm)]

    llm = model.get_llm()
    model.token_ids_for(llm, labels)  # fail early if a label isn't a single token
    prompt = prompts.tier2_prompt(context, question, choices)
    with model.lock:
        t0 = time.time()
        try:
            n_prompt = len(llm.tokenize(prompt.encode("utf-8"), special=True))
            room = settings.N_CTX - n_prompt - 64  # window left for thinking
            if room < 256:
                raise PromptTooLongError("Prompt too long for the reasoner's context window.")
            max_tokens = min(budget, room)
            deadline = t0 + seconds
            llm.reset()
            out = llm.create_completion(
                prompt=prompt, max_tokens=max_tokens, temperature=0.6, top_p=0.95, top_k=20, seed=0,
                stop=["<|im_end|>"],
                stopping_criteria=StoppingCriteriaList([lambda ids, logits: time.time() > deadline]))
            text = out["choices"][0]["text"]
            n_tok = out["usage"]["completion_tokens"]
            timed_out = time.time() > deadline
            after = text.split("</think>")[-1] if "</think>" in text else ""
            m = _ANSWER_RE.findall(after)
            forced = not (m and m[-1] in labels)
            if forced:
                pos = _forced_answer(llm, prompt, text.split("</think>")[0], labels)
            else:
                pos = labels.index(m[-1])
        except BaseException:
            llm.reset()  # never leave a half-finished generation in the KV cache for the next request
            raise
        latency = time.time() - t0
    return {"index": perm[pos], "forced": forced, "timed_out": timed_out, "tokens": n_tok,
            "latency_s": latency}
