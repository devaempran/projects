import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis import *
import json
out = {}
T0 = 6.75
# ---- label mass buckets (ARC test rot0, baseline prod rows from phase 1)
t = load("p1_test.json")
mass = np.array([r["mass"] for r in t]); corr = np.array([int(np.argmax(r["lp"]) == r["gold"]) for r in t])
conf = np.array([softmax(r["lp"], T0).max() for r in t]); top_ok = np.array([r["top_is_label"] for r in t])
print("mass min/p1/p5/median", mass.min(), np.percentile(mass, [1, 5, 50]), "top-token-not-label:", int((~top_ok).sum()))
qs = np.quantile(mass, [0, .25, .5, .75, 1.0]); b = []
for lo, hi in zip(qs[:-1], qs[1:]):
    m = (mass >= lo) & (mass <= hi); b.append(dict(lo=float(lo), hi=float(hi), n=int(m.sum()), acc=float(corr[m].mean())))
out["mass_buckets"] = b; print(b)
lowm = mass < 0.999
print("mass<0.999: n", int(lowm.sum()), "acc", corr[lowm].mean() if lowm.any() else None, "vs rest", corr[~lowm].mean())
from math import isnan
def auroc(score, y):
    o = np.argsort(score); r = np.empty(len(o)); r[o] = np.arange(1, len(o) + 1)
    n1 = y.sum(); n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
out["auroc_conf"] = float(auroc(conf, corr)); out["auroc_mass"] = float(auroc(mass, corr)); print("AUROC conf", out["auroc_conf"], "mass", out["auroc_mass"])
# ---- K-scaling + calibration methods
def kdata(split):
    d = {}
    for K in (2, 4, 8, 16, 26): d[K] = load(f"k_{K}_{split}.json")
    return d
kv, kt = kdata("validation"), kdata("test")
def pav(x, y):
    o = np.argsort(x); xs, ys = np.asarray(x)[o], np.asarray(y, float)[o]
    blocks = [[ys[i], 1.0, xs[i], xs[i]] for i in range(len(ys))]
    st = []
    for b_ in blocks:
        st.append(b_)
        while len(st) > 1 and st[-2][0] / st[-2][1] >= st[-1][0] / st[-1][1]:
            a_, c_ = st[-2], st[-1]; st[-2:] = [[a_[0] + c_[0], a_[1] + c_[1], a_[2], c_[3]]]
    return np.array([s[3] for s in st]), np.array([s[0] / s[1] for s in st])
def iso_apply(model, c):
    xs, ys = model; return float(ys[min(np.searchsorted(xs, c), len(ys) - 1)])
kres = {}
gold = lambda rows: [r["gold"] for r in rows]
allv = [r for K in kv for r in kv[K]]; allt = [r for K in kt for r in kt[K]]
Tg = fit_generic(lambda T: [softmax(r["lp"], T) for r in allv], gold(allv))
TK = {K: fit_generic(lambda T: [softmax(r["lp"], T) for r in kv[K]], gold(kv[K])) for K in kv}
print("global T (pooled K)", Tg, "per-K T", TK)
iso = pav([softmax(r["lp"], Tg).max() for r in allv], [int(np.argmax(r["lp"]) == r["gold"]) for r in allv])
def method_probs(rows, method):
    P = []
    for r in rows:
        K = r["K"]
        if method == "T_arc": p = softmax(r["lp"], T0)
        elif method == "T_pooled": p = softmax(r["lp"], Tg)
        elif method == "T_perK": p = softmax(r["lp"], TK[K])
        elif method == "isotonic":
            p = softmax(r["lp"], Tg); c = iso_apply(iso, p.max()); rest = p.copy(); rest[p.argmax()] = 0
            rest = rest / rest.sum() * (1 - c) if rest.sum() > 0 else rest; rest[p.argmax()] = c; p = rest
        P.append(p)
    return P
for method in ("T_arc", "T_pooled", "T_perK", "isotonic"):
    kres[method] = {}
    for K in kt:
        P = method_probs(kt[K], method); m = metrics_from_probs(P, gold(kt[K])); kres[method][K] = m
    P = method_probs(allt, method); kres[method]["pooled"] = metrics_from_probs(P, gold(allt))
    print(method, {K: (round(v["acc"], 3), round(v["ece"], 3), round(v["nll"], 3)) for K, v in kres[method].items()})
out["kscale"] = kres; out["T_pooled"] = Tg; out["T_perK"] = TK
# normalized confidence (K p - 1)/(K - 1): mean & accuracy by normalized-conf bin, pooled test
nc = np.array([(r["K"] * softmax(r["lp"], TK[r["K"]]).max() - 1) / (r["K"] - 1) for r in allt])
ok = np.array([int(np.argmax(r["lp"]) == r["gold"]) for r in allt]); ks = np.array([r["K"] for r in allt])
nb = []
for lo in np.arange(0, 1, .2):
    m = (nc >= lo) & (nc < lo + .2 + (lo > .79) * 1e-9)
    nb.append(dict(lo=float(lo), n=int(m.sum()), acc=float(ok[m].mean()), acc_by_K={int(K): (float(ok[m & (ks == K)].mean()) if (m & (ks == K)).sum() > 5 else None) for K in (2, 4, 8, 16, 26)}))
out["normconf_bins"] = nb; print("normconf", nb)
# ---- cross-domain
xd = {}
for n in ("mmlu", "csqa", "boolq"):
    rows = load(f"xd_{n}.json"); h = len(rows) // 2; A, B = rows[:h], rows[h:]
    Tn, _ = fit_T([r["lp"] for r in A], gold(A))
    un = metrics_from_probs([softmax(r["lp"], T0) for r in B], gold(B), [r["mass"] for r in B])
    re_ = metrics_from_probs([softmax(r["lp"], Tn) for r in B], gold(B), [r["mass"] for r in B])
    xd[n] = {"n_eval": len(B), "T_refit": Tn, "unrefit(T=6.75)": un, "refit": re_}
    print(n, "T_refit", Tn, "UNREFIT", {k: round(v, 3) for k, v in un.items()}, "REFIT", {k: round(v, 3) for k, v in re_.items()})
out["xd"] = xd
save("report2.json", out)
