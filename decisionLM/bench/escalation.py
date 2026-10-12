import os, sys, json; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np
split = sys.argv[1] if len(sys.argv) > 1 else "validation"
base = load("p1_val.json" if split == "validation" else "p1_test.json")
th = {}
for l in open(os.path.join(RES, f"think_{split}.jsonl")):
    d = json.loads(l); th[d["id"]] = d
conf = np.array([softmax(r["lp"], 6.75).max() for r in base]); ok1 = np.array([int(np.argmax(r["lp"]) == r["gold"]) for r in base])
ids = [r["id"] for r in base]; t1 = np.array([r["t_total"] for r in base])
ok2 = np.array([th[i]["pred"] == th[i]["gold"] if i in th else np.nan for i in ids]); t2 = np.array([th[i]["t"] if i in th else np.nan for i in ids])
inth = ~np.isnan(ok2)
print(split, "reasoner on escalated set: acc", np.nanmean(ok2), "vs tier-1 on same items", ok1[inth].mean(), "mean s", np.nanmean(t2), "forced", np.mean([d["forced"] for d in th.values()]))
rows = []
for theta in [0, .5, .6, .7, .8, .85, .9, .93, .95]:
    esc = conf < theta
    if (esc & ~inth).any(): continue
    acc = np.where(esc, np.nan_to_num(ok2), ok1).mean(); lat = (t1 + np.where(esc, np.nan_to_num(t2), 0)).mean()
    rows.append(dict(theta=theta, esc_frac=float(esc.mean()), acc=float(acc), mean_lat_s=float(lat)))
    print(f"theta={theta:.2f} escalated={esc.mean():.3f} acc={acc:.4f} mean_latency={lat:.2f}s")
save(f"escalation_{split}.json", rows)
