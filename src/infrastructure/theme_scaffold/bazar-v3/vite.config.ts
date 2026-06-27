import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import { numuTheme } from "@numueg/theme-plugin";

/**
 * Bazar V3 build config.
 *
 * We pin `build.lib` ourselves (rather than letting @numueg/theme-plugin
 * supply its default) for ONE reason: `cssFileName`. The Tailwind decision
 * (see styles.css / src/theme.css) relies on Vite emitting the compiled CSS
 * as `dist/theme.css` — the exact name the host loads via
 * `external_theme.css_url`. Vite's lib mode otherwise names the CSS after
 * the package (`bazar-v3.css`), which the host would never fetch, and the
 * plugin's closeBundle would then copy the thin fallback styles.css over a
 * (correctly absent) theme.css. Setting `cssFileName: "theme"` makes Vite
 * write the COMPILED stylesheet to dist/theme.css directly; the plugin's
 * copy step then no-ops because the file already exists.
 *
 * The entry/format/fileName mirror the plugin's own defaults so federation
 * behaviour is unchanged. `federate: false` keeps the self-contained bundle
 * this theme already shipped.
 */
export default defineConfig({
  plugins: [react(), numuTheme({ federate: false }) as unknown as PluginOption],
  build: {
    lib: {
      entry: "src/main.tsx",
      formats: ["es"],
      fileName: () => "theme.js",
      cssFileName: "theme",
    },
    cssCodeSplit: false,
  },
  server: { port: 5173 },
});
