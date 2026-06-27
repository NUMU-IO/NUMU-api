import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import { numuTheme } from "@numueg/theme-plugin";

export default defineConfig({
  plugins: [react(), numuTheme({ federate: false }) as unknown as PluginOption],
  server: { port: 5173 },
});
