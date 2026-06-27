import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { numuTheme } from "@numueg/theme-plugin";

// The @numueg/theme-plugin handles all the NUMU-specific glue:
//   - validates theme.json + settings_schema.json + entry point
//   - externalizes React + @numueg/theme-sdk (host-provided)
//   - emits dist/manifest.json + dist/import-map.json after build
//
// You don't need to repeat `build.lib` / `build.rollupOptions.external`
// — the plugin sets sensible defaults when they're omitted.

export default defineConfig({
  plugins: [react(), numuTheme()],
  server: { port: 5173 },
});
