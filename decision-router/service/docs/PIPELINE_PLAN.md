# Confidence-routed decisions: design

Status: **implemented.** `/api/decide` (the cascade) is in `src/decisionlm/`, and the Redis Streams pipeline that feeds it is in
`../../pipeline`. This document records the design, why it is shaped this way, and what is still open.
Measured results are in [`../RESULTS.md`](../RESULTS.md).

## The idea

A small local model answers every question cheaply and says how sure it is. Only the questions it is unsure about pay for a slow, careful
second look. The value is a high share of questions that never escalate, at high accuracy.

```
request {Context, Question, Options}
   │
   ▼
Tier 1  (local, about 0.1 s)  one forward pass, probability of each option letter, temperature-scaled
   │      confidence = top probability
   ▼
confidence >= theta  and  label mass >= 0.5 ?
   ├─ yes ──> return the answer, source = "fast"
   └─ no  ──> Tier 2  (same model, thinking on, about 20 s)
                 ├─ answered ──> return it, source = "reasoner"
                 ├─ ran out of budget or time ──> answer forced from logits, source = "reasoner"
                 └─ failed ──> return tier 1's answer with low_confidence = true
```

## Why these choices

| Decision | Reason (evidence) |
|---|---|
| Read logits instead of generating | one forward pass; probabilities are real, not parsed text. About 98% of latency is prompt processing. |
| Temperature scaling (T = 7.5) | the raw model is overconfident. T fitted on ARC validation gives ECE about 0.03 and transfers to MMLU, CommonsenseQA and BoolQ (ECE under 0.05). It never changes the winner. |
| Route on confidence, not on "Not sure" | an explicit "I'm not sure" option was tested and rejected: no better at separating right from wrong (AUROC 0.857 and 0.892 against 0.896 for plain confidence). |
| Route on confidence, not on label mass | label mass is nearly constant (0.9998 to 1.0) and separates right from wrong poorly (AUROC 0.71 against 0.90). It stays only as a safety net (below 0.5 escalates). |
| `theta` = 0.85 | the knee of accuracy against cost on ARC validation (0.886 to 0.930 for 24% escalated); confirmed once on test. Must be re-chosen from real data. |
| Reasoner sees shuffled options | the model flips its answer for 13.4% of items under a rotation of the options. Shuffling stops the reasoner inheriting tier 1's position bias. Tier 1's own hint is not passed to it (it could anchor it to a wrong answer). |
| 6,000-token budget, 150 s cap | at 1,500 tokens a third of escalations hit the cap and were forced; at 6,000 none were, with a median of 677 thinking tokens. The time cap exists because the server handles one request at a time. |
| Same model for both tiers | simplest to run (one resident copy, 10 GB). A larger tier-2 model has not been tried. |
| Return `low_confidence` instead of failing | a reasoner error should not lose the decision; the caller decides what to do with an uncertain one. |
| Pipeline fixed Question and Options | the real use case is one recurring decision (chunk boundaries), so only Context varies. Options map one-to-one to downstream streams. |

## What the cascade does not fix

- Tier 1's confident errors. About 1.2% of answers at confidence 0.9 and above are wrong, and the reasoner never sees them. Of 15 examined on ARC test:
  5 factual recall, 5 multi-step reasoning, 5 bad or debatable labels. Raising `theta` trades fewer of these for more escalations.
- Distribution shift: `theta` and T come from a 4-option science exam. Calibration degrades with many options (ECE 0.065 at 26).
- The reasoner has no calibrated probability. The response says which path produced the answer (`source`) so consumers can treat the paths differently.

## Operating it

- **Choose `theta` from data:** sweep it on a labeled sample of real decisions and pick it from a target such as "fast path at least 98% accurate" or "at most 30% escalated". Do not reuse one threshold across different option counts.
- **Monitor:** per request, log source, confidence and latency. Periodically label a sample of fast-path decisions. A rising escalation rate means the inputs are drifting.
- **Probabilities are noisy by about 0.01** (occasionally 0.1) when the KV cache is reused, because GPU kernels are not bit-stable across batch shapes. Do not set thresholds finer than that; disable prefix reuse if exact reproducibility matters.

## Open questions

- Which tier-2 model: a larger local model or an API model? (`tier2.py` is the only place that would change.)
- What accuracy and escalation-rate target should set `theta` for the real use case? Is there a labeled sample of real decisions?
- Should the reasoner see tier 1's top two options as a hint? The anchoring risk has not been tested either way.
- Rotation averaging of tier 1 (about +1 point accuracy for 4x compute) on the escalation path only.
