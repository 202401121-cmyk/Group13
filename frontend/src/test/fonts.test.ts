/**
 * The fonts stay on our own server and keep the rupee sign (F-16). This reads the source, so a font service or a
 * dropped subset fails here at once; e2e/quality/fonts.spec.ts checks what the browser really loads and draws.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const RUPEE = 0x20b9;
const css = readFileSync("src/fonts.css", "utf8");
const preloads = readFileSync("src/lib/fonts.ts", "utf8");

/** The web fonts' @font-face rules, leaving out the local stand-ins. */
const faces = [...css.matchAll(/@font-face \{([\s\S]*?)\n\}/g)]
  .map((m) => m[1]!)
  .filter((body) => body.includes("url("));

const field = (body: string, name: string) => new RegExp(`${name}:\\s*([^;]+);`).exec(body)?.[1];
const fileOf = (body: string) => /url\("([^"]+)"\)/.exec(body)![1]!;

function covers(body: string, codePoint: number): boolean {
  return (field(body, "unicode-range") ?? "").split(",").some((part) => {
    const [from, to] = part.trim().replace("U+", "").split("-");
    const start = parseInt(from!, 16);
    return codePoint >= start && codePoint <= parseInt(to ?? from!, 16);
  });
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return ["test", "types"].includes(entry.name) ? [] : sourceFiles(path);
    return /\.(tsx?|css)$/.test(entry.name) ? [path] : [];
  });
}

describe("fonts", () => {
  it("nothing in the app points at a font service", () => {
    const using = sourceFiles("src").filter((file) =>
      /fonts\.(googleapis|gstatic)\.com/.test(readFileSync(file, "utf8")),
    );
    expect(using).toEqual([]);
  });

  it("every file exists in its pinned package, and only Latin subsets are declared", () => {
    expect(faces).toHaveLength(7);
    for (const face of faces) {
      expect(existsSync(join("node_modules", fileOf(face))), fileOf(face)).toBe(true);
      expect(fileOf(face)).toMatch(/-latin(-ext)?-/);
    }
  });

  it("text never waits for a font", () => {
    for (const face of faces) expect(field(face, "font-display"), fileOf(face)).toBe("swap");
  });

  it("each family has an upright file that holds the rupee sign", () => {
    for (const family of ["Fraunces", "Geist", "Geist Mono"]) {
      const holding = faces.filter(
        (face) =>
          field(face, "font-family") === `"${family}"` &&
          field(face, "font-style") === "normal" &&
          covers(face, RUPEE),
      );
      expect(holding.map(fileOf), family).toHaveLength(1);
      expect(fileOf(holding[0]!)).toContain("latin-ext");
    }
  });

  it("only preloads files the stylesheet declares", () => {
    const files = [...preloads.matchAll(/from "([^"]+)\?url"/g)].map((m) => m[1]);
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) expect(css, file).toContain(file);
  });
});
