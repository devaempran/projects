import os, sys, time; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np
val, test = load_arc("validation"), load_arc("test")
sc = Scorer(logits_all=True)
vrows = run_items(sc, val, tag="val")
T, Tnll = fit_T([r["lp"] for r in vrows], [r["gold"] for r in vrows])
print("T", T, "T_nll", Tnll)
trows = run_items(sc, test, tag="test-direct")
# production path (create_completion w/ logprobs) over all test items, 10 warmups discarded
for it in test[:10]:
    sc.score_production(build_prompt(it), list(LABELS[:len(it["Options"])]))
prod = []
for j, it in enumerate(test):
    prod.append(sc.score_production(build_prompt(it), list(LABELS[:len(it["Options"])])))
    if (j+1) % 300 == 0: print("  prod", j+1, flush=True)
save("p1_val.json", vrows); save("p1_test.json", trows)
save("p1_prod.json", [{"t_total": p["t_total"], "lp": p["lp"], "prompt_tokens": p["prompt_tokens"]} for p in prod])
s = summarize(trows, T)
print("TEST direct", s)
for key in ("t_tok", "t_prefill", "t_extract", "t_total"):
    print(key, lat_stats([r[key] for r in trows]))
print("prod", lat_stats([p["t_total"] for p in prod]))
pt = np.array([r["prompt_tokens"] for r in trows]); tp = np.array([r["t_prefill"] for r in trows])
print("prompt tokens mean/p50/p95/max", pt.mean(), np.percentile(pt, 50), np.percentile(pt, 95), pt.max())
print("corr(prefill, tokens)", np.corrcoef(pt, tp)[0, 1], "slope ms/token", np.polyfit(pt, tp*1000, 1))
print("max |lp diff| direct vs prod", max(abs(a-b) for r, p in zip(trows, prod) for a, b in zip(r["lp"], p["lp"])))
save("p1_summary.json", {"T": T, "test": s})
