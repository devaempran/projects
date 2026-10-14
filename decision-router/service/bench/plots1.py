import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis import *
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e3e2de"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": .6, "axes.axisbelow": True})
R = load("report2.json"); P = os.path.join(RES, "plots") + os.sep
kt = {K: load(f"k_{K}_test.json") for K in (2, 4, 8, 16, 26)}; kv = {K: load(f"k_{K}_validation.json") for K in kt}
allt = [r for K in kt for r in kt[K]]; allv = [r for K in kv for r in kv[K]]
Tg, TK = R["T_pooled"], {int(k): v for k, v in R["T_perK"].items()}
def pav(x, y):
    o = np.argsort(x); xs, ys = np.asarray(x)[o], np.asarray(y, float)[o]; st = []
    for i in range(len(ys)):
        st.append([ys[i], 1.0, xs[i]])
        while len(st) > 1 and st[-2][0] / st[-2][1] >= st[-1][0] / st[-1][1]:
            a, c = st[-2], st[-1]; st[-2:] = [[a[0] + c[0], a[1] + c[1], c[2]]]
    return np.array([s[2] for s in st]), np.array([s[0] / s[1] for s in st])
iso = pav([softmax(r["lp"], Tg).max() for r in allv], [int(np.argmax(r["lp"]) == r["gold"]) for r in allv])
ok = np.array([int(np.argmax(r["lp"]) == r["gold"]) for r in allt])
conf = {"Scalar T (ARC, 6.75)": np.array([softmax(r["lp"], 6.75).max() for r in allt]),
        "Scalar T (pooled)": np.array([softmax(r["lp"], Tg).max() for r in allt]),
        "Per-K T": np.array([softmax(r["lp"], TK[r["K"]]).max() for r in allt])}
c = conf["Scalar T (pooled)"]
conf["Isotonic on max-prob"] = np.array([iso[1][min(np.searchsorted(iso[0], x), len(iso[1]) - 1)] for x in c])
fig, axs = plt.subplots(1, 4, figsize=(14, 3.6), sharey=True)
for ax, (n, cf) in zip(axs, conf.items()):
    ax.plot([0, 1], [0, 1], color=MUTED, lw=1, ls="--")
    xs, ys, ns = [], [], []
    for b in range(10):
        m = (cf >= b / 10) & ((cf < (b + 1) / 10) | (b == 9))
        if m.sum() >= 10: xs.append(cf[m].mean()); ys.append(ok[m].mean()); ns.append(m.sum())
    ax.plot(xs, ys, color=BLUE, lw=2, marker="o", ms=6, mfc=BLUE, mec="#fcfcfb", mew=1.5)
    ax.set_title(f"{n}\nECE {ece_score(cf, ok):.3f}", fontsize=10, color=INK, loc="left"); ax.set_xlabel("confidence"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
axs[0].set_ylabel("accuracy"); fig.suptitle("Reliability on pooled synthetic K=2..26 ARC test (bins with n>=10; dashed = perfect)", x=0.01, ha="left", fontsize=10, color=MUTED)
fig.tight_layout(); fig.savefig(P + "reliability_methods.png", dpi=150); plt.close(fig)
# --- accuracy and ECE vs K
Ks = [2, 4, 8, 16, 26]; ks = R["kscale"]
fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.4))
a1.plot(Ks, [ks["T_pooled"][str(K)]["acc"] for K in Ks], color=BLUE, lw=2, marker="o"); a1.set_title("Accuracy vs option count K", loc="left", color=INK, fontsize=10); a1.set_xscale("log", base=2); a1.set_xticks(Ks); a1.set_xticklabels(Ks); a1.set_ylim(.7, 1)
a2.plot(Ks, [ks["T_pooled"][str(K)]["ece"] for K in Ks], color=BLUE, lw=2, marker="o", label="pooled T"); a2.plot(Ks, [ks["T_perK"][str(K)]["ece"] for K in Ks], color=ORANGE, lw=2, marker="o", label="per-K T")
a2.plot(Ks, [ks["isotonic"][str(K)]["ece"] for K in Ks], color=MUTED, lw=2, marker="o", label="isotonic"); a2.legend(frameon=False); a2.set_title("ECE vs K (ARC test)", loc="left", color=INK, fontsize=10); a2.set_xscale("log", base=2); a2.set_xticks(Ks); a2.set_xticklabels(Ks)
for a in (a1, a2): a.set_xlabel("K")
fig.tight_layout(); fig.savefig(P + "k_scaling.png", dpi=150); plt.close(fig)
# --- accuracy by label-mass quartile
mb = R["mass_buckets"]; fig, ax = plt.subplots(figsize=(5, 3.2))
ax.bar(range(4), [b["acc"] for b in mb], color=BLUE, width=.6); ax.set_xticks(range(4)); ax.set_xticklabels(["Q1 (lowest mass)", "Q2", "Q3", "Q4 (highest)"], fontsize=8)
for i, b in enumerate(mb): ax.text(i, b["acc"] + .01, f"{b['acc']:.3f}", ha="center", color=INK, fontsize=9)
ax.set_ylim(.6, 1.02); ax.set_title("ARC test accuracy by label-mass quartile\n(mass spans 0.9998 .. 1.0 only)", loc="left", fontsize=10, color=INK); ax.set_ylabel("accuracy")
fig.tight_layout(); fig.savefig(P + "mass_buckets.png", dpi=150); plt.close(fig)
print("ok")
