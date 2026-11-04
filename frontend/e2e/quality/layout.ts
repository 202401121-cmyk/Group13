/**
 * Layout probes for the quality specs: sideways overflow, clipped text, small touch targets and focus that
 * is hidden or obscured. Each runs inside the page and returns short, readable descriptions of what failed.
 */
import type { Page } from "@playwright/test";

/** Our touch-target minimum: WCAG 2.5.8 asks for 24 px, ShopSense's design asks for 44 px. */
export const MIN_TARGET = 44;

const DESCRIBE = `
  window.__describe = (el) => {
    const name = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("placeholder") || "")
      .replace(/\\s+/g, " ").trim().slice(0, 40);
    const id = el.id ? "#" + el.id : "";
    return el.tagName.toLowerCase() + id + (name ? ' "' + name + '"' : "");
  };
  window.__isHidden = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.display === "none" || cs.visibility === "hidden") return true;
      if (n.getAttribute("aria-hidden") === "true" && cs.pointerEvents === "none") return true;
      if (cs.position === "absolute" && cs.clip !== "auto" && n.getBoundingClientRect().width <= 1) return true;
    }
    return false;
  };
`;

async function install(page: Page) {
  await page.evaluate(DESCRIBE);
}

/**
 * Elements that stick out past the left or right edge of the viewport. body has overflow-x: hidden, so a
 * page can look fine while text is cut off at the edge; this finds those cases too.
 */
export async function horizontalOverflow(page: Page): Promise<string[]> {
  await install(page);
  return page.evaluate(() => {
    const w = window as unknown as {
      __describe: (el: Element) => string;
      __isHidden: (el: Element) => boolean;
    };
    const vw = document.documentElement.clientWidth;
    const out: string[] = [];
    if (document.documentElement.scrollWidth > vw + 1) {
      out.push(`page scrolls sideways: ${document.documentElement.scrollWidth}px > ${vw}px`);
    }
    for (const el of document.querySelectorAll("body *")) {
      if (!(el instanceof HTMLElement) && !(el instanceof SVGElement)) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.right <= vw + 1 && r.left >= -1) continue;
      if (w.__isHidden(el)) continue;
      // Only report the outermost offender, not every child inside it.
      const parent = el.parentElement?.getBoundingClientRect();
      if (parent && (parent.right > vw + 1 || parent.left < -1)) continue;
      out.push(
        `${w.__describe(el)} spans ${Math.round(r.left)}..${Math.round(r.right)} of ${vw}px`,
      );
    }
    return out;
  });
}

/** Text boxes whose content is wider or taller than the box and is cut off (overflow hidden or clip). */
export async function clippedText(page: Page): Promise<string[]> {
  await install(page);
  return page.evaluate(() => {
    const w = window as unknown as {
      __describe: (el: Element) => string;
      __isHidden: (el: Element) => boolean;
    };
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>("body *")) {
      const cs = getComputedStyle(el);
      const clipsX = cs.overflowX === "hidden" || cs.overflowX === "clip";
      const clipsY = cs.overflowY === "hidden" || cs.overflowY === "clip";
      if (!clipsX && !clipsY) continue;
      if (!el.textContent?.trim()) continue;
      if (el.closest(".sr-only") || w.__isHidden(el)) continue;
      if (el === document.body || el.tagName === "HTML") continue;
      const overX = clipsX && el.scrollWidth > el.clientWidth + 1;
      const overY = clipsY && el.scrollHeight > el.clientHeight + 1;
      if (overX || overY) {
        out.push(`${w.__describe(el)} cuts off text (${overX ? "width" : "height"})`);
      }
    }
    return out;
  });
}

/**
 * Pointer targets smaller than 44 x 44 px. Links inside a sentence are left out, as WCAG 2.5.8 allows.
 * Disabled controls are left out because pressing them does nothing.
 */
export async function smallTargets(page: Page, min = MIN_TARGET): Promise<string[]> {
  await install(page);
  return page.evaluate((minSize) => {
    const w = window as unknown as {
      __describe: (el: Element) => string;
      __isHidden: (el: Element) => boolean;
    };
    const sel =
      "a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [tabindex]:not([tabindex='-1'])";
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>(sel)) {
      if ((el as HTMLButtonElement).disabled) continue;
      if (w.__isHidden(el) || el.closest(".sr-only")) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const inSentence =
        el.tagName === "A" &&
        !!el.parentElement &&
        ["P", "SPAN", "LI", "DD"].includes(el.parentElement.tagName) &&
        (el.parentElement.textContent ?? "").trim().length >
          (el.textContent ?? "").trim().length + 1;
      if (inSentence) continue;
      if (r.height < minSize - 0.5 || r.width < minSize - 0.5) {
        out.push(`${w.__describe(el)} is ${Math.round(r.width)}x${Math.round(r.height)}px`);
      }
    }
    return out;
  }, min);
}

export interface FocusStop {
  name: string;
  /** A visible outline or ring. */
  indicator: boolean;
  /** The outline alone (box-shadow rings vanish in forced-colours mode). */
  outline: boolean;
  /** How much of the element a sticky header or fixed composer covers. */
  obscured: "no" | "partly" | "fully";
  inView: boolean;
  isBody: boolean;
}

/** Describes the element that has focus now. */
export async function focusStop(page: Page): Promise<FocusStop> {
  await install(page);
  return page.evaluate(() => {
    const w = window as unknown as { __describe: (el: Element) => string };
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) {
      return {
        name: "body",
        indicator: false,
        outline: false,
        obscured: "no" as const,
        inView: true,
        isBody: true,
      };
    }
    const cs = getComputedStyle(el);
    const outline = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 1;
    const ring = !!cs.boxShadow && cs.boxShadow !== "none";
    const r = el.getBoundingClientRect();
    // The visible parts of sticky and fixed bars that do not contain the focused element.
    const covers: DOMRect[] = [];
    for (const bar of document.querySelectorAll<HTMLElement>(
      "header, form, [data-sonner-toaster]",
    )) {
      const pos = getComputedStyle(bar).position;
      if (pos !== "sticky" && pos !== "fixed") continue;
      if (bar.contains(el)) continue;
      const parts = bar.tagName === "HEADER" ? [...bar.children] : [bar];
      for (const p of parts) covers.push(p.getBoundingClientRect());
    }
    const area = Math.max(1, r.width * r.height);
    let covered = 0;
    for (const c of covers) {
      const x = Math.max(0, Math.min(r.right, c.right) - Math.max(r.left, c.left));
      const y = Math.max(0, Math.min(r.bottom, c.bottom) - Math.max(r.top, c.top));
      covered = Math.max(covered, x * y);
    }
    const obscured = covered >= area - 1 ? "fully" : covered > 1 ? "partly" : "no";
    const inView = r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
    return {
      name: w.__describe(el),
      indicator: outline || ring,
      outline,
      obscured,
      inView,
      isBody: false,
    } as const;
  });
}

/**
 * Presses Tab (or Shift+Tab) through the page and describes every stop, until focus leaves the page or
 * comes back to where it started. A page that never lets focus out has a keyboard trap.
 */
export async function walkFocus(
  page: Page,
  key: "Tab" | "Shift+Tab",
  max = 120,
): Promise<{ stops: FocusStop[]; escaped: boolean }> {
  const stops: FocusStop[] = [];
  let first: string | null = null;
  for (let i = 0; i < max; i++) {
    await page.keyboard.press(key);
    const stop = await focusStop(page);
    if (stop.isBody) return { stops, escaped: true };
    if (first === null) first = stop.name;
    else if (stop.name === first && stops.length > 2) return { stops, escaped: true };
    stops.push(stop);
  }
  return { stops, escaped: false };
}

/** Presses Tab until the focused element matches, and fails if it is never reached. */
export async function tabTo(page: Page, matches: (name: string) => boolean, max = 80) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const stop = await focusStop(page);
    if (matches(stop.name)) return stop;
  }
  throw new Error("The element was never reached with Tab");
}
