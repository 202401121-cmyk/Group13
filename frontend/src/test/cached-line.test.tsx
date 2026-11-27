/**
 * US-34 (S5-7): a repeated search answered from the cache says when its search was made, in one line of the results
 * header. A fresh search, or one with no usable time, says nothing.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import { ResultsView } from "@/components/results/ResultsView";
import { cachedLine } from "@/lib/results/cachedLine";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import type { SearchResult } from "@/types/contracts/SearchResult";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a href="/" className={className}>
      {children}
    </a>
  ),
}));

const example = searchExample as unknown as SearchResult;
const MADE = "2026-11-28T08:45:00Z";

let root: Root | null = null;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
});

function cachedResult(): SearchResult {
  return { ...structuredClone(example), cached: true, cached_at: MADE };
}

describe("cachedLine", () => {
  const made = new Date(MADE);
  const time = made.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });

  it("gives the time alone for a search made today", () => {
    expect(cachedLine(MADE, made.getTime() + 60_000)).toBe(`From a search made at ${time}`);
  });

  it("gives the day too for a search made another day", () => {
    const day = made.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
    expect(cachedLine(MADE, made.getTime() + 2 * 86_400_000)).toBe(
      `From a search made at ${day}, ${time}`,
    );
  });

  it("says nothing without a usable time", () => {
    expect(cachedLine(null, Date.now())).toBeNull();
    expect(cachedLine(undefined, Date.now())).toBeNull();
    expect(cachedLine("not a time", Date.now())).toBeNull();
  });
});

describe("the live adapter", () => {
  it("passes a cached result's time on, and none for a fresh search", () => {
    expect(resultFromSearch(cachedResult(), []).cachedAt).toBe(MADE);
    expect(
      resultFromSearch({ ...cachedResult(), cached: false, cached_at: null }, []).cachedAt,
    ).toBeNull();
  });
});

describe("the results header", () => {
  function render(result: SearchResult): HTMLElement {
    const host = document.body.appendChild(document.createElement("div"));
    root = createRoot(host);
    act(() =>
      root!.render(
        <ResultsView result={resultFromSearch(result, [])} heading="Results" sessionId="sess_x" />,
      ),
    );
    return host;
  }

  it("shows the line on a cached result", () => {
    expect(render(cachedResult()).textContent).toContain("From a search made at");
  });

  it("shows nothing of the kind on a fresh search", () => {
    expect(render({ ...cachedResult(), cached: false, cached_at: null }).textContent).not.toContain(
      "From a search made",
    );
  });
});
