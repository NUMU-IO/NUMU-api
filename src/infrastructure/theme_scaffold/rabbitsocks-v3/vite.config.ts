import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import { numuTheme } from "@numueg/theme-plugin";

/**
 * Rabbitsocks V3 build config.
 *
 * Tailwind-in-bundle (mirrors bazar/vionne): src/main.tsx imports src/theme.css,
 * whose `@tailwind` directives + the ported V2 rabbitsocks utility classes are
 * compiled by Vite's PostCSS step. `cssFileName: "theme"` makes Vite emit the
 * COMPILED stylesheet as dist/theme.css (the exact name the host loads via
 * external_theme.css_url), so the plugin's fallback-copy of styles.css no-ops.
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
