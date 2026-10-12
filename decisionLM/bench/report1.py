import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis import *
import json
names = ["fmt_colon_nosys","fmt_paren_nosys","fmt_bparen_nosys","fmt_num_nosys","fmt_colon_sys","fmt_paren_sys","fmt_bparen_sys","fmt_num_sys",
         "short_instr","q4km_official","q5km","q8_0","qwen3_4b_q4km","flash_attn"]
out = {}
for n in names:
    v, t = load_cfg(n)
    vi, ti = by_item(v["rows"]), by_item(t["rows"])
    res = {}
    for var in ("rot0", "rot0_cf", "ens", "ens_cf"):
        res[var] = eval_variant(vi, ti, v["cf"], t["cf"], var)
    T0 = res["rot0"]["T"]
    res["pos_val"] = position_stats(vi, T0)
    res["pos_test"] = position_stats(ti, T0)
    r0 = [x for x in t["rows"] if x["rot"] == 0]
    res["lat"] = lat_stats([x["t_total"] for x in r0]); res["tok"] = float(np.mean([x["prompt_tokens"] for x in r0]))
    out[n] = res
    a = res["rot0"]; print(f"{n:18s} T={a['T']:5.2f} VAL acc={a['val']['acc']:.3f} flip={a['val_flip']['flip_any']:.3f} | TEST acc={a['test']['acc']:.3f} ece={a['test']['ece']:.3f} brier={a['test']['brier']:.3f} nll={a['test']['nll']:.3f} flip={a['test_flip']['flip_any']:.3f} p50={res['lat']['p50_ms']:.0f} p95={res['lat']['p95_ms']:.0f} tok={res['tok']:.0f}")
save("report1.json", out)
