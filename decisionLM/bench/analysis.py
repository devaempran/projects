"""Analysis over cfg_*.json files: rotation/flip/position bias, debiasing, metrics. Importable."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np
from collections import defaultdict


def by_item(rows):
    d = defaultdict(dict)
    for r in rows:
        d[r["id"]][r["rot"]] = r
    return d


def disp_lp(r):
    """label logprobs in DISPLAY order (position 0 = letter A)."""
    return [r["lp"][oi] for oi in r["order"]]


def to_orig(vec_disp, order):
    out = [0.0] * len(order)
    for pos, oi in enumerate(order):
        out[oi] = vec_disp[pos]
    return np.array(out)


def cf_adjust(r, cf):
    """contextual calibration: divide the content-free letter prior out (display order), return orig-order lp."""
    prior = np.array(cf[str(r["k"])])
    lp = np.array(disp_lp(r)) - prior
    return to_orig(lp, r["order"])


def probs_for(rows, T, mode="raw", cf=None):
    """Per-row probabilities (orig order). mode raw|cf."""
    out = []
    for r in rows:
        lp = np.array(r["lp"]) if mode == "raw" else cf_adjust(r, cf)
        out.append(softmax(lp, T))
    return out


def ens_probs(items_rot, T, mode="raw", cf=None, how="prob"):
    """Average over rotations. how=prob (mean of probs) | logit (mean of logprobs, then one softmax)."""
    out = {}
    for iid, rots in items_rot.items():
        rs = list(rots.values())
        if how == "prob":
            out[iid] = np.mean(probs_for(rs, T, mode, cf), axis=0)
        else:
            lps = [np.array(r["lp"]) if mode == "raw" else cf_adjust(r, cf) for r in rs]
            out[iid] = softmax(np.mean(lps, axis=0), T)
    return out


def fit_generic(fn, golds, grid=None):
    """fn(T) -> list of prob vectors. Same selection rule as production (NLL-min, floored at mean_conf<=acc)."""
    grid = grid or [1 + 0.25 * k for k in range(157)]
    best = under = None
    for T in grid:
        P = fn(T)
        nll = -np.mean([math.log(max(p[g], 1e-12)) for p, g in zip(P, golds)])
        conf = np.mean([p.max() for p in P]); acc = np.mean([p.argmax() == g for p, g in zip(P, golds)])
        if best is None or nll < best[1]: best = (T, nll)
        if under is None and conf <= acc: under = T
    return max(best[0], under if under is not None else grid[-1])


def flip_stats(items_rot, T=1.0, mode="raw", cf=None):
    anyflip = pair = n = 0
    for iid, rots in items_rot.items():
        if len(rots) < 2: continue
        preds = [int(np.argmax(probs_for([r], T, mode, cf)[0])) for r in rots.values()]
        n += 1
        anyflip += len(set(preds)) > 1
        pair += sum(p != preds[0] for p in preds[1:]) / (len(preds) - 1)
    return {"flip_any": anyflip / n, "flip_vs_rot0": pair / n, "n": n}


def position_stats(items_rot, T, mode="raw", cf=None):
    """accuracy by gold display position; mean prob by display letter (rot-all)."""
    acc_pos = defaultdict(list); mean_letter = defaultdict(list); max_letter_pred = defaultdict(int)
    for iid, rots in items_rot.items():
        for r in rots.values():
            p = probs_for([r], T, mode, cf)[0]
            gold_pos = r["order"].index(r["gold"])
            acc_pos[gold_pos].append(int(np.argmax(p) == r["gold"]))
            for pos, oi in enumerate(r["order"]):
                if r["k"] == 4:
                    mean_letter[pos].append(p[oi])
            if r["k"] == 4:
                max_letter_pred[r["order"].index(int(np.argmax(p)))] += 1
    tot = sum(max_letter_pred.values())
    return {"acc_by_gold_pos(4-opt)": {int(k): round(float(np.mean(v)), 4) for k, v in sorted(acc_pos.items()) if k < 4},
            "mean_prob_by_letter(4-opt)": {"ABCD"[k]: round(float(np.mean(v)), 4) for k, v in sorted(mean_letter.items())},
            "pred_share_by_letter(4-opt)": {"ABCD"[k]: round(v / tot, 4) for k, v in sorted(max_letter_pred.items())}}


def eval_variant(val_it, test_it, cf_val, cf_test, variant):
    """variant in: rot0 | rot0_cf | ens | ens_cf. T refit on val. Returns metrics on test (and val)."""
    mode = "cf" if variant.endswith("cf") else "raw"
    ens = variant.startswith("ens")
    def P_of(items_rot, T, cf):
        ids = list(items_rot)
        if ens:
            e = ens_probs(items_rot, T, mode, cf)
            return ids, [e[i] for i in ids]
        P = [probs_for([items_rot[i][0]], T, mode, cf)[0] for i in ids]
        return ids, P
    golds_v = [next(iter(val_it[i].values()))["gold"] for i in val_it]
    T = fit_generic(lambda t: P_of(val_it, t, cf_val)[1], golds_v)
    res = {"T": T}
    for nm, it, cf in (("val", val_it, cf_val), ("test", test_it, cf_test)):
        ids, P = P_of(it, T, cf)
        g = [next(iter(it[i].values()))["gold"] for i in ids]
        m = metrics_from_probs(P, g, [it[i][0]["mass"] for i in ids])
        res[nm] = m
        res[nm + "_flip"] = flip_stats(it, T, mode, cf) if not ens else {"flip_any": 0.0, "note": "invariant by construction (k<=4)"}
    return res


def load_cfg(name):
    v = load(f"cfg_{name}_validation.json")
    try:
        t = load(f"cfg_{name}_test.json")
    except FileNotFoundError:
        t = None
    return v, t
