"""Synthetic K-option variants of ARC (val+test): gold + own distractors, topped up with distractors from other items."""
import os, sys, random; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np
sc = Scorer(logits_all=False)
for split in ("validation", "test"):
    items = load_arc(split)
    pool = sorted({o for it in items for o in it["Options"]})
    for K in (2, 4, 8, 16, 26):
        rng = random.Random(1000 + K)
        syn = []
        for it in items:
            gold = it["Options"][it["gold"]]
            own = [o for i, o in enumerate(it["Options"]) if i != it["gold"]]
            rng.shuffle(own)
            d = own[:K - 1]
            used = set(d) | {gold} | set(it["Options"])
            while len(d) < K - 1:
                c = rng.choice(pool)
                if c not in used:
                    d.append(c); used.add(c)
            opts = d + [gold]; rng.shuffle(opts)
            syn.append({"id": it["id"] + f"-K{K}", "Context": "", "Question": it["Question"], "Options": opts, "gold": opts.index(gold)})
        rows = run_items(sc, syn, tag=f"K{K}/{split}", warm=5)
        for r in rows: r["K"] = K
        save(f"k_{K}_{split}.json", rows)
        print(split, K, "acc", np.mean([int(np.argmax(r["lp"]) == r["gold"]) for r in rows]), "tok", np.mean([r["prompt_tokens"] for r in rows]), flush=True)
