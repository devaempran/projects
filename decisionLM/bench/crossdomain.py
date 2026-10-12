import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np
sc = Scorer(logits_all=False)
out = {}
for name, loader in (("mmlu", load_mmlu), ("csqa", load_csqa), ("boolq", load_boolq)):
    items = loader(n=1000)
    keep = [it for it in items if len(sc.tokenize(build_prompt(it))) < 3800]
    rows = run_items(sc, keep, tag=name)
    save(f"xd_{name}.json", rows)
    print(name, len(keep), "acc", np.mean([int(np.argmax(r["lp"]) == r["gold"]) for r in rows]), flush=True)
