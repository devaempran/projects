import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e3e2de"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
v = load("escalation_validation.json"); t = load("escalation_test.json")
fig, ax = plt.subplots(figsize=(6, 3.6))
ax.plot([r["mean_lat_s"] for r in v], [r["acc"] for r in v], color=BLUE, lw=2, marker="o", label="validation (θ swept)")
ax.plot([r["mean_lat_s"] for r in t], [r["acc"] for r in t], color=ORANGE, lw=2, marker="o", label="test (θ ≤ 0.85 only)")
ax.set_xlabel("mean latency per decision (s)"); ax.set_ylabel("overall accuracy"); ax.legend(frameon=False)
ax.set_title("Escalation curve: tier-1 + thinking reasoner", loc="left", fontsize=10, color=INK)
fig.tight_layout(); fig.savefig(os.path.join(RES, "plots", "escalation_curve.png"), dpi=150)
