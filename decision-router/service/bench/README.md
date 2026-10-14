# Benchmark harness

Everything behind the numbers in [`../RESULTS.md`](../RESULTS.md). The narrative report is [`FINDINGS.md`](FINDINGS.md) and the
full table is [`RESULTS_TABLE.md`](RESULTS_TABLE.md). All scripts find their own paths, so they run from any directory.

```
common.py        shared loaders (ARC, MMLU, ...), prompt builder, a standalone scorer, metrics (ECE, Brier, NLL)
phase1.py        baseline accuracy, calibration and latency          phase2a/2b.py   speed changes that should not alter outputs
evalcfg.py       one configuration (format, system prompt, quantisation, flash attention) over ARC validation + test
analysis.py      rotation, flip rate, debiasing, per-config metrics  report1/2.py    summaries     plots1/2.py   figures
crossdomain.py   MMLU, CommonsenseQA, BoolQ                           kscale.py       2 to 26 options
think.py         escalation reasoner (1,500-token budget)             escalation.py   accuracy and latency versus threshold
loadtest.py      concurrency and throughput                           serve.py        run the app under Flask or waitress
download.py      fetch the other GGUF quantisations (about 20 GB)     make_table.py   regenerate RESULTS_TABLE.md
run_all.sh       the full queue of configurations (resumable: skips configs whose result file exists)
_env.sh          sourced by run_*.sh: cd to the project root, pick the Python (.venv, or PYTHON=...)
results/         raw output. Committed: plots, summary JSON, load tests, escalation and cross-domain data, think_*.jsonl, decide_test.jsonl.
                 Not committed (large, regenerated): cfg_*.json, k_*.json, logs.
```

## Reproduce

```bash
python bench/download.py                    # extra GGUFs into bench/models (about 20 GB), needs huggingface_hub
bash bench/run_all.sh                       # hours on one GPU; PYTHON=/path/to/python to choose the interpreter
python bench/make_table.py                  # rebuild RESULTS_TABLE.md from results/cfg_*.json
python bench/escalation.py validation       # analysis-only scripts need no GPU, only results/
```

The datasets download from Hugging Face on first use (`allenai/ai2_arc`, `cais/mmlu`, ...). The production model for `phase1.py` is
`models/qwen3-8b-q4_k_m.gguf` (the original build; see `common.py: PROD_MODEL`).
Note: `scorer_before.py.txt` is the pre-evaluation copy of the scorer, kept for comparison.
