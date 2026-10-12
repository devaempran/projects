import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np
val = load_arc("validation")
T = load("p1_summary.json")["T"]
ref = load("p1_val.json")
Pref = [softmax(r["lp"], T) for r in ref]
configs = [
 ("base logits_all=True (rep1)", dict(logits_all=True)),
 ("base logits_all=True (rep2)", dict(logits_all=True)),
 ("logits_all=False", dict(logits_all=False)),
 ("logits_all=False + flash_attn", dict(logits_all=False, flash_attn=True)),
]
for nb in (512, 1024, 2048):
    configs.append((f"logits_all=False n_batch=n_ubatch={nb}", dict(logits_all=False, n_batch=nb, n_ubatch=nb)))
configs.append(("logits_all=False + flash + 1024/1024", dict(logits_all=False, flash_attn=True, n_batch=1024, n_ubatch=1024)))
out = {}
for name, kw in configs:
    sc = Scorer(**kw)
    rows = run_items(sc, val, tag=name)
    P = [softmax(r["lp"], T) for r in rows]
    d = np.array([np.abs(a - b).max() for a, b in zip(P, Pref)])
    dlp = np.array([np.abs(np.array(r["lp"]) - np.array(q["lp"])).max() for r, q in zip(rows, ref)])
    s = summarize(rows, T)
    s["vs_base"] = dict(max_dp=float(d.max()), mean_dp=float(d.mean()), n_gt_1e4=int((d > 1e-4).sum()), n_gt_1e3=int((d > 1e-3).sum()),
                        argmax_flips=int(sum(a.argmax() != b.argmax() for a, b in zip(P, Pref))), max_dlogprob=float(dlp.max()))
    s["t_prefill_p50_ms"] = lat_stats([r["t_prefill"] for r in rows])["p50_ms"]
    out[name] = s
    print(name, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}, flush=True)
    del sc
# production path for reference (create_completion + top-N logprobs)
sc = Scorer(logits_all=True)
for it in val[:10]: sc.score_production(build_prompt(it), list(LABELS[:len(it["Options"])]))
pr = [sc.score_production(build_prompt(it), list(LABELS[:len(it["Options"])])) for it in val]
out["production create_completion"] = lat_stats([p["t_total"] for p in pr])
print(out["production create_completion"])
save("p2a.json", out)
