. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"   # cd to the project root, set $PY
M=bench/models
run() { n=$1; shift; [ -f bench/results/cfg_${n}_test.json ] || $PY bench/evalcfg.py $n "$@"; }
run fmt_colon_nosys --fmt colon
for fmt in paren bparen num; do run fmt_${fmt}_nosys --fmt $fmt; done
for fmt in colon paren bparen num; do run fmt_${fmt}_sys --fmt $fmt --system; done
run short_instr --instr short
run q4km_official --model $M/Qwen3-8B-Q4_K_M.gguf
run q5km --model $M/Qwen3-8B-Q5_K_M.gguf
run q8_0 --model $M/Qwen3-8B-Q8_0.gguf
run qwen3_4b_q4km --model $M/Qwen3-4B-Q4_K_M.gguf
run flash_attn --flash
[ -f bench/results/xd_boolq.json ] || $PY bench/crossdomain.py
[ -f bench/results/k_26_test.json ] || $PY bench/kscale.py
touch bench/results/queue.done
