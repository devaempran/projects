. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"   # cd to the project root, set $PY
for fmt in colon paren bparen num; do
  $PY bench/evalcfg.py fmt_${fmt}_nosys --fmt $fmt
  $PY bench/evalcfg.py fmt_${fmt}_sys --fmt $fmt --system
done
$PY bench/evalcfg.py short_instr --instr short
