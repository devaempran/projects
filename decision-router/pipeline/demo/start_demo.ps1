# Start the whole demo with one command: the deciders, a worker per topic and the dashboard (decisionLM too, unless it is already up).
#   powershell -File demo\start_demo.ps1            (run from decision-router/pipeline; Redis must already be up)
# Then publish data from another terminal:   python demo\publisher\publish.py --edge --dupes 5
# Dashboard: http://127.0.0.1:5055
param([string]$Python = "python")

Set-Location (Split-Path -Parent $PSScriptRoot)
& $Python run.py start --config demo\pipeline.demo.toml
