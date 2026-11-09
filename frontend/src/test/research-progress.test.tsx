/** What the progress screen works out from a run's events (US-12), and how it draws it. */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ResearchProgress } from "@/components/results/ResearchProgress";
import {
  STAGES,
  announcementFor,
  mergeEvents,
  sourceCounts,
  stageRows,
  type StageRow,
} from "@/lib/results/progress";
import type { ProgressEvent, ProgressStatus } from "@/lib/results/types";

const ev = (
  id: number,
  stage: string,
  status: ProgressStatus,
  message = "A plain message.",
  sources: Record<string, number> = {},
): ProgressEvent => ({ id, stage, status, message, sources });

const row = (rows: StageRow[], key: string) => rows.find((r) => r.key === key)!;
const NO_SOURCES = { reddit: 0, web_search: 0, review_site: 0, youtube: 0 };

describe("event order", () => {
  it("events arriving out of order are shown in event_id order", () => {
    const seen = mergeEvents(
      mergeEvents([], [ev(2, "research", "done"), ev(3, "extract", "started")]),
      [ev(1, "research", "started")],
    );
    expect(seen.map((e) => e.id)).toEqual([1, 2, 3]);
    // The late "started" must not pull a finished stage back to running.
    const rows = stageRows(seen);
    expect(row(rows, "research").state).toBe("done");
    expect(row(rows, "extract").state).toBe("running");
  });

  it("the rows come out the same whatever order the events are in", () => {
    const inOrder = [ev(1, "research", "started"), ev(2, "research", "done", "Finished.")];
    expect(stageRows([...inOrder].reverse())).toEqual(stageRows(inOrder));
    expect(announcementFor([...inOrder].reverse())).toBe(announcementFor(inOrder));
    expect(row(stageRows([...inOrder].reverse()), "research").state).toBe("done");
  });

  it("a duplicate event after a reconnect is ignored", () => {
    const seen = mergeEvents([], [ev(1, "research", "started"), ev(2, "research", "done")]);
    expect(mergeEvents(seen, [ev(2, "research", "failed", "A repeat that differs.")])).toBe(seen);
    expect(mergeEvents(seen, [ev(1, "research", "started"), ev(2, "research", "done")])).toBe(seen);
    expect(row(stageRows(seen), "research").state).toBe("done");
  });

  it("a replay after the stream reopens keeps each event once", () => {
    const seen = mergeEvents([], [ev(1, "research", "started"), ev(2, "research", "done")]);
    const replay = [ev(1, "research", "started"), ev(2, "research", "done")];
    const next = mergeEvents(seen, [...replay, ev(3, "extract", "started")]);
    expect(next.map((e) => e.id)).toEqual([1, 2, 3]);
  });
});

describe("stage rows", () => {
  it("lists the eight stages in the run's order, all waiting at first", () => {
    const rows = stageRows([]);
    expect(rows.map((r) => r.key)).toEqual([
      "research",
      "extract",
      "match",
      "verify",
      "enrich",
      "decide",
      "explain",
      "commerce",
    ]);
    expect(rows.every((r) => r.state === "waiting" && r.message === null)).toBe(true);
    expect(STAGES).toHaveLength(8);
  });

  it("a stage goes waiting, running, done", () => {
    expect(row(stageRows([]), "extract").state).toBe("waiting");
    const running = [ev(1, "extract", "started", "Reading what each source says.")];
    expect(row(stageRows(running), "extract")).toMatchObject({ state: "running", message: null });
    const done = [...running, ev(2, "extract", "done", "Finished reading the sources.")];
    expect(row(stageRows(done), "extract")).toMatchObject({
      state: "done",
      message: "Finished reading the sources.",
    });
  });

  it("a skipped stage shows its reason", () => {
    const reason = "That step took too long, so a simpler way was used.";
    const rows = stageRows([ev(1, "enrich", "started"), ev(2, "enrich", "fallback", reason)]);
    expect(row(rows, "enrich")).toMatchObject({ state: "skipped", message: reason });
  });

  it("a stage the backend calls done stays done, whatever its message says", () => {
    const rows = stageRows([ev(1, "enrich", "done", "Skipped because it wasn't needed.")]);
    expect(row(rows, "enrich")).toMatchObject({
      state: "done",
      message: "Skipped because it wasn't needed.",
    });
  });

  it("a failed stage shows it failed", () => {
    const rows = stageRows([ev(1, "verify", "failed", "That step did not work: it timed out.")]);
    expect(row(rows, "verify")).toMatchObject({
      state: "failed",
      message: "That step did not work: it timed out.",
    });
  });

  it("a stage this screen doesn't know still gets a row, in plain words", () => {
    const rows = stageRows([ev(1, "price_history", "started")]);
    expect(rows).toHaveLength(9);
    expect(row(rows, "price_history")).toMatchObject({ label: "Price history", state: "running" });
  });
});

describe("sources found", () => {
  it("sources found are shown per type as they arrive", () => {
    expect(sourceCounts([ev(1, "research", "started", "m", NO_SOURCES)])).toEqual([]);
    const counts = sourceCounts([
      ev(1, "research", "started", "m", NO_SOURCES),
      ev(2, "research", "done", "m", {
        ...NO_SOURCES,
        reddit: 20,
        web_search: 18,
        review_site: 10,
      }),
    ]);
    expect(counts).toEqual([
      { type: "reddit", label: "Reddit", count: 20 },
      { type: "web_search", label: "Web pages", count: 18 },
      { type: "review_site", label: "Review sites", count: 10 },
    ]);
  });

  it("the newest counts win, in event_id order", () => {
    const seen = mergeEvents(
      [],
      [
        ev(4, "extract", "started", "m", { reddit: 20 }),
        ev(2, "research", "done", "m", { reddit: 5 }),
      ],
    );
    expect(sourceCounts(seen)).toEqual([{ type: "reddit", label: "Reddit", count: 20 }]);
  });

  it("a later event with no counts doesn't wipe them", () => {
    const counts = sourceCounts([
      ev(2, "research", "done", "m", { reddit: 20 }),
      ev(3, "extract", "started", "m", NO_SOURCES),
    ]);
    expect(counts).toEqual([{ type: "reddit", label: "Reddit", count: 20 }]);
  });

  it("a source type this screen doesn't know is shown in plain words", () => {
    expect(sourceCounts([ev(1, "research", "done", "m", { trade_press: 2 })])).toEqual([
      { type: "trade_press", label: "Trade press", count: 2 },
    ]);
  });
});

describe("what a screen reader hears", () => {
  it("a stage change is announced", () => {
    expect(announcementFor([])).toBe("");
    expect(announcementFor([ev(1, "research", "started")])).toMatch(/^Finding .*: running\.$/);
    const done = [ev(1, "research", "started"), ev(2, "research", "done")];
    expect(announcementFor(done)).toMatch(/: done\. 1 of 8 steps finished\.$/);
    const skipped = [ev(1, "enrich", "fallback", "A simpler way was used.")];
    expect(announcementFor(skipped)).toMatch(/: skipped\. A simpler way was used\.$/);
    const failed = [ev(1, "verify", "failed", "That step did not work.")];
    expect(announcementFor(failed)).toMatch(/: failed\. That step did not work\.$/);
  });

  it("a count tick announces nothing", () => {
    const before = [ev(1, "research", "started", "m", NO_SOURCES)];
    const tick = [...before, ev(2, "research", "started", "m", { ...NO_SOURCES, reddit: 7 })];
    expect(announcementFor(tick)).toBe(announcementFor(before));
  });

  it("a count tick on an earlier stage doesn't announce that stage again", () => {
    const before = [ev(1, "research", "started"), ev(2, "extract", "started")];
    const tick = [...before, ev(3, "research", "started", "m", { reddit: 7 })];
    expect(announcementFor(tick)).toBe(announcementFor(before));
    expect(announcementFor(tick)).toMatch(/^Reading .*: running\.$/);
  });
});

describe("the progress list", () => {
  const html = (events: ProgressEvent[]) =>
    renderToStaticMarkup(<ResearchProgress events={events} />);

  it("says each state in words, not only by colour", () => {
    const out = html([
      ev(1, "research", "done", "Finished looking for sources."),
      ev(2, "extract", "started"),
      ev(3, "match", "fallback", "A simpler way was used."),
      ev(4, "verify", "failed", "That step did not work."),
    ]);
    for (const word of ["Done", "Running", "Skipped", "Failed", "Waiting"]) {
      expect(out).toContain(`>${word}<`);
    }
    expect(out).toContain("A simpler way was used.");
    expect(out).toContain("That step did not work.");
    expect(out).toContain('aria-current="step"');
  });

  it("stops the spinner when reduced motion is on", () => {
    const out = html([ev(1, "research", "started")]);
    expect(out).toContain("animate-spin");
    expect(out).toContain("motion-reduce:animate-none");
  });

  it("says so when looking found nothing", () => {
    const out = html([ev(1, "research", "done", "m", NO_SOURCES)]);
    expect(out).toContain("None found.");
    expect(out).not.toContain("Counted once");
  });

  it("holds a place for the counts, so the page doesn't jump when they arrive", () => {
    expect(html([])).toContain("Counted once ShopSense has finished looking");
    const out = html([ev(1, "research", "done", "m", { reddit: 20, review_site: 10 })]);
    expect(out).not.toContain("Counted once");
    expect(out).toContain("Reddit");
    expect(out).toContain("Review sites");
  });
});
