# Launch the Flywheel UI.
# Double-click, or run  .\run.ps1  from PowerShell. Always uses the project's own
# environment via uv, so it does not matter which Python or conda env is active.
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed. Install it from https://docs.astral.sh/uv/ then re-run." -ForegroundColor Red
    exit 1
}
uv run flywheel @args
