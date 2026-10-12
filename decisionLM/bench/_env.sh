# Sourced by the run_*.sh scripts: go to the project root and pick a Python.
#   PYTHON=/path/to/python bash bench/run_all.sh      # override the interpreter
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
if [ -z "$PYTHON" ]; then
  if [ -x .venv/Scripts/python.exe ]; then PYTHON=.venv/Scripts/python.exe   # Windows venv
  elif [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python                 # Linux/macOS venv
  else PYTHON=python; fi
fi
PY="$PYTHON -I"    # -I: isolated mode, so scripts must find bench/common.py themselves (they do)
