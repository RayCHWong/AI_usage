#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$BASH_SOURCE")"
rm -rf build dist
uv sync --group build
uv run python3 setup_app.py py2app
if [[ -d dist/main.app && ! -d dist/usag.app ]]; then
  mv dist/main.app dist/usag.app
fi
echo "Built: dist/usag.app"
