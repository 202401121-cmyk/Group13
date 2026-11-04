// Explicit Vite setup (it replaces Lovable's @lovable.dev/vite-tanstack-config wrapper).
// TanStack Start builds the app and its server-side rendering; nitro turns that server into a plain
// Node program in .output/ that `npm start` runs.
import { fileURLToPath } from "node:url";
import { defineConfig, searchForWorkspaceRoot } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsConfigPaths from "vite-tsconfig-paths";
import { nitro } from "nitro/vite";

export default defineConfig({
  server: {
    // The backend only accepts browser calls from FRONTEND_ORIGIN, which defaults to http://localhost:3000.
    port: 3000,
    strictPort: true,
    // Live mode checks answers against the contract schemas in ../shared/contracts (@contracts/...).
    fs: {
      allow: [
        searchForWorkspaceRoot(process.cwd()),
        fileURLToPath(new URL("../shared/contracts", import.meta.url)),
      ],
    },
  },
  resolve: {
    alias: { "@contracts": fileURLToPath(new URL("../shared/contracts", import.meta.url)) },
    dedupe: ["react", "react-dom"],
  },
  plugins: [
    tsConfigPaths(),
    tanstackStart({
      // src/server.ts wraps TanStack Start's server entry with our own error page.
      server: { entry: "server" },
    }),
    nitro({
      preset: "node-server",
      // Pre-compress the built files (gzip and brotli) so `npm start` serves them compressed: about 70%
      // less to download on a phone. Found by the FE-4 Lighthouse runs (uses-text-compression).
      compressPublicAssets: { gzip: true, brotli: true },
    }),
    viteReact(),
    tailwindcss(),
  ],
});
