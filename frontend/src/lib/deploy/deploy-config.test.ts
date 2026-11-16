// DEP-1 (S3-17, P-08): the frontend's staging settings on Vercel, and the laptop's `npm start`, from one config.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PUBLIC_ENV_KEYS } from "@/lib/config";

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

/** Every script in src/, tests and generated contract types included. */
function everyScript(folder: string): string[] {
  return readdirSync(folder).flatMap((name) => {
    const path = join(folder, name);
    if (statSync(path).isDirectory()) return everyScript(path);
    return /\.(ts|tsx|mts|js|jsx|mjs)$/.test(name) ? [path] : [];
  });
}

const WHOLE = "(the env read whole)";

// Vite's own values, set by the build itself and never from a variable: safe to read anywhere.
const VITE_BUILT_INS = new Set(["BASE_URL", "MODE", "DEV", "PROD", "SSR"]);

/**
 * Each place a file reads Vite's env: the key, for `import.meta.env.KEY`, else WHOLE. The file is parsed, so a comment
 * or a string that names the env is not a read. Every form a build fills in counts, and any but `.KEY` as WHOLE:
 * `import.meta.env` passed on, spread or destructured, `import.meta.env["KEY"]`, `import.meta.env?.KEY` and
 * `import.meta["env"]`. (`const m = import.meta; m.env` isn't seen, but a build doesn't fill that in either.)
 */
function envReads(fileName: string, text: string): string[] {
  const kind = /x$/.test(fileName) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, kind);
  const reads: string[] = [];
  const visit = (node: ts.Node): void => {
    if (isEnv(node)) {
      const parent = node.parent;
      const byName =
        ts.isPropertyAccessExpression(parent) &&
        parent.expression === node &&
        !parent.questionDotToken &&
        ts.isIdentifier(parent.name);
      reads.push(byName ? parent.name.text : WHOLE);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return reads;
}

/** `import.meta.env` or `import.meta["env"]`. */
function isEnv(node: ts.Node): boolean {
  const onImportMeta = (target: ts.Expression) =>
    ts.isMetaProperty(target) && target.keywordToken === ts.SyntaxKind.ImportKeyword;
  if (ts.isPropertyAccessExpression(node)) {
    return onImportMeta(node.expression) && node.name.text === "env";
  }
  if (ts.isElementAccessExpression(node)) {
    const key = node.argumentExpression;
    return onImportMeta(node.expression) && ts.isStringLiteralLike(key) && key.text === "env";
  }
  return false;
}

describe("VITE_ variables", () => {
  // Vite copies into the browser's code what the code reads from its env: a key read by name copies that one value,
  // the env read whole copies every VITE_ variable set where the build runs. So src/lib/config.ts reads each public
  // key by name, from its allow-list, and no other file reads a VITE_ key (DEP-1 follow-up, S4-19).
  // vite-env-build.test.ts checks what a build keeps.
  const reads = everyScript(src).flatMap((path) => {
    const file = relative(src, path).replace(/\\/g, "/");
    return envReads(path, readFileSync(path, "utf8")).map((key) => ({ file, key }));
  });

  it("named in the source are exactly the allow-list", () => {
    const names = new Set(
      sourceFiles(src).flatMap(
        (file) => readFileSync(file, "utf8").match(/\bVITE_[A-Z0-9_]+\b/g) ?? [],
      ),
    );
    expect([...names].sort()).toEqual([...PUBLIC_ENV_KEYS].sort());
  });

  it("are never read as the whole env, in any file in src/", () => {
    expect(reads.filter(({ key }) => key === WHOLE).map(({ file }) => file)).toEqual([]);
  });

  it("are read only in src/lib/config.ts, each by name, and only the allow-list's", () => {
    const named = reads.filter(({ key }) => key !== WHOLE && !VITE_BUILT_INS.has(key));
    expect(named.map(({ file, key }) => `${file}: ${key}`).sort()).toEqual(
      PUBLIC_ENV_KEYS.map((key) => `lib/config.ts: ${key}`).sort(),
    );
  });

  it("the check above finds every way of reading the env, and skips comments and strings", () => {
    const env = "import.meta" + ".env"; // spelled in two parts, so this file holds no read of its own
    const whole = [
      `const env = ${env};`,
      `const { VITE_API_MODE } = ${env};`,
      `const all = { ...${env} };`,
      `apiConfig(${env});`,
      `const mode = ${env}["VITE_API_MODE"];`,
      `const mode = ${env}?.VITE_API_MODE;`,
      `const env = import.meta["env"];`,
    ];
    for (const sample of whole) expect(envReads("sample.ts", sample), sample).toEqual([WHOLE]);
    expect(envReads("sample.ts", `const mode = ${env}.VITE_API_MODE;`)).toEqual(["VITE_API_MODE"]);
    expect(envReads("sample.tsx", `const p = <p>{${env}.VITE_X}</p>;`)).toEqual(["VITE_X"]);
    expect(envReads("sample.ts", `// ${env}\n/* ${env} */\nconst s = "${env}";`)).toEqual([]);
  });
});
