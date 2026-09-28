import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served at https://figure.rebuilder.ai/3dgen/ ; the API lives at /3dgen/api on the same origin.
export default defineConfig({
  base: "/3dgen/",
  plugins: [react()],
  server: {
    proxy: { "/3dgen/api": process.env.FIG3D_API ?? "http://127.0.0.1:8030" },
  },
  build: { chunkSizeWarningLimit: 900 },
});
