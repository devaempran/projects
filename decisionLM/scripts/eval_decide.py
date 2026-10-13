"""
Test /api/decide's reasoner on the ARC test items the ORIGINAL (baseline Q4, T=6.75) model was
below 0.9 confidence on. theta=2 forces escalation so every item reaches the reasoner; the
response's tier1_answer gives the production tier-1 prediction for comparison.
Resumable: appends to bench/results/decide_test.jsonl.   usage: python eval_decide.py [--url ...] [--max-think 6000]
"""
import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bench"))
from common import load, load_arc, softmax

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:5001")
ap.add_argument("--max-think", type=int, default=6000)
ap.add_argument("--out", default=os.path.join(ROOT, "bench", "results", "decide_test.jsonl"))
args = ap.parse_args()

base = load("p1_test.json")
low = [r["id"] for r in base if softmax(r["lp"], 6.75).max() < 0.9]
conf0 = {r["id"]: float(softmax(r["lp"], 6.75).max()) for r in base}
items = {it["id"]: it for it in load_arc("test")}
done = set(json.loads(line)["id"] for line in open(args.out)) if os.path.exists(args.out) else set()
print(len(low), "items below 0.9;", len(done), "done", flush=True)
for n, iid in enumerate(low, 1):
    if iid in done:
        continue
    it = items[iid]
    body = json.dumps({"Context": "", "Question": it["Question"], "Options": it["Options"],
                       "theta": 2, "max_think_tokens": args.max_think}).encode()
    req = urllib.request.Request(args.url + "/api/decide", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    opts = it["Options"]
    row = {"id": iid, "gold": it["gold"], "base_conf": conf0[iid], "k": len(opts),
           "pred": opts.index(d["answer"]), "tier1": opts.index(d["tier1_answer"]),
           "source": d["source"], "forced": d.get("forced"), "tokens": d.get("reasoner_tokens"),
           "t": d["latency_s"], "err": d.get("reasoner_error")}
    with open(args.out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(n, iid, row["pred"] == row["gold"], row["tokens"], f"{row['t']:.1f}s", "forced" if row["forced"] else "", flush=True)
