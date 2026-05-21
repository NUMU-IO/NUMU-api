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

echo "[builder] Copying dist output to /theme-dist..."
cp -r dist/. /theme-dist/

echo "[builder] Build complete."
ls -la /theme-dist
