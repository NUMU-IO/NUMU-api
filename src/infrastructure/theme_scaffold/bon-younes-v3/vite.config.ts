import { defineConfig, type Plugin, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import { numuTheme } from "@numueg/theme-plugin";
import fs from "node:fs";
import path from "node:path";

/**
 * Local workaround for @numueg/theme-plugin@0.1.0.
 *
 * The published plugin's dev middleware only serves `/theme.js`,
 * `/theme.css`, and `/sections.json` from `dist/`. But Vite's lib build
 * produces hashed chunks (e.g. `main-BD6BrFd1.js`, `by-hero-*.js`) that
 * `theme.js` imports at runtime. Without a middleware for those, the
 * iframe gets 404 → "Failed to fetch dynamically imported module".
 *
 * This local plugin serves any file in `dist/` at its corresponding URL
 * with permissive CORS. Mirrors what the source plugin does but the
 * published dist hasn't shipped that code yet (see
 * project_v3_sdk_dist_gap memory — same problem class). Once a newer
 * @numueg/theme-plugin ships with the fix, this can be deleted.
 */
function serveBuildChunks(): Plugin {
  const types: Record<string, string> = {
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
  };
  return {
    name: "by-serve-dist-chunks",
    configureServer(server) {
      const distDir = path.join(server.config.root, "dist");
      server.middlewares.use((req, res, next) => {
        if (req.method !== "GET" && req.method !== "HEAD") return next();
        const url = (req.url || "").split("?")[0];
        if (!url || url === "/") return next();
        const rel = url.startsWith("/") ? url.slice(1) : url;
        if (rel.includes("..")) return next();
        // theme.js / theme.css / sections.json are already handled by
        // the numuTheme plugin's middleware — let those pass through.
        if (
          rel === "theme.js" ||
          rel === "theme.css" ||
          rel === "sections.json" ||
          rel === "theme.json" ||
          rel === "settings_schema.json"
        ) {
          return next();
        }
        const filePath = path.join(distDir, rel);
        if (!fs.existsSync(filePath)) return next();
        const stat = fs.statSync(filePath);
        if (!stat.isFile()) return next();
        const ext = path.extname(rel).toLowerCase();
        res.setHeader("Content-Type", types[ext] || "application/octet-stream");
        res.setHeader("Content-Length", String(stat.size));
        res.setHeader("Access-Control-Allow-Origin", "*");
        res.setHeader("Cache-Control", "no-store");
        if (req.method === "HEAD") {
          res.end();
          return;
        }
        fs.createReadStream(filePath).pipe(res);
      });
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    numuTheme({ federate: false }) as unknown as PluginOption,
    serveBuildChunks(),
  ],
  server: { port: 5173 },
});
