/**
 * Checks 6, 7 and 8 of the quality gate: motion and display preferences, contrast on the moving background,
 * and the background itself.
 *
 * The rebuilt frontend has no WebGL background. Its one moving background is a slow CSS colour wash behind the
 * home page hero (bg-ambient + animate-ambient in src/styles.css). These specs check that wash: it stops for
 * reduced motion, pauses off screen, does not stop typing and keeps text readable at its first and last
 * frame. webgl-off.spec.ts checks the app with WebGL turned off.
 */
import { expect, test, type Page } from "@playwright/test";
import { focusStop, walkFocus } from "./layout";
import { finishResearch, reachBrief, reachUnderstood, setTheme, waitForHydration } from "./helpers";

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("the background is still and everything is shown at once", async ({ page }) => {
    await page.clock.install();
    await page.goto("/");
    await waitForHydration(page, "#research-query");
    const wash = page.locator(".animate-ambient");
    expect(await wash.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
    const untyped = await page
      .locator("[aria-hidden] .invisible")
      .evaluateAll((els) => els.map((e) => e.textContent).join(""));
    expect(untyped, "typed text is complete at once").toBe("");
    const faded = await page
      .locator("[data-reveal]")
      .evaluateAll((els) => els.filter((e) => getComputedStyle(e).opacity !== "1").length);
    expect(faded, "no section waits to fade in").toBe(0);
    const looping = await page.evaluate(
      () =>
        document
          .getAnimations()
          .filter((a) => a.playState === "running" && a.effect?.getTiming().iterations === Infinity)
          .length,
    );
    expect(looping, "no endless animation runs").toBe(0);

    await reachBrief(page);
    await page.getByRole("button", { name: /Start research/ }).click();
    await finishResearch(page);
    await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
    const entrance = await page
      .locator(".animate-rise-in, .animate-fade-soft")
      .evaluateAll((els) => els.map((e) => getComputedStyle(e).animationName));
    expect(
      entrance.every((n) => n === "none"),
      "results appear without movement",
    ).toBe(true);
    await expect(page.getByText(/Sample laptop A/).first()).toBeVisible();
  });
});

test.describe("motion allowed", () => {
  test.use({ reducedMotion: "no-preference" });

  test("the background runs on screen and pauses off screen", async ({ page }) => {
    await page.goto("/");
    const wash = page.locator(".animate-ambient");
    await expect(wash).toHaveClass(/motion-visible/);
    expect(await wash.evaluate((el) => getComputedStyle(el).animationPlayState)).toBe("running");
    await page.locator("footer").scrollIntoViewIfNeeded();
    await expect(wash).not.toHaveClass(/motion-visible/);
    expect(await wash.evaluate((el) => getComputedStyle(el).animationPlayState)).toBe("paused");
  });

  test("the background stops drawing when the tab is hidden", async ({ page, context }) => {
    await page.goto("/");
    const other = await context.newPage();
    await other.goto("about:blank");
    await other.bringToFront();
    const state = await page.evaluate(() => document.visibilityState);
    test.skip(
      state !== "hidden",
      "This browser keeps background pages visible, so it can't be checked here",
    );
    const frames = await page.evaluate(
      () =>
        new Promise<number>((resolve) => {
          let n = 0;
          const tick = () => {
            n++;
            requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
          setTimeout(() => resolve(n), 1000);
        }),
    );
    console.log(`[motion] frames drawn in 1 s while hidden: ${frames}`);
    expect(frames).toBeLessThan(5);
  });

  test("typing is never dropped and stays smooth", async ({ page }) => {
    // The wash sits behind the hero only; the request box is further down, so the wash is paused (off
    // screen) whenever someone types. This checks typing itself: every key lands and frames keep coming.
    await page.goto("/");
    await waitForHydration(page, "#research-query");
    const box = page.locator("#research-query");
    const text = "Laptop for coding under 70k, at least 16 GB RAM, not too heavy, no refurbished";
    const measure = async (rate: number) => {
      await box.fill("");
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("Emulation.setCPUThrottlingRate", { rate });
      await page.evaluate(() => {
        const w = window as unknown as { __frames: number[] };
        w.__frames = [];
        const tick = (t: number) => {
          w.__frames.push(t);
          if (w.__frames.length < 100_000) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
      await box.focus();
      await page.keyboard.type(text, { delay: 30 });
      await expect(box, "no key press is lost").toHaveValue(text);
      const stats = await page.evaluate(() => {
        const f = (window as unknown as { __frames: number[] }).__frames;
        const gaps = f.slice(1).map((t, i) => t - f[i]!);
        const seconds = (f.at(-1)! - f[0]!) / 1000;
        return { fps: Math.round(f.length / seconds), worstGapMs: Math.round(Math.max(...gaps)) };
      });
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
      console.log(
        `[motion] typing at ${rate}x CPU: ${stats.fps} fps, longest frame ${stats.worstGapMs} ms`,
      );
      return stats;
    };
    const normal = await measure(1);
    expect(normal.fps, "frame rate while typing").toBeGreaterThanOrEqual(30);
    // With a 4x slower CPU the number depends on the machine; it is reported, and no key may be lost.
    await measure(4);
  });

  test("text on the moving background keeps its contrast at the first and last frame", async ({
    page,
  }) => {
    await page.goto("/");
    // Let the typing effect finish so every letter is drawn.
    await expect(
      page.locator("section").first().locator("[aria-hidden] .invisible").filter({ hasText: /./ }),
    ).toHaveCount(0, { timeout: 20_000 });
    const rows: string[] = [];
    for (const theme of ["light", "dark"] as const) {
      await setTheme(page, theme);
      for (const frame of ["first", "last"] as const) {
        await page.locator(".animate-ambient").evaluate((el, f) => {
          const a = el.getAnimations()[0];
          if (!a) return;
          a.pause();
          a.currentTime = f === "first" ? 0 : 26_000;
        }, frame);
        for (const [label, selector, min] of [
          ["heading", "section h1 > span[aria-hidden]", 3],
          ["heading accent", "section h1 em", 3],
          ["intro text", "section h1 + p > span[aria-hidden]", 4.5],
          ["link", "a[href='#how']", 4.5],
        ] as const) {
          const ratio = await worstContrast(page, selector);
          rows.push(`${theme} ${frame} frame, ${label}: ${ratio.toFixed(2)}:1`);
          expect.soft(ratio, `${label} (${theme}, ${frame} frame)`).toBeGreaterThanOrEqual(min);
        }
      }
    }
    console.log(`[contrast] ${rows.join("; ")}`);
  });
});

/**
 * Contrast of an element's text against the lightest and darkest pixel behind it. All text in its section is
 * hidden (so neighbouring letters are not counted as background), the element's area is photographed, and
 * both are measured in the page with a canvas.
 */
async function worstContrast(page: Page, selector: string): Promise<number> {
  const el = page.locator(selector).first();
  const color = await el.evaluate((e) => getComputedStyle(e).color);
  const hide = (show: boolean) =>
    el.evaluate((e, visible) => {
      const section = e.closest("section") ?? e;
      for (const n of [section, ...section.querySelectorAll<HTMLElement>("*")]) {
        if (visible) (n as HTMLElement).style.removeProperty("color");
        else (n as HTMLElement).style.setProperty("color", "transparent", "important");
      }
    }, show);
  await hide(false);
  const shot = await el.screenshot({ animations: "allow" });
  await hide(true);
  return page.evaluate(
    async ({ png, text }) => {
      const lum = ([r, g, b]: number[]) => {
        const lin = (v: number) => {
          const s = v / 255;
          return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * lin(r!) + 0.7152 * lin(g!) + 0.0722 * lin(b!);
      };
      const probe = document.createElement("canvas").getContext("2d")!;
      probe.fillStyle = text;
      probe.fillRect(0, 0, 1, 1);
      const t = lum([...probe.getImageData(0, 0, 1, 1).data]);
      const img = new Image();
      img.src = `data:image/png;base64,${png}`;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width;
      c.height = img.height;
      const ctx = c.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let lo = 1;
      let hi = 0;
      for (let i = 0; i < d.length; i += 4) {
        const l = lum([d[i]!, d[i + 1]!, d[i + 2]!]);
        lo = Math.min(lo, l);
        hi = Math.max(hi, l);
      }
      const ratio = (a: number, b: number) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      return Math.min(ratio(t, lo), ratio(t, hi));
    },
    { png: shot.toString("base64"), text: color },
  );
}

test.describe("forced colours", () => {
  test("focus outlines, button edges and badges stay visible", async ({ page }) => {
    await page.emulateMedia({ forcedColors: "active" });
    await page.clock.install();

    const noOutline: string[] = [];
    const check = async (label: string) => {
      const { stops } = await walkFocus(page, "Tab");
      for (const s of stops) if (!s.outline) noOutline.push(`${label}: ${s.name}`);
    };
    await page.goto("/");
    await check("home");
    await page.goto("/research");
    await waitForHydration(page, "#request-text");
    await check("research");
    await reachUnderstood(page);
    await page.getByRole("button", { name: "Edit budget" }).click();
    await page.keyboard.press("Tab");
    expect((await focusStop(page)).outline).toBe(true);
    await page.getByRole("button", { name: "Cancel" }).click();
    await check("what we understood");

    const buttons = await page.locator(".primary-button:visible").evaluateAll((els) =>
      els
        .filter((e) => {
          const cs = getComputedStyle(e);
          return cs.borderTopStyle === "none" || parseFloat(cs.borderTopWidth) < 1;
        })
        .map((e) => (e.textContent ?? "").trim()),
    );
    console.log(`[forced colours] buttons without an edge: ${buttons.join(", ") || "none"}`);
    expect.soft(buttons, "primary buttons keep a visible edge").toEqual([]);

    await reachBrief(page);
    await page.getByLabel(/Preview scenario/).selectOption("disputed");
    await page.getByRole("button", { name: /Start research/ }).click();
    await finishResearch(page);
    await expect(page.getByRole("heading", { level: 1, name: /Results for your/ })).toBeVisible();
    await page.getByText("Show all criteria").first().click();
    await check("results");
    const badges = await page.getByText(/Sources disagree|Preview: mock data/).evaluateAll((els) =>
      els.map((e) => {
        const cs = getComputedStyle(e);
        return { text: e.textContent, color: cs.color, visible: cs.visibility === "visible" };
      }),
    );
    expect(badges.length).toBeGreaterThan(0);
    for (const b of badges) expect(b.visible && b.color !== "rgba(0, 0, 0, 0)").toBe(true);

    console.log(`[forced colours] focus without an outline: ${noOutline.join("; ") || "none"}`);
    expect.soft(noOutline, "every focus stop keeps an outline in forced colours").toEqual([]);
  });
});
