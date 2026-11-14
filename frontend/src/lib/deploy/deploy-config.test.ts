// DEP-1 (S3-17, P-08): the frontend's staging settings on Vercel, and the laptop's `npm start`, from one config.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { nitroPreset } from "./nitro-preset";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(here, "../../..");
const src = join(frontend, "src");

describe("nitroPreset", () => {
  it("builds the plain Node server that `npm start` runs, by default", () => {
    expect(nitroPreset({})).toBe("node-server");
  });

  it("builds for Vercel's functions when Vercel runs the build", () => {
    expect(nitroPreset({ VERCEL: "1" })).toBe("vercel");
  });

  it("lets NITRO_PRESET choose, over both", () => {
    expect(nitroPreset({ VERCEL: "1", NITRO_PRESET: "node-server" })).toBe("node-server");
    expect(nitroPreset({ NITRO_PRESET: " vercel " })).toBe("vercel");
  });

  it("ignores an empty value", () => {
    expect(nitroPreset({ VERCEL: "", NITRO_PRESET: "" })).toBe("node-server");
  });
});

describe("vercel.json", () => {
  const config = JSON.parse(readFileSync(join(frontend, "vercel.json"), "utf8")) as Record<
    string,
    unknown
  >;

  it("names TanStack Start, so Vercel never has to guess the framework", () => {
    expect(config["framework"]).toBe("tanstack-start");
  });

  it("installs with npm ci, from the lockfile, as everywhere else", () => {
    expect(config["installCommand"]).toBe("npm ci");
  });

  it("builds Vercel's functions by naming the preset, never by relying on Vercel's system variables", () => {
    const command = String(config["buildCommand"]);
    expect(command).toMatch(/^NITRO_PRESET=vercel npm run build$/);
    expect(nitroPreset({ NITRO_PRESET: "vercel" })).toBe("vercel"); // even with VERCEL unset (system variables off)
  });
});

function sourceFiles(folder: string): string[] {
  return readdirSync(folder).flatMap((name) => {
    const path = join(folder, name);
    if (statSync(path).isDirectory()) return name === "types" ? [] : sourceFiles(path); // types/: generated contracts
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

describe("VITE_ variables", () => {
  // The source names only these two. The build copies every VITE_ variable set where it runs into the browser's code
  // (src/lib/api/client.ts reads import.meta.env whole), so the Vercel project must set no others and keep its own
  // VITE_VERCEL_* copies off: docs/deploy.md, section 3.
  it("named in the source are only the two public settings", () => {
    const names = new Set(
      sourceFiles(src).flatMap(
        (file) => readFileSync(file, "utf8").match(/\bVITE_[A-Z0-9_]+\b/g) ?? [],
      ),
    );
    expect([...names].sort()).toEqual(["VITE_API_BASE_URL", "VITE_API_MODE"]);
  });
});
