#!/usr/bin/env bash
# Launch the Flywheel terminal app.
# Run  ./run.sh  (or  bash run.sh ). Uses the project's own environment via uv,
# so the active conda/system Python does not matter.
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it from https://docs.astral.sh/uv/ then re-run." >&2
  exit 1
fi
exec uv run flywheel "$@"
