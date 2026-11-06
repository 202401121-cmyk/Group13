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
const isSmall = (body: string) => fileOf(body).startsWith("./assets/fonts/");
const uprightOf = (family: string) =>
  faces.filter(
    (face) =>
      field(face, "font-family") === `"${family}"` && field(face, "font-style") === "normal",
  );

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

  it("every file exists, and only Latin subsets of the pinned packages are declared", () => {
    expect(faces).toHaveLength(16);
    for (const face of faces) {
      const file = fileOf(face);
      if (isSmall(face)) {
        expect(existsSync(join("src", file)), file).toBe(true);
      } else {
        expect(existsSync(join("node_modules", file)), file).toBe(true);
        expect(file).toMatch(/-latin(-ext)?-/);
      }
    }
  });

  it("the small files come after the packaged ones, so they win for the characters they hold", () => {
    for (const family of ["Fraunces", "Geist", "Geist Mono"]) {
      const upright = uprightOf(family);
      const firstSmall = upright.findIndex(isSmall);
      expect(firstSmall, family).toBeGreaterThan(0);
      expect(upright.slice(firstSmall).every(isSmall), family).toBe(true);
    }
  });

  it("the packaged files stay declared, so a rare letter or a heavier weight gets the real font", () => {
    const E_ACUTE = 0xe9;
    for (const family of ["Fraunces", "Geist", "Geist Mono"]) {
      const upright = uprightOf(family);
      const holding = upright.filter((face) => covers(face, E_ACUTE));
      expect(holding.length, family).toBeGreaterThan(0);
      expect(holding.some(isSmall), family).toBe(false);
      expect(
        upright.some((face) => /^\d+ 900$/.test(field(face, "font-weight") ?? "")),
        family,
      ).toBe(true);
    }
  });

  it("the italic file holds every letter of the home page's italic text", () => {
    const hero = faces.find((face) => fileOf(face).includes("hero-italic"))!;
    const route = readFileSync("src/routes/index.tsx", "utf8");
    const text = /\{ text: "([^"]+)", as: "em"/.exec(route)![1]!;
    for (const letter of text) expect(covers(hero, letter.codePointAt(0)!), letter).toBe(true);
  });

  it("text never waits for a font", () => {
    for (const face of faces) expect(field(face, "font-display"), fileOf(face)).toBe("swap");
  });

  it("each family has an upright file that holds the rupee sign", () => {
    for (const family of ["Fraunces", "Geist", "Geist Mono"]) {
      const holding = uprightOf(family).filter((face) => covers(face, RUPEE));
      expect(holding.length, family).toBeGreaterThan(0);
      const expected = family === "Geist Mono" ? /latin-ext/ : /rupee/;
      expect(fileOf(holding.at(-1)!), family).toMatch(expected);
    }
  });

  it("only preloads small files the stylesheet declares", () => {
    const files = [...preloads.matchAll(/from "@\/([^"]+)\?url"/g)].map((m) => m[1]!);
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) expect(css, file).toContain(`./${file}`);
  });
});
