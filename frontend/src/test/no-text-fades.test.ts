/**
 * Text at part opacity fails contrast on the frames it is shown (F-13), so no animation may change the opacity of
 * anything that holds text. This reads the source, so a new fade fails here at once. The browser check is
 * e2e/quality/first-frame.spec.ts, which samples what really runs on every screen.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/styles.css", "utf8");

/** Each @keyframes block in the stylesheet: its name and body. */
function keyframes(): [string, string][] {
  return [...css.matchAll(/@keyframes ([\w-]+) \{\n([\s\S]*?)\n\}/g)].map((m) => [m[1]!, m[2]!]);
}

/** The body of the first rule with exactly this selector. */
function rule(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, selector).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf("}", start));
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "test" ? [] : sourceFiles(path);
    return /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

describe("no animation fades text", () => {
  it("only three keyframes touch opacity, and none of them draws text", () => {
    const fading = keyframes()
      .filter(([, body]) => body.includes("opacity"))
      .map(([name]) => name);
    // The typing cursor, the status dot and the line between the workflow steps.
    expect(fading.sort()).toEqual(["pipeline-line", "pipeline-status", "type-blink"]);
  });

  it("the entrance moves without fading", () => {
    expect(rule(".reveal-pending")).toContain("translate: 0 10px");
    for (const selector of [".reveal-pending", "[data-reveal]", ".reveal-complete"]) {
      expect(rule(selector), selector).not.toContain("opacity");
    }
  });

  it("no component uses a fade or an entrance class", () => {
    const fades = /\b(fade-in|fade-out|animate-in|animate-out|animate-rise-in|animate-fade-soft)\b/;
    const using = sourceFiles("src").filter((file) => fades.test(readFileSync(file, "utf8")));
    expect(using).toEqual([]);
  });

  it("toasts do not fade either", () => {
    expect(rule("[data-sonner-toaster] [data-sonner-toast]")).toContain(
      "transition-property: transform, height, box-shadow !important",
    );
    expect(rule('[data-sonner-toaster] [data-sonner-toast][data-mounted="false"]')).toContain(
      "opacity: 1",
    );
  });
});

describe("reduced motion turns it all off", () => {
  const reduced = css.slice(css.lastIndexOf("@media (prefers-reduced-motion: reduce)"));

  it("stops the entrance and every loop", () => {
    expect(reduced).toMatch(/\[data-reveal\] \{\n\s+translate: none;/);
    for (const selector of [".animate-ambient", ".product-float", ".pipeline-icon", ".skeleton"]) {
      expect(reduced, selector).toContain(selector);
    }
  });
});
