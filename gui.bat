@echo off
REM Open the Flywheel graphical app in your browser.
REM Double-click this file, or run  gui.bat  from any terminal.
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed. Install it from https://docs.astral.sh/uv/ then re-run.
  pause
  exit /b 1
)
uv run flywheel gui %*
if errorlevel 1 pause
