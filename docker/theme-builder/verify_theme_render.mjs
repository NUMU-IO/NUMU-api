#!/usr/bin/env node
/**
 * Render gate invoker (theme-enforcement Phase 2), run INSIDE the isolated
 * theme-builder container after the build.
 *
 * SSR-renders every required template against fixture data via the theme's
 * own `@numueg/theme-sdk` `/verify` harness and exits non-zero if any template
 * throws or renders empty. Runs offline (no network) — the build container is
 * launched with --network=none, which is fine for a server render.
 *
 * Forward-compatible: exits 0 (skip) when the theme is client-only (no
 * dist/theme.server.js) or its SDK predates the `/verify` entry, so enabling
 * the gate never breaks builds of older themes — it only enforces where it can.
 *
 * Usage: node verify_theme_render.mjs <themeDir>
 */
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { existsSync } from "node:fs";
import path from "node:path";

const themeDir = process.argv[2] || process.cwd();
const serverBundle = path.join(themeDir, "dist", "theme.server.js");

if (!existsSync(serverBundle)) {
  console.log("[render-gate] no dist/theme.server.js — client-only theme, skipping");
  process.exit(0);
}

const requireFromTheme = createRequire(path.join(themeDir, "package.json"));

let harness;
try {
  const verifyEntry = requireFromTheme.resolve("@numueg/theme-sdk/verify");
  harness = await import(pathToFileURL(verifyEntry).href);
} catch {
  console.log(
    "[render-gate] theme's @numueg/theme-sdk has no /verify entry — skipping " +
      "(upgrade @numueg/theme-sdk to enforce render verification)",
  );
  process.exit(0);
}

let mod;
try {
  mod = await import(pathToFileURL(serverBundle).href);
} catch (err) {
  console.error(
    "[render-gate] FAIL: could not load dist/theme.server.js — " +
      (err && err.stack ? err.stack.split("\n")[0] : String(err)),
  );
  process.exit(1);
}

const createApp = mod.createApp ?? (mod.default && mod.default.createApp);
const result = await harness.verifyThemeRender({ createApp });

for (const r of result.results) {
  if (r.ok) {
    console.log(`[render-gate] PASS ${r.template} (${r.htmlLength} chars)`);
  } else {
    console.log(
      `[render-gate] FAIL ${r.template}: ${(r.error || "").split("\n")[0]}`,
    );
  }
}

process.exit(result.ok ? 0 : 1);
