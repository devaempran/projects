# decisionLM evaluation findings

Run on an RTX 5060 Ti, llama-cpp-python 0.3.36 (cu124 wheel), Qwen3-8B. Date: 2026-10-10.
All code is in `bench/`; per-item rows, summaries and plots are in `bench/results/`.
Full table: `RESULTS_TABLE.md`.

## Protocol

- T was tuned and refit on ARC-Challenge **validation** (n=299) using the production rule from
  `fit_temperature.py` (NLL-minimising T, floored at the smallest T where mean confidence <= accuracy).
  ARC **test** (n=1,172) was scored once per configuration. Numbers below are test unless stated.
- Every run logs per-item rows: id, option order, full-vocabulary label logprobs, label mass,
  top unconstrained token, latency, prompt tokens. Everything else is derived from those rows.
- Accuracy runs used a direct eval path (`llm.eval` + last-row logits) with `logits_all=False`, except the
  baseline row, which uses `logits_all=True`.
- Rotation tests score each item under up to 4 cyclic rotations of the options.
- Load tests used a small threaded Python client (no locust/hey installed): 160 validation requests.
- Qwen-official Q4/Q5/Q8 and Qwen3-4B GGUFs came from `Qwen/Qwen3-*-GGUF`. The production GGUF is the Ollama build.

## Phase 1: baseline

| metric | value |
|---|---|
| accuracy / ECE / Brier / NLL | 0.902 / 0.032 / 0.149 / 0.299 (T=6.75) |
| mean label mass | 0.99999 |
| latency p50 / p95 (direct eval, logits_all=True) | 45 / 65 ms |
| latency p50 / p95 (production `create_completion`) | 156 / 205 ms |
| prompt tokens mean / p95 / max | 101 / 144 / 232 |

Latency breakdown (p50): tokenization 0.2 ms, prefill 44 ms, logprob extraction 0.9 ms. Prefill is about 98% of
the time and scales at about 0.36 ms per prompt token (r=0.97). The production `create_completion` path adds
about 110 ms of Python-side full-vocabulary logprob handling on top of the same forward pass.

Note: the T fitted here (6.75) differs from the shipped `calibration.json` (7.0). That file was fit through the
server path, whose probabilities carry prefix-cache noise (see Phase 2). The difference is within that noise.

Load test (HTTP, includes JSON and server overhead):

| server | concurrency | throughput (req/s) | p95 | result |
|---|---|---|---|---|
| Flask dev server | 1 | 5.6 | 222 ms | ok |
| Flask dev server | 4 | n/a | n/a | **server crashed** (ggml assertion) |
| Flask dev server | 16 | n/a | n/a | **server crashed**, reproduced on a fresh server |
| waitress, 1 thread | 1 / 4 / 16 | 5.5 / 5.8 / 5.9 | 225 ms / 772 ms / 2.96 s | stable, 0 errors |

The dev server runs requests on concurrent threads against a non-thread-safe llama.cpp context, so `app.py` is not
safe under any concurrent traffic. Serialising through one worker thread fixes it. Queueing then shows up as
linear p95 growth, since throughput is capped by one model.

## Phase 2: speed changes that should not alter outputs

Reference is the baseline path on validation. `max dP` is the largest per-item probability difference at T=6.75.

| change | p50 ms | max dP | argmax flips | verdict |
|---|---|---|---|---|
| baseline (`logits_all=True`) | 46 | 0 | 0 | |
| `logits_all=False` | 39 | 0.010 | 0 | free in practice, -15% |
| + `flash_attn` | 37 | 0.152 | 1 of 299 | not free: val ECE 0.035 -> 0.051; test acc 0.900, ECE 0.030 |
| n_batch / n_ubatch 512, 1024, 2048 | 39 | identical to the 512 row | 0 | no effect: all prompts are under 512 tokens |
| production `create_completion` | 170 | | | the largest overhead |

**The 1e-4 equivalence criterion cannot be met by any change.** Q4_K_M CUDA kernels are not bit-stable across batch
shapes. Re-evaluating the same tokens after truncating the KV cache changes label logprobs by 0.3 to 2+ nats, and GPU
vs CPU differs by similar amounts, mostly in the far tail. The same prompt gives identical output on repeat runs, so
it is shape-dependent, not random. This also affects production: `create_completion` reuses the previous request's
KV prefix, so the same prompt gets a different distribution depending on the prior request. 196 of 1,172 test items
differ by more than 1e-3 between fresh and production-path scoring, with a mean |dP| of 0.002, a max of 0.17, 2 argmax
flips, and indistinguishable metrics. After temperature scaling this noise is small but real. Treat any probability
as +/- about 0.01 (occasionally 0.1), and do not set thresholds finer than that.

Prefix cache reuse (manual KV-prefix reuse, K questions sharing a context; context is the BoolQ passages concatenated):

| K questions / context | avg prompt tokens | fresh per decision | cached per decision | later-question p50 |
|---|---|---|---|---|
| 3 | 439 | 174 ms | 68 ms | 30 ms |
| 5 | 697 | 248 ms | 72 ms | 33 ms |

Max dP 0.08 to 0.11 against fresh scoring, with 1 argmax flip in 200 decisions (the noise above). Scoring several
questions from one cached prefix is the same mechanism, so it was not measured separately.

Serving: see the waitress rows above. One model instance per GPU, with a queue in front.

## Phase 3: speed changes that alter outputs (T refit each)

| config | T | acc | ECE | Brier | NLL | p50 ms | tokens |
|---|---|---|---|---|---|---|---|
| baseline | 6.75 | 0.902 | 0.032 | 0.149 | 0.299 | 45 | 101 |
| shorter instruction | 5.25 | 0.885 | 0.037 | 0.165 | 0.332 | 33 | 83 |
| Q4_K_M (Qwen official) | 5.50 | 0.898 | 0.026 | 0.149 | 0.301 | 39 | 101 |
| Q5_K_M | 6.50 | 0.905 | 0.030 | 0.145 | 0.291 | 40 | 101 |
| Q8_0 | 7.00 | 0.907 | 0.025 | 0.134 | 0.278 | 40 | 101 |
| Qwen3-4B Q4_K_M | 7.25 | 0.847 | 0.033 | 0.228 | 0.461 | 27 | 101 |

Notes: latencies other than the baseline use `logits_all=False`. Q4 to Q8 costs about half a point of accuracy and
0.02 NLL, with no measurable speed difference at this prompt length. Trimming the instruction saves 18% of tokens and
about 12 ms but loses 1.7 points of accuracy and raises the flip rate. The 4B model is 5.5 points worse, well outside the
1-point bar for the fast tier.

## Phase 4: accuracy and calibration

**Position sensitivity.** 13.4% of items change their answer under a cyclic rotation of the options (baseline),
although the model has little letter bias: mean probability by letter A/B/C/D is 0.259/0.244/0.244/0.253, and accuracy by gold
position is 0.909/0.888/0.905/0.893. The problem is order sensitivity, not a letter prior.

| debiasing | T | acc | ECE | Brier | NLL | flip rate |
|---|---|---|---|---|---|---|
| none | 6.75 | 0.902 | 0.032 | 0.149 | 0.299 | 0.134 |
| (b) contextual calibration | 6.25 | 0.880 | 0.016 | 0.181 | 0.370 | 0.229 |
| (a) average 4 rotations | 6.50 | 0.910 | 0.043 | 0.128 | 0.260 | 0 (by construction) |
| (c) both | 4.75 | 0.907 | 0.032 | 0.133 | 0.270 | 0 (by construction) |

Contextual calibration hurts here: the letter prior it removes is small, and the correction adds noise (lower
accuracy, higher flip rate). Rotation averaging is the only one that helps accuracy and proper scoring rules, at 4x
compute. (a) on Q8_0: acc 0.921, NLL 0.237.

**Label mass.** It is nearly constant (0.9998 to 1.0; no item had a non-label top token). Its AUROC for correctness is 0.71,
against 0.90 for confidence. Not useful as an escalation trigger on this prompt.

**Calibration methods** on pooled synthetic K = 2, 4, 8, 16, 26 variants (distractors from other items). Fit on
validation-derived, scored on test-derived data:

| method | pooled ECE | pooled NLL |
|---|---|---|
| scalar T from ARC (6.75) | 0.038 | 0.521 |
| scalar T, refit on pooled data (6.5) | 0.029 | 0.515 |
| separate T per K | 0.033 | 0.517 |
| isotonic on max-prob | 0.021 | 0.532 |

Isotonic gives the best ECE but the worst NLL, since it flattens the top bin. Per-K T gains nothing, because the
fitted Ts are nearly equal (6.25 to 7.0). Normalised confidence (K p - 1)/(K - 1) separates the bins well, but accuracy at
a given normalised value is still higher for small K (0.99 at K=2 vs 0.93 at K=26 in the top bin), so a single
threshold is not equivalent across K.

**Option-count scaling** (ARC test, pooled T): accuracy 0.953 / 0.898 / 0.858 / 0.823 / 0.779 and ECE
0.018 / 0.023 / 0.039 / 0.034 / 0.065 for K = 2 / 4 / 8 / 16 / 26. Calibration degrades at large K.

**Cross-domain** (held-out halves of 1,000-item subsets; unrefit uses the ARC T=6.75):

| dataset | n | acc | ECE unrefit | ECE refit (T) | NLL unrefit / refit |
|---|---|---|---|---|---|
| MMLU | 500 | 0.736 | 0.047 | 0.059 (9.0) | 0.678 / 0.695 |
| CommonsenseQA | 491 | 0.811 | 0.033 | 0.033 (6.75) | 0.554 / 0.554 |
| BoolQ (binary) | 500 | 0.866 | 0.044 | 0.024 (8.75) | 0.341 / 0.332 |

ARC's T transfers well enough (ECE under 0.05 everywhere). Refitting helps BoolQ, is neutral on CommonsenseQA and is
slightly worse on MMLU, within sampling noise at n=500. **Not done:** a labeled sample of real chunk-boundary decisions.
That remains the check that matters for the intended use, and needs data we don't have.

**Prompt format** (rot0, test; T refit per row; all rows in `RESULTS_TABLE.md`):

| format | no system prompt: acc / Brier / flip | with system prompt: acc / Brier / flip |
|---|---|---|
| `A:` | 0.902 / 0.149 / 0.134 | 0.908 / 0.140 / 0.118 |
| `A)` | 0.898 / 0.149 / 0.139 | 0.911 / 0.141 / 0.121 |
| `(A)` | 0.904 / 0.149 / 0.146 | 0.907 / 0.140 / 0.123 |
| `1.` | 0.892 / 0.153 / 0.138 | 0.907 / 0.140 / 0.129 |

Letter style makes no real difference (all within sampling noise, about +/- 1 point). A short system prompt gives a small, consistent gain
in Brier and flip rate for 17 more tokens. Differences of 0.5 to 1 point should not drive decisions at n=1,172.

**Escalation** (tier 1 plus Qwen3-8B with thinking, 1,500-token cap, forced answer on truncation):

| split | threshold | escalated | accuracy | mean latency / decision |
|---|---|---|---|---|
| validation | none | 0% | 0.886 | 0.05 s |
| validation | 0.70 | 11% | 0.910 | 1.8 s |
| validation | 0.80 | 19% | 0.926 | 2.9 s |
| validation | **0.85** | 24% | **0.930** | 3.4 s |
| validation | 0.90 / 0.95 | 33% / 53% | 0.930 / 0.926 | 4.3 / 5.8 s |
| **test (single run)** | 0.85 | 22% | **0.940** | 3.3 s |

The knee is around 0.85 on validation, and the test run confirmed it (0.902 -> 0.940). On the escalated items, the
reasoner scores 0.82 on test vs 0.65 for tier 1, and takes 15 s per item. 33% of test escalations hit the token cap and
needed a forced answer, so a larger budget could improve accuracy at higher latency. Thresholds above 0.95 were
not run. The tier-2 model here is the same 8B model, not a larger one.

**Confident-error review** (test, confidence >= 0.9, wrong: 15 items; tags are the reviewer's judgment):
5 factual recall, 5 multi-step reasoning or negation, 5 bad or debatable gold labels. None were mainly position bias
(11 of 15 gave the same wrong answer under all 4 rotations). Escalation by confidence cannot catch these by
definition. A reasoner pass could fix some of the reasoning errors, and nothing fixes the label errors.

## Not done / caveats

- No labeled real chunk-boundary data, so there is no number for the intended use case.
- Rotation tests ran only on ARC. K-scaling and cross-domain used single orderings.
- Cross-domain used 1,000-item subsets (500 evaluated after the split); MMLU is a random sample across subjects.
- Latency was measured on one GPU with other processes idle; the baseline row and the quantization rows use different code
  paths (`logits_all`), so compare p50s within a table section, not across.
- p50/p95 for non-baseline rows come from rot0 of the rotation runs on test.
- ECE uses 10 equal-width bins; differences of about 0.01 are within noise at these sample sizes.

## Thinking-reasoner budget test (`/api/decide`, 2026-10-12)

Test: the 331 ARC test items the baseline (Q4, T=6.75) scored below 0.9 confidence on (tier-1 accuracy 0.698 there). Each item was
sent through `/api/decide` with escalation forced, thinking budget 6,000 tokens (150 s cap), option order shuffled for the reasoner.
Run via `scripts/eval_decide.py`; rows in `results/decide_test.jsonl`.

| | accuracy |
|---|---|
| baseline Q4 | 0.698 |
| production tier 1 (Q8, T=7.5) | 0.752 |
| reasoner (6,000 tokens) | 0.846 |

Fixed 52, broke 21, 30 still wrong. Gains in every confidence band (below 0.5: 0.684 to 0.789, 0.5 to 0.7: 0.636 to 0.798,
0.7 to 0.9: 0.825 to 0.881). Forced answers 0% (33% at the 1,500-token budget); no reasoner errors. Thinking tokens mean 943, p95 2,626,
max 5,093; per-item latency mean 20.9 s, median 14.6 s, p95 59.5 s, max 122 s.

Caveats: the same 8B model is both tiers; one run at temperature 0.6 with a fixed seed (no repeat-run variance measured); the
whole-test figure of about 0.944 is an estimate that keeps baseline answers above 0.9 confidence, not a measured cascade run; the 0.752
tier-1 figure uses the production Q8 path, so it differs from the 0.698 baseline for two reasons (model and path), not one.
