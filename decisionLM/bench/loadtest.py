import sys, json, time, threading, urllib.request, urllib.error
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np
port, tag = sys.argv[1], sys.argv[2]
url = f"http://127.0.0.1:{port}/api/score"
items = load_arc("validation")[:160]
def call(it):
    body = json.dumps({k: it[k] for k in ("Context", "Question", "Options")}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as r: out = json.load(r)
        return time.perf_counter() - t, out
    except Exception as e:
        return time.perf_counter() - t, {"error": str(e)}
for it in items[:5]: call(it)       # warm
ref = {}                            # sequential reference
for it in items: ref[it["id"]] = call(it)[1]
res = {}
for conc in [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "1,4,16").split(",")]:
    lats, outs, errs = [], {}, 0
    q = list(items); lock = threading.Lock()
    def worker():
        global errs
        while True:
            with lock:
                if not q: return
                it = q.pop()
            dt, o = call(it)
            with lock:
                lats.append(dt); outs[it["id"]] = o
                if "error" in o: errs += 1
    t0 = time.perf_counter()
    th = [threading.Thread(target=worker) for _ in range(conc)]
    [x.start() for x in th]; [x.join() for x in th]
    wall = time.perf_counter() - t0
    # consistency vs sequential reference (same prompt -> should be same up to kernel noise)
    bad = 0; maxd = 0.0; flips = 0
    for it in items:
        o, r = outs.get(it["id"]), ref[it["id"]]
        if not o or "error" in o or "error" in r: continue
        d = max(abs(o[k] - r[k]) for k in r); maxd = max(maxd, d)
        flips += max(o, key=o.get) != max(r, key=r.get)
    l = np.array(lats) * 1000
    res[conc] = dict(n=len(items), wall_s=wall, throughput_rps=len(items)/wall, p50_ms=float(np.percentile(l, 50)),
                     p95_ms=float(np.percentile(l, 95)), errors=errs, max_dprob_vs_sequential=maxd, argmax_flips=int(flips))
    print(conc, res[conc], flush=True)
save(f"load_{tag}.json", res)
