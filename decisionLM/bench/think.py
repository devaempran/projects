"""Escalation reasoner: Qwen3-8B Q4_K_M with thinking enabled. usage: think.py SPLIT THETA  (items with tier-1 conf < THETA)"""
import sys, re, json, os, time; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
split, theta = sys.argv[1], float(sys.argv[2])
T = 6.75
base = load("p1_val.json" if split == "validation" else "p1_test.json")
items = {it["id"]: it for it in load_arc(split)}
todo = [r["id"] for r in base if softmax(r["lp"], T).max() < theta]
path = os.path.join(RES, f"think_{split}.jsonl")
done = set()
if os.path.exists(path):
    done = {json.loads(l)["id"] for l in open(path)}
print(len(todo), "items below theta;", len(done), "already done", flush=True)
sc = Scorer(logits_all=False)
BUD = 1500
for iid in todo:
    if iid in done: continue
    it = items[iid]; k = len(it["Options"]); labs = list(LABELS[:k])
    p = build_prompt(it)
    head = p.split("\n\nRespond with exactly one")[0]
    prompt = head + "\n\nThink it through, then finish with a line of the form 'Answer: <letter>'.<|im_end|>\n<|im_start|>assistant\n"
    t0 = time.perf_counter()
    out = sc.llm.create_completion(prompt=prompt, max_tokens=BUD, temperature=0.6, top_p=0.95, top_k=20, seed=0, stop=["<|im_end|>"])
    text = out["choices"][0]["text"]; ntok = out["usage"]["completion_tokens"]
    after = text.split("</think>")[-1] if "</think>" in text else ""
    m = re.findall(r"Answer:\s*\**\(?([A-Z])\b", after)
    forced = False
    if m and m[-1] in labs:
        pred = labs.index(m[-1])
    else:
        forced = True
        body = text.split("</think>")[0]
        r = sc.score_prompt(prompt + body + "\n\nI must answer now.\n</think>\n\nAnswer: ", labs)
        pred = int(np.argmax(r["lp"]))
    dt = time.perf_counter() - t0
    with open(path, "a") as f:
        f.write(json.dumps({"id": iid, "pred": pred, "gold": it["gold"], "forced": forced, "tokens": ntok, "t": dt}) + "\n")
    print(iid, pred == it["gold"], ntok, f"{dt:.1f}s", "forced" if forced else "", flush=True)
