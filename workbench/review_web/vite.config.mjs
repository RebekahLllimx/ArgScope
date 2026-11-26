import { defineConfig } from "../../sourcelens/web/node_modules/vite/dist/node/index.js";
import { fileURLToPath } from "node:url";
const path = (relative) => fileURLToPath(new URL(relative, import.meta.url));
export default defineConfig({
  root: path("./"),
  base: "/",
  resolve: {
    alias: {
      "react-dom": path("../../sourcelens/web/node_modules/react-dom"),
      react: path("../../sourcelens/web/node_modules/react"),
    },
  },
  build: {
    outDir: path("../workbench/review_web"),
    emptyOutDir: true,
    assetsDir: "review-assets",
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("/node_modules/katex/")) return "math";
        },
      },
    },
  },
  server: { host: "127.0.0.1", proxy: { "/api": "http://127.0.0.1:8767" } },
});
