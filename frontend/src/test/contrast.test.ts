/**
 * WCAG AA: secondary text (--muted-foreground) needs a contrast of at least 4.5:1 on every surface it sits
 * on, in light and dark mode. The ratios are worked out from the oklch tokens in src/styles.css.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

type Oklch = [number, number, number];

const css = readFileSync(resolve(__dirname, "../styles.css"), "utf8");

function tokensOf(selector: string): Record<string, Oklch> {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} block in styles.css`).toBeGreaterThanOrEqual(0);
  const body = css.slice(start, css.indexOf("}", start));
  const out: Record<string, Oklch> = {};
  for (const m of body.matchAll(/--([a-z-]+):\s*oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)/g)) {
    out[m[1]!] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}

/** Relative luminance of an oklch colour (oklch -> oklab -> linear sRGB, clipped to the sRGB gamut). */
function luminance([L, C, h]: Oklch): number {
  const a = C * Math.cos((h * Math.PI) / 180);
  const b = C * Math.sin((h * Math.PI) / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const clip = (v: number) => Math.min(1, Math.max(0, v));
  const r = clip(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s);
  const g = clip(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s);
  const bl = clip(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s);
  return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
}

function contrast(x: Oklch, y: Oklch): number {
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe("muted text contrast", () => {
  it("matches known WCAG ratios (black on white is 21:1)", () => {
    expect(contrast([0, 0, 0], [1, 0, 0])).toBeCloseTo(21, 1);
    expect(contrast([1, 0, 0], [1, 0, 0])).toBeCloseTo(1, 5);
  });

  for (const [mode, selector] of [
    ["light", ":root"],
    ["dark", ".dark"],
  ] as const) {
    for (const surface of ["background", "muted", "secondary", "accent", "card"]) {
      it(`is at least 4.5:1 on --${surface} in ${mode} mode`, () => {
        const t = tokensOf(selector);
        expect(contrast(t["muted-foreground"]!, t[surface]!)).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});

describe("ink panel contrast (the deep panels on home, sign-in and the profile)", () => {
  for (const [mode, selector] of [
    ["light", ":root"],
    ["dark", ".dark"],
  ] as const) {
    for (const surface of ["ink", "ink-raised"]) {
      for (const text of ["ink-foreground", "ink-muted"]) {
        it(`--${text} is at least 4.5:1 on --${surface} in ${mode} mode`, () => {
          const t = tokensOf(selector);
          expect(contrast(t[text]!, t[surface]!)).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
    it(`--ink-foreground is at least 4.5:1 on an icon's --ink-well in ${mode} mode`, () => {
      const t = tokensOf(selector);
      expect(contrast(t["ink-foreground"]!, t["ink-well"]!)).toBeGreaterThanOrEqual(4.5);
    });
    it(`a --glow icon is at least 3:1 on its --ink-well in ${mode} mode`, () => {
      const t = tokensOf(selector);
      expect(contrast(t["glow"]!, t["ink-well"]!)).toBeGreaterThanOrEqual(3);
    });
  }

  it("the avatar's white initials are at least 4.5:1 on every stop of its disc", () => {
    const start = css.indexOf("@utility avatar-disc {");
    expect(start).toBeGreaterThanOrEqual(0);
    const gradient = css.slice(start, css.indexOf(");", start));
    const stops = [...gradient.matchAll(/oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)/g)].map(
      (m) => [Number(m[1]), Number(m[2]), Number(m[3])] as Oklch,
    );
    expect(stops).toHaveLength(3);
    const initials = tokensOf(":root")["ink-foreground"]!;
    for (const stop of stops) expect(contrast(initials, stop)).toBeGreaterThanOrEqual(4.5);
  });
});
