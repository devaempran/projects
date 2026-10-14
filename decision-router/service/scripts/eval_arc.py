"""
Evaluate decisionLM on ARC-Challenge (allenai/ai2_arc).

Pulls the split from Hugging Face, converts each row into the app's input
format {Context, Question, Options}, scores it via the running server's
/api/score/detailed endpoint, and reports accuracy and calibration.

Usage:
    python eval_arc.py [--split test] [-n 200] [--url http://127.0.0.1:5000]
"""
import argparse
import json
import urllib.request

import pandas as pd

SPLITS = {
    "train": "ARC-Challenge/train-00000-of-00001.parquet",
    "test": "ARC-Challenge/test-00000-of-00001.parquet",
    "validation": "ARC-Challenge/validation-00000-of-00001.parquet",
}


def load_arc(split):
    df = pd.read_parquet("hf://datasets/allenai/ai2_arc/" + SPLITS[split])
    items = []
    for _, row in df.iterrows():
        texts = list(row["choices"]["text"])
        labels = list(row["choices"]["label"])
        if row["answerKey"] not in labels:
            continue
        gold = texts[labels.index(row["answerKey"])]
        if len(set(texts)) != len(texts):
            continue  # the API requires unique option strings
        items.append({
            "Context": "",
            "Question": row["question"],
            "Options": texts,
            "gold": gold,
        })
    return items


def score(url, item):
    body = json.dumps({k: item[k] for k in ("Context", "Question", "Options")}).encode()
    req = urllib.request.Request(
        url + "/api/score/detailed", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def ece(confs, correct, bins=10):
    n, total = len(confs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(confs) if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if not idx:
            continue
        acc = sum(correct[i] for i in idx) / len(idx)
        conf = sum(confs[i] for i in idx) / len(idx)
        print(f"  conf {lo:.1f}-{hi:.1f}: n={len(idx):4d}  avg_conf={conf:.3f}  accuracy={acc:.3f}")
        total += len(idx) / n * abs(acc - conf)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=SPLITS)
    ap.add_argument("-n", type=int, default=200, help="number of questions (0 = all)")
    ap.add_argument("--url", default="http://127.0.0.1:5000")
    args = ap.parse_args()

    items = load_arc(args.split)
    if args.n:
        items = items[: args.n]
    print(f"Scoring {len(items)} ARC-Challenge '{args.split}' questions...")

    confs, correct, masses, errors = [], [], [], 0
    for i, item in enumerate(items, 1):
        try:
            res = score(args.url, item)
        except Exception as e:
            errors += 1
            print(f"  [{i}] error: {e}")
            continue
        best = max(res["choices"], key=lambda c: c["probability"])
        confs.append(best["probability"])
        correct.append(int(best["text"] == item["gold"]))
        masses.append(res["total_label_probability_mass"])
        if i % 25 == 0:
            print(f"  {i}/{len(items)}  running accuracy={sum(correct)/len(correct):.3f}")

    n = len(correct)
    if not n:
        print("No successful scores.")
        return
    print(f"\nScored: {n}  errors: {errors}")
    print(f"Accuracy:            {sum(correct)/n:.3f}")
    print(f"Mean confidence:     {sum(confs)/n:.3f}")
    print(f"Confidence - acc:    {sum(confs)/n - sum(correct)/n:+.3f}  (>0 = overconfident)")
    print(f"Mean label mass:     {sum(masses)/n:.3f}  (min {min(masses):.3f})")
    print("Calibration:")
    print(f"ECE: {ece(confs, correct):.3f}")


if __name__ == "__main__":
    main()
