import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np, time
T = load("p1_summary.json")["T"]
bq = load_boolq(n=600, seed=1)
sc = Scorer(logits_all=False)
labs = ["A", "B"]
out = {}
for K in (3, 5):
    groups = [bq[i:i + K] for i in range(0, len(bq) - K + 1, K)][:40]
    # build one shared context per group (concatenated passages) with K questions
    fresh_t, cache_t, first_t, later_t, dps, flips, toks, reused = [], [], [], [], [], 0, [], []
    for g in groups:
        ctx = "\n\n".join(x["Context"].strip() for x in g)
        qs = [dict(x, Context=ctx) for x in g]
        prompts = [build_prompt(q) for q in qs]
        for p in prompts[:1]: sc.score_prompt(p, labs)  # warm kernels for shape
        fr = [sc.score_prompt(p, labs) for p in prompts]                       # K independent fresh calls
        sc.llm.reset()
        ca = [sc.score_prompt(p, labs, reuse_prefix=True) for p in prompts]    # first = miss, rest reuse prefix
        fresh_t.append(sum(r["t_total"] for r in fr)); cache_t.append(sum(r["t_total"] for r in ca))
        first_t.append(fr[0]["t_total"]); later_t += [r["t_total"] for r in ca[1:]]
        toks.append(np.mean([r["prompt_tokens"] for r in fr])); reused += [r["reused"] for r in ca[1:]]
        for a, b in zip(fr, ca):
            pa, pb = softmax(a["lp"], T), softmax(b["lp"], T)
            dps.append(np.abs(pa - pb).max()); flips += pa.argmax() != pb.argmax()
    fl = [r for r in later_t]
    # fresh latency of the same later questions for a like-for-like per-question comparison
    out[K] = dict(groups=len(groups), prompt_tokens_mean=float(np.mean(toks)), reused_tokens_mean=float(np.mean(reused)),
                  fresh_per_decision_ms=1000 * float(np.sum(fresh_t) / (K * len(groups))),
                  cached_per_decision_ms=1000 * float(np.sum(cache_t) / (K * len(groups))),
                  fresh_per_decision_p50_ms=None, later_q_cached_p50_ms=1000 * float(np.median(later_t)),
                  first_q_p50_ms=1000 * float(np.median(first_t)) / 1.0 if False else None,
                  max_dp=float(np.max(dps)), mean_dp=float(np.mean(dps)), argmax_flips=int(flips))
    print(K, out[K], flush=True)
save("p2b.json", out)
