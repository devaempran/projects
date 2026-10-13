"""The decide cascade: tier 1 answers confident cases, tier 2 (the thinking reasoner) takes the rest.

Kept free of HTTP so it can be tested and reused without Flask."""
from __future__ import annotations

import logging
import time
from typing import Sequence

from decisionlm import settings, tier1, tier2

log = logging.getLogger("decisionLM")


def decide(context: str, question: str, options: Sequence[str], theta: float | None = None,
           max_think_tokens: int | None = None, max_think_seconds: float | None = None) -> dict:
    """
    Tier 1 scores the options; if its confidence is below `theta` (or the option letters hold under
    `min_label_mass` of the model's probability) the reasoner decides instead. theta above 1 forces
    escalation; 0 never escalates. If the reasoner fails, tier 1's answer is returned with
    low_confidence=True and reasoner_error says why.
    """
    cfg = settings.get()
    theta = cfg.theta if theta is None else theta
    t0 = time.time()
    result = tier1.score_options(context, question, options)  # validates the type itself
    choices = result["choices"]
    top = max(choices, key=lambda c: c["probability"])
    out = {"answer": top["text"], "source": "fast", "confidence": top["probability"],
           "probabilities": {c["text"]: c["probability"] for c in choices},
           "tier1_answer": top["text"], "low_confidence": False}

    if top["probability"] < theta or result["total_label_probability_mass"] < cfg.min_label_mass:
        try:
            r = tier2.reason(context, question, [c["text"] for c in choices], max_think_tokens, max_think_seconds)
            out.update(answer=choices[r["index"]]["text"], source="reasoner", forced=r["forced"],
                       timed_out=r["timed_out"], reasoner_tokens=r["tokens"], reasoner_latency_s=r["latency_s"])
        except Exception as e:  # tier 2 must never take the request down
            log.warning("reasoner failed: %r", e)
            out.update(source="fast", low_confidence=True, reasoner_error=str(e))
    out["latency_s"] = time.time() - t0
    return out
