# Open the Flywheel graphical app in your browser.
# Run  .\gui.ps1  from PowerShell, or double-click. Uses the project's own
# environment via uv, so the active conda/system Python does not matter.
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed. Install it from https://docs.astral.sh/uv/ then re-run." -ForegroundColor Red
    exit 1
}
uv run flywheel gui @args
