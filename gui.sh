#!/usr/bin/env bash
# Open the Flywheel graphical app in your browser.
# Run  ./gui.sh  (or  bash gui.sh ). Uses the project's own environment via uv.
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it from https://docs.astral.sh/uv/ then re-run." >&2
  exit 1
fi
exec uv run flywheel gui "$@"
