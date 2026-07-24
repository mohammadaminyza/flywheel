@echo off
REM Launch the Flywheel UI.
REM Double-click this file, or run  run.bat  from any terminal.
REM Uses the project's own environment via uv, ignoring conda/system Python.
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed. Install it from https://docs.astral.sh/uv/ then re-run.
  pause
  exit /b 1
)
uv run flywheel %*
if errorlevel 1 pause
