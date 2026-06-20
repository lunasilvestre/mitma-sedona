#!/usr/bin/env bash
# ui_test.sh — re-runnable headless-browser UI smoke test for docs/explore.html.
#
# Stands up a static server over docs/, drives headless chromium with software
# WebGL (SwiftShader) to the deck.gl geo-browser, waits for the map to render,
# captures full-page + map-canvas screenshots to tests/ui/proof/_smoke/, and
# verifies the canvas is non-blank. Exit 0 only on a real rendered map.
#
# Use after each rebuild / UI update:
#     scripts/ui_test.sh
#
# First run on a fresh machine? Install the browser once:
#     scripts/ui_test.sh --install      # npm ci + playwright chromium
#
# Requires: node, npm, python3. No GPU needed (software WebGL).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI_DIR="$REPO_ROOT/tests/ui"

cd "$UI_DIR"

if [[ "${1:-}" == "--install" ]]; then
  echo "[ui_test] installing node deps (tests/ui)…"
  npm install --no-audit --no-fund
  echo "[ui_test] installing playwright chromium (browser only, no apt deps)…"
  npx playwright install chromium
  echo "[ui_test] install complete."
  shift || true
fi

# Ensure the browser binary is present before running; guide the user if not.
if [[ ! -d "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}" ]] \
   || ! ls "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"/chromium-*/ >/dev/null 2>&1; then
  echo "[ui_test] chromium not found — run: scripts/ui_test.sh --install" >&2
fi

echo "[ui_test] running smoke test…"
exec node "$UI_DIR/smoke.mjs"
