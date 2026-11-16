// @vitest-environment node
// DEP-1 follow-up (S4-19): a Vite build keeps only the public values src/lib/config.ts reads by name. Every VITE_
// variable set where a build runs is offered to the browser's code; only what the code reads goes in.
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build, createBuilder } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(here, "../../..");

// A decoy no code asks for, and test values for the two keys the code reads. Random, so no file holds them by chance.
const tag = randomBytes(8).toString("hex");
const SET = {
  VITE_LEAK_CANARY: `leak-canary-${tag}`,
  VITE_API_BASE_URL: `https://allowed-${tag}.example`,
  VITE_API_MODE: `mode-${tag}`,
} as const;

let work = "";
let noEnvFiles = "";
let putAside: [string, string | undefined][] = [];

beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "vite-env-build-"));
  noEnvFiles = join(work, "env");
  mkdirSync(noEnvFiles);
  // Only these VITE_ variables are set while the builds run. Any the shell or the test runner set are put aside,
  // unread, and restored afterwards.
  putAside = Object.entries(process.env).filter(([key]) => key.startsWith("VITE_"));
  for (const [key] of putAside) delete process.env[key];
  Object.assign(process.env, SET);
});

afterAll(() => {
  for (const key of Object.keys(process.env)) if (key.startsWith("VITE_")) delete process.env[key];
  for (const [key, value] of putAside) if (value !== undefined) process.env[key] = value;
  if (work) rmSync(work, { recursive: true, force: true });
});

/** The JavaScript Vite builds from one module, in memory: what a browser would be sent. */
async function built(entry: string): Promise<string> {
  const result = await build({
    configFile: false,
    root: work,
    envDir: noEnvFiles, // an empty folder: no .env file is read, so the variables come only from those set above
    mode: "production",
    logLevel: "silent",
    publicDir: false,
    build: {
      write: false,
      minify: false,
      outDir: join(work, "out"),
      lib: { entry, formats: ["es"], fileName: "out" },
    },
  });
  return codeOf(result);
}

/**
 * The app's own browser code, as `npm run build` makes it: vite.config.ts with all its plugins, every dependency
 * included. In memory: nothing is written to .output/.
 */
async function builtApp(): Promise<string> {
  const builder = await createBuilder({
    configFile: join(frontend, "vite.config.ts"),
    root: frontend,
    envDir: noEnvFiles,
    mode: "production",
    logLevel: "silent",
    build: { write: false, emptyOutDir: false, outDir: join(work, "app") },
  });
  const client = builder.environments["client"];
  if (!client) throw new Error("vite.config.ts builds no client environment");
  return codeOf(await builder.build(client));
}

function codeOf(result: Awaited<ReturnType<typeof build>>): string {
  const outputs = Array.isArray(result) ? result : [result];
  return outputs
    .flatMap((output) => ("output" in output ? output.output : []))
    .flatMap((chunk) => (chunk.type === "chunk" ? [chunk.code] : []))
    .join("\n");
}

// The checks look for the values without printing the code, so a failure never shows a value it found.
describe("a build", () => {
  it("of src/lib/config.ts keeps the two public values, and no other VITE_ value", async () => {
    const code = await built(join(frontend, "src/lib/config.ts"));
    expect(code.length).toBeGreaterThan(0);
    expect(code.includes(SET.VITE_API_BASE_URL), "VITE_API_BASE_URL's value").toBe(true);
    expect(code.includes(SET.VITE_API_MODE), "VITE_API_MODE's value").toBe(true);
    expect(code.includes(SET.VITE_LEAK_CANARY), "the decoy's value").toBe(false);
    expect(code.includes("VITE_LEAK_CANARY"), "the decoy's name").toBe(false);
  }, 60_000);

  // The whole app, so a `define` in vite.config.ts or a dependency that reads the env whole is caught too.
  it("of the app's browser code keeps the API's address, and no other VITE_ value", async () => {
    const code = await builtApp();
    expect(code.includes(SET.VITE_API_BASE_URL), "VITE_API_BASE_URL's value").toBe(true);
    expect(code.includes(SET.VITE_LEAK_CANARY), "the decoy's value").toBe(false);
    expect(code.includes("VITE_LEAK_CANARY"), "the decoy's name").toBe(false);
  }, 120_000);

  it("of a module that reads the env whole keeps the decoy too, so the checks above can fail", async () => {
    const entry = join(work, "reads-whole.ts");
    writeFileSync(entry, `export const env = ${"import.meta"}.env;\n`, "utf8");
    const code = await built(entry);
    expect(code.includes(SET.VITE_LEAK_CANARY), "the decoy's value").toBe(true);
  }, 60_000);
});
