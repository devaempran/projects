# Start the whole demo: 2 deciders, a worker per downstream topic, and the dashboard, each in its own window.
#   powershell -File demo\start_demo.ps1            (run from redis-streams-pipeline; Redis and decisionLM must already be up)
# Then publish data from another terminal:   python demo\publisher\publish.py --edge --dupes 5
# Dashboard: http://127.0.0.1:5055
param([string]$Python = "python", [int]$Deciders = 2)

$root = Split-Path -Parent $PSScriptRoot
$env:PIPELINE_CONFIG = Join-Path $root "demo\pipeline.demo.toml"
$env:PYTHONPATH = Join-Path $root "src"
Set-Location $root

function Start-Window($title, $command) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location '$root'; `$env:PIPELINE_CONFIG = '$env:PIPELINE_CONFIG'; `$env:PYTHONPATH = '$env:PYTHONPATH'; $command"
}

& $Python run.py setup
if ($LASTEXITCODE -ne 0) { throw "setup failed: is Redis running at the host and port in demo\pipeline.demo.toml?" }

1..$Deciders | ForEach-Object { Start-Window "decider $_" "$Python run.py decider demo-decider-$_" }
& $Python -c "from pipeline import config; print('\n'.join(config.DECISION_STREAMS))" | ForEach-Object {
    $name = $_ -replace '^demo:', ''
    Start-Window "worker $name" "$Python run.py worker $_ worker-$name"
}
Start-Window "dashboard" "$Python demo\dashboard\server.py"
Write-Host "Started. Dashboard: http://127.0.0.1:5055"
