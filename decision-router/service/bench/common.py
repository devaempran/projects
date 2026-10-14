"""Shared harness for the decisionLM evaluation plan (in-process, no server).

Everything logs per-item rows (raw full-vocab label logprobs, so any T / debias
can be applied afterwards). Production prompt format is the default.
"""
import json
import math
import os
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "bench", "results")
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PROD_MODEL = os.path.join(ROOT, "models", "qwen3-8b-q4_k_m.gguf")


# ---------------------------------------------------------------- data
def _norm(items):
    return items


def load_arc(split):
    p = {"validation": "validation", "test": "test", "train": "train"}[split]
    df = pd.read_parquet(f"hf://datasets/allenai/ai2_arc/ARC-Challenge/{p}-00000-of-00001.parquet")
    items = []
    for i, row in df.iterrows():
        texts = list(row["choices"]["text"])
        labels = list(row["choices"]["label"])
        if row["answerKey"] not in labels or len(set(texts)) != len(texts):
            continue
        items.append({"id": f"arc-{split}-{row['id']}", "Context": "", "Question": row["question"],
                      "Options": texts, "gold": labels.index(row["answerKey"])})
    return items


def load_mmlu(n=1000, seed=0):
    df = pd.read_parquet("hf://datasets/cais/mmlu/all/test-00000-of-00001.parquet")
    df = df.sample(n=n, random_state=seed)
    items = []
    for i, r in df.iterrows():
        o = [str(x) for x in r["choices"]]
        if len(set(o)) != len(o):
            continue
        items.append({"id": f"mmlu-{i}", "Context": "", "Question": r["question"], "Options": o,
                      "gold": int(r["answer"])})
    return items


def load_csqa(n=1000, seed=0):
    df = pd.read_parquet("hf://datasets/tau/commonsense_qa/data/validation-00000-of-00001.parquet")
    df = df.sample(n=min(n, len(df)), random_state=seed)
    items = []
    for _, r in df.iterrows():
        t, l = list(r["choices"]["text"]), list(r["choices"]["label"])
        if len(set(t)) != len(t):
            continue
        items.append({"id": f"csqa-{r['id']}", "Context": "", "Question": r["question"], "Options": t,
                      "gold": l.index(r["answerKey"])})
    return items


def load_boolq(n=1000, seed=0):
    df = pd.read_parquet("hf://datasets/google/boolq/data/validation-00000-of-00001.parquet")
    df = df.sample(n=min(n, len(df)), random_state=seed)
    items = []
    for i, r in df.iterrows():
        q = r["question"].strip()
        q = q[0].upper() + q[1:] + ("?" if not q.endswith("?") else "")
        items.append({"id": f"boolq-{i}", "Context": r["passage"], "Question": q,
                      "Options": ["Yes", "No"], "gold": 0 if r["answer"] else 1})
    return items


# ---------------------------------------------------------------- prompts
SYS = "You are a careful expert. Answer multiple-choice questions accurately."

# fmt: (label renderer, option-line renderer, instruction)
def _labels(fmt, k):
    return [str(i + 1) for i in range(k)] if fmt == "num" else list(LABELS[:k])


def _line(fmt, lab, text):
    return {"colon": f"{lab}: {text}", "paren": f"{lab}) {text}", "bparen": f"({lab}) {text}",
            "num": f"{lab}. {text}"}[fmt]


def build_prompt(item, order=None, fmt="colon", system=False, instr="full"):
    """order: permutation of option indices (display position -> original index)."""
    k = len(item["Options"])
    order = order if order is not None else list(range(k))
    labels = _labels(fmt, k)
    lines = []
    if item.get("Context", "").strip():
        lines.append(f"Context: {item['Context'].strip()}\n")
    lines.append(f"Question: {item['Question'].strip()}\n")
    lines.append("Choices:")
    for lab, oi in zip(labels, order):
        lines.append(_line(fmt, lab, item["Options"][oi]))
    kind = "number" if fmt == "num" else "letter"
    if instr == "full":
        lines.append(f"\nRespond with exactly one {kind} ({', '.join(labels)}) and nothing else. "
                     f"Do not explain your answer.")
    else:  # trimmed
        lines.append(f"\nAnswer with one {kind} only.")
    body = "\n".join(lines)
    sys_part = f"<|im_start|>system\n{SYS}<|im_end|>\n" if system else ""
    return sys_part + f"<|im_start|>user\n{body}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


# ---------------------------------------------------------------- model
class Scorer:
    def __init__(self, path=PROD_MODEL, logits_all=True, flash_attn=False, n_batch=512, n_ubatch=512,
                 n_ctx=4096, n_gpu_layers=-1):
        from llama_cpp import Llama
        self.path = path
        self.cfg = dict(logits_all=logits_all, flash_attn=flash_attn, n_batch=n_batch, n_ubatch=n_ubatch)
        self.llm = Llama(model_path=path, n_gpu_layers=n_gpu_layers, n_ctx=n_ctx, verbose=False,
                         logits_all=logits_all, flash_attn=flash_attn, n_batch=n_batch, n_ubatch=n_ubatch)
        self.n_vocab = self.llm.n_vocab()
        self._ids = {}

    def label_ids(self, labels):
        out = []
        for l in labels:
            if l not in self._ids:
                t = self.llm.tokenize(l.encode(), add_bos=False)
                assert len(t) == 1, (l, t)
                self._ids[l] = t[0]
            out.append(self._ids[l])
        return out

    def tokenize(self, prompt):
        return self.llm.tokenize(prompt.encode("utf-8"), special=True)

    def _last_logits(self):
        llm = self.llm
        if self.cfg["logits_all"]:
            return llm.scores[llm.n_tokens - 1, :]
        ptr = llm._ctx.get_logits_ith(-1)
        return np.ctypeslib.as_array(ptr, shape=(self.n_vocab,))

    def score_prompt(self, prompt, labels, reuse_prefix=False):
        """Direct eval path. Returns dict with raw full-vocab label logprobs + timing breakdown."""
        llm = self.llm
        ids = self.label_ids(labels)
        t0 = time.perf_counter()
        toks = self.tokenize(prompt)
        t1 = time.perf_counter()
        n_reused = 0
        if reuse_prefix and llm.n_tokens > 0:
            prev = llm._input_ids[: llm.n_tokens] if hasattr(llm, "_input_ids") else llm.input_ids[: llm.n_tokens]
            m = 0
            lim = min(len(prev), len(toks))
            while m < lim and prev[m] == toks[m]:
                m += 1
            n_reused = min(m, len(toks) - 1)
            llm.n_tokens = n_reused
            llm.eval(toks[n_reused:])
        else:
            llm.reset()
            llm.eval(toks)
        t2 = time.perf_counter()
        row = self._last_logits().astype(np.float64)
        mx = row.max()
        lse = mx + math.log(np.exp(row - mx).sum())
        lp = row[ids] - lse
        top = int(row.argmax())
        t3 = time.perf_counter()
        return {"lp": lp.tolist(), "mass": float(np.exp(lp).sum()), "top_tok": llm.detokenize([top]).decode(errors="replace"),
                "top_is_label": top in ids, "prompt_tokens": len(toks), "reused": n_reused,
                "t_tok": t1 - t0, "t_prefill": t2 - t1, "t_extract": t3 - t2, "t_total": t3 - t0}

    def score_production(self, prompt, labels, n_top=None):
        """Mirrors scorer.py: create_completion with logprobs top-N. Requires logits_all=True."""
        n_top = n_top or max(len(labels) + 10, 30)
        t0 = time.perf_counter()
        out = self.llm.create_completion(prompt=prompt, max_tokens=1, temperature=0, logprobs=n_top, echo=False)
        dt = time.perf_counter() - t0
        top = out["choices"][0]["logprobs"]["top_logprobs"][0]
        lp = []
        for l in labels:
            c = [(t, v) for t, v in top.items() if t.strip() == l]
            bare = [x for x in c if x[0] == l]
            lp.append(float((bare or c)[0][1]) if c else float("nan"))
        return {"lp": lp, "t_total": dt, "prompt_tokens": out["usage"]["prompt_tokens"]}


def run_items(sc, items, orders=None, fmt="colon", system=False, instr="full", warm=10, reuse_prefix=False,
              tag=""):
    """Score each item (optionally under a given display order). Returns per-item rows.
    lp is returned in ORIGINAL option order."""
    rows = []
    # warmup (discarded)
    for it in items[:warm]:
        sc.score_prompt(build_prompt(it, None, fmt, system, instr), _labels(fmt, len(it["Options"])))
    for j, it in enumerate(items):
        k = len(it["Options"])
        order = orders[j] if orders is not None else list(range(k))
        labels = _labels(fmt, k)
        r = sc.score_prompt(build_prompt(it, order, fmt, system, instr), labels, reuse_prefix=reuse_prefix)
        lp_orig = [None] * k
        for pos, oi in enumerate(order):
            lp_orig[oi] = r["lp"][pos]
        r["lp"] = lp_orig
        r.update(id=it["id"], order=order, gold=it["gold"], k=k)
        rows.append(r)
        if (j + 1) % 200 == 0:
            print(f"  [{tag}] {j+1}/{len(items)}", flush=True)
    return rows


# ---------------------------------------------------------------- metrics
def softmax(lp, T=1.0):
    a = np.asarray(lp, dtype=np.float64) / T
    a = a - a.max()
    e = np.exp(a)
    return e / e.sum()


def ece_score(conf, correct, bins=10):
    conf, correct = np.asarray(conf), np.asarray(correct, dtype=float)
    tot = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        m = (conf >= lo) & ((conf < hi) | (b == bins - 1))
        if m.any():
            tot += m.mean() * abs(correct[m].mean() - conf[m].mean())
    return float(tot)


def metrics_from_probs(P, golds, masses=None):
    """P: list of prob vectors (original option order)."""
    conf = np.array([p.max() for p in P])
    pred = np.array([int(p.argmax()) for p in P])
    g = np.array(golds)
    correct = (pred == g).astype(float)
    nll = -np.mean([math.log(max(p[gi], 1e-12)) for p, gi in zip(P, golds)])
    brier = np.mean([sum((p[i] - (i == gi)) ** 2 for i in range(len(p))) for p, gi in zip(P, golds)])
    out = {"n": len(P), "acc": float(correct.mean()), "ece": ece_score(conf, correct), "brier": float(brier),
           "nll": float(nll), "mean_conf": float(conf.mean())}
    if masses is not None:
        out["mass"] = float(np.mean(masses))
    return out


def fit_T(lps, golds):
    """Production rule (fit_temperature.py): T minimizing NLL, but not below the smallest T whose
    mean confidence <= accuracy."""
    grid = [1 + 0.25 * k for k in range(157)]
    best, under = None, None
    for T in grid:
        P = [softmax(l, T) for l in lps]
        nll = -np.mean([math.log(max(p[g], 1e-12)) for p, g in zip(P, golds)])
        conf = np.mean([p.max() for p in P])
        acc = np.mean([int(p.argmax()) == g for p, g in zip(P, golds)])
        if best is None or nll < best[1]:
            best = (T, nll)
        if under is None and conf <= acc:
            under = T
    return max(best[0], under if under is not None else grid[-1]), best[0]


def lat_stats(xs):
    xs = np.asarray(xs) * 1000
    return {"p50_ms": float(np.percentile(xs, 50)), "p95_ms": float(np.percentile(xs, 95)), "mean_ms": float(xs.mean())}


def summarize(rows, T):
    P = [softmax(r["lp"], T) for r in rows]
    m = metrics_from_probs(P, [r["gold"] for r in rows], [r["mass"] for r in rows])
    m.update(lat_stats([r["t_total"] for r in rows]))
    m["prompt_tokens"] = float(np.mean([r["prompt_tokens"] for r in rows]))
    m["T"] = T
    return m


def save(name, obj):
    os.makedirs(RES, exist_ok=True)
    with open(os.path.join(RES, name), "w") as f:
        json.dump(obj, f)


def load(name):
    with open(os.path.join(RES, name)) as f:
        return json.load(f)
