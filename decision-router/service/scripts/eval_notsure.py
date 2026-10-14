"""
A/B test: does appending a "Not sure" option improve confidence quality on ARC?

Scores validation + test with and without a trailing "I'm not sure" option via
the running server, caches raw logprobs to notsure_results.json, then compares:
  baseline : softmax over the real options only
  ns_keep  : softmax over real options + Not sure; confidence = top REAL option's
             probability (mass lost to Not sure lowers confidence)
  ns_renorm: Not sure dropped, softmax renormalized over the real options
For each variant T is fit on validation (min binary log-loss of correctness vs.
confidence, floored so mean confidence <= accuracy), then evaluated on test.

Usage: python eval_notsure.py [-n 0] [--url ...] [--refresh]
"""
import argparse
import json
import os

import numpy as np
from eval_arc import load_arc, score

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "bench", "results", "notsure_results.json")
NS = "I'm not sure"


def collect(url, split, n):
    items = load_arc(split)
    items = items[:n] if n else items
    out = []
    for i, it in enumerate(items, 1):
        row = {"gold": it["Options"].index(it["gold"])}
        for name, opts in (("base", it["Options"]), ("ns", it["Options"] + [NS])):
            res = score(url, dict(it, Options=opts))
            row[name] = [c["logprob"] for c in res["choices"]]
        out.append(row)
        if i % 100 == 0:
            print(f"  {split} {i}/{len(items)}")
    return out


def softmax(lps, T):
    a = np.array(lps) / T
    a = np.exp(a - a.max())
    return a / a.sum()


def variant(row, name, T):
    """Return (pred_index, confidence, p_notsure)."""
    if name == "base":
        p = softmax(row["base"], T)
        return int(p.argmax()), float(p.max()), 0.0
    p = softmax(row["ns"], T)
    real = p[:-1]
    if name == "ns_keep":
        k = int(real.argmax())
        return k, float(real[k]), float(p[-1])
    q = softmax(row["ns"][:-1], T)
    return int(q.argmax()), float(q.max()), float(p[-1])


def evaluate(rows, name, T):
    pred, conf, pns = zip(*(variant(r, name, T) for r in rows), strict=True)
    corr = np.array([p == r["gold"] for p, r in zip(pred, rows, strict=True)], float)
    return corr, np.array(conf), np.array(pns)


def auroc(conf, corr):
    pos, neg = conf[corr == 1], conf[corr == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    allv = np.concatenate([pos, neg])
    ranks = allv.argsort().argsort() + 1.0
    # average ties
    for v in np.unique(allv):
        m = allv == v
        ranks[m] = ranks[m].mean()
    return (ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def ece(conf, corr, bins=10):
    tot = 0.0
    for b in range(bins):
        m = (conf >= b / bins) & ((conf < (b + 1) / bins) | (b == bins - 1))
        if m.any():
            tot += m.mean() * abs(corr[m].mean() - conf[m].mean())
    return tot


def fit_T(rows, name):
    best, best_t = None, None
    grid = [1 + 0.25 * k for k in range(157)]
    for T in grid:
        corr, conf, _ = evaluate(rows, name, T)
        c = np.clip(conf, 1e-6, 1 - 1e-6)
        nll = -np.mean(corr * np.log(c) + (1 - corr) * np.log(1 - c))
        if best is None or nll < best:
            best, best_t = nll, T
    for T in grid:
        if T < best_t:
            continue
        corr, conf, _ = evaluate(rows, name, T)
        if conf.mean() <= corr.mean():
            return T
    return grid[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=0)
    ap.add_argument("--url", default="http://127.0.0.1:5000")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    if os.path.exists(CACHE) and not args.refresh:
        data = json.load(open(CACHE))
    else:
        data = {s: collect(args.url, s, args.n) for s in ("validation", "test")}
        json.dump(data, open(CACHE, "w"))
    val, test = data["validation"], data["test"]

    # Not-sure usage, independent of T (T=1 raw view and fitted view below)
    corr_b, _, _ = evaluate(test, "base", 1.0)
    print(f"Test n={len(test)}\n")
    for name in ("base", "ns_keep", "ns_renorm"):
        T = fit_T(val, name)
        corr, conf, pns = evaluate(test, name, T)
        print(f"== {name}  (T={T})")
        print(f"  accuracy {corr.mean():.3f}  mean_conf {conf.mean():.3f}  "
              f"gap {conf.mean()-corr.mean():+.3f}  ECE {ece(conf, corr):.3f}  "
              f"AUROC {auroc(conf, corr):.3f}")
        if name != "base":
            w, r = corr == 0, corr == 1
            print(f"  P(Not sure): mean {pns.mean():.4f}  wrong {pns[w].mean():.4f}  "
                  f"right {pns[r].mean():.4f}  NS is argmax on "
                  f"{sum(int(np.argmax(softmax(x['ns'], T)) == len(x['ns'])-1) for x in test)} items")
        print("  threshold  coverage  accuracy_of_passed  accuracy_of_escalated")
        for th in (0.5, 0.7, 0.8, 0.9, 0.95):
            m = conf >= th
            esc = corr[~m].mean() if (~m).any() else float("nan")
            acc = corr[m].mean() if m.any() else float("nan")
            print(f"  {th:9.2f}  {m.mean():8.3f}  {acc:18.3f}  {esc:21.3f}")
        print()


if __name__ == "__main__":
    main()
