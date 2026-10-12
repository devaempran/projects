"""Run one configuration: ARC val (+test) under cyclic rotations, plus content-free priors.
usage: evalcfg.py NAME [--model PATH] [--fmt colon|paren|bparen|num] [--system] [--instr full|short] [--flash] [--splits validation,test]
Saves bench/results/cfg_NAME_<split>.json = {"rows":[...], "cf":{k: [lp...]}}"""
import os, sys, argparse; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
from common import _labels
ap = argparse.ArgumentParser()
ap.add_argument("name"); ap.add_argument("--model", default=PROD_MODEL)
ap.add_argument("--fmt", default="colon"); ap.add_argument("--system", action="store_true")
ap.add_argument("--instr", default="full"); ap.add_argument("--flash", action="store_true")
ap.add_argument("--splits", default="validation,test"); ap.add_argument("--no-rot", action="store_true")
a = ap.parse_args()
sc = Scorer(path=a.model, logits_all=False, flash_attn=a.flash)
CF_Q = ["N/A", "[MASK]", ""]
def content_free(k):
    lps = []
    labs = _labels(a.fmt, k)
    for q in CF_Q:
        it = {"Context": "", "Question": q, "Options": ["N/A"] * k}
        # empty question: build_prompt strips -> "Question: "
        r = sc.score_prompt(build_prompt(it, None, a.fmt, a.system, a.instr), labs)
        p = softmax(r["lp"], 1.0)
        lps.append(np.log(p))
    return np.mean(lps, axis=0).tolist()
for split in a.splits.split(","):
    items = load_arc(split)
    rows = []
    R = 1 if a.no_rot else 4
    for r in range(R):
        sub = [it for it in items if len(it["Options"]) > r]
        orders = [[(i + r) % len(it["Options"]) for i in range(len(it["Options"]))] for it in sub]
        rr = run_items(sc, sub, orders=orders, fmt=a.fmt, system=a.system, instr=a.instr, warm=10 if r == 0 else 2,
                       tag=f"{a.name}/{split}/rot{r}")
        for x in rr: x["rot"] = r
        rows += rr
    cf = {str(k): content_free(k) for k in sorted({len(it["Options"]) for it in items})}
    save(f"cfg_{a.name}_{split}.json", {"rows": rows, "cf": cf, "args": vars(a)})
    r0 = [x for x in rows if x["rot"] == 0]
    print(a.name, split, "rot0 acc", np.mean([int(np.argmax(x["lp"]) == x["gold"]) for x in r0]),
          "p50ms", lat_stats([x["t_total"] for x in r0])["p50_ms"], "tok", np.mean([x["prompt_tokens"] for x in r0]), flush=True)
