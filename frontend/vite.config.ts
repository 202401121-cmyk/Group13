// Explicit Vite setup (it replaces Lovable's @lovable.dev/vite-tanstack-config wrapper).
// TanStack Start builds the app and its server-side rendering; nitro turns that server into a plain
// Node program in .output/ that `npm start` runs.
import { defineConfig } from "vite";
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
  },
  resolve: {
    dedupe: ["react", "react-dom"],
  },
  plugins: [
    tsConfigPaths(),
    tanstackStart({
      // src/server.ts wraps TanStack Start's server entry with our own error page.
      server: { entry: "server" },
    }),
    nitro({ preset: "node-server" }),
    viteReact(),
    tailwindcss(),
  ],
});
