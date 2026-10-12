"""
Fit the softmax temperature T on the ARC-Challenge validation split and write
config/calibration.json (read by decisionlm.settings at startup).

Raw label logprobs don't depend on T, so this works against a server running
with any current T. Selection rule: take the T that minimizes log-loss, but
never go below the smallest T at which mean confidence <= accuracy -- we'd
rather be underconfident than overconfident.

Usage: python fit_temperature.py [--url http://127.0.0.1:5000] [-n 0]
"""
import argparse
import json
import math
import os

from eval_arc import load_arc, score

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "calibration.json")


def probs(lps, T):
    m = max(lps)
    e = [math.exp((lp - m) / T) for lp in lps]
    z = sum(e)
    return [x / z for x in e]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5000")
    ap.add_argument("-n", type=int, default=0)
    args = ap.parse_args()

    items = load_arc("validation")
    if args.n:
        items = items[: args.n]
    print(f"Collecting logprobs for {len(items)} validation questions...")
    data = []  # (logprobs, gold_index)
    for i, item in enumerate(items, 1):
        res = score(args.url, item)
        lps = [c["logprob"] for c in res["choices"]]
        data.append((lps, item["Options"].index(item["gold"])))
        if i % 50 == 0:
            print(f"  {i}/{len(items)}")

    def stats(T):
        nll = conf = acc = 0.0
        for lps, g in data:
            p = probs(lps, T)
            nll -= math.log(max(p[g], 1e-12))
            conf += max(p)
            acc += p.index(max(p)) == g
        n = len(data)
        return nll / n, conf / n, acc / n

    grid = [1 + 0.25 * k for k in range(0, 157)]  # 1.0 .. 40.0
    rows = [(T,) + stats(T) for T in grid]
    t_nll = min(rows, key=lambda r: r[1])[0]
    t_under = next((r[0] for r in rows if r[2] <= r[3]), grid[-1])
    T = max(t_nll, t_under)
    print(f"\nT minimizing log-loss: {t_nll}")
    print(f"Smallest T with mean confidence <= accuracy: {t_under}")
    print(f"Chosen T: {T}")
    for T_, nll, conf, acc in rows:
        if T_ in (1.0, t_nll, t_under, T):
            print(f"  T={T_:5.2f}  logloss={nll:.4f}  mean_conf={conf:.3f}  acc={acc:.3f}")

    with open(OUT, "w") as f:
        json.dump({"temperature": T, "fit_on": "ARC-Challenge validation", "n": len(data)}, f, indent=2)
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
