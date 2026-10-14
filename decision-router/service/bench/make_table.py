import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
r1 = load("report1.json"); p1 = load("p1_summary.json")["test"]; t = load("p1_test.json")
from analysis import by_item, flip_stats, load_cfg
v, tt = load_cfg("fmt_colon_nosys"); fl = flip_stats(by_item(tt["rows"]), 6.75)["flip_any"]
L = ["| config | T | acc | ECE | Brier | NLL | flip rate | p50 ms | p95 ms | prompt tok |", "|---|---|---|---|---|---|---|---|---|---|",
     f"| **baseline** Q4_K_M, A:, no sys (logits_all=True) | 6.75 | {p1['acc']:.3f} | {p1['ece']:.3f} | {p1['brier']:.3f} | {p1['nll']:.3f} | {fl:.3f} | {p1['p50_ms']:.0f} | {p1['p95_ms']:.0f} | {p1['prompt_tokens']:.0f} |"]
for n, res in r1.items():
    a = res["rot0"]; m = a["test"]
    L.append(f"| {n} | {a['T']:.2f} | {m['acc']:.3f} | {m['ece']:.3f} | {m['brier']:.3f} | {m['nll']:.3f} | {a['test_flip']['flip_any']:.3f} | {res['lat']['p50_ms']:.0f} | {res['lat']['p95_ms']:.0f} | {res['tok']:.0f} |")
for n in ("fmt_colon_nosys", "fmt_colon_sys", "q8_0"):
    for var, lab in (("rot0_cf", "contextual cal."), ("ens", "4-rotation avg"), ("ens_cf", "rot-avg + ctx cal.")):
        a = r1[n][var]; m = a["test"]
        L.append(f"| {n} + {lab} | {a['T']:.2f} | {m['acc']:.3f} | {m['ece']:.3f} | {m['brier']:.3f} | {m['nll']:.3f} | {a['test_flip']['flip_any']:.3f} | {'4x' if 'ens' in var else '+0'} | | |")
open(os.path.join(ROOT, "bench", "RESULTS_TABLE.md"), "w").write("\n".join(L) + "\n"); print("\n".join(L))
