#!/bin/sh
# Isolated theme build entrypoint.
#
# - /theme-src is a read-only mount with the theme source (ZIP contents)
# - /theme-dist is a writable mount where the dist/ folder must end up
# - /workspace is a writable tmpfs for the build itself
#
# The theme must produce dist/theme.js (and optionally dist/theme.css).

set -e

echo "[builder] Copying source to writable workspace..."
cp -r /theme-src/. /workspace/

cd /workspace

echo "[builder] Installing dependencies (--ignore-scripts)..."
npm install --ignore-scripts --no-audit --no-fund --prefer-offline 2>&1 | tail -20

echo "[builder] Running build..."
NODE_ENV=production npm run build 2>&1 | tail -30

if [ ! -d "dist" ]; then
  echo "[builder] ERROR: build did not produce dist/ directory"
  exit 1
fi

# Render gate (theme-enforcement Phase 2). SSR every template against fixtures
# via the theme's own @numueg/theme-sdk /verify harness — in this sandboxed
# container, where node_modules exists (offline render is fine under
# --network=none). `set -e` makes a render failure abort the build BEFORE dist
# is published. Skips gracefully for client-only themes / older SDKs. Gated by
# NUMU_THEME_RENDER_GATE so it can be rolled out safely.
if [ "${NUMU_THEME_RENDER_GATE:-0}" = "1" ]; then
  echo "[builder] Render-verifying templates..."
  node /opt/verify_theme_render.mjs /workspace
fi

echo "[builder] Copying dist output to /theme-dist..."
cp -r dist/. /theme-dist/

echo "[builder] Build complete."
ls -la /theme-dist
