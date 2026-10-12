. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"   # cd to the project root, set $PY
until [ -f bench/results/cfg_short_instr_test.json ]; do sleep 20; done
sleep 5
# wait for downloads
until grep -q "Qwen3-4B-Q4_K_M" bench/results/download.log; do sleep 20; done
M=bench/models
$PY bench/evalcfg.py q4km_official --model $M/Qwen3-8B-Q4_K_M.gguf
$PY bench/evalcfg.py q5km --model $M/Qwen3-8B-Q5_K_M.gguf
$PY bench/evalcfg.py q8_0 --model $M/Qwen3-8B-Q8_0.gguf
$PY bench/evalcfg.py qwen3_4b_q4km --model $M/Qwen3-4B-Q4_K_M.gguf
$PY bench/evalcfg.py flash_attn --flash
$PY bench/crossdomain.py
$PY bench/kscale.py
touch bench/results/queue.done
